from __future__ import annotations

import hashlib
import json
import os
import re
import tomllib
import typing
import warnings
from contextlib import contextmanager
from os.path import expandvars
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from pydantic import (
    AliasChoices,
    AliasGenerator,
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    field_validator,
    model_validator,
)
from pydantic_settings import (
    BaseSettings,
    InitSettingsSource,
    JsonConfigSettingsSource,
    PydanticBaseSettingsSource,
    PyprojectTomlConfigSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
    YamlConfigSettingsSource,
)

from uv_toolbox.errors import (
    ConfigFileNotFoundError,
    EnvironmentNotFoundError,
    MissingConfigFileError,
    MultipleEnvironmentsError,
)
from uv_toolbox.lockfile import UvToolboxLock, read_lockfile
from uv_toolbox.utils import _filter_nulls

if typing.TYPE_CHECKING:
    from collections.abc import Iterator

    import typer


def _to_kebab(name: str) -> str:
    """Convert snake_case to kebab-case."""
    return name.replace('_', '-')


def _validation_alias(name: str) -> AliasChoices:
    """Accept both snake_case and kebab-case for settings keys.

    `AliasChoices` order matters: we list the snake_case field name first so if both
    variants are present (e.g. env var + pyproject), the env var wins.
    """
    return AliasChoices(name, _to_kebab(name))


_ALIASES = AliasGenerator(
    validation_alias=_validation_alias,
    serialization_alias=_to_kebab,
)

# Bump when environment materialization semantics change. Including this in
# the CAS hash moves environments to fresh venvs after an upgrade.
_CACHE_FORMAT_VERSION = 3

# Bump when the inputs covered by UvToolboxEnvironment.lock_fingerprint
# change, so existing repo lockfiles are re-locked automatically.
_LOCK_FINGERPRINT_VERSION = 2

# `[tool.uv]` keys that uv-toolbox sets itself and so may not be passed through.
_MANAGED_UV_KEYS = {
    'package': 'uv-toolbox always generates a non-package project (package = false).',
    'sources': "use the environment's top-level 'sources' key instead.",
}

_DEPRECATED_REQUIREMENT_KEYS = ('requirements', 'requirements_file', 'requirements-file')


class UvSource(BaseModel):
    """One entry of an environment's `sources` table, as in `[tool.uv.sources]`.

    Exactly one of `git`, `path`, `url` or `index` must be set. Relative
    `path` values are resolved against the directory of the config file.
    """

    git: str | None = None
    tag: str | None = None
    branch: str | None = None
    rev: str | None = None
    subdirectory: str | None = None
    path: str | None = None
    url: str | None = None
    index: str | None = None
    editable: bool | None = None
    package: bool | None = None
    marker: str | None = None
    extra: str | None = None
    group: str | None = None

    model_config = ConfigDict(extra='forbid')

    @model_validator(mode='after')
    def check_kind(self) -> UvSource:
        """Validate that the source names exactly one location."""
        kinds = [kind for kind in ('git', 'path', 'url', 'index') if getattr(self, kind) is not None]
        if len(kinds) != 1:
            msg = 'A source must set exactly one of git, path, url or index.'
            raise ValueError(msg)
        refs = [ref for ref in ('tag', 'branch', 'rev') if getattr(self, ref) is not None]
        if refs and self.git is None:
            msg = f'{refs[0]!r} is only valid for git sources.'
            raise ValueError(msg)
        if len(refs) > 1:
            msg = 'A git source may set only one of tag, branch or rev.'
            raise ValueError(msg)
        return self

    def to_toml_data(self) -> dict[str, object]:
        """Return the source as `[tool.uv.sources]` data, omitting unset keys."""
        return self.model_dump(exclude_none=True)


