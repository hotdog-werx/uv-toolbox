import difflib
import os
import sys
import warnings
from pathlib import Path
from typing import Annotated, TextIO

import typer

from uv_toolbox.errors import CommandDelimiterRequiredError, LockfileOutdatedError, UvToolboxError
from uv_toolbox.lock import check_lock, generate_lock, stale_environments, update_lock
from uv_toolbox.lockfile import lockfiles_equal, read_lockfile, write_lockfile
from uv_toolbox.migrate import migrate_config
from uv_toolbox.process import run_checked
from uv_toolbox.settings import UvToolboxSettings
from uv_toolbox.shims import create_shims
from uv_toolbox.utils import _filter_nulls, _venv_python
from uv_toolbox.uv_helpers import initialize_virtualenv

app = typer.Typer(help='CLI tool for managing UV tool environments.')


def _show_warning(  # noqa: PLR0913
    message: Warning | str,
    category: type[Warning],
    filename: str,  # noqa: ARG001
    lineno: int,  # noqa: ARG001
    file: TextIO | None = None,  # noqa: ARG001
    line: str | None = None,  # noqa: ARG001
) -> None:
    """Print warnings (such as config deprecations) without source locations."""
    typer.secho(f'{category.__name__}: {message}', err=True, fg=typer.colors.YELLOW)


def _require_lockfile_path(
    settings: UvToolboxSettings,
    *,
    check: bool,
) -> Path:
    """Return the configured lockfile path or exit with a user-facing error."""
    lockfile_path = settings.lockfile_path
    if lockfile_path is None:
        typer.secho(
            'Cannot locate lockfile: no config file location known.',
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)
    if check and not lockfile_path.exists():
        typer.secho(
            f'Lockfile is missing: {lockfile_path}',
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)
    return lockfile_path


def _check_lockfile(
    settings: UvToolboxSettings,
    lockfile_path: Path,
) -> None:
    """Validate that the repo lockfile is up to date with the configuration.

    Never writes the lockfile. Fails on an outdated lockfile format, on
    fingerprint mismatches (configuration changed since locking), and when
    `uv lock --check` reports that an environment's uv.lock no longer
    satisfies its dependencies.
    """
    existing_lock = read_lockfile(lockfile_path)
    if existing_lock.outdated_format:
        raise LockfileOutdatedError(lockfile_path)
    stale = stale_environments(settings, existing_lock)
    if stale:
        typer.secho(
            f'Lockfile is out of date: {lockfile_path}. Configuration changed for: '
            f'{", ".join(stale)}. Run `uv-toolbox lock`.',
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)
    failures = check_lock(settings, existing_lock)
    if failures:
        details = ''.join(f'\n  {name}: {problem}' for name, problem in failures.items())
        typer.secho(
            f'Lockfile is out of date: {lockfile_path}. Run `uv-toolbox lock`.{details}',
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)
    typer.echo(f'Lockfile is up to date: {lockfile_path}')


def _write_lockfile(
    settings: UvToolboxSettings,
    lockfile_path: Path,
    *,
    upgrade: bool = False,
    upgrade_packages: list[str] | None = None,
    refresh: bool = False,
) -> None:
    """Lock every environment and write the repo lockfile, keeping existing pins."""
    existing_lock = read_lockfile(lockfile_path) if lockfile_path.exists() else None
    lock_data = generate_lock(
        settings=settings,
        existing_lock=existing_lock,
        upgrade=upgrade,
        upgrade_packages=upgrade_packages or [],
        refresh=refresh,
    )
    if existing_lock is not None and lockfiles_equal(lock_data, existing_lock):
        typer.echo(f'Lockfile is up to date: {lockfile_path}')
        return
    write_lockfile(lock_data, lockfile_path)
    typer.echo(f'Wrote {lockfile_path}')


