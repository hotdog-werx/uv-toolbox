"""Generate and drive the uv project behind each environment.

Every environment is materialized as a non-package uv project in a working
directory next to the config file (see `UvToolboxSettings.project_dir`).
Locking runs `uv lock` there, and installing runs `uv sync --frozen` with
`UV_PROJECT_ENVIRONMENT` pointing at the environment's content-addressed venv.
"""

from __future__ import annotations

import functools
import json
import os
import re
import shutil
import tempfile
import tomllib
import typing
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path

import typer
from packaging.requirements import InvalidRequirement, Requirement
from packaging.version import InvalidVersion, Version

from uv_toolbox.errors import ExternalCommandError, UnsupportedRequirementError, UvVersionError
from uv_toolbox.process import run_checked

if typing.TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Iterator, Mapping, Sequence

    from uv_toolbox.settings import UvToolboxEnvironment, UvToolboxSettings

# Oldest uv release whose project commands and flags uv-toolbox relies on.
MIN_UV_VERSION = Version('0.8.0')

# Name of the generated project; it appears as the virtual root package in
# every embedded uv.lock.
PROJECT_NAME = 'uv-toolbox-env'

_INLINE_COMMENT = re.compile(r'\s+#.*$')
_BARE_KEY = re.compile(r'^[A-Za-z0-9_-]+$')


@dataclass
class ProjectSpec:
    """Inputs for an environment's generated pyproject.

    Attributes:
        dependencies: PEP 508 requirement strings.
        sources: `[tool.uv.sources]` data. Relative `path` values are relative
            to the config file directory.
        uv_settings: Additional `[tool.uv]` settings.
    """

    dependencies: list[str] = field(default_factory=list)
    sources: dict[str, typing.Any] = field(default_factory=dict)
    uv_settings: dict[str, typing.Any] = field(default_factory=dict)