class UvToolboxEnvironment(BaseModel, alias_generator=_ALIASES, populate_by_name=True):
    """Definition of a UV toolbox environment.

    Attributes:
        name: The environment's name, used to select it on the command line.
        dependencies: PEP 508 requirement strings, as in a pyproject's
            `[project] dependencies`.
        sources: Where to fetch named dependencies from, as in
            `[tool.uv.sources]`.
        requires_python: The Python versions the environment is locked for.
            Defaults to the previously locked value, or to the version of the
            interpreter uv finds.
        uv: Settings copied verbatim into the generated project's `[tool.uv]`
            table.
        requirements: Deprecated. A pip requirements string (mutually
            exclusive with dependencies and requirements_file).
        requirements_file: Deprecated. A path to a pip requirements file
            (mutually exclusive with dependencies and requirements).
        environment: Environment variables for uv commands, `exec` and shims.
            `$VAR` references are expanded when used.
        executables_override: Exact executable names to expose instead of
            discovering scripts from first-order requirements. An empty list
            exposes no executables.
        omit_executables: Executable names to omit from automatic discovery.
    """

    name: str
    dependencies: list[str] | None = None
    sources: dict[str, UvSource | list[UvSource]] = Field(default_factory=dict)
    requires_python: str | None = None
    uv: dict[str, typing.Any] = Field(default_factory=dict)
    requirements: str | None = None
    requirements_file: Path | None = None
    environment: dict[str, str] = Field(default_factory=dict)
    executables_override: list[str] | None = Field(
        default=None,
        validation_alias=AliasChoices(
            'executables_override',
            'executables-override',
            'executables',
        ),
    )
    omit_executables: list[str] = Field(default_factory=list)

    # Injected at settings load time from the repo lockfile; not part of the
    # config schema. When set, _get_requirements_hash derives the CAS address
    # from the locked packages instead of the declared inputs.
    _locked_uv_lock: str | None = PrivateAttr(default=None)

    @model_validator(mode='before')
    @classmethod
    def warn_about_legacy_executables(cls, data: object) -> object:
        """Warn when the legacy executable override key is used."""
        if isinstance(data, dict) and 'executables' in data:
            warnings.warn(
                "The 'executables' configuration key is deprecated and will be "
                "removed in uv-toolbox 1.0; use 'executables_override' instead.",
                FutureWarning,
                stacklevel=2,
            )
        return data

    @model_validator(mode='before')
    @classmethod
    def warn_about_requirements(cls, data: object) -> object:
        """Warn when the deprecated requirements keys are used."""
        if isinstance(data, dict) and any(data.get(key) for key in _DEPRECATED_REQUIREMENT_KEYS):
            warnings.warn(
                f'Environment {data.get("name")!r}: the requirements and '
                'requirements_file configuration keys are deprecated and will be '
                "removed in uv-toolbox 1.0; use 'dependencies' and 'sources' "
                'instead. Run `uvtb migrate-config` to convert your config.',
                FutureWarning,
                stacklevel=2,
            )
        return data

    @field_validator('dependencies')
    @classmethod
    def check_dependencies(cls, value: list[str] | None) -> list[str] | None:
        """Validate that every dependency is a PEP 508 requirement."""
        for dependency in value or []:
            try:
                Requirement(dependency)
            except InvalidRequirement as exc:
                msg = f'Invalid dependency {dependency!r}: {exc}'
                raise ValueError(msg) from exc
        return value

    @field_validator('requires_python')
    @classmethod
    def check_requires_python(cls, value: str | None) -> str | None:
        """Validate that requires_python is a version specifier."""
        if value is not None:
            try:
                SpecifierSet(value)
            except InvalidSpecifier as exc:
                msg = f'Invalid requires_python {value!r}: {exc}'
                raise ValueError(msg) from exc
        return value

    @field_validator('uv')
    @classmethod
    def check_uv_passthrough(cls, value: dict[str, typing.Any]) -> dict[str, typing.Any]:
        """Reject `[tool.uv]` keys that uv-toolbox manages itself."""
        for key, reason in _MANAGED_UV_KEYS.items():
            if key in value:
                msg = f'The {key!r} key cannot be set in the uv passthrough: {reason}'
                raise ValueError(msg)
        return value

    @model_validator(mode='after')
    def check_requirements(self) -> UvToolboxEnvironment:
        """Validate that exactly one way of declaring dependencies is used."""
        declared = [self.dependencies is not None, bool(self.requirements), bool(self.requirements_file)]
        if sum(declared) != 1:
            msg = 'Exactly one of dependencies, requirements or requirements_file must be set.'
            raise ValueError(msg)
        if self.sources and self.dependencies is None:
            msg = 'sources can only be used with dependencies, not with requirements or requirements_file.'
            raise ValueError(msg)
        return self

    @property
    def uses_deprecated_requirements(self) -> bool:
        """Whether dependencies are declared with requirements or requirements_file."""
        return self.dependencies is None

    @property
    def locked_uv_lock(self) -> str | None:
        """The environment's uv.lock content from the repo lockfile, if any."""
        return self._locked_uv_lock

    @staticmethod
    def _normalize_requirements(req: str) -> str:
        """Normalize requirements string for consistent hashing.

        - Removes comments and blank lines
        - Strips whitespace
        - Sorts lines alphabetically for consistency
        """
        lines = [line.strip() for line in req.strip().split('\n')]
        # Remove empty lines and comments
        lines = [line for line in lines if line and not line.startswith('#')]
        # Sort for consistency
        return '\n'.join(sorted(lines))

    def requirements_text(self) -> str:
        """Return the deprecated requirements content (inline or from the file)."""
        if self.requirements_file is not None:
            return self.requirements_file.read_text()
        return self.requirements or ''

    def _declared_inputs(self) -> dict[str, object]:
        """Return the configured inputs that determine what gets resolved."""
        inputs: dict[str, object] = {
            'requires_python': self.requires_python,
            'uv': self.uv,
        }
        if self.dependencies is not None:
            inputs['dependencies'] = self.dependencies
            inputs['sources'] = self.sources_data()
        else:
            inputs['requirements'] = self._normalize_requirements(self.requirements_text())
        return inputs

    def sources_data(self) -> dict[str, object]:
        """Return `sources` as `[tool.uv.sources]` data, paths as configured."""
        return {
            name: [entry.to_toml_data() for entry in source] if isinstance(source, list) else source.to_toml_data()
            for name, source in self.sources.items()
        }

    def _venv_inputs(self, settings: UvToolboxSettings) -> dict[str, object]:
        """Return the declared inputs, made specific to this checkout where needed.

        Relative local paths read the same in every clone or worktree but
        point at different code, so they are made absolute. Environments
        without local paths keep sharing a venv across projects.
        """
        inputs = self._declared_inputs()
        if self.dependencies is not None:
            inputs['sources'] = absolute_source_paths(self.sources_data(), base=settings.config_dir)
        elif any(_is_local_requirement(line) for line in self.requirements_text().splitlines()):
            inputs['config_dir'] = str(settings.config_dir)
        return inputs

    def _get_requirements_hash(self, settings: UvToolboxSettings) -> str:
        """Generate the content address of the environment's venv.

        When _locked_uv_lock is set (injected from the repo lockfile), hashes
        the locked packages so the CAS address reflects what gets installed
        rather than the declared inputs.

        Returns a 12-character hex string.
        """
        if self._locked_uv_lock is not None:
            content = locked_packages_digest(self._locked_uv_lock, project_dir=settings.project_dir(self))
        else:
            content = json.dumps(self._venv_inputs(settings), sort_keys=True, default=str)
        # Use SHA-256 for hashing (truncated to 12 chars for readability)
        cache_input = f'v{_CACHE_FORMAT_VERSION}\n{content}'
        return hashlib.sha256(cache_input.encode()).hexdigest()[:12]

    def lock_fingerprint(self) -> str:
        """Return a digest of the inputs that determine this environment's lock.

        Covers the declared dependencies and sources (or the normalized
        deprecated requirements), requires_python, the uv passthrough and the
        unexpanded configured environment variables (which may point uv at
        other indexes). Unexpanded values keep the fingerprint stable across
        machines. A repo lockfile entry whose fingerprint differs was locked
        from different inputs and must be re-locked.

        Returns:
            A ``sha256:``-prefixed hex digest.
        """
        payload = json.dumps(
            {
                'version': _LOCK_FINGERPRINT_VERSION,
                **self._declared_inputs(),
                'environment': self.environment,
            },
            sort_keys=True,
            default=str,
        )
        return f'sha256:{hashlib.sha256(payload.encode()).hexdigest()}'

    def venv_path(self, settings: UvToolboxSettings) -> Path:
        """Get the path to the virtual environment for this environment.

        Uses content-based addressing: the venv location is determined by
        hashing the locked packages (or the declared inputs when there is no
        repo lockfile), so identical environments share a venv across projects.
        Local path sources are hashed by their absolute location, so separate
        checkouts of the same project never share a venv built from one of them.
        """
        req_hash = self._get_requirements_hash(settings)
        return settings.resolved_venv_path / req_hash

    def configured_env(self) -> dict[str, str]:
        """Return the expanded environment variables declared by the user."""
        return {key: expandvars(value) for key, value in self.environment.items()}

    def process_env(self, settings: UvToolboxSettings) -> dict[str, str]:
        """Return configured variables plus the materialized venv location."""
        return {
            **self.configured_env(),
            'VIRTUAL_ENV': str(self.venv_path(settings=settings)),
        }