def _relock_if_stale(settings: UvToolboxSettings) -> None:
    """Re-lock environments whose config changed since the repo lockfile was written.

    Does nothing when there is no repo lockfile. Otherwise re-resolves stale
    environments, drops entries for removed ones, rewrites the lockfile, and
    points the settings at the fresh pins.
    """
    lockfile_path = settings.lockfile_path
    if lockfile_path is None or not lockfile_path.exists():
        return
    existing_lock = read_lockfile(lockfile_path)
    stale = stale_environments(settings, existing_lock)
    configured = {env.name for env in settings.environments}
    if not stale and existing_lock.environments.keys() == configured:
        return
    if existing_lock.outdated_format:
        typer.secho(
            f'Lockfile format is outdated; re-locking: {lockfile_path}',
            err=True,
            fg=typer.colors.YELLOW,
        )
    elif stale:
        typer.secho(
            f'Lockfile is out of date; re-locking: {", ".join(stale)}',
            err=True,
            fg=typer.colors.YELLOW,
        )
    lock_data = update_lock(settings, existing_lock, stale)
    write_lockfile(lock_data, lockfile_path)
    typer.secho(f'Updated {lockfile_path}', err=True)
    settings.apply_lock(lock_data)


@app.callback()
def _root(
    ctx: typer.Context,
    config_file: Annotated[
        Path | None,
        typer.Option(
            ...,
            '--config',
            '-c',
            help='Path to a uv-toolbox config file.',
        ),
    ] = None,
) -> None:
    """UV Toolbox CLI."""
    warnings.showwarning = _show_warning  # ty: ignore[invalid-assignment]
    ctx.obj = _filter_nulls({'config_file': config_file})