@dataclass
class ParsedRequirements:
    """Deprecated requirements content split into what `uv add` and `[tool.uv]` take."""

    requirements: list[str] = field(default_factory=list)
    editables: list[str] = field(default_factory=list)
    requirement_files: list[Path] = field(default_factory=list)
    uv_settings: dict[str, typing.Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# uv version
# ---------------------------------------------------------------------------


@functools.cache
def ensure_uv_version() -> None:
    """Fail early when the installed uv is older than MIN_UV_VERSION.

    Raises:
        UvVersionError: If uv is too old or its version cannot be parsed.
    """
    output = run_checked(args=['uv', '--version'])
    match = re.search(r'\d+\.\d+\.\d+\S*', output)
    try:
        found = Version(match.group(0)) if match else None
    except InvalidVersion:
        found = None
    if found is None or found < MIN_UV_VERSION:
        raise UvVersionError(found=output, minimum=str(MIN_UV_VERSION))


# ---------------------------------------------------------------------------
# Deprecated requirements conversion
# ---------------------------------------------------------------------------


def logical_requirement_lines(content: str) -> Iterator[str]:
    """Yield logical, non-comment lines from requirements-file content.

    Joins backslash continuations and strips whole-line and trailing comments.
    """
    pending = ''
    for raw_line in content.splitlines():
        line = _INLINE_COMMENT.sub('', raw_line).strip()
        if not line or line.startswith('#'):
            if pending:
                yield pending.strip()
                pending = ''
            continue
        pending += line
        if pending.endswith('\\'):
            pending = pending[:-1].rstrip() + ' '
            continue
        yield pending
        pending = ''
    if pending.strip():
        yield pending.strip()


def _split_option(line: str, names: Sequence[str]) -> str | None:
    """Return the value of `line` if it is one of the options in `names`."""
    for name in names:
        for separator in (' ', '='):
            if line.startswith(name + separator):
                return line[len(name) + 1 :].strip()
    return None


_UNSUPPORTED_HINT = (
    'Declare the environment with dependencies, sources and the uv passthrough '
    '(which is copied into [tool.uv]) instead.'
)


_CONSTRAINTS_HINT = 'Constraints files may only contain requirements.'


def parse_requirements(content: str, *, base_dir: Path) -> ParsedRequirements:  # noqa: C901
    """Split deprecated requirements content for conversion into a uv project.

    Requirement lines and `-r` includes are passed to `uv add`, and `-e` lines
    to `uv add --editable`. Options that `uv add` would silently ignore are
    mapped onto `[tool.uv]` settings: `-c` files to `constraint-dependencies`,
    index options to `index` / `find-links` / `no-index`, and `--pre` to
    `prerelease`.

    Args:
        content: Requirements file content.
        base_dir: Directory that `-r` and `-c` paths are relative to.

    Returns:
        The parsed requirements.

    Raises:
        UnsupportedRequirementError: For an option with no uv project equivalent.
    """
    parsed = ParsedRequirements()
    settings = parsed.uv_settings
    for line in logical_requirement_lines(content):
        if not line.startswith('-'):
            parsed.requirements.append(line)
        elif (value := _split_option(line, ['-r', '--requirement'])) is not None:
            parsed.requirement_files.append(base_dir / value)
        elif (value := _split_option(line, ['-e', '--editable'])) is not None:
            parsed.editables.append(value)
        elif (value := _split_option(line, ['-c', '--constraint'])) is not None:
            constraints_file = base_dir / value
            settings.setdefault('constraint-dependencies', []).extend(
                _constraint_lines(constraints_file.read_text(), line),
            )
        elif (value := _split_option(line, ['-i', '--index-url'])) is not None:
            settings.setdefault('index', []).append({'url': value, 'default': True})
        elif (value := _split_option(line, ['--extra-index-url'])) is not None:
            settings.setdefault('index', []).append({'url': value})
        elif (value := _split_option(line, ['-f', '--find-links'])) is not None:
            settings.setdefault('find-links', []).append(value)
        elif line == '--no-index':
            settings['no-index'] = True
        elif line == '--pre':
            settings['prerelease'] = 'allow'
        else:
            raise UnsupportedRequirementError(line, _UNSUPPORTED_HINT)
    return parsed


def _constraint_lines(content: str, include_line: str) -> list[str]:
    """Return the requirement lines of a constraints file."""
    constraints = []
    for line in logical_requirement_lines(content):
        if line.startswith('-'):
            context = f'{line} (in {include_line})'
            raise UnsupportedRequirementError(context, _CONSTRAINTS_HINT)
        constraints.append(line)
    return constraints


def merge_uv_settings(*layers: Mapping[str, typing.Any]) -> dict[str, typing.Any]:
    """Merge `[tool.uv]` settings; later layers win, list values are concatenated."""
    merged: dict[str, typing.Any] = {}
    for layer in layers:
        for key, value in layer.items():
            if isinstance(value, list) and isinstance(merged.get(key), list):
                merged[key] = [*merged[key], *value]
            else:
                merged[key] = value
    return merged


def _requirements_base_dir(env: UvToolboxEnvironment, settings: UvToolboxSettings) -> Path:
    """Directory that `-r` / `-c` paths in deprecated requirements are relative to."""
    if env.requirements_file is not None:
        return env.requirements_file.parent
    return settings.config_dir


def convert_requirements(
    env: UvToolboxEnvironment,
    settings: UvToolboxSettings,
    *,
    requires_python: str,
) -> ProjectSpec:
    """Convert an environment's deprecated requirements with `uv add`.

    `uv add` names unnamed URL requirements (building them if necessary) and
    moves direct references into `[tool.uv.sources]`. It runs from the config
    file directory, so relative local paths resolve as they do in config.

    Args:
        env: An environment declared with requirements or requirements_file.
        settings: UV toolbox settings.
        requires_python: requires-python for the scratch project.

    Returns:
        The equivalent dependencies, sources and `[tool.uv]` settings.
    """
    parsed = parse_requirements(env.requirements_text(), base_dir=_requirements_base_dir(env, settings))
    scratch_parent = _working_root(settings)
    with tempfile.TemporaryDirectory(dir=scratch_parent, prefix='convert-') as scratch_raw:
        scratch = Path(scratch_raw)
        pyproject = scratch / 'pyproject.toml'
        pyproject.write_text(render_pyproject(ProjectSpec(), requires_python=requires_python))
        add = ['uv', 'add', '--project', str(scratch), '--frozen']
        uv_env = {**_uv_env(env), **_index_env(parsed.uv_settings)}
        if parsed.requirements or parsed.requirement_files:
            includes = [arg for path in parsed.requirement_files for arg in ('-r', str(path))]
            run_checked(
                args=[*add, *parsed.requirements, *includes],
                cwd=settings.config_dir,
                extra_env=uv_env,
                show_command=settings.show_commands,
            )
        if parsed.editables:
            run_checked(
                args=[*add, '--editable', *parsed.editables],
                cwd=settings.config_dir,
                extra_env=uv_env,
                show_command=settings.show_commands,
            )
        data = tomllib.loads(pyproject.read_text())
        sources = data.get('tool', {}).get('uv', {}).get('sources', {})
        return ProjectSpec(
            dependencies=list(data.get('project', {}).get('dependencies', [])),
            sources=_rebase_sources(sources, start=scratch, to=settings.config_dir),
            uv_settings=merge_uv_settings(parsed.uv_settings, env.uv),
        )


def _index_env(uv_settings: Mapping[str, typing.Any]) -> dict[str, str]:
    """Environment variables that make `uv add` honor converted index options."""
    env: dict[str, str] = {}
    indexes = uv_settings.get('index', [])
    default = [index['url'] for index in indexes if index.get('default')]
    extra = [index['url'] for index in indexes if not index.get('default')]
    if default:
        env['UV_DEFAULT_INDEX'] = default[-1]
    if extra:
        env['UV_INDEX'] = ' '.join(extra)
    if uv_settings.get('find-links'):
        env['UV_FIND_LINKS'] = ','.join(uv_settings['find-links'])
    if uv_settings.get('no-index'):
        env['UV_NO_INDEX'] = '1'
    return env


def project_spec(
    env: UvToolboxEnvironment,
    settings: UvToolboxSettings,
    *,
    requires_python: str,
) -> ProjectSpec:
    """Return the full project inputs for an environment, converting if needed."""
    if env.dependencies is not None:
        return ProjectSpec(
            dependencies=list(env.dependencies),
            sources=env.sources_data(),
            uv_settings=dict(env.uv),
        )
    return convert_requirements(env, settings, requires_python=requires_python)


def _frozen_project_spec(env: UvToolboxEnvironment, settings: UvToolboxSettings) -> ProjectSpec:
    """Return project inputs sufficient for `uv sync --frozen`.

    A frozen sync installs exactly what uv.lock lists, so the dependencies are
    not needed and deprecated requirements need no (networked) conversion.
    The `[tool.uv]` settings still matter, e.g. index names for credentials.
    """
    if env.dependencies is not None:
        return ProjectSpec(sources=env.sources_data(), uv_settings=dict(env.uv))
    parsed = parse_requirements(env.requirements_text(), base_dir=_requirements_base_dir(env, settings))
    return ProjectSpec(uv_settings=merge_uv_settings(parsed.uv_settings, env.uv))


# ---------------------------------------------------------------------------
# pyproject rendering
# ---------------------------------------------------------------------------


def toml_key(key: str) -> str:
    """Render a TOML key, quoting it unless it is a bare key."""
    return key if _BARE_KEY.match(key) else json.dumps(key)


def toml_value(value: object) -> str:
    """Render a value as inline TOML."""
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, datetime | date | time):
        return json.dumps(value.isoformat())
    if isinstance(value, str):
        # JSON string escapes are a subset of TOML basic string escapes.
        return json.dumps(value)
    if isinstance(value, list | tuple):
        return '[' + ', '.join(toml_value(item) for item in value) + ']'
    if isinstance(value, dict):
        items = ', '.join(f'{toml_key(str(k))} = {toml_value(v)}' for k, v in value.items())
        return '{ ' + items + ' }' if items else '{}'
    msg = f'Cannot render {value!r} as TOML.'
    raise TypeError(msg)


