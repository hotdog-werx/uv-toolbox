from __future__ import annotations

import os
import subprocess
import typing

import typer

from uv_toolbox.errors import ExternalCommandError, MissingCliError

if typing.TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path


def _print_command(args: Sequence[str]) -> None:
    """Print a command in a nice format to stderr.

    Args:
        args: The command and arguments to print.
    """
    cmd = ' '.join(args)
    typer.secho(f'▶ {cmd}', fg=typer.colors.BRIGHT_BLACK, err=True)


def _process_env(extra_env: Mapping[str, str | None] | None) -> dict[str, str]:
    """Merge overrides into the current environment, removing None values."""
    environment = dict(os.environ)
    for key, value in (extra_env or {}).items():
        if value is None:
            environment.pop(key, None)
        else:
            environment[key] = value
    return environment


def run_checked(  # noqa: PLR0913
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    capture_stdout: bool = True,
    capture_stderr: bool = True,
    extra_env: Mapping[str, str | None] | None = None,
    show_command: bool = False,
) -> str:
    """Run a command and raise a UvToolboxError on failure.

    Args:
        args: The command and arguments to execute.
        cwd: Optional working directory for the command.
        capture_stdout: If false, stdout is not captured.
        capture_stderr: If false, stderr is not captured.
        extra_env: Environment variables to set for the command. A value of
            None removes an inherited variable.
        show_command: If true, print the command before executing.

    Returns:
        The stripped stdout of the command.

    Raises:
        MissingCliError: If the executable is not found.
        ExternalCommandError: If the command exits non-zero.
    """
    if show_command:
        _print_command(args)

    try:
        stdout = subprocess.PIPE if capture_stdout else None
        stderr = subprocess.PIPE if capture_stderr else None
        res = subprocess.run(  # noqa: S603
            list(args),
            cwd=cwd,
            check=True,
            text=True,
            stdout=stdout,
            stderr=stderr,
            env=_process_env(extra_env),
        )
    except FileNotFoundError as exc:
        raise MissingCliError(args[0]) from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or '').strip() if capture_stderr else ''
        raise ExternalCommandError(
            cmd_args=args,
            returncode=exc.returncode,
            stderr=stderr,
        ) from exc

    return (res.stdout or '').strip() if capture_stdout else ''
