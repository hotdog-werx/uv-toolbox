from __future__ import annotations

from pathlib import Path

import pytest

from uv_toolbox.errors import (
    EnvironmentNotFoundError,
    MultipleEnvironmentsError,
)
from uv_toolbox.settings import UvToolboxEnvironment, UvToolboxSettings


def _make_settings(
    *,
    envs: list[dict[str, object]],
    venv_path: Path | None = None,
    default_environment: str | None = None,
) -> UvToolboxSettings:
    payload: dict[str, object] = {
        'environments': envs,
    }
    if venv_path is not None:
        payload['venv_path'] = venv_path
    if default_environment is not None:
        payload['default_environment'] = default_environment
    return UvToolboxSettings.model_validate(payload)


@pytest.mark.parametrize(
    ('requirements', 'requirements_file'),
    [
        (None, None),
        ('ruff', Path('requirements.txt')),
    ],
)
def test_environment_requires_exactly_one_requirements(
    requirements: str | None,
    requirements_file: Path | None,
) -> None:
    with pytest.raises(ValueError, match='Exactly one of requirements'):
        UvToolboxEnvironment(
            name='env1',
            requirements=requirements,
            requirements_file=requirements_file,
        )


def test_environment_paths_from_settings(tmp_path: Path) -> None:
    settings = _make_settings(
        venv_path=tmp_path / '.uv-toolbox',
        envs=[{'name': 'env1', 'requirements': 'ruff'}],
    )
    env = settings.environments[0]

    # Venv path should be content-addressed (hash-based)
    venv_path = env.venv_path(settings=settings)
    assert venv_path.parent == tmp_path / '.uv-toolbox'
    assert len(venv_path.name) == 12  # Hash is 12 characters


def test_environment_process_env_expands_vars(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv('TEST_ROOT', str(tmp_path))
    settings = _make_settings(
        venv_path=tmp_path / '.uv-toolbox',
        envs=[
            {
                'name': 'env1',
                'requirements': 'ruff',
                'environment': {'TOOLS': '$TEST_ROOT/tools'},
            },
        ],
    )
    env = settings.environments[0]

    process_env = env.process_env(settings=settings)

    assert Path(process_env['TOOLS']) == tmp_path / 'tools'
    # VIRTUAL_ENV should be content-addressed
    virtual_env = Path(process_env['VIRTUAL_ENV'])
    assert virtual_env.parent == tmp_path / '.uv-toolbox'
    assert len(virtual_env.name) == 12  # Hash is 12 characters


def test_settings_reject_duplicate_env_names() -> None:
    with pytest.raises(ValueError, match='Duplicate environment names'):
        _make_settings(
            envs=[
                {'name': 'env1', 'requirements': 'ruff'},
                {'name': 'env1', 'requirements': 'black'},
            ],
        )


def test_settings_reject_invalid_default_environment() -> None:
    with pytest.raises(ValueError, match='Default environment'):
        _make_settings(
            envs=[{'name': 'env1', 'requirements': 'ruff'}],
            default_environment='env2',
        )


def test_select_environment_by_name() -> None:
    settings = _make_settings(
        envs=[
            {'name': 'env1', 'requirements': 'ruff'},
            {'name': 'env2', 'requirements': 'black'},
        ],
    )

    env = settings.select_environment('env2')

    assert env.name == 'env2'


def test_select_environment_uses_default() -> None:
    settings = _make_settings(
        envs=[
            {'name': 'env1', 'requirements': 'ruff'},
            {'name': 'env2', 'requirements': 'black'},
        ],
        default_environment='env1',
    )

    env = settings.select_environment(None)

    assert env.name == 'env1'


def test_select_environment_single_env_without_name() -> None:
    settings = _make_settings(envs=[{'name': 'env1', 'requirements': 'ruff'}])

    env = settings.select_environment(None)

    assert env.name == 'env1'


def test_select_environment_missing_name() -> None:
    settings = _make_settings(envs=[{'name': 'env1', 'requirements': 'ruff'}])

    with pytest.raises(EnvironmentNotFoundError):
        settings.select_environment('missing')


def test_select_environment_requires_name_with_multiple_envs() -> None:
    settings = _make_settings(
        envs=[
            {'name': 'env1', 'requirements': 'ruff'},
            {'name': 'env2', 'requirements': 'black'},
        ],
    )

    with pytest.raises(MultipleEnvironmentsError):
        settings.select_environment(None)
