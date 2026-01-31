from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

from typer.testing import CliRunner

from uv_toolbox import cli as cli_module
from uv_toolbox.cli import app
from uv_toolbox.errors import CommandDelimiterRequiredError

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
    assert args == ['uv', 'run', '--active', '--', 'ruff', '--version']
    extra_env = run_mock.call_args.kwargs['extra_env']
    assert extra_env['VIRTUAL_ENV'] == str(venv_root / 'env1')


def test_exec_requires_delimiter(mocker: MockerFixture, tmp_path: Path) -> None:
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
    venv_root = tmp_path / '.uv-toolbox'
    config_path = _write_config(
        tmp_path,
        venv_path=venv_root,
        envs=[('env1', 'ruff'), ('env2', 'black')],
    )
    result = runner.invoke(app, ['--config', str(config_path), 'shim'])

    assert result.exit_code == 0
    bin_dir = 'Scripts' if os.name == 'nt' else 'bin'
    expected = os.pathsep.join(
        [
            str(venv_root / 'env1' / bin_dir),
            str(venv_root / 'env2' / bin_dir),
        ],
    )
    assert result.stdout.strip() == f'export PATH="{expected}{os.pathsep}$PATH"'


def test_install_initializes_all_envs(mocker: MockerFixture, tmp_path: Path) -> None:
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
    app_mock = mocker.patch('uv_toolbox.cli.app')

    cli_module.main()

    app_mock.assert_called_once_with()
