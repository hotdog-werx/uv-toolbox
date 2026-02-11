import os
import sys
from pathlib import Path
from typing import Annotated

import typer

from uv_toolbox.errors import CommandDelimiterRequiredError, UvToolboxError
from uv_toolbox.process import run_checked
from uv_toolbox.settings import UvToolboxSettings
from uv_toolbox.shims import create_shims
from uv_toolbox.utils import _filter_nulls
from uv_toolbox.uv_helpers import initialize_virtualenv

app = typer.Typer(help='CLI tool for managing UV tool environments.')


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
) -> None:
    """Install UV tool environments."""
    settings = UvToolboxSettings.from_context(ctx, venv_path=venv_path)
    for env in settings.environments:
        try:
            initialize_virtualenv(env=env, settings=settings, clear=clear)
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
        env = settings.select_environment(
            env_name=env_name,
        )

        if not env.venv_path(settings=settings).exists() or clear:
            initialize_virtualenv(
                env=env,
                settings=settings,
                clear=clear,
            )

        run_checked(
            args=['uv', 'run', '--isolated', '--', *command],
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
) -> None:
    """Create shim scripts for tools and emit shell code to add shims to PATH."""
    settings = UvToolboxSettings.from_context(ctx, venv_path=venv_path)

    try:
        shim_dirs = create_shims(settings=settings)
        if shim_dirs:
            # Join all shim directories in config order
            shim_path = os.pathsep.join(str(d) for d in shim_dirs)
            typer.echo(f'export PATH="{shim_path}{os.pathsep}$PATH"')
        else:
            # No shims created (no venvs or no executables)
            typer.echo('# No shims to add to PATH')
    except UvToolboxError as exc:
        typer.secho(str(exc), err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc


def main() -> None:
    """Main entry point for the CLI."""
    app()
