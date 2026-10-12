from __future__ import annotations

from typing import TYPE_CHECKING

from uv_toolbox.lock import check_lock, generate_lock, stale_environments, update_lock
from uv_toolbox.lockfile import EnvironmentLock, UvToolboxLock
from uv_toolbox.settings import UvToolboxSettings

if TYPE_CHECKING:
    from pathlib import Path

    from pytest_mock import MockerFixture


def _settings(tmp_path: Path, *envs: tuple[str, list[str]]) -> UvToolboxSettings:
    return UvToolboxSettings.model_validate(
        {
            'config_file': tmp_path / 'uv-toolbox.yaml',
            'environments': [{'name': name, 'dependencies': dependencies} for name, dependencies in envs],
        },
    )


def _current(settings: UvToolboxSettings, name: str, uv_lock: str = 'locked') -> EnvironmentLock:
    env = next(env for env in settings.environments if env.name == name)
    return EnvironmentLock(uv_lock=uv_lock, fingerprint=env.lock_fingerprint())


def test_generate_lock_locks_every_environment_from_existing_entries(tmp_path: Path, mocker: MockerFixture) -> None:
    """Each environment is locked starting from its existing uv.lock (or v1 pins), with flags forwarded."""
    settings = _settings(tmp_path, ('fmt', ['ruff']), ('test', ['pytest']), ('new', ['mypy']))
    lock_mock = mocker.patch('uv_toolbox.lock.lock_environment', side_effect=lambda env, *_, **__: f'{env.name}-lock')
    existing = UvToolboxLock(
        environments={
            'fmt': EnvironmentLock(uv_lock='fmt-old'),
            'test': EnvironmentLock(legacy_requirements='pytest==9'),
            'removed': EnvironmentLock(uv_lock='gone'),
        },
    )

    lock = generate_lock(settings, existing_lock=existing, upgrade_packages=['ruff'], refresh=True)

    assert list(lock.environments) == ['fmt', 'test', 'new']
    for env in settings.environments:
        assert lock.environments[env.name] == EnvironmentLock(
            uv_lock=f'{env.name}-lock',
            fingerprint=env.lock_fingerprint(),
        )
    calls = {call.args[0].name: call.kwargs for call in lock_mock.call_args_list}
    assert calls['fmt']['uv_lock'] == 'fmt-old'
    assert calls['test'] == {
        'uv_lock': None,
        'legacy_requirements': 'pytest==9',
        'upgrade': False,
        'upgrade_packages': ['ruff'],
        'refresh': True,
    }
    assert calls['new']['uv_lock'] is None


def test_generate_lock_without_existing_lock_forwards_upgrade(tmp_path: Path, mocker: MockerFixture) -> None:
    """Without a lockfile every environment is locked from scratch."""
    settings = _settings(tmp_path, ('fmt', ['ruff']))
    lock_mock = mocker.patch('uv_toolbox.lock.lock_environment', return_value='lock')

    generate_lock(settings, upgrade=True)

    assert lock_mock.call_args.kwargs['uv_lock'] is None
    assert lock_mock.call_args.kwargs['upgrade'] is True


def test_stale_environments(tmp_path: Path) -> None:
    """Missing, fingerprint-mismatched and version 1 entries are stale; current entries are not."""
    settings = _settings(tmp_path, ('current', ['ruff']), ('changed', ['black']), ('missing', ['mypy']), ('v1', ['x']))
    v1_env = settings.environments[3]
    lock = UvToolboxLock(
        environments={
            'current': _current(settings, 'current'),
            'changed': EnvironmentLock(uv_lock='locked', fingerprint='sha256:old'),
            'v1': EnvironmentLock(legacy_requirements='x==1', fingerprint=v1_env.lock_fingerprint()),
        },
    )

    assert stale_environments(settings, lock) == ['changed', 'missing', 'v1']


def test_update_lock_relocks_only_stale_environments(tmp_path: Path, mocker: MockerFixture) -> None:
    """Up-to-date entries are copied unchanged, stale ones re-locked from their old lock, removed ones dropped."""
    settings = _settings(tmp_path, ('current', ['ruff']), ('changed', ['black>=24']))
    lock_mock = mocker.patch('uv_toolbox.lock.lock_environment', return_value='relocked')
    current = _current(settings, 'current')
    existing = UvToolboxLock(
        environments={
            'current': current,
            'changed': EnvironmentLock(uv_lock='old', fingerprint='sha256:old'),
            'removed': EnvironmentLock(uv_lock='gone'),
        },
    )

    lock = update_lock(settings, existing, ['changed'])

    assert lock.environments == {
        'current': current,
        'changed': EnvironmentLock(uv_lock='relocked', fingerprint=settings.environments[1].lock_fingerprint()),
    }
    lock_mock.assert_called_once()
    assert lock_mock.call_args.kwargs['uv_lock'] == 'old'


def test_check_lock_collects_failures(tmp_path: Path, mocker: MockerFixture) -> None:
    """Every environment is checked against its uv.lock; failures are reported by name."""
    settings = _settings(tmp_path, ('ok', ['ruff']), ('bad', ['black']))
    check_mock = mocker.patch(
        'uv_toolbox.lock.check_environment',
        side_effect=lambda env, *_, **__: None if env.name == 'ok' else 'needs to be updated',
    )
    lock = UvToolboxLock(environments={'ok': _current(settings, 'ok', 'a'), 'bad': _current(settings, 'bad', 'b')})

    assert check_lock(settings, lock) == {'bad': 'needs to be updated'}
    assert [call.kwargs['uv_lock'] for call in check_mock.call_args_list] == ['a', 'b']