@app.command(name='install')
def install(
    ctx: typer.Context,
    *,
    venv_path: Annotated[
        Path | None,
        typer.Option(
            ...,
            '--venv-path',
            help='Path to the directory where virtual environments are stored.',
        ),
    ] = None,
    clear: Annotated[
        bool,
        typer.Option(
            ...,
            '--clear',
            '-c',
            help='Clear and recreate the virtual environments.',
        ),
    ] = False,
    upgrade: Annotated[
        bool,
        typer.Option(
            ...,
            '--upgrade',
            '-u',
            help='Re-resolve dependencies and refresh the lockfile.',
        ),
    ] = False,
) -> None:
    """Install UV tool environments."""
    settings = UvToolboxSettings.from_context(ctx, venv_path=venv_path)
    try:
        _relock_if_stale(settings)
    except UvToolboxError as exc:
        typer.secho(str(exc), err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc
    for env in settings.environments:
        try:
            initialize_virtualenv(
                env=env,
                settings=settings,
                clear=clear,
                upgrade=upgrade,
            )
        except UvToolboxError as exc:
            typer.secho(str(exc), err=True, fg=typer.colors.RED)
            raise typer.Exit(code=1) from exc


@app.command(name='lock')
def lock(  # noqa: PLR0913
    ctx: typer.Context,
    venv_path: Annotated[
        Path | None,
        typer.Option(
            ...,
            '--venv-path',
            help='Path to the directory where virtual environments are stored.',
        ),
    ] = None,
    *,
    check: Annotated[
        bool,
        typer.Option(
            '--check',
            help='Verify that uv-toolbox.lock matches the current configuration without writing it.',
        ),
    ] = False,
    refresh: Annotated[
        bool,
        typer.Option(
            '--refresh',
            help='Forward --refresh to `uv lock` to bypass cached package metadata.',
        ),
    ] = False,
    upgrade: Annotated[
        bool,
        typer.Option(
            '--upgrade',
            help='Forward --upgrade to `uv lock` to re-resolve every package to its latest compatible version.',
        ),
    ] = False,
    upgrade_packages: Annotated[
        list[str] | None,
        typer.Option(
            '--upgrade-package',
            '-P',
            help='Forward --upgrade-package to `uv lock` to re-resolve only this package. Repeatable.',
        ),
    ] = None,
) -> None:
    """Generate, update, or verify the repo lockfile.

    Existing pins are kept wherever they still satisfy the configuration; use
    --upgrade or --upgrade-package to move them.
    """
    settings = UvToolboxSettings.from_context(ctx, venv_path=venv_path)
    lockfile_path = _require_lockfile_path(settings, check=check)
    try:
        if check:
            if upgrade or upgrade_packages:
                typer.secho('--check cannot be combined with --upgrade.', err=True, fg=typer.colors.RED)
                raise typer.Exit(code=1)
            _check_lockfile(settings, lockfile_path)
        else:
            _write_lockfile(
                settings,
                lockfile_path,
                upgrade=upgrade,
                upgrade_packages=upgrade_packages,
                refresh=refresh,
            )
    except UvToolboxError as exc:
        typer.secho(str(exc), err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc


@app.command(name='exec')
def exec_(
    ctx: typer.Context,
    *,
    command: Annotated[
        list[str],
        typer.Argument(
            ...,
            help='Command to run in the environment.',
        ),
    ],
    env_name: Annotated[
        str | None,
        typer.Option(
            ...,
            '--env',
            '-e',
            help='Environment name (required when multiple environments exist).',
        ),
    ] = None,
    clear: Annotated[
        bool,
        typer.Option(
            ...,
            '--clear',
            '-c',
            help='Clear and recreate the virtual environment.',
        ),
    ] = False,
    venv_path: Annotated[
        Path | None,
        typer.Option(
            ...,
            '--venv-path',
            help='Path to the directory where virtual environments are stored.',
        ),
    ] = None,
) -> None:
    """Execute a command within a UV tool environment."""
    if '--' not in sys.argv:
        raise CommandDelimiterRequiredError
    settings = UvToolboxSettings.from_context(ctx, venv_path=venv_path)
    try:
        _relock_if_stale(settings)
        env = settings.select_environment(
            env_name=env_name,
        )

        venv_path = env.venv_path(settings=settings)
        if not venv_path.exists() or clear:
            initialize_virtualenv(
                env=env,
                settings=settings,
                clear=clear,
            )

        # --no-project prevents automatic project detection, and an explicit
        # --python pins the toolbox venv's interpreter (as the shims do), so a
        # .python-version, UV_PYTHON or another active venv can't redirect it.
        run_checked(
            args=['uv', 'run', '--no-project', '--python', str(_venv_python(venv_path)), '--', *command],
            capture_stdout=False,
            capture_stderr=False,
            extra_env=env.process_env(settings=settings),
            show_command=settings.show_commands,
        )
    except UvToolboxError as exc:
        typer.secho(str(exc), err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc


@app.command(name='shim')
def shim(
    ctx: typer.Context,
    venv_path: Annotated[
        Path | None,
        typer.Option(
            ...,
            '--venv-path',
            help='Path to the directory where virtual environments are stored.',
        ),
    ] = None,
    *,
    list_paths: Annotated[
        bool,
        typer.Option(
            '--list-paths',
            help='Print shim directories one per line instead of emitting shell code.',
        ),
    ] = False,
) -> None:
    """Create shim scripts for tools and emit shell code to add shims to PATH."""
    settings = UvToolboxSettings.from_context(ctx, venv_path=venv_path)

    try:
        shim_dirs = create_shims(settings=settings)
        if list_paths:
            for d in shim_dirs:
                typer.echo(str(d))
        elif shim_dirs:
            shim_path = os.pathsep.join(str(d) for d in shim_dirs)
            typer.echo(f'export PATH="{shim_path}{os.pathsep}$PATH"')
        else:
            typer.echo('# No shims to add to PATH')
    except UvToolboxError as exc:
        typer.secho(str(exc), err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc


@app.command(name='migrate-config')
def migrate_config_command(
    ctx: typer.Context,
    *,
    yes: Annotated[
        bool,
        typer.Option(
            '--yes',
            '-y',
            help='Write the converted config without asking for confirmation.',
        ),
    ] = False,
) -> None:
    """Convert deprecated requirements / requirements_file environments to dependencies."""
    try:
        settings = UvToolboxSettings.from_context(ctx)
        migration = migrate_config(settings)
    except UvToolboxError as exc:
        typer.secho(str(exc), err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc
    config_file = settings.config_file
    if not migration.migrated or config_file is None:
        typer.echo('No environments use requirements or requirements_file; nothing to migrate.')
        return
    if not migration.rewritable:
        typer.echo(
            f'{config_file} cannot be rewritten automatically. Replace the '
            f'[[tool.uv-toolbox.environments]] tables for {", ".join(migration.migrated)} with:\n',
        )
        typer.echo(migration.content)
        return
    original = config_file.read_text()
    diff = difflib.unified_diff(
        original.splitlines(keepends=True),
        migration.content.splitlines(keepends=True),
        fromfile=str(config_file),
        tofile=str(config_file),
    )
    typer.echo(''.join(diff))
    typer.secho(
        'Comments and formatting in the config file are not preserved.',
        err=True,
        fg=typer.colors.YELLOW,
    )
    if not yes and not typer.confirm(f'Write {config_file}?'):
        raise typer.Exit(code=1)
    config_file.write_text(migration.content)
    typer.echo(f'Migrated {", ".join(migration.migrated)} in {config_file}. Run `uv-toolbox lock` to re-lock.')


def main() -> None:
    """Main entry point for the CLI."""
    app()
