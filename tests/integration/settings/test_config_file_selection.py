from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
from typer.testing import CliRunner

from uv_toolbox.cli import app
from uv_toolbox.errors import ConfigFileNotFoundError, MissingConfigFileError
from uv_toolbox.settings import UvToolboxSettings

if TYPE_CHECKING:
    from pathlib import Path

    import typer
    from pytest_mock import MockerFixture

runner = CliRunner()


def _write_config(tmp_path: Path) -> Path:
    config_path = tmp_path / 'uvtb.yaml'
    config_path.write_text(
        '\n'.join(
            [
                f'venv_path: {tmp_path / ".uv-toolbox"}',
                'environments:',
                '  - name: env1',
                '    requirements: ruff',
                '',
            ],
        ),
    )
    return config_path


def test_from_context_raises_when_no_config_found(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    ctx = cast('typer.Context', SimpleNamespace(obj={}))

    with pytest.raises(MissingConfigFileError):
        UvToolboxSettings.from_context(ctx)


def test_from_context_raises_when_config_file_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    ctx = cast(
        'typer.Context',
        SimpleNamespace(obj={'config_file': tmp_path / 'missing.yaml'}),
    )

    with pytest.raises(ConfigFileNotFoundError):
        UvToolboxSettings.from_context(ctx)


def test_cli_config_option_is_used(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    config_path = _write_config(tmp_path)
    mocker.patch.object(
        sys,
        'argv',
        ['uvtb', 'exec', '--', 'ruff', '--version'],
    )
    mocker.patch('uv_toolbox.cli.initialize_virtualenv')
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
    run_mock.assert_called_once()


def test_cli_config_env_var_is_used(
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = _write_config(tmp_path)
    monkeypatch.setenv('UV_TOOLBOX_CONFIG_FILE', str(config_path))
    mocker.patch.object(
        sys,
        'argv',
        ['uvtb', 'exec', '--', 'ruff', '--version'],
    )
    mocker.patch('uv_toolbox.cli.initialize_virtualenv')
    run_mock = mocker.patch('uv_toolbox.cli.run_checked')

    result = runner.invoke(
        app,
        ['exec', '--env', 'env1', '--', 'ruff', '--version'],
    )

    assert result.exit_code == 0
    run_mock.assert_called_once()


def test_from_context_raises_when_pyproject_missing_tool_section(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pyproject_path = tmp_path / 'pyproject.toml'
    pyproject_path.write_text('[tool.other]\nvalue = "nope"\n')
    monkeypatch.chdir(tmp_path)
    ctx = cast('typer.Context', SimpleNamespace(obj={}))

    with pytest.raises(MissingConfigFileError):
        UvToolboxSettings.from_context(ctx)


def test_from_context_raises_when_pyproject_tool_is_not_mapping(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pyproject_path = tmp_path / 'pyproject.toml'
    pyproject_path.write_text('tool = "not-a-table"\n')
    monkeypatch.chdir(tmp_path)
    ctx = cast('typer.Context', SimpleNamespace(obj={}))

    with pytest.raises(MissingConfigFileError):
        UvToolboxSettings.from_context(ctx)


def test_from_context_raises_when_pyproject_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pyproject_path = tmp_path / 'pyproject.toml'
    pyproject_path.write_text('[tool.uv-toolbox\n')
    monkeypatch.chdir(tmp_path)
    ctx = cast('typer.Context', SimpleNamespace(obj={}))

    with pytest.raises(MissingConfigFileError):
        UvToolboxSettings.from_context(ctx)


def test_from_context_reads_pyproject_when_config_set(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pyproject_path = tmp_path / 'pyproject.toml'
    pyproject_path.write_text(
        '\n'.join(
            [
                '[tool.uv-toolbox]',
                f'venv_path = "{tmp_path / ".uv-toolbox"}"',
                '[[tool.uv-toolbox.environments]]',
                'name = "env1"',
                'requirements = "ruff"',
                '',
            ],
        ),
    )
    monkeypatch.chdir(tmp_path)
    ctx = cast(
        'typer.Context',
        SimpleNamespace(obj={'config_file': pyproject_path}),
    )

    settings = UvToolboxSettings.from_context(ctx)

    assert settings.environments[0].name == 'env1'


@pytest.mark.parametrize('suffix', ['json', 'toml'])
def test_from_context_reads_json_and_toml_configs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    suffix: str,
) -> None:
    config_path = tmp_path / f'config.{suffix}'
    if suffix == 'json':
        config_path.write_text(
            '{'
            f'"venv_path": "{tmp_path / ".uv-toolbox"}", '
            '"environments": [{"name": "env1", "requirements": "ruff"}]'
            '}',
        )
    else:
        config_path.write_text(
            '\n'.join(
                [
                    f'venv_path = "{tmp_path / ".uv-toolbox"}"',
                    '[[environments]]',
                    'name = "env1"',
                    'requirements = "ruff"',
                    '',
                ],
            ),
        )
    monkeypatch.chdir(tmp_path)
    ctx = cast(
        'typer.Context',
        SimpleNamespace(obj={'config_file': config_path}),
    )

    settings = UvToolboxSettings.from_context(ctx)

    assert settings.environments[0].name == 'env1'


def test_from_context_rejects_unsupported_config_extension(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / 'config.ini'
    config_path.write_text('[dummy]\nvalue = true\n')
    monkeypatch.chdir(tmp_path)
    ctx = cast(
        'typer.Context',
        SimpleNamespace(obj={'config_file': config_path}),
    )

    with pytest.raises(ValueError, match='Unsupported config file extension'):
        UvToolboxSettings.from_context(ctx)
