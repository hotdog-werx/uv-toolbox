from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


class UvToolboxError(RuntimeError):
    """Base error for uv-toolbox.."""


class MissingCliError(UvToolboxError):
    """Raised when a required CLI executable is missing."""

    cli_names: list[str]

    def __init__(self, cli_names: str | Sequence[str]) -> None:
        self.cli_names = [cli_names] if isinstance(cli_names, str) else list(cli_names)
        if len(self.cli_names) == 1:
            message = f'Required CLI {self.cli_names[0]!r} is not installed or not on PATH.'
        else:
            joined = ', '.join(repr(name) for name in self.cli_names)
            message = f'Required CLIs {joined} are not installed or not on PATH.'
        super().__init__(message)


class ExternalCommandError(UvToolboxError):
    """Raised when an external command returns a non-zero status."""

    cmd_args: list[str]
    returncode: int
    stderr: str

    def __init__(
        self,
        *,
        cmd_args: Sequence[str],
        returncode: int,
        stderr: str | None = None,
    ) -> None:
        self.cmd_args = list(cmd_args)
        self.returncode = returncode
        self.stderr = (stderr or '').strip()

        cmd = ' '.join(self.cmd_args)
        message = f'Command failed ({self.returncode}): {cmd}'
        if self.stderr:
            message = f'{message}\n{self.stderr}'
        super().__init__(message)


class EnvironmentNotFoundError(UvToolboxError):
    """Raised when the requested environment name does not exist."""

    env_name: str
    available: list[str]

    def __init__(self, env_name: str, available: list[str]) -> None:
        self.env_name = env_name
        self.available = available
        available_list = ', '.join(available) or '<none>'
        super().__init__(
            f'Environment {env_name!r} not found. Available environments: {available_list}.',
        )


class MultipleEnvironmentsError(UvToolboxError):
    """Raised when multiple environments exist but none was selected."""

    available: list[str]

    def __init__(self, available: list[str]) -> None:
        self.available = available
        available_list = ', '.join(available) or '<none>'
        super().__init__(
            f'Multiple environments configured; use --env to select one. Available environments: {available_list}.',
        )


class CommandDelimiterRequiredError(UvToolboxError):
    """Raised when an exec command is missing the required `--` delimiter."""

    def __init__(self) -> None:
        super().__init__('Commands must be passed after `--`.')
