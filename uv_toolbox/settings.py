from __future__ import annotations

import hashlib
import os
import tomllib
import typing
import warnings
from contextlib import contextmanager
from os.path import expandvars
from pathlib import Path

from pydantic import (
    AliasChoices,
    AliasGenerator,
    BaseModel,
    ConfigDict,
    Field,
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


class UvToolboxEnvironment(BaseModel):
    """Definition of a UV toolbox environment.

    Attributes:
        requirements: A pip requirements string (mutually exclusive with
            requirements_file).
        requirements_file: A path to a pip requirements file (mutually
            exclusive with requirements).
    """

    name: str
    requirements: str | None = None
    requirements_file: Path | None = None
    environment: dict[str, str] = {}

    model_config = ConfigDict(
        alias_generator=_ALIASES,
        populate_by_name=True,
    )

    @model_validator(mode='after')
    def check_requirements(self) -> UvToolboxEnvironment:
        """Validate that only one of requirements or requirements_file is set."""
        if not (bool(self.requirements) ^ bool(self.requirements_file)):
            msg = 'Exactly one of requirements or requirements_file must be set.'
            raise ValueError(msg)
        return self

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

    def _get_requirements_hash(self) -> str:
        """Generate a hash of the environment's requirements.

        Returns a 12-character hex string based on the normalized requirements.
        """
        if self.requirements:
            normalized = self._normalize_requirements(self.requirements)
        else:
            # Read and normalize requirements file
            if self.requirements_file is None:  # pragma: no cover
                # Impossible: check_requirements validator ensures one is set
                msg = 'Either requirements or requirements_file must be set'
                raise ValueError(msg)  # pragma: no cover
            normalized = self._normalize_requirements(
                self.requirements_file.read_text(),
            )

        # Use SHA-256 for hashing (truncated to 12 chars for readability)
        return hashlib.sha256(normalized.encode()).hexdigest()[:12]

    def venv_path(self, settings: UvToolboxSettings) -> Path:
        """Get the path to the virtual environment for this environment.

        Uses content-based addressing: the venv location is determined by
        hashing the normalized requirements, ensuring identical requirements
        share the same venv across projects.
        """
        req_hash = self._get_requirements_hash()
        return settings.resolved_venv_path / req_hash

    def process_env(self, settings: UvToolboxSettings) -> dict[str, str]:
        """Environment variables for processes run in this environment."""
        return {
            **{k: expandvars(v) for k, v in self.environment.items()},
            'VIRTUAL_ENV': str(self.venv_path(settings=settings)),
        }


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
