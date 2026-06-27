from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from uv_toolbox.errors import ExternalCommandError
from uv_toolbox.settings import UvToolboxEnvironment, UvToolboxSettings
from uv_toolbox.uv_helpers import (
    _lockfile_path,
    create_virtualenv,
    initialize_virtualenv,
    install_requirements,
)

if TYPE_CHECKING:
    from pathlib import Path

    from pytest_mock import MockerFixture


def _make_settings(
    tmp_path: Path,
    *,
    env: UvToolboxEnvironment,
) -> UvToolboxSettings:
    return UvToolboxSettings.model_validate(
        {
            'venv_path': tmp_path / '.uv-toolbox',
            'show_commands': False,
            'environments': [
                {
                    'name': env.name,
                    'requirements': env.requirements,
                    'requirements_file': env.requirements_file,
                    'environment': env.environment,
                },
            ],
        },
    )


# ── create_virtualenv ─────────────────────────────────────────────────────────


def test_create_virtualenv_runs_uv_venv(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    """Calls `uv venv` with the content-addressed venv path and the environment's process env."""
    env = UvToolboxEnvironment(name='env1', requirements='ruff')
    settings = _make_settings(tmp_path, env=env)
    run_mock = mocker.patch('uv_toolbox.uv_helpers.run_checked')

    create_virtualenv(env=env, settings=settings)

    run_mock.assert_called_once_with(
        args=['uv', 'venv', str(env.venv_path(settings=settings))],
        extra_env=env.process_env(settings=settings),
        capture_stdout=False,
        capture_stderr=False,
        show_command=False,
    )


def test_create_virtualenv_with_clear_flag(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    """Appends `--clear` to the `uv venv` invocation when clear=True."""
    env = UvToolboxEnvironment(name='env1', requirements='ruff')
    settings = _make_settings(tmp_path, env=env)
    run_mock = mocker.patch('uv_toolbox.uv_helpers.run_checked')

    create_virtualenv(env=env, settings=settings, clear=True)

    run_mock.assert_called_once_with(
        args=['uv', 'venv', str(env.venv_path(settings=settings)), '--clear'],
        extra_env=env.process_env(settings=settings),
        capture_stdout=False,
        capture_stderr=False,
        show_command=False,
    )


# ── install_requirements: initial install (no lockfile) ───────────────────────


def test_install_requirements_uses_requirements_file_on_first_install(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    """Passes the requirements file path directly to `uv pip install --exact`, skipping temp file creation."""
    req_file = tmp_path / 'requirements.txt'
    req_file.write_text('ruff\n')
    env = UvToolboxEnvironment(name='env1', requirements_file=req_file)
    settings = _make_settings(tmp_path, env=env)
    run_mock = mocker.patch(
        'uv_toolbox.uv_helpers.run_checked',
        side_effect=['', 'ruff==0.14.14'],
    )
    rmtree_mock = mocker.patch('uv_toolbox.uv_helpers.shutil.rmtree')
    mkdtemp_mock = mocker.patch('uv_toolbox.uv_helpers.tempfile.mkdtemp')

    install_requirements(env=env, settings=settings)

    assert run_mock.call_count == 2
    run_mock.assert_any_call(
        args=['uv', 'pip', 'sync', str(req_file)],
        extra_env=env.process_env(settings=settings),
        capture_stdout=False,
        capture_stderr=False,
        show_command=False,
    )
    run_mock.assert_any_call(
        args=['uv', 'pip', 'freeze'],
        extra_env=env.process_env(settings=settings),
        capture_stdout=True,
        capture_stderr=False,
        show_command=False,
    )
    rmtree_mock.assert_not_called()
    mkdtemp_mock.assert_not_called()


def test_install_requirements_writes_lockfile_after_first_install(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    """Writes inline requirements to a named temp file and passes it to uv.

    Verifies the temp directory is cleaned up after the sync completes.
    """
    env = UvToolboxEnvironment(
        name='env1',
        requirements='ruff==0.13.1',
    )
    settings = _make_settings(tmp_path, env=env)
    venv_path = env.venv_path(settings=settings)
    lockfile = _lockfile_path(venv_path)

    mocker.patch(
        'uv_toolbox.uv_helpers.run_checked',
        side_effect=['', 'ruff==0.14.14'],
    )

    install_requirements(env=env, settings=settings)

    assert lockfile.read_text() == 'ruff==0.14.14'


def test_install_requirements_writes_temp_file_for_inline_requirements(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    env = UvToolboxEnvironment(name='env1', requirements='ruff==0.13.1')
    settings = _make_settings(tmp_path, env=env)
    run_mock = mocker.patch(
        'uv_toolbox.uv_helpers.run_checked',
        side_effect=['', 'ruff==0.13.1'],
    )
    rmtree_mock = mocker.patch('uv_toolbox.uv_helpers.shutil.rmtree')
    temp_dir = tmp_path / 'reqs'
    temp_dir.mkdir()
    mocker.patch(
        'uv_toolbox.uv_helpers.tempfile.mkdtemp',
        return_value=str(temp_dir),
    )

    install_requirements(env=env, settings=settings)

    temp_req_file = temp_dir / 'requirements_env1.txt'
    assert temp_req_file.read_text() == 'ruff==0.13.1'
    run_mock.assert_any_call(
        args=['uv', 'pip', 'sync', str(temp_req_file)],
        extra_env=env.process_env(settings=settings),
        capture_stdout=False,
        capture_stderr=False,
        show_command=False,
    )
    rmtree_mock.assert_called_once_with(temp_dir)


# ── install_requirements: lockfile exists ────────────────────────────────────


def test_install_requirements_syncs_offline_when_lockfile_exists(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    env = UvToolboxEnvironment(name='env1', requirements='ruff==0.14.14')
    settings = _make_settings(tmp_path, env=env)
    venv_path = env.venv_path(settings=settings)
    lockfile = _lockfile_path(venv_path)
    lockfile.parent.mkdir(parents=True, exist_ok=True)
    lockfile.write_text('ruff==0.14.14')

    run_mock = mocker.patch(
        'uv_toolbox.uv_helpers.run_checked',
        return_value='',
    )

    install_requirements(env=env, settings=settings)

    run_mock.assert_called_once_with(
        args=['uv', 'pip', 'sync', str(lockfile), '--offline'],
        extra_env=env.process_env(settings=settings),
        capture_stdout=False,
        capture_stderr=False,
        show_command=False,
    )


def test_install_requirements_falls_back_online_when_offline_fails(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    env = UvToolboxEnvironment(name='env1', requirements='ruff==0.14.14')
    settings = _make_settings(tmp_path, env=env)
    venv_path = env.venv_path(settings=settings)
    lockfile = _lockfile_path(venv_path)
    lockfile.parent.mkdir(parents=True, exist_ok=True)
    lockfile.write_text('ruff==0.14.14')

    offline_error = ExternalCommandError(
        cmd_args=['uv', 'pip', 'sync', str(lockfile), '--offline'],
        returncode=2,
        stderr='error: Network connectivity is disabled',
    )
    run_mock = mocker.patch(
        'uv_toolbox.uv_helpers.run_checked',
        side_effect=[offline_error, ''],
    )

    install_requirements(env=env, settings=settings)

    assert run_mock.call_count == 2
    run_mock.assert_called_with(
        args=['uv', 'pip', 'sync', str(lockfile)],
        extra_env=env.process_env(settings=settings),
        capture_stdout=False,
        capture_stderr=False,
        show_command=False,
    )


def test_install_requirements_does_not_update_lockfile_after_online_fallback(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    env = UvToolboxEnvironment(name='env1', requirements='ruff==0.14.14')
    settings = _make_settings(tmp_path, env=env)
    venv_path = env.venv_path(settings=settings)
    lockfile = _lockfile_path(venv_path)
    lockfile.parent.mkdir(parents=True, exist_ok=True)
    lockfile.write_text('ruff==0.14.14')

    offline_error = ExternalCommandError(
        cmd_args=['uv', 'pip', 'sync', str(lockfile), '--offline'],
        returncode=2,
        stderr='error: Network connectivity is disabled',
    )
    mocker.patch(
        'uv_toolbox.uv_helpers.run_checked',
        side_effect=[offline_error, ''],
    )

    install_requirements(env=env, settings=settings)

    assert lockfile.read_text() == 'ruff==0.14.14'


# ── install_requirements: upgrade ────────────────────────────────────────────


def test_install_requirements_upgrade_deletes_lockfile_and_reinstalls(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    env = UvToolboxEnvironment(name='env1', requirements='ruff==0.14.14')
    settings = _make_settings(tmp_path, env=env)
    venv_path = env.venv_path(settings=settings)
    lockfile = _lockfile_path(venv_path)
    lockfile.parent.mkdir(parents=True, exist_ok=True)
    lockfile.write_text('ruff==0.14.14')

    mocker.patch(
        'uv_toolbox.uv_helpers.run_checked',
        side_effect=['', 'ruff==0.14.14'],
    )
    temp_dir = tmp_path / 'reqs'
    temp_dir.mkdir()
    mocker.patch(
        'uv_toolbox.uv_helpers.tempfile.mkdtemp',
        return_value=str(temp_dir),
    )

    install_requirements(env=env, settings=settings, upgrade=True)

    # Lockfile was replaced with fresh freeze output
    assert lockfile.read_text() == 'ruff==0.14.14'


def test_install_requirements_upgrade_triggers_initial_install_even_if_lockfile_existed(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    env = UvToolboxEnvironment(name='env1', requirements='ruff==0.14.14')
    settings = _make_settings(tmp_path, env=env)
    venv_path = env.venv_path(settings=settings)
    lockfile = _lockfile_path(venv_path)
    lockfile.parent.mkdir(parents=True, exist_ok=True)
    lockfile.write_text('ruff==0.13.0')  # stale

    run_mock = mocker.patch(
        'uv_toolbox.uv_helpers.run_checked',
        side_effect=['', 'ruff==0.14.14'],
    )
    temp_dir = tmp_path / 'reqs'
    temp_dir.mkdir()
    mocker.patch(
        'uv_toolbox.uv_helpers.tempfile.mkdtemp',
        return_value=str(temp_dir),
    )

    install_requirements(env=env, settings=settings, upgrade=True)

    # Should have called sync + freeze, not the offline sync path
    assert run_mock.call_count == 2
    first_call_args = run_mock.call_args_list[0].kwargs['args']
    assert first_call_args[2] == 'sync'
    assert '--offline' not in first_call_args


# ── install_requirements: resolved requirements path ─────────────────────────


def test_install_requirements_uses_resolved_when_no_machine_lockfile(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    env = UvToolboxEnvironment(name='env1', requirements='ruff')
    env._resolved_requirements = 'ruff==0.14.14 \\\n    --hash=sha256:aaaa\n'
    settings = _make_settings(tmp_path, env=env)

    temp_dir = tmp_path / 'tmp'
    temp_dir.mkdir()
    mocker.patch(
        'uv_toolbox.uv_helpers.tempfile.mkdtemp',
        return_value=str(temp_dir),
    )
    rmtree_mock = mocker.patch('uv_toolbox.uv_helpers.shutil.rmtree')
    run_mock = mocker.patch(
        'uv_toolbox.uv_helpers.run_checked',
        return_value='',
    )

    install_requirements(env=env, settings=settings)

    temp_req_file = temp_dir / 'requirements_env1.txt'
    assert temp_req_file.read_text() == 'ruff==0.14.14 \\\n    --hash=sha256:aaaa\n'
    run_mock.assert_called_once_with(
        args=['uv', 'pip', 'sync', str(temp_req_file)],
        extra_env=env.process_env(settings=settings),
        capture_stdout=False,
        capture_stderr=False,
        show_command=False,
    )
    rmtree_mock.assert_called_once_with(temp_dir)


def test_install_requirements_machine_lockfile_written_with_resolved_content(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    resolved = 'ruff==0.14.14 \\\n    --hash=sha256:aaaa\n'
    env = UvToolboxEnvironment(name='env1', requirements='ruff')
    env._resolved_requirements = resolved
    settings = _make_settings(tmp_path, env=env)
    venv_path = env.venv_path(settings=settings)
    lockfile = _lockfile_path(venv_path)

    temp_dir = tmp_path / 'tmp'
    temp_dir.mkdir()
    mocker.patch(
        'uv_toolbox.uv_helpers.tempfile.mkdtemp',
        return_value=str(temp_dir),
    )
    mocker.patch('uv_toolbox.uv_helpers.shutil.rmtree')
    mocker.patch('uv_toolbox.uv_helpers.run_checked', return_value='')

    install_requirements(env=env, settings=settings)

    assert lockfile.read_text() == resolved


def test_install_requirements_prefers_machine_lockfile_over_resolved(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    env = UvToolboxEnvironment(name='env1', requirements='ruff')
    env._resolved_requirements = 'ruff==0.14.14 \\\n    --hash=sha256:aaaa\n'
    settings = _make_settings(tmp_path, env=env)
    venv_path = env.venv_path(settings=settings)
    lockfile = _lockfile_path(venv_path)
    lockfile.parent.mkdir(parents=True, exist_ok=True)
    lockfile.write_text('ruff==0.14.14')  # machine lockfile already present

    run_mock = mocker.patch(
        'uv_toolbox.uv_helpers.run_checked',
        return_value='',
    )

    install_requirements(env=env, settings=settings)

    # Should take the offline-first path with the existing machine lockfile
    run_mock.assert_called_once_with(
        args=['uv', 'pip', 'sync', str(lockfile), '--offline'],
        extra_env=env.process_env(settings=settings),
        capture_stdout=False,
        capture_stderr=False,
        show_command=False,
    )


# ── initialize_virtualenv ─────────────────────────────────────────────────────


def test_initialize_virtualenv_calls_helpers_in_order(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    """Calls create_virtualenv before install_requirements."""
    env = UvToolboxEnvironment(name='env1', requirements='ruff')
    settings = _make_settings(tmp_path, env=env)
    calls: list[str] = []

    def _record_create(**_kwargs: object) -> None:
        calls.append('create')

    def _record_install(**_kwargs: object) -> None:
        calls.append('install')

    mocker.patch(
        'uv_toolbox.uv_helpers.create_virtualenv',
        side_effect=_record_create,
    )
    mocker.patch(
        'uv_toolbox.uv_helpers.install_requirements',
        side_effect=_record_install,
    )

    initialize_virtualenv(env=env, settings=settings)

    assert calls == ['create', 'install']


@pytest.mark.parametrize('clear', [True, False])
def test_initialize_virtualenv_skips_create_when_venv_exists_and_no_clear(
    mocker: MockerFixture,
    tmp_path: Path,
    clear: bool,  # noqa: FBT001
) -> None:
    env = UvToolboxEnvironment(name='env1', requirements='ruff')
    settings = _make_settings(tmp_path, env=env)
    venv_path = env.venv_path(settings=settings)
    venv_path.mkdir(parents=True)

    create_mock = mocker.patch('uv_toolbox.uv_helpers.create_virtualenv')
    mocker.patch('uv_toolbox.uv_helpers.install_requirements')

    initialize_virtualenv(env=env, settings=settings, clear=clear)

    if clear:
        create_mock.assert_called_once()
    else:
        create_mock.assert_not_called()
