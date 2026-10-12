from __future__ import annotations

from typing import TYPE_CHECKING

from uv_toolbox.settings import UvToolboxSettings
from uv_toolbox.uv_helpers import _machine_lock_path, initialize_virtualenv

if TYPE_CHECKING:
    from pathlib import Path

    from pytest_mock import MockerFixture


_REPO_LOCK = 'version = 1\nrevision = 3\n'


def _settings(tmp_path: Path) -> UvToolboxSettings:
    return UvToolboxSettings.model_validate(
        {
            'config_file': tmp_path / 'uv-toolbox.yaml',
            'venv_path': tmp_path / '.venvs',
            'environments': [{'name': 'tools', 'dependencies': ['ruff']}],
        },
    )


def test_machine_lock_is_a_sibling_of_the_venv(tmp_path: Path) -> None:
    """The machine lock lives next to the venv so clearing the venv keeps it."""
    assert _machine_lock_path(tmp_path / 'abc123') == tmp_path / 'abc123.uv.lock'


def test_installs_from_repo_lock_without_locking(tmp_path: Path, mocker: MockerFixture) -> None:
    """An environment in the repo lockfile is synced from its uv.lock; no machine lock is written."""
    settings = _settings(tmp_path)
    env = settings.environments[0]
    env._locked_uv_lock = _REPO_LOCK
    lock_mock = mocker.patch('uv_toolbox.uv_helpers.lock_environment')
    sync_mock = mocker.patch('uv_toolbox.uv_helpers.sync_environment')

    initialize_virtualenv(env, settings, clear=True, upgrade=True)

    lock_mock.assert_not_called()
    sync_mock.assert_called_once_with(env, settings, uv_lock=_REPO_LOCK, clear=True)
    assert not _machine_lock_path(env.venv_path(settings)).exists()


def test_first_install_without_repo_lock_writes_machine_lock(tmp_path: Path, mocker: MockerFixture) -> None:
    """Without a repo lockfile, the first install locks online and keeps the uv.lock next to the venv."""
    settings = _settings(tmp_path)
    env = settings.environments[0]
    lock_mock = mocker.patch('uv_toolbox.uv_helpers.lock_environment', return_value='fresh-lock')
    sync_mock = mocker.patch('uv_toolbox.uv_helpers.sync_environment')

    initialize_virtualenv(env, settings)

    lock_mock.assert_called_once_with(env, settings, upgrade=False)
    sync_mock.assert_called_once_with(env, settings, uv_lock='fresh-lock', clear=False)
    assert _machine_lock_path(env.venv_path(settings)).read_text() == 'fresh-lock'


def test_later_installs_reuse_machine_lock(tmp_path: Path, mocker: MockerFixture) -> None:
    """An existing machine lock is synced without locking again."""
    settings = _settings(tmp_path)
    env = settings.environments[0]
    machine_lock = _machine_lock_path(env.venv_path(settings))
    machine_lock.parent.mkdir(parents=True)
    machine_lock.write_text('machine-lock')
    lock_mock = mocker.patch('uv_toolbox.uv_helpers.lock_environment')
    sync_mock = mocker.patch('uv_toolbox.uv_helpers.sync_environment')

    initialize_virtualenv(env, settings)

    lock_mock.assert_not_called()
    sync_mock.assert_called_once_with(env, settings, uv_lock='machine-lock', clear=False)


def test_upgrade_relocks_machine_lock(tmp_path: Path, mocker: MockerFixture) -> None:
    """--upgrade re-resolves and replaces the machine lock."""
    settings = _settings(tmp_path)
    env = settings.environments[0]
    machine_lock = _machine_lock_path(env.venv_path(settings))
    machine_lock.parent.mkdir(parents=True)
    machine_lock.write_text('old-lock')
    lock_mock = mocker.patch('uv_toolbox.uv_helpers.lock_environment', return_value='new-lock')
    mocker.patch('uv_toolbox.uv_helpers.sync_environment')

    initialize_virtualenv(env, settings, upgrade=True)

    lock_mock.assert_called_once_with(env, settings, upgrade=True)
    assert machine_lock.read_text() == 'new-lock'