def _rebase_sources(sources: Mapping[str, typing.Any], *, start: Path, to: Path) -> dict[str, typing.Any]:
    """Re-express relative source paths, relative to `start`, as relative to `to`."""

    def rebase(source: Mapping[str, typing.Any]) -> dict[str, typing.Any]:
        source = dict(source)
        path = source.get('path')
        if isinstance(path, str) and not Path(path).is_absolute():
            source['path'] = Path(os.path.relpath(os.path.normpath(start / path), to)).as_posix()
        return source

    return {
        name: [rebase(entry) for entry in source] if isinstance(source, list) else rebase(source)
        for name, source in sources.items()
    }


def render_pyproject(
    spec: ProjectSpec,
    *,
    requires_python: str,
    project_dir: Path | None = None,
    config_dir: Path | None = None,
) -> str:
    """Render the pyproject.toml of an environment's generated project.

    Args:
        spec: The project inputs.
        requires_python: The project's requires-python.
        project_dir: Where the pyproject will live. With config_dir, used to
            make relative source paths relative to the project.
        config_dir: The directory relative source paths in spec start from.

    Returns:
        The pyproject.toml content.
    """
    sources = spec.sources
    if project_dir is not None and config_dir is not None:
        sources = _rebase_sources(sources, start=config_dir, to=project_dir)
    lines = [
        '# Generated by uv-toolbox from its config file; do not edit.',
        '[project]',
        f'name = "{PROJECT_NAME}"',
        'version = "0.0.0"',
        f'requires-python = {toml_value(requires_python)}',
        'dependencies = [',
        *(f'    {toml_value(dependency)},' for dependency in spec.dependencies),
        ']',
        '',
        '[tool.uv]',
        'package = false',
        *(f'{toml_key(key)} = {toml_value(value)}' for key, value in spec.uv_settings.items()),
    ]
    if sources:
        lines.extend(['', '[tool.uv.sources]'])
        lines.extend(f'{toml_key(name)} = {toml_value(source)}' for name, source in sources.items())
    return '\n'.join(lines) + '\n'


