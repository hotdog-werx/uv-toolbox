from __future__ import annotations

import typing

if typing.TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


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


class ConfigFileNotFoundError(UvToolboxError):
    """Raised when an explicit config file path does not exist."""

    config_file: Path

    def __init__(self, config_file: Path) -> None:
        self.config_file = config_file
        super().__init__(f'Configuration file not found: {config_file}')


class MissingConfigFileError(UvToolboxError):
    """Raised when no configuration file can be found."""

    searched_files: list[Path]

    def __init__(self, searched_files: list[Path]) -> None:
        self.searched_files = searched_files
        searched = ', '.join(str(path) for path in searched_files)
        super().__init__(
            'No configuration file found. '
            f'Searched: {searched}. '
            'Use --config or UV_TOOLBOX_CONFIG_FILE to specify a file.',
        )


class UnsupportedRequirementError(UvToolboxError):
    """Raised when a deprecated requirements line cannot be converted to a uv project."""

    line: str

    def __init__(self, line: str, hint: str) -> None:
        self.line = line
        super().__init__(f'Cannot convert requirements line {line!r} to a uv project. {hint}')


class UvVersionError(UvToolboxError):
    """Raised when the installed uv is older than uv-toolbox requires."""

    def __init__(self, *, found: str, minimum: str) -> None:
        super().__init__(f'uv-toolbox requires uv >= {minimum}, found: {found.strip() or "unknown"}.')


class LockfileOutdatedError(UvToolboxError):
    """Raised when the repo lockfile was written in an older format."""

    def __init__(self, path: Path) -> None:
        super().__init__(f'Lockfile format is outdated: {path}. Run `uv-toolbox lock`.')
