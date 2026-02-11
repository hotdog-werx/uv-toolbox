from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from typer.testing import CliRunner

from uv_toolbox import cli as cli_module
from uv_toolbox.cli import app
from uv_toolbox.errors import CommandDelimiterRequiredError, UvToolboxError
from uv_toolbox.settings import UvToolboxSettings
from uv_toolbox.utils import _venv_bin_path

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


def _create_fake_venv(venv_path: Path, tools: list[str]) -> None:
    """Create a fake venv with specified tool executables."""
    bin_path = _venv_bin_path(venv_path)
    bin_path.mkdir(parents=True, exist_ok=True)

    for tool in tools:
        tool_path = bin_path / tool
        tool_path.write_text('#!/usr/bin/env python\nprint("fake tool")\n')
        # Make executable on Unix
        if os.name != 'nt':
            tool_path.chmod(
                tool_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH,
            )


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
    assert args == ['uv', 'run', '--isolated', '--', 'ruff', '--version']
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
    # Write config with executables specified
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

    # Create settings to compute venv paths
    settings = UvToolboxSettings.model_validate(
        {
            'venv_path': venv_root,
            'environments': [
                {'name': 'env1', 'requirements': 'ruff', 'executables': ['ruff']},
                {'name': 'env2', 'requirements': 'black', 'executables': ['black']},
            ],
        }
    )

    # Create fake venvs
    _create_fake_venv(settings.environments[0].venv_path(settings=settings), ['ruff'])
    _create_fake_venv(settings.environments[1].venv_path(settings=settings), ['black'])

    result = runner.invoke(app, ['--config', str(config_path), 'shim'])

    assert result.exit_code == 0
    output = result.stdout.strip()
    # Check format is correct
    assert output.startswith('export PATH="')
    assert output.endswith(f'{os.pathsep}$PATH"')
    # Should contain shim directories (per-venv)
    assert 'shims' in output


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


def test_install_handles_uv_toolbox_error(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
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


def test_shim_outputs_comment_when_no_shims(tmp_path: Path) -> None:
    venv_root = tmp_path / '.uv-toolbox'
    config_path = _write_config(
        tmp_path,
        venv_path=venv_root,
        envs=[('env1', 'ruff')],
    )

    # Don't create any venvs - should output comment
    result = runner.invoke(app, ['--config', str(config_path), 'shim'])

    assert result.exit_code == 0
    output = result.stdout.strip()
    assert output == '# No shims to add to PATH'


def test_shim_handles_uv_toolbox_error(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
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