# ---------------------------------------------------------------------------
# Working directories and uv invocations
# ---------------------------------------------------------------------------


def _working_root(settings: UvToolboxSettings) -> Path:
    """Create (if needed) and return the git-ignored `.uv-toolbox` directory."""
    root = settings.config_dir / '.uv-toolbox'
    root.mkdir(parents=True, exist_ok=True)
    gitignore = root / '.gitignore'
    if not gitignore.exists():
        gitignore.write_text('# Generated by uv-toolbox.\n*\n')
    return root


def _uv_env(env: UvToolboxEnvironment) -> dict[str, str | None]:
    """Environment for uv project commands: configured variables, no active venv."""
    return {**env.configured_env(), 'VIRTUAL_ENV': None, 'UV_PROJECT_ENVIRONMENT': None}


def write_project(
    env: UvToolboxEnvironment,
    settings: UvToolboxSettings,
    spec: ProjectSpec,
    *,
    requires_python: str,
    uv_lock: str | None,
) -> Path:
    """Write an environment's generated project and return its directory.

    Args:
        env: The environment.
        settings: UV toolbox settings.
        spec: The project inputs.
        requires_python: The project's requires-python.
        uv_lock: uv.lock content to start from, or None to start without one.

    Returns:
        The project directory.
    """
    _working_root(settings)
    project_dir = settings.project_dir(env)
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / 'pyproject.toml').write_text(
        render_pyproject(
            spec,
            requires_python=requires_python,
            project_dir=project_dir,
            config_dir=settings.config_dir,
        ),
    )
    lock_path = project_dir / 'uv.lock'
    if uv_lock is None:
        lock_path.unlink(missing_ok=True)
    else:
        lock_path.write_text(uv_lock)
    return project_dir


