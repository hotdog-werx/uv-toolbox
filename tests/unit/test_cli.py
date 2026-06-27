from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

from typer.testing import CliRunner

from tests.utils import create_fake_venv
from uv_toolbox import cli as cli_module
from uv_toolbox.cli import app
from uv_toolbox.errors import CommandDelimiterRequiredError, UvToolboxError
from uv_toolbox.settings import UvToolboxSettings

if TYPE_CHECKING:
    from pathlib import Path

    from pytest_mock import MockerFixture

runner = CliRunner()


def _write_config(
    tmp_path: Path,
    *,
    venv_path: Path,
    envs: list[tuple[str, str]],
) -> Path:
    env_lines = []
    for name, requirements in envs:
        env_lines.extend(
            [
                f'  - name: {name}',
                f'    requirements: {requirements!r}',
            ],
        )
    contents = '\n'.join(
        [
            f'venv_path: {venv_path}',
            'environments:',
            *env_lines,
            '',
        ],
    )
    config_path = tmp_path / 'uvtb.yaml'
    config_path.write_text(contents)
    return config_path


def test_exec_runs_uv_command(mocker: MockerFixture, tmp_path: Path) -> None:
    """Runs `uv run --no-project` with the correct args and sets VIRTUAL_ENV to the content-addressed path."""
    venv_root = tmp_path / '.uv-toolbox'
    config_path = _write_config(
        tmp_path,
        venv_path=venv_root,
        envs=[('env1', 'ruff')],
    )

    mocker.patch.object(
        sys,
        'argv',
        ['uvtb', 'exec', '--', 'ruff', '--version'],
    )
    init_mock = mocker.patch('uv_toolbox.cli.initialize_virtualenv')
    run_mock = mocker.patch('uv_toolbox.cli.run_checked')

    result = runner.invoke(
        app,
        [
            '--config',
            str(config_path),
            'exec',
            '--env',
            'env1',
            '--',
            'ruff',
            '--version',
        ],
    )

    assert result.exit_code == 0
    init_mock.assert_called_once()
    run_mock.assert_called_once()
    args = run_mock.call_args.kwargs['args']
    assert args == ['uv', 'run', '--no-project', '--', 'ruff', '--version']

    extra_env = run_mock.call_args.kwargs['extra_env']
    assert extra_env['VIRTUAL_ENV'].startswith(str(venv_root))


def test_exec_requires_delimiter(mocker: MockerFixture, tmp_path: Path) -> None:
    """The `exec` command raises CommandDelimiterRequiredError when `--` is absent from argv."""
    config_path = _write_config(
        tmp_path,
        venv_path=tmp_path / '.uv-toolbox',
        envs=[('env1', 'ruff')],
    )
    mocker.patch.object(sys, 'argv', ['uvtb', 'exec', '--env', 'env1', 'ruff'])

    result = runner.invoke(
        app,
        ['--config', str(config_path), 'exec', '--env', 'env1', 'ruff'],
    )

    assert result.exit_code != 0
    assert isinstance(result.exception, CommandDelimiterRequiredError)


def test_shim_outputs_paths(tmp_path: Path) -> None:
    """Emits `export PATH=...` shell code containing shim directories for each env with listed executables."""
    venv_root = tmp_path / '.uv-toolbox'
    env_lines = [
        '  - name: env1',
        "    requirements: 'ruff'",
        '    executables: [ruff]',
        '  - name: env2',
        "    requirements: 'black'",
        '    executables: [black]',
    ]
    contents = '\n'.join(
        [
            f'venv_path: {venv_root}',
            'environments:',
            *env_lines,
            '',
        ],
    )
    config_path = tmp_path / 'uvtb.yaml'
    config_path.write_text(contents)

    settings = UvToolboxSettings.model_validate(
        {
            'venv_path': venv_root,
            'environments': [
                {
                    'name': 'env1',
                    'requirements': 'ruff',
                    'executables': ['ruff'],
                },
                {
                    'name': 'env2',
                    'requirements': 'black',
                    'executables': ['black'],
                },
            ],
        },
    )

    create_fake_venv(
        settings.environments[0].venv_path(settings=settings),
        ['ruff'],
    )
    create_fake_venv(
        settings.environments[1].venv_path(settings=settings),
        ['black'],
    )

    result = runner.invoke(app, ['--config', str(config_path), 'shim'])

    assert result.exit_code == 0
    output = result.stdout.strip()
    assert output.startswith('export PATH="')
    assert output.endswith(f'{os.pathsep}$PATH"')
    assert 'shims' in output


