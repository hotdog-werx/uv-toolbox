from __future__ import annotations

import json
import tomllib
from textwrap import dedent
from typing import TYPE_CHECKING

import pytest
import yaml

from uv_toolbox.migrate import migrate_config
from uv_toolbox.project import ProjectSpec
from uv_toolbox.settings import UvToolboxSettings

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path
    from unittest.mock import MagicMock

    from pytest_mock import MockerFixture

pytestmark = pytest.mark.filterwarnings('ignore::FutureWarning')

_SPEC = ProjectSpec(
    dependencies=['ruff', 'tool'],
    sources={'tool': {'git': 'https://example.com/tool.git', 'rev': 'v1'}},
    uv_settings={'constraint-dependencies': ['ruff<1'], 'prerelease': 'allow'},
)

_EXPECTED_ENV = {
    'name': 'old',
    'dependencies': ['ruff', 'tool'],
    'sources': {'tool': {'git': 'https://example.com/tool.git', 'rev': 'v1'}},
    'uv': {'constraint-dependencies': ['ruff<1'], 'prerelease': 'allow'},
    'omit_executables': ['x'],
}


def _settings(config_file: Path, table: Mapping[str, object]) -> UvToolboxSettings:
    return UvToolboxSettings.model_validate({'config_file': config_file, **table})


@pytest.fixture
def convert(mocker: MockerFixture) -> MagicMock:
    """Stub the uv-backed conversion and the requires-python lookup."""
    mocker.patch('uv_toolbox.migrate.resolve_requires_python', return_value='>=3.12')
    return mocker.patch('uv_toolbox.migrate.convert_requirements', return_value=_SPEC)


_TABLE = {
    'venv_path': '.venvs',
    'environments': [
        {'name': 'new', 'dependencies': ['black']},
        {'name': 'old', 'requirements': 'ruff\n-c c.txt\n', 'uv': {'prerelease': 'allow'}, 'omit_executables': ['x']},
    ],
}


def test_migrate_yaml_rewrites_only_deprecated_environments(tmp_path: Path, convert: MagicMock) -> None:
    """Deprecated environments are replaced in place; other environments and settings are kept."""
    config_file = tmp_path / 'uv-toolbox.yaml'
    config_file.write_text(yaml.safe_dump(_TABLE, sort_keys=False), encoding='utf-8')

    migration = migrate_config(_settings(config_file, _TABLE))

    assert migration.migrated == ['old']
    assert migration.rewritable
    assert '  - name: new\n' in migration.content
    data = yaml.safe_load(migration.content)
    assert data['venv_path'] == '.venvs'
    assert data['environments'] == [{'name': 'new', 'dependencies': ['black']}, _EXPECTED_ENV]
    _settings(config_file, data)


def test_migrate_json(tmp_path: Path, convert: MagicMock) -> None:
    """JSON configs are rewritten as indented JSON."""
    config_file = tmp_path / 'uv-toolbox.json'
    config_file.write_text(json.dumps(_TABLE), encoding='utf-8')

    migration = migrate_config(_settings(config_file, _TABLE))

    assert json.loads(migration.content)['environments'][1] == _EXPECTED_ENV


def test_migrate_toml_renders_sub_tables(tmp_path: Path, convert: MagicMock) -> None:
    """uv-toolbox.toml is rewritten with sources and uv as environment sub-tables."""
    config_file = tmp_path / 'uv-toolbox.toml'
    config_file.write_text(
        dedent("""\
            venv_path = ".venvs"

            [[environments]]
            name = "new"
            dependencies = ["black"]

            [[environments]]
            name = "old"
            requirements = "ruff\\n-c c.txt\\n"
            omit_executables = ["x"]

            [environments.uv]
            prerelease = "allow"
        """),
        encoding='utf-8',
    )

    migration = migrate_config(_settings(config_file, _TABLE))

    assert '[environments.sources]' in migration.content
    data = tomllib.loads(migration.content)
    assert data['venv_path'] == '.venvs'
    assert data['environments'][1] == _EXPECTED_ENV


def test_migrate_pyproject_prints_tables_instead_of_rewriting(tmp_path: Path, convert: MagicMock) -> None:
    """pyproject.toml is never rewritten; the replacement tables for migrated environments are returned."""
    config_file = tmp_path / 'pyproject.toml'
    config_file.write_text(
        dedent("""\
            [project]
            name = "app"

            [[tool.uv-toolbox.environments]]
            name = "old"
            requirements = "ruff"
            omit_executables = ["x"]
        """),
        encoding='utf-8',
    )

    migration = migrate_config(
        _settings(
            config_file,
            {'environments': [{'name': 'old', 'requirements': 'ruff', 'omit_executables': ['x']}]},
        ),
    )

    assert not migration.rewritable
    assert migration.content.startswith('[[tool.uv-toolbox.environments]]\n')
    data = tomllib.loads(migration.content)
    assert data['tool']['uv-toolbox']['environments'] == [
        {key: value for key, value in _EXPECTED_ENV.items() if key != 'omit_executables'}
        | {'omit_executables': ['x']},
    ]


def test_migrate_without_deprecated_environments(tmp_path: Path, convert: MagicMock) -> None:
    """Nothing is converted when every environment already uses dependencies."""
    config_file = tmp_path / 'uv-toolbox.yaml'
    table = {'environments': [{'name': 'new', 'dependencies': ['black']}]}
    config_file.write_text(yaml.safe_dump(table), encoding='utf-8')

    migration = migrate_config(_settings(config_file, table))

    assert migration.migrated == []
    convert.assert_not_called()


def test_migrate_omits_empty_sources_and_uv(tmp_path: Path, mocker: MockerFixture) -> None:
    """Environments without sources or uv settings get only dependencies."""
    mocker.patch('uv_toolbox.migrate.resolve_requires_python', return_value='>=3.12')
    mocker.patch('uv_toolbox.migrate.convert_requirements', return_value=ProjectSpec(dependencies=['ruff']))
    config_file = tmp_path / 'uv-toolbox.yaml'
    table = {'environments': [{'name': 'old', 'requirements': 'ruff'}]}
    config_file.write_text(yaml.safe_dump(table), encoding='utf-8')

    migration = migrate_config(_settings(config_file, table))

    assert yaml.safe_load(migration.content) == {'environments': [{'name': 'old', 'dependencies': ['ruff']}]}