def locked_requires_python(uv_lock: str) -> str | None:
    """Return the requires-python recorded in uv.lock content."""
    value = tomllib.loads(uv_lock).get('requires-python')
    return value if isinstance(value, str) else None


def _interpreter_requires_python(env: UvToolboxEnvironment, settings: UvToolboxSettings) -> str:
    """Return `>=X.Y` for the interpreter uv would use by default."""
    version = run_checked(
        args=['uv', 'python', 'find', '--no-project', '--show-version'],
        cwd=settings.config_dir,
        extra_env=_uv_env(env),
        show_command=settings.show_commands,
    )
    release = Version(version.split()[-1]).release
    return f'>={release[0]}.{release[1]}'


def resolve_requires_python(
    env: UvToolboxEnvironment,
    settings: UvToolboxSettings,
    uv_lock: str | None,
) -> str:
    """Return the requires-python to lock an environment for.

    The configured value wins. Otherwise the previously locked value is kept,
    so the lock does not change with whichever interpreter a machine has.
    Only a first lock falls back to the default interpreter's version.
    """
    if env.requires_python is not None:
        return env.requires_python
    if uv_lock is not None and (locked := locked_requires_python(uv_lock)) is not None:
        return locked
    return _interpreter_requires_python(env, settings)


def _legacy_pins(requirements: str) -> list[str]:
    """Return the version pins of a version 1 (`uv pip compile`) lock entry."""
    pins = []
    for line in logical_requirement_lines(requirements):
        spec = line.split(' --hash', 1)[0].strip()
        if not spec or spec.startswith('-'):
            continue
        try:
            requirement = Requirement(spec)
        except InvalidRequirement:
            continue
        if requirement.url is None and any(s.operator == '==' for s in requirement.specifier):
            pins.append(str(requirement))
    return pins


def _seed_from_legacy_pins(
    env: UvToolboxEnvironment,
    settings: UvToolboxSettings,
    spec: ProjectSpec,
    *,
    requires_python: str,
    legacy_requirements: str,
) -> str | None:
    """Lock once with a version 1 entry's pins as constraints.

    Returns the seeded uv.lock content, or None if the old pins cannot be
    satisfied any more, in which case locking starts from scratch.
    """
    pins = _legacy_pins(legacy_requirements)
    if not pins:
        return None
    seeded_spec = ProjectSpec(
        dependencies=spec.dependencies,
        sources=spec.sources,
        uv_settings=merge_uv_settings(spec.uv_settings, {'constraint-dependencies': pins}),
    )
    project_dir = write_project(env, settings, seeded_spec, requires_python=requires_python, uv_lock=None)
    try:
        run_checked(
            args=['uv', 'lock'],
            cwd=project_dir,
            extra_env=_uv_env(env),
            show_command=settings.show_commands,
        )
    except ExternalCommandError:
        typer.secho(
            f'Could not keep the previously locked versions for {env.name!r}; locking from scratch.',
            err=True,
            fg=typer.colors.YELLOW,
        )
        return None
    return (project_dir / 'uv.lock').read_text()


