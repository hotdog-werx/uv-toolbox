import os
from pathlib import Path
from typing import Annotated

import typer

from uv_toolbox.errors import UvToolboxError
from uv_toolbox.process import run_checked
from uv_toolbox.settings import UvToolboxSettings
from uv_toolbox.utils import _filter_nulls, _venv_bin_path
from uv_toolbox.uv_helpers import initialize_virtualenv

app = typer.Typer(help='CLI tool for managing UV tool environments.')


@app.callback()
def _root(ctx: typer.Context) -> None:
    """UV Toolbox CLI."""


@app.command(name='install')
def install(
    venv_path: Annotated[
        Path | None,
        typer.Option(
            str(UvToolboxSettings.model_validate({}).venv_path),
            '--venv-path',
            help='Path to the directory where virtual environments are stored.',
        ),
    ] = None,
) -> None:
    """Install UV tool environments."""
    cli_args = _filter_nulls({'venv_path': venv_path})
    settings = UvToolboxSettings.model_validate(cli_args)
    for env in settings.environments:
        try:
            initialize_virtualenv(env=env, settings=settings)
        except UvToolboxError as exc:
            typer.secho(str(exc), err=True, fg=typer.colors.RED)
            raise typer.Exit(code=1) from exc


@app.command(name='exec')
def exec_(
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
    force_reinitialize: Annotated[
        bool,
        typer.Option(
            ...,
            '--force-reinitialize',
            '-f',
            help='Force re-initialization of the virtual environment.',
        ),
    ] = False,
    venv_path: Annotated[
        Path | None,
        typer.Option(
            str(UvToolboxSettings.model_validate({}).venv_path),
            '--venv-path',
            help='Path to the directory where virtual environments are stored.',
        ),
    ] = None,
) -> None:
    """Execute a command within a UV tool environment."""
    cli_args = _filter_nulls({'venv_path': venv_path})
    settings = UvToolboxSettings.model_validate(cli_args)
    try:
        env = settings.select_environment(
            env_name=env_name,
        )

        if not env.venv_path(settings=settings).exists() or force_reinitialize:
            initialize_virtualenv(env=env, settings=settings)

        run_checked(
            args=['uv', 'run', '--active', '--', *command],
            capture_stdout=False,
            capture_stderr=False,
            extra_env=env.process_env(settings=settings),
        )
    except UvToolboxError as exc:
        typer.secho(str(exc), err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc


@app.command(name='shim')
def shim(
    venv_path: Annotated[
        Path | None,
        typer.Option(
            str(UvToolboxSettings.model_validate({}).venv_path),
            '--venv-path',
            help='Path to the directory where virtual environments are stored.',
        ),
    ] = None,
) -> None:
    """Emit shell code to prepend environment bin paths to PATH."""
    cli_args = _filter_nulls({'venv_path': venv_path})
    settings = UvToolboxSettings.model_validate(cli_args)
    shim_paths = [str(_venv_bin_path(env.venv_path(settings=settings))) for env in settings.environments]
    shim_path = os.pathsep.join(shim_paths)
    typer.echo(f'export PATH="{shim_path}{os.pathsep}$PATH"')


def main() -> None:
    """Main entry point for the CLI."""
    app()
