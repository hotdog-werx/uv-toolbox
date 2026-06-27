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


# ── lockfile_path ─────────────────────────────────────────────────────────────


def test_lockfile_path_is_none_without_config_file() -> None:
    settings = _make_settings(envs=[{'name': 'env1', 'requirements': 'ruff'}])
    assert settings.lockfile_path is None


def test_lockfile_path_is_sibling_of_config_file(tmp_path: Path) -> None:
    config = tmp_path / 'uv-toolbox.yaml'
    config.write_text('environments:\n  - name: env1\n    requirements: ruff\n')
    settings = UvToolboxSettings.model_validate({'config_file': config, 'environments': [{'name': 'env1', 'requirements': 'ruff'}]})
    assert settings.lockfile_path == tmp_path / 'uv-toolbox.lock'


# ── inject_resolved_requirements ──────────────────────────────────────────────


def test_inject_resolved_requirements_populates_env(tmp_path: Path) -> None:
    from uv_toolbox.lockfile import EnvironmentLock, UvToolboxLock, write_lockfile

    lock = UvToolboxLock(environments={'env1': EnvironmentLock(requirements='ruff==0.14.14\n')})
    lock_path = tmp_path / 'uv-toolbox.lock'
    write_lockfile(lock, lock_path)

    config = tmp_path / 'uv-toolbox.yaml'
    config.write_text('environments:\n  - name: env1\n    requirements: ruff\n')

    settings = UvToolboxSettings.model_validate({
        'config_file': config,
        'environments': [{'name': 'env1', 'requirements': 'ruff'}],
    })

    env = settings.environments[0]
    assert env._resolved_requirements is not None
    assert 'ruff==0.14.14' in env._resolved_requirements


def test_inject_resolved_requirements_skipped_when_no_lockfile(tmp_path: Path) -> None:
    config = tmp_path / 'uv-toolbox.yaml'
    config.write_text('environments:\n  - name: env1\n    requirements: ruff\n')

    settings = UvToolboxSettings.model_validate({
        'config_file': config,
        'environments': [{'name': 'env1', 'requirements': 'ruff'}],
    })

    env = settings.environments[0]
    assert env._resolved_requirements is None


def test_inject_resolved_requirements_ignores_missing_env_in_lockfile(tmp_path: Path) -> None:
    from uv_toolbox.lockfile import EnvironmentLock, UvToolboxLock, write_lockfile

    lock = UvToolboxLock(environments={'other': EnvironmentLock(requirements='black==24.0.0\n')})
    lock_path = tmp_path / 'uv-toolbox.lock'
    write_lockfile(lock, lock_path)

    config = tmp_path / 'uv-toolbox.yaml'
    config.write_text('environments:\n  - name: env1\n    requirements: ruff\n')

    settings = UvToolboxSettings.model_validate({
        'config_file': config,
        'environments': [{'name': 'env1', 'requirements': 'ruff'}],
    })

    env = settings.environments[0]
    assert env._resolved_requirements is None


# ── CAS hash with resolved requirements ───────────────────────────────────────


def test_venv_path_changes_when_resolved_requirements_set(tmp_path: Path) -> None:
    from uv_toolbox.lockfile import EnvironmentLock, UvToolboxLock, write_lockfile

    settings_no_lock = _make_settings(
        venv_path=tmp_path / '.uv-toolbox',
        envs=[{'name': 'env1', 'requirements': 'ruff'}],
    )
    path_without_lock = settings_no_lock.environments[0].venv_path(settings=settings_no_lock)

    lock = UvToolboxLock(environments={'env1': EnvironmentLock(requirements='ruff==0.14.14 \\\n    --hash=sha256:aaaa\n')})
    lock_path = tmp_path / 'uv-toolbox.lock'
    write_lockfile(lock, lock_path)
    config = tmp_path / 'uv-toolbox.yaml'
    config.write_text('environments:\n  - name: env1\n    requirements: ruff\n')

    settings_with_lock = UvToolboxSettings.model_validate({
        'config_file': config,
        'venv_path': tmp_path / '.uv-toolbox',
        'environments': [{'name': 'env1', 'requirements': 'ruff'}],
    })
    path_with_lock = settings_with_lock.environments[0].venv_path(settings=settings_with_lock)

    assert path_without_lock != path_with_lock


# ── _normalize_resolved_requirements ─────────────────────────────────────────


def test_normalize_resolved_requirements_strips_comments_and_blanks() -> None:
    env = UvToolboxEnvironment(name='e', requirements='ruff')
    raw = '# generated by uv\nruff==0.14.14 \\\n    --hash=sha256:aaaa\n\n'
    result = env._normalize_resolved_requirements(raw)
    assert '# generated' not in result
    assert '' not in result.split('\n')


def test_normalize_resolved_requirements_preserves_hash_continuation_order() -> None:
    env = UvToolboxEnvironment(name='e', requirements='ruff')
    raw = 'ruff==0.14.14 \\\n    --hash=sha256:aaaa \\\n    --hash=sha256:bbbb\n'
    result = env._normalize_resolved_requirements(raw)
    lines = result.splitlines()
    assert lines[0].startswith('ruff==')
    assert lines[1].strip().startswith('--hash=sha256:aaaa')
    assert lines[2].strip().startswith('--hash=sha256:bbbb')