def test_install_initializes_all_envs(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    """The `install` command calls initialize_virtualenv for every environment in config order."""
    venv_root = tmp_path / '.uv-toolbox'
    config_path = _write_config(
        tmp_path,
        venv_path=venv_root,
        envs=[('env1', 'ruff'), ('env2', 'black')],
    )

    init_mock = mocker.patch('uv_toolbox.cli.initialize_virtualenv')

    result = runner.invoke(app, ['--config', str(config_path), 'install'])

    assert result.exit_code == 0
    assert init_mock.call_count == 2
    names = [call.kwargs['env'].name for call in init_mock.call_args_list]
    assert names == ['env1', 'env2']


def test_main_invokes_app(mocker: MockerFixture) -> None:
    """The main() entry point calls the Typer app."""
    app_mock = mocker.patch('uv_toolbox.cli.app')

    cli_module.main()

    app_mock.assert_called_once_with()


def test_install_handles_uv_toolbox_error(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    """The `install` command exits with code 1 and prints the error message when initialize_virtualenv raises."""
    venv_root = tmp_path / '.uv-toolbox'
    config_path = _write_config(
        tmp_path,
        venv_path=venv_root,
        envs=[('env1', 'ruff')],
    )

    mocker.patch(
        'uv_toolbox.cli.initialize_virtualenv',
        side_effect=UvToolboxError('Test error'),
    )

    result = runner.invoke(app, ['--config', str(config_path), 'install'])

    assert result.exit_code == 1
    assert 'Test error' in result.stderr


def test_exec_handles_uv_toolbox_error(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    """The `exec` command exits with code 1 and prints the error message on UvToolboxError."""
    venv_root = tmp_path / '.uv-toolbox'
    config_path = _write_config(
        tmp_path,
        venv_path=venv_root,
        envs=[('env1', 'ruff')],
    )

    mocker.patch.object(sys, 'argv', ['uvtb', 'exec', '--', 'ruff'])
    mocker.patch(
        'uv_toolbox.cli.initialize_virtualenv',
        side_effect=UvToolboxError('Test error'),
    )

    result = runner.invoke(
        app,
        ['--config', str(config_path), 'exec', '--env', 'env1', '--', 'ruff'],
    )

    assert result.exit_code == 1
    assert 'Test error' in result.stderr


def test_shim_list_paths_prints_one_dir_per_line(tmp_path: Path) -> None:
    """Passing `--list-paths` prints each shim directory as a plain path per line, not shell export syntax."""
    venv_root = tmp_path / '.uv-toolbox'
    env_lines = [
        '  - name: env1',
        "    requirements: 'ruff'",
        '    executables: [ruff]',
    ]
    config_path = tmp_path / 'uvtb.yaml'
    config_path.write_text(
        '\n'.join([f'venv_path: {venv_root}', 'environments:', *env_lines, '']),
    )

    settings = UvToolboxSettings.model_validate(
        {
            'venv_path': venv_root,
            'environments': [
                {
                    'name': 'env1',
                    'requirements': 'ruff',
                    'executables': ['ruff'],
                },
            ],
        },
    )
    create_fake_venv(
        settings.environments[0].venv_path(settings=settings),
        ['ruff'],
    )

    result = runner.invoke(
        app,
        ['--config', str(config_path), 'shim', '--list-paths'],
    )

    assert result.exit_code == 0
    lines = result.stdout.strip().splitlines()
    assert len(lines) == 1
    assert lines[0].endswith('shims')


def test_shim_outputs_comment_when_no_shims(tmp_path: Path) -> None:
    """The `shim` command emits a shell comment when no venvs exist and no shims are created."""
    venv_root = tmp_path / '.uv-toolbox'
    config_path = _write_config(
        tmp_path,
        venv_path=venv_root,
        envs=[('env1', 'ruff')],
    )

    result = runner.invoke(app, ['--config', str(config_path), 'shim'])

    assert result.exit_code == 0
    output = result.stdout.strip()
    assert output == '# No shims to add to PATH'


def test_shim_handles_uv_toolbox_error(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    """The `shim` command exits with code 1 and prints the error message when create_shims raises."""
    venv_root = tmp_path / '.uv-toolbox'
    config_path = _write_config(
        tmp_path,
        venv_path=venv_root,
        envs=[('env1', 'ruff')],
    )

    mocker.patch(
        'uv_toolbox.cli.create_shims',
        side_effect=UvToolboxError('Test error'),
    )

    result = runner.invoke(app, ['--config', str(config_path), 'shim'])

    assert result.exit_code == 1
    assert 'Test error' in result.stderr
