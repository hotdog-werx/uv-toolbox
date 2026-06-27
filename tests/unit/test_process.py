from __future__ import annotations

import subprocess
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from uv_toolbox.errors import ExternalCommandError, MissingCliError
from uv_toolbox.process import run_checked

if TYPE_CHECKING:
    from pytest_mock import MockerFixture


def test_run_checked_returns_stdout(mocker: MockerFixture) -> None:
    """Returns stripped stdout on a successful subprocess run."""
    completed = SimpleNamespace(stdout='ok\n')
    run_mock = mocker.patch(
        'uv_toolbox.process.subprocess.run',
        return_value=completed,
    )

    result = run_checked(['echo', 'ok'])

    assert result == 'ok'
    run_mock.assert_called_once()


def test_run_checked_skips_stdout_when_disabled(mocker: MockerFixture) -> None:
    """Passes stdout=None to subprocess and returns an empty string when capture_stdout=False."""
    completed = SimpleNamespace(stdout=None)
    run_mock = mocker.patch(
        'uv_toolbox.process.subprocess.run',
        return_value=completed,
    )

    result = run_checked(['echo', 'ok'], capture_stdout=False)

    assert result == ''
    assert run_mock.call_args.kwargs['stdout'] is None


def test_run_checked_skips_stderr_when_disabled(mocker: MockerFixture) -> None:
    """Passes stderr=None to subprocess when capture_stderr=False."""
    completed = SimpleNamespace(stdout='ok\n')
    run_mock = mocker.patch(
        'uv_toolbox.process.subprocess.run',
        return_value=completed,
    )

    run_checked(['echo', 'ok'], capture_stderr=False)

    assert run_mock.call_args.kwargs['stderr'] is None


def test_run_checked_raises_missing_cli(mocker: MockerFixture) -> None:
    """Raises MissingCliError when the executable is not found (FileNotFoundError from subprocess)."""
    mocker.patch(
        'uv_toolbox.process.subprocess.run',
        side_effect=FileNotFoundError,
    )

    with pytest.raises(MissingCliError):
        run_checked(['missing'])


def test_missing_cli_error_message_for_multiple_cli_names() -> None:
    """MissingCliError formats a readable message when multiple CLI names are provided."""
    exc = MissingCliError(['uv', 'python'])

    assert 'Required CLIs' in str(exc)


def test_run_checked_raises_external_command_error(
    mocker: MockerFixture,
) -> None:
    """Raises ExternalCommandError with the correct returncode and stripped stderr on a non-zero exit."""
    err = subprocess.CalledProcessError(2, ['cmd'], output='', stderr='boom\n')
    mocker.patch('uv_toolbox.process.subprocess.run', side_effect=err)

    with pytest.raises(ExternalCommandError) as exc_info:
        run_checked(['cmd'])

    assert exc_info.value.returncode == 2
    assert exc_info.value.stderr == 'boom'


def test_run_checked_omits_stderr_when_capture_disabled(
    mocker: MockerFixture,
) -> None:
    """ExternalCommandError carries an empty stderr string when capture_stderr=False."""
    err = subprocess.CalledProcessError(2, ['cmd'], output='', stderr='boom\n')
    mocker.patch('uv_toolbox.process.subprocess.run', side_effect=err)

    with pytest.raises(ExternalCommandError) as exc_info:
        run_checked(['cmd'], capture_stderr=False)

    assert exc_info.value.stderr == ''


def test_run_checked_prints_command_when_show_command_enabled(
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Prints the command to stderr with an arrow prefix when show_command=True."""
    completed = SimpleNamespace(stdout='ok\n')
    mocker.patch(
        'uv_toolbox.process.subprocess.run',
        return_value=completed,
    )

    run_checked(['uv', 'venv', '/path/to/venv'], show_command=True)

    captured = capsys.readouterr()
    assert '▶' in captured.err
    assert 'uv venv /path/to/venv' in captured.err