# uv.lock source kinds that refer to a local path, relative to the project.
_LOCAL_LOCK_SOURCES = ('path', 'directory', 'editable')

# Deprecated requirement lines that install from a local path.
_LOCAL_REQUIREMENT = re.compile(r'^(-e\s|--editable[\s=]|\.|/|~|file:|[A-Za-z]:[\\/])|@\s*file:')


def _is_local_requirement(line: str) -> bool:
    """Whether a deprecated requirements line installs from a local path."""
    return bool(_LOCAL_REQUIREMENT.search(line.strip()))


def _absolute(path: str, base: Path) -> str:
    """Return path made absolute against base, normalized but without resolving symlinks."""
    return Path(os.path.normpath(base / path)).as_posix()


def absolute_source_paths(sources: dict[str, typing.Any], *, base: Path) -> dict[str, typing.Any]:
    """Return `[tool.uv.sources]` data with relative `path` values made absolute against base."""

    def convert(source: dict[str, typing.Any]) -> dict[str, typing.Any]:
        if 'path' not in source:
            return source
        return {**source, 'path': _absolute(source['path'], base)}

    return {
        name: [convert(entry) for entry in source] if isinstance(source, list) else convert(source)
        for name, source in sources.items()
    }


def _absolute_lock_source(source: dict[str, str], project_dir: Path | None) -> dict[str, str]:
    """Return a uv.lock source with local paths made absolute against project_dir."""
    if project_dir is None:
        return source
    return {
        kind: _absolute(value, project_dir) if kind in _LOCAL_LOCK_SOURCES else value for kind, value in source.items()
    }