def lock_environment(  # noqa: PLR0913
    env: UvToolboxEnvironment,
    settings: UvToolboxSettings,
    *,
    uv_lock: str | None = None,
    legacy_requirements: str | None = None,
    upgrade: bool = False,
    upgrade_packages: Sequence[str] = (),
    refresh: bool = False,
) -> str:
    """Run `uv lock` for an environment and return the resulting uv.lock.

    Args:
        env: The environment to lock.
        settings: UV toolbox settings.
        uv_lock: The existing uv.lock content. uv keeps its pins wherever they
            still satisfy the environment's dependencies.
        legacy_requirements: Pins from a version 1 lockfile entry, used to
            seed the lock when there is no uv.lock content yet.
        upgrade: Forward `--upgrade`, re-resolving every package.
        upgrade_packages: Forward `--upgrade-package` for each name.
        refresh: Forward `--refresh` to bypass cached package metadata.

    Returns:
        The new uv.lock content.
    """
    ensure_uv_version()
    requires_python = resolve_requires_python(env, settings, uv_lock)
    spec = project_spec(env, settings, requires_python=requires_python)
    if uv_lock is None and legacy_requirements is not None and not upgrade:
        uv_lock = _seed_from_legacy_pins(
            env,
            settings,
            spec,
            requires_python=requires_python,
            legacy_requirements=legacy_requirements,
        )
    project_dir = write_project(env, settings, spec, requires_python=requires_python, uv_lock=uv_lock)
    run_checked(
        args=[
            'uv',
            'lock',
            *(['--upgrade'] if upgrade else []),
            *(arg for package in upgrade_packages for arg in ('--upgrade-package', package)),
            *(['--refresh'] if refresh else []),
        ],
        cwd=project_dir,
        capture_stderr=False,
        extra_env=_uv_env(env),
        show_command=settings.show_commands,
    )
    return (project_dir / 'uv.lock').read_text()


def check_environment(
    env: UvToolboxEnvironment,
    settings: UvToolboxSettings,
    *,
    uv_lock: str,
) -> str | None:
    """Run `uv lock --check` for an environment against its locked uv.lock.

    Args:
        env: The environment to check.
        settings: UV toolbox settings.
        uv_lock: The locked uv.lock content.

    Returns:
        None when the lock is up to date, otherwise uv's explanation.
    """
    ensure_uv_version()
    requires_python = resolve_requires_python(env, settings, uv_lock)
    spec = project_spec(env, settings, requires_python=requires_python)
    project_dir = write_project(env, settings, spec, requires_python=requires_python, uv_lock=uv_lock)
    try:
        run_checked(
            args=['uv', 'lock', '--check'],
            cwd=project_dir,
            extra_env=_uv_env(env),
            show_command=settings.show_commands,
        )
    except ExternalCommandError as exc:
        return exc.stderr or str(exc)
    return None


def sync_environment(
    env: UvToolboxEnvironment,
    settings: UvToolboxSettings,
    *,
    uv_lock: str,
    clear: bool = False,
) -> None:
    """Install exactly the packages in uv.lock into the environment's venv.

    Tries an offline `uv sync --frozen` first (warm uv cache, no network) and
    falls back to an online sync.

    Args:
        env: The environment to install.
        settings: UV toolbox settings.
        uv_lock: The uv.lock content to install from.
        clear: Remove the venv first so it is recreated from scratch.
    """
    ensure_uv_version()
    venv_path = env.venv_path(settings=settings)
    if clear and venv_path.exists():
        shutil.rmtree(venv_path)
    requires_python = locked_requires_python(uv_lock) or resolve_requires_python(env, settings, None)
    project_dir = write_project(
        env,
        settings,
        _frozen_project_spec(env, settings),
        requires_python=requires_python,
        uv_lock=uv_lock,
    )
    sync_env = {**_uv_env(env), 'UV_PROJECT_ENVIRONMENT': str(venv_path)}
    sync = ['uv', 'sync', '--frozen']
    try:
        run_checked(
            args=[*sync, '--offline'],
            cwd=project_dir,
            extra_env=sync_env,
            show_command=settings.show_commands,
        )
    except ExternalCommandError:
        run_checked(
            args=sync,
            cwd=project_dir,
            capture_stdout=False,
            capture_stderr=False,
            extra_env=sync_env,
            show_command=settings.show_commands,
        )
