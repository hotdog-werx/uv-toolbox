from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from uv_toolbox.lock import generate_environment_lock, generate_lock
from uv_toolbox.lockfile import EnvironmentLock, UvToolboxLock
from uv_toolbox.settings import UvToolboxEnvironment, UvToolboxSettings

if TYPE_CHECKING:
    from pathlib import Path

    from pytest_mock import MockerFixture

_COMPILED = 'ruff==0.14.14 \\\n    --hash=sha256:aaaa'


def _make_settings(tmp_path: Path, *, envs: list[UvToolboxEnvironment]) -> UvToolboxSettings:
    return UvToolboxSettings.model_validate(
        {
            'venv_path': tmp_path / '.uv-toolbox',
            'show_commands': False,
            'environments': [
                {
                    'name': e.name,
                    'requirements': e.requirements,
                    'requirements_file': e.requirements_file,
                }
                for e in envs
            ],
        },
    )


def test_generate_environment_lock_with_requirements_file(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    req_file = tmp_path / 'requirements.txt'
    req_file.write_text('ruff\n')
    env = UvToolboxEnvironment(name='fmt', requirements_file=req_file)
    settings = _make_settings(tmp_path, envs=[env])

    run_mock = mocker.patch('uv_toolbox.lock.run_checked', return_value=_COMPILED)
    result = generate_environment_lock(env=env, settings=settings)

    run_mock.assert_called_once_with(
        args=[
            'uv', 'pip', 'compile',
            '--generate-hashes', '--universal',
            '--no-header', '--no-annotate',
            '-o', '-',
            str(req_file),
        ],
        capture_stdout=True,
        capture_stderr=False,
        show_command=False,
    )
    assert result == _COMPILED


def test_generate_environment_lock_with_inline_requirements(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    env = UvToolboxEnvironment(name='fmt', requirements='ruff\n')
    settings = _make_settings(tmp_path, envs=[env])

    temp_dir = tmp_path / 'tmp'
    temp_dir.mkdir()
    mocker.patch('uv_toolbox.lock.tempfile.mkdtemp', return_value=str(temp_dir))
    rmtree_mock = mocker.patch('uv_toolbox.lock.shutil.rmtree')
    run_mock = mocker.patch('uv_toolbox.lock.run_checked', return_value=_COMPILED)

    result = generate_environment_lock(env=env, settings=settings)

    temp_req_file = temp_dir / 'requirements_fmt.txt'
    assert temp_req_file.read_text() == 'ruff\n'
    run_mock.assert_called_once_with(
        args=[
            'uv', 'pip', 'compile',
            '--generate-hashes', '--universal',
            '--no-header', '--no-annotate',
            '-o', '-',
            str(temp_req_file),
        ],
        capture_stdout=True,
        capture_stderr=False,
        show_command=False,
    )
    rmtree_mock.assert_called_once_with(temp_dir)
    assert result == _COMPILED


def test_generate_environment_lock_does_not_pass_virtual_env(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    env = UvToolboxEnvironment(name='fmt', requirements='ruff\n')
    settings = _make_settings(tmp_path, envs=[env])
    run_mock = mocker.patch('uv_toolbox.lock.run_checked', return_value=_COMPILED)

    generate_environment_lock(env=env, settings=settings)

    call_kwargs = run_mock.call_args.kwargs
    assert 'extra_env' not in call_kwargs


def test_generate_lock_covers_all_environments(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    envs = [
        UvToolboxEnvironment(name='fmt', requirements='ruff'),
        UvToolboxEnvironment(name='test', requirements='pytest'),
    ]
    settings = _make_settings(tmp_path, envs=envs)

    mocker.patch(
        'uv_toolbox.lock.generate_environment_lock',
        side_effect=['ruff==0.14.14\n', 'pytest==9.0.2\n'],
    )

    lock = generate_lock(settings=settings)

    assert isinstance(lock, UvToolboxLock)
    assert set(lock.environments) == {'fmt', 'test'}
    assert lock.environments['fmt'].requirements == 'ruff==0.14.14\n'
    assert lock.environments['test'].requirements == 'pytest==9.0.2\n'


def test_generate_lock_returns_environment_lock_instances(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    env = UvToolboxEnvironment(name='fmt', requirements='ruff')
    settings = _make_settings(tmp_path, envs=[env])
    mocker.patch('uv_toolbox.lock.generate_environment_lock', return_value=_COMPILED)

    lock = generate_lock(settings=settings)

    assert isinstance(lock.environments['fmt'], EnvironmentLock)
