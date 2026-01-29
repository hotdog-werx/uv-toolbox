from __future__ import annotations

from typing import Annotated

import typer

from uv_toolbox.errors import (
    EnvironmentNotFoundError,
    MultipleEnvironmentsError,
    UvToolboxError,
)
from uv_toolbox.process import run_checked
from uv_toolbox.settings import UvToolboxEnvironment, UvToolboxSettings
from uv_toolbox.uv_helpers import initialize_virtualenv

app = typer.Typer(help='CLI tool for managing UV tool environments.')


@app.callback()
def _root(ctx: typer.Context) -> None:
    """UV Toolbox CLI."""
    settings = UvToolboxSettings.model_validate({})
    ctx.obj = settings

    default_map: dict[str, object] = {}

    if ctx.default_map is None:
        ctx.default_map = default_map
    else:
        ctx.default_map = {
            **ctx.default_map,
            **default_map,
        }


@app.command(name='install')
def install() -> None:
    """Install UV tool environments."""
    for env in UvToolboxSettings.model_validate({}).environments:
        try:
            initialize_virtualenv(env=env)
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
) -> None:
    """Execute a command within a UV tool environment."""
    settings = UvToolboxSettings.model_validate({})
    try:
        env = _select_environment(settings.environments, env_name)
        if not env.venv_path.exists():
            initialize_virtualenv(env=env)
        run_checked(
            args=['uv', 'run', '--active', '--', *command],
            capture_stdout=False,
            capture_stderr=False,
            extra_env=env.process_env,
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
