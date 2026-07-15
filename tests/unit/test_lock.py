from __future__ import annotations

from typing import TYPE_CHECKING

from uv_toolbox.lock import generate_environment_lock, generate_lock
from uv_toolbox.lockfile import EnvironmentLock, UvToolboxLock
from uv_toolbox.settings import UvToolboxEnvironment, UvToolboxSettings

if TYPE_CHECKING:
    from pathlib import Path

    import pytest
    from pytest_mock import MockerFixture

_COMPILED = 'ruff==0.14.14 \\\n    --hash=sha256:aaaa'


def _make_settings(
    tmp_path: Path,
    *,
    envs: list[UvToolboxEnvironment],
) -> UvToolboxSettings:
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
    """Compiles a lockfile directly from a requirements_file path via `uv pip compile --generate-hashes`."""
    req_file = tmp_path / 'requirements.txt'
    req_file.write_text('ruff\n')
    env = UvToolboxEnvironment(name='fmt', requirements_file=req_file)
    settings = _make_settings(tmp_path, envs=[env])

    temp_dir = tmp_path / 'compile'
    temp_dir.mkdir()
    temporary_directory = mocker.patch(
        'uv_toolbox.lock.tempfile.TemporaryDirectory',
    )
    temporary_directory.return_value.__enter__.return_value = str(temp_dir)
    output_path = temp_dir / 'compiled-requirements.txt'
    output_path.write_text(_COMPILED)
    run_mock = mocker.patch('uv_toolbox.lock.run_checked')
    result = generate_environment_lock(env=env, settings=settings)

    run_mock.assert_called_once_with(
        args=[
            'uv',
            'pip',
            'compile',
            '--generate-hashes',
            '--universal',
            '--no-header',
            '--no-annotate',
            '-o',
            str(output_path),
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
    """Writes inline requirements to a named temp file, compiles it, then cleans up the temp directory."""
    env = UvToolboxEnvironment(name='fmt', requirements='ruff\n')
    settings = _make_settings(tmp_path, envs=[env])

    temp_dir = tmp_path / 'tmp'
    temp_dir.mkdir()
    temporary_directory = mocker.patch(
        'uv_toolbox.lock.tempfile.TemporaryDirectory',
    )
    temporary_directory.return_value.__enter__.return_value = str(temp_dir)
    output_path = temp_dir / 'compiled-requirements.txt'
    output_path.write_text(_COMPILED)
    run_mock = mocker.patch('uv_toolbox.lock.run_checked')

    result = generate_environment_lock(env=env, settings=settings)

    temp_req_file = temp_dir / 'requirements_fmt.txt'
    assert temp_req_file.read_text() == 'ruff\n'
    run_mock.assert_called_once_with(
        args=[
            'uv',
            'pip',
            'compile',
            '--generate-hashes',
            '--universal',
            '--no-header',
            '--no-annotate',
            '-o',
            str(output_path),
            str(temp_req_file),
        ],
        capture_stdout=True,
        capture_stderr=False,
        show_command=False,
    )
    assert result == _COMPILED


def test_generate_environment_lock_uses_declared_requirements_when_lock_is_injected(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    """Refreshing a lock resolves source requirements rather than existing locked content."""
    env = UvToolboxEnvironment(name='fmt', requirements='ruff>=0.14\n')
    env._resolved_requirements = 'ruff==0.14.14\n'
    settings = _make_settings(tmp_path, envs=[env])
    temp_dir = tmp_path / 'compile'
    temp_dir.mkdir()
    temporary_directory = mocker.patch(
        'uv_toolbox.lock.tempfile.TemporaryDirectory',
    )
    temporary_directory.return_value.__enter__.return_value = str(temp_dir)
    (temp_dir / 'compiled-requirements.txt').write_text(_COMPILED)
    mocker.patch('uv_toolbox.lock.run_checked')

    generate_environment_lock(env=env, settings=settings)

    assert (temp_dir / 'requirements_fmt.txt').read_text() == 'ruff>=0.14\n'


def test_generate_environment_lock_preserves_dash_file(
    mocker: MockerFixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lock generation never changes a caller-owned file named `-`."""
    monkeypatch.chdir(tmp_path)
    dash_file = tmp_path / '-'
    dash_file.write_text('keep me')
    env = UvToolboxEnvironment(name='fmt', requirements='ruff\n')
    settings = _make_settings(tmp_path, envs=[env])
    temp_dir = tmp_path / 'compile'
    temp_dir.mkdir()
    temporary_directory = mocker.patch(
        'uv_toolbox.lock.tempfile.TemporaryDirectory',
    )
    temporary_directory.return_value.__enter__.return_value = str(temp_dir)
    (temp_dir / 'compiled-requirements.txt').write_text(_COMPILED)
    mocker.patch('uv_toolbox.lock.run_checked')

    generate_environment_lock(env=env, settings=settings)

    assert dash_file.read_text() == 'keep me'


def test_generate_environment_lock_does_not_pass_virtual_env(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    """Compiling a lockfile does not set extra_env, since `uv pip compile` should not target a specific venv."""
    env = UvToolboxEnvironment(name='fmt', requirements='ruff\n')
    settings = _make_settings(tmp_path, envs=[env])
    temp_dir = tmp_path / 'compile'
    temp_dir.mkdir()
    temporary_directory = mocker.patch(
        'uv_toolbox.lock.tempfile.TemporaryDirectory',
    )
    temporary_directory.return_value.__enter__.return_value = str(temp_dir)
    (temp_dir / 'compiled-requirements.txt').write_text(_COMPILED)
    run_mock = mocker.patch('uv_toolbox.lock.run_checked')

    generate_environment_lock(env=env, settings=settings)

    call_kwargs = run_mock.call_args.kwargs
    assert 'extra_env' not in call_kwargs


def test_generate_lock_covers_all_environments(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    """generate_lock produces a UvToolboxLock with an entry for every configured environment."""
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
    """generate_lock wraps each environment's compiled requirements in an EnvironmentLock instance."""
    env = UvToolboxEnvironment(name='fmt', requirements='ruff')
    settings = _make_settings(tmp_path, envs=[env])
    mocker.patch(
        'uv_toolbox.lock.generate_environment_lock',
        return_value=_COMPILED,
    )

    lock = generate_lock(settings=settings)

    assert isinstance(lock.environments['fmt'], EnvironmentLock)
