from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import typer

from uv_toolbox.errors import (
    EnvironmentNotFoundError,
    MultipleEnvironmentsError,
    UvToolboxError,
)
from uv_toolbox.process import run_checked
from uv_toolbox.settings import UvToolboxEnvironment, UvToolboxSettings
from uv_toolbox.uv_helpers import initialize_virtualenv

if TYPE_CHECKING:
    from pathlib import Path

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
    settings = UvToolboxSettings.model_validate({'venv_path': venv_path})
    for env in settings.environments:
        try:
            initialize_virtualenv(env=env, settings=settings)
        except UvToolboxError as exc:
            typer.secho(str(exc), err=True, fg=typer.colors.RED)
            raise typer.Exit(code=1) from exc


@app.command(name='exec')
def exec_(
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
    settings = UvToolboxSettings.model_validate({'venv_path': venv_path})
    try:
        env = _select_environment(
            environments=settings.environments,
            env_name=env_name or settings.default_environment,
        )
        if not env.venv_path(settings=settings).exists():
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


def _select_environment(
    environments: list[UvToolboxEnvironment],
    env_name: str | None,
) -> UvToolboxEnvironment:
    if env_name is not None:
        for env in environments:
            if env.name == env_name:
                return env
        available = [env.name for env in environments]
        raise EnvironmentNotFoundError(env_name, available)
    if len(environments) == 1:
        return environments[0]
    available = [env.name for env in environments]
    raise MultipleEnvironmentsError(available)


def main() -> None:
    """Main entry point for the CLI."""
    app()
