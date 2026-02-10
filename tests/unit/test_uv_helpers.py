from __future__ import annotations

from typing import TYPE_CHECKING

from uv_toolbox.settings import UvToolboxEnvironment, UvToolboxSettings
from uv_toolbox.uv_helpers import (
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


def test_create_virtualenv_runs_uv_venv(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    env = UvToolboxEnvironment(name='env1', requirements='ruff')
    settings = _make_settings(tmp_path, env=env)
    run_mock = mocker.patch('uv_toolbox.uv_helpers.run_checked')

    create_virtualenv(env=env, settings=settings)

    run_mock.assert_called_once_with(
        args=['uv', 'venv', str(env.venv_path(settings=settings))],
        extra_env=env.process_env(settings=settings),
        capture_stdout=False,
        capture_stderr=False,
        show_command=True,
    )


def test_create_virtualenv_with_clear_flag(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    env = UvToolboxEnvironment(name='env1', requirements='ruff')
    settings = _make_settings(tmp_path, env=env)
    run_mock = mocker.patch('uv_toolbox.uv_helpers.run_checked')

    create_virtualenv(env=env, settings=settings, clear=True)

    run_mock.assert_called_once_with(
        args=['uv', 'venv', str(env.venv_path(settings=settings)), '--clear'],
        extra_env=env.process_env(settings=settings),
        capture_stdout=False,
        capture_stderr=False,
        show_command=True,
    )


def test_install_requirements_uses_requirements_file(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    req_file = tmp_path / 'requirements.txt'
    req_file.write_text('ruff\n')
    env = UvToolboxEnvironment(
        name='env1',
        requirements_file=req_file,
    )
    settings = _make_settings(tmp_path, env=env)
    run_mock = mocker.patch('uv_toolbox.uv_helpers.run_checked')
    rmtree_mock = mocker.patch('uv_toolbox.uv_helpers.shutil.rmtree')
    mkdtemp_mock = mocker.patch('uv_toolbox.uv_helpers.tempfile.mkdtemp')

    install_requirements(env=env, settings=settings)

    run_mock.assert_called_once_with(
        args=['uv', 'pip', 'install', '-r', str(req_file), '--exact'],
        extra_env=env.process_env(settings=settings),
        capture_stdout=False,
        capture_stderr=False,
        show_command=True,
    )
    rmtree_mock.assert_not_called()
    mkdtemp_mock.assert_not_called()


def test_install_requirements_writes_temp_file(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    env = UvToolboxEnvironment(
        name='env1',
        requirements='ruff==0.13.1',
    )
    settings = _make_settings(tmp_path, env=env)
    run_mock = mocker.patch('uv_toolbox.uv_helpers.run_checked')
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
    run_mock.assert_called_once_with(
        args=['uv', 'pip', 'install', '-r', str(temp_req_file), '--exact'],
        extra_env=env.process_env(settings=settings),
        capture_stdout=False,
        capture_stderr=False,
        show_command=True,
    )
    rmtree_mock.assert_called_once_with(temp_dir)


def test_initialize_virtualenv_calls_helpers_in_order(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
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
