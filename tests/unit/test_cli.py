from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from typer.testing import CliRunner

from uv_toolbox import cli as cli_module
from uv_toolbox.cli import app
from uv_toolbox.errors import CommandDelimiterRequiredError

if TYPE_CHECKING:
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
    # Check that VIRTUAL_ENV is in the venv_root (content-addressed subdir)
    assert extra_env['VIRTUAL_ENV'].startswith(str(venv_root))
    # Extract just the final directory name (the hash) - platform independent

    venv_hash = Path(extra_env['VIRTUAL_ENV']).name
    assert len(venv_hash) == 12  # Hash is 12 chars


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
    output = result.stdout.strip()
    # Check format is correct
    assert output.startswith('export PATH="')
    assert output.endswith(f'{os.pathsep}$PATH"')
    # Check both venvs are included (content-addressed, so hashes not names)
    bin_dir = 'Scripts' if os.name == 'nt' else 'bin'
    assert str(venv_root) in output
    # Platform-independent check for bin/Scripts directory in output
    assert f'{os.sep}{bin_dir}{os.pathsep}' in output or f'{os.sep}{bin_dir}"' in output
    # Check two paths are present (two hashes)
    assert output.count(str(venv_root)) == 2


def test_install_initializes_all_envs(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
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