def locked_packages_digest(uv_lock: str, *, project_dir: Path | None = None) -> str:
    """Return a stable description of the packages a uv.lock installs.

    Only each package's name, version and source are included, so changes to
    uv's lock format or revision do not move environments to new venvs. The
    generated project itself (a virtual source) is excluded.

    Args:
        uv_lock: uv.lock content.
        project_dir: The generated project the lock belongs to. When given,
            local (path, directory and editable) sources are made absolute
            against it, so checkouts in different places get different venvs.

    Returns:
        A canonical JSON string listing the locked packages.
    """
    entries = [
        [package.get('name'), package.get('version'), _absolute_lock_source(package.get('source', {}), project_dir)]
        for package in tomllib.loads(uv_lock).get('package', [])
        if 'virtual' not in package.get('source', {})
    ]
    entries.sort(key=lambda entry: json.dumps(entry, sort_keys=True))
    return json.dumps(entries, sort_keys=True)


class UvToolboxSettings(BaseSettings):
    """Settings loaded from config files.

    Precedence (highest first):
      1. CLI args (passed to initialization)
      2. Environment variables (with `UV_TOOLBOX_` prefix)
      3. Configuration files, in order:
        1. uv-toolbox.yaml
        2. uv-toolbox.json
        3. uv-toolbox.toml
        4. pyproject.toml ([tool.uv-toolbox])
    """

    config_file: Path | None = None
    venv_path: Path = Path.home() / '.cache' / 'uv-toolbox'
    default_environment: str | None = None
    show_commands: bool = False
    environments: typing.Annotated[
        list[UvToolboxEnvironment],
        Field(min_length=1),
    ]

    model_config = SettingsConfigDict(
        env_prefix='UV_TOOLBOX_',
        env_nested_delimiter='__',
        extra='forbid',
        pyproject_toml_table_header=('tool', 'uv-toolbox'),
        alias_generator=_ALIASES,
        populate_by_name=True,
    )

    @property
    def lockfile_path(self) -> Path | None:
        """Path to the repo lockfile, or None if no config file is known."""
        if self.config_file is None:
            return None
        return self.config_file.parent / 'uv-toolbox.lock'

    @property
    def config_dir(self) -> Path:
        """Directory that relative config paths are resolved against."""
        if self.config_file is None:
            return Path.cwd()
        return self.config_file.parent.absolute()

    def project_dir(self, env: UvToolboxEnvironment) -> Path:
        """Working directory holding the uv project generated for an environment.

        It lives at a fixed location next to the config file so that relative
        local paths recorded in uv.lock are the same on every machine.
        """
        return self.config_dir / '.uv-toolbox' / 'projects' / _safe_dir_name(env.name)

    @property
    def resolved_venv_path(self) -> Path:
        """Resolve the venv path.

        Behavior:
        - Relative paths: resolved relative to config file location
        - Absolute paths: used as-is

        Individual venvs are stored in content-addressed subdirectories based
        on hashing their requirements.
        """
        # Determine base path
        if self.venv_path.is_absolute():
            return self.venv_path
        if self.config_file is None:  # pragma: no cover
            # Defensive: shouldn't happen in normal CLI use (only in direct model_validate)
            return Path.cwd() / self.venv_path
        # Relative path - resolve from config file location
        return self.config_file.parent / self.venv_path

    def select_environment(self, env_name: str | None) -> UvToolboxEnvironment:
        """Select an environment by name.

        If env_name is none
        """
        env_name = env_name or self.default_environment

        if env_name is not None:
            for env in self.environments:
                if env.name == env_name:
                    return env
            available = [env.name for env in self.environments]
            raise EnvironmentNotFoundError(env_name, available)

        if len(self.environments) == 1:
            return self.environments[0]

        available = [env.name for env in self.environments]
        raise MultipleEnvironmentsError(available)

    @model_validator(mode='after')
    def ensure_unique_env_names(
        self,
    ) -> UvToolboxSettings:
        """Validate that environment names are unique."""
        all_env_names = [env.name for env in self.environments]
        duplicate_names = {name for name in all_env_names if all_env_names.count(name) > 1}
        if duplicate_names:
            msg = f'Duplicate environment names found: {", ".join(sorted(duplicate_names))}'
            raise ValueError(msg)
        return self

    @model_validator(mode='after')
    def ensure_valid_default_environment(
        self,
    ) -> UvToolboxSettings:
        """Validate that the default environment name exists."""
        if self.default_environment is not None:
            env_names = {env.name for env in self.environments}
            if self.default_environment not in env_names:
                msg = f'Default environment {self.default_environment!r} not found in environments.'
                raise ValueError(msg)
        return self

    @model_validator(mode='after')
    def resolve_requirements_files(self) -> UvToolboxSettings:
        """Resolve relative requirements_file paths against the config file directory."""
        if self.config_file is None:
            return self
        for env in self.environments:
            if env.requirements_file is not None and not env.requirements_file.is_absolute():
                env.requirements_file = self.config_dir / env.requirements_file
        return self

    @model_validator(mode='after')
    def inject_locked_environments(self) -> UvToolboxSettings:
        """Inject locked environments from the repo lockfile.

        When uv-toolbox.lock exists next to the config file, each environment's
        _locked_uv_lock private attribute is populated with its uv.lock content.
        This causes venv_path() to return a CAS address derived from the
        locked packages, ensuring that running uvtb lock followed by uvtb
        install always uses a fresh venv.
        """
        lockfile_path = self.lockfile_path
        if lockfile_path is None or not lockfile_path.exists():
            return self

        self.apply_lock(read_lockfile(lockfile_path))
        return self

    def apply_lock(self, lock: UvToolboxLock) -> None:
        """Point each environment at its locked packages in lock.

        Entries are applied even when their fingerprint is stale so that
        read-only commands (such as ``shim``) keep using the existing venvs;
        commands that install re-lock first and then re-apply.

        Args:
            lock: The repo lockfile contents to apply.
        """
        for env in self.environments:
            env_lock = lock.environments.get(env.name)
            env._locked_uv_lock = env_lock.uv_lock if env_lock is not None else None

    @classmethod
    def from_context(
        cls,
        ctx: typer.Context,
        **overrides: object,
    ) -> UvToolboxSettings:
        """Create settings from a Typer context and optional overrides.

        Args:
            ctx: Typer context containing CLI args.
            **overrides: Additional settings to override.

        Returns:
            UvToolboxSettings instance.
        """
        cli_args = {
            **(ctx.obj or {}),
            **_filter_nulls(overrides),
        }
        _verify_config_file(cli_args)
        with _suppress_unused_pyproject_warning():
            return UvToolboxSettings.model_validate(cli_args)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,  # noqa: ARG003
        file_secret_settings: PydanticBaseSettingsSource,  # noqa: ARG003
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Customize settings sources for UV Toolbox."""
        init_kwargs = typing.cast(
            'InitSettingsSource',
            init_settings,
        ).init_kwargs
        config_file = init_kwargs.get('config_file')

        if config_file is None:
            config_file = os.environ.get('UV_TOOLBOX_CONFIG_FILE')

        if config_file:
            config_source = _config_file_source(settings_cls, Path(config_file))
            return (
                init_settings,
                env_settings,
                config_source,
            )

        uv_toolbox_yaml = YamlConfigSettingsSource(
            settings_cls,
            yaml_file='uv-toolbox.yaml',
        )
        uv_toolbox_json = JsonConfigSettingsSource(
            settings_cls,
            json_file='uv-toolbox.json',
        )
        uv_toolbox_toml = TomlConfigSettingsSource(
            settings_cls,
            toml_file='uv-toolbox.toml',
        )
        pyproject_toml = PyprojectTomlConfigSettingsSource(settings_cls)
        return (
            init_settings,
            env_settings,
            uv_toolbox_yaml,
            uv_toolbox_json,
            uv_toolbox_toml,
            pyproject_toml,
        )


def _safe_dir_name(name: str) -> str:
    """Return a filesystem-safe directory name for an environment name."""
    return re.sub(r'[^A-Za-z0-9._-]', '_', name)


def _config_file_source(
    settings_cls: type[BaseSettings],
    config_file: Path,
) -> PydanticBaseSettingsSource:
    if config_file.name == 'pyproject.toml':
        return PyprojectTomlConfigSettingsSource(
            settings_cls,
            toml_file=config_file,
        )

    suffix = config_file.suffix.lower()
    if suffix in {'.yaml', '.yml'}:
        return YamlConfigSettingsSource(settings_cls, yaml_file=config_file)
    if suffix == '.json':
        return JsonConfigSettingsSource(settings_cls, json_file=config_file)
    if suffix == '.toml':
        return TomlConfigSettingsSource(settings_cls, toml_file=config_file)
    msg = f'Unsupported config file extension: {config_file}'
    raise ValueError(msg)


def _pyproject_has_uv_toolbox_config(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return False
    tool_section = data.get('tool')
    if not isinstance(tool_section, dict):
        return False
    uv_section = tool_section.get('uv-toolbox')
    return isinstance(uv_section, dict)


def _check_directory_for_config(directory: Path) -> Path | None:
    """Check a directory for UV Toolbox config files.

    Args:
        directory: Directory to check

    Returns:
        Path to config file if found, None otherwise
    """
    config_names = [
        'uv-toolbox.yaml',
        'uv-toolbox.json',
        'uv-toolbox.toml',
    ]

    # Check for dedicated config files
    for name in config_names:
        config_path = directory / name
        if config_path.exists():
            return config_path

    # Check pyproject.toml with [tool.uv-toolbox] section
    pyproject = directory / 'pyproject.toml'
    if pyproject.exists() and _pyproject_has_uv_toolbox_config(pyproject):
        return pyproject

    return None


def _find_config_file(start_path: Path | None = None) -> Path | None:
    """Find UV Toolbox config file by walking up the directory tree.

    Searches for config files in this order:
    1. uv-toolbox.yaml
    2. uv-toolbox.json
    3. uv-toolbox.toml
    4. pyproject.toml (with [tool.uv-toolbox] section)

    Stops at:
    - First config file found
    - .git directory (project boundary)
    - Filesystem root

    Args:
        start_path: Directory to start searching from (defaults to cwd)

    Returns:
        Path to config file, or None if not found
    """
    current = start_path or Path.cwd()

    for parent in [current, *current.parents]:
        # Check this directory for config files
        config_file = _check_directory_for_config(parent)
        if config_file:
            return config_file

        # Stop at git root as a boundary
        if (parent / '.git').exists():
            break

    return None


def _verify_config_file(cli_args: dict[str, object]) -> None:
    config_file = cli_args.get('config_file') or os.getenv(
        'UV_TOOLBOX_CONFIG_FILE',
    )
    if config_file:
        config_path = Path(str(config_file))
        if not config_path.exists():
            raise ConfigFileNotFoundError(config_path)
        # Store the config file path for later use
        cli_args['config_file'] = config_path
        return

    # Search up the directory tree for a config file
    found_config = _find_config_file()
    if found_config:
        # Store the found config file path
        cli_args['config_file'] = found_config
        return

    # No config file found - show what we searched for
    current = Path.cwd()
    searched_dirs = [current, *current.parents]
    # Stop at git root if found
    for i, directory in enumerate(searched_dirs):
        if (directory / '.git').exists():
            searched_dirs = searched_dirs[: i + 1]
            break

    # Show example paths from the current directory
    config_candidates = [
        current / 'uv-toolbox.yaml',
        current / 'uv-toolbox.json',
        current / 'uv-toolbox.toml',
        current / 'pyproject.toml',
    ]
    raise MissingConfigFileError(config_candidates)


@contextmanager
def _suppress_unused_pyproject_warning() -> Iterator[None]:
    with warnings.catch_warnings():
        warnings.filterwarnings(
            'ignore',
            message=(
                r'Config key `pyproject_toml_table_header` is set in model_config '
                r'but will be ignored because no PyprojectTomlConfigSettingsSource '
                r'source is configured\..*'
            ),
            category=UserWarning,
        )
        yield
