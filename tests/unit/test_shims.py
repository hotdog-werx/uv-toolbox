from __future__ import annotations

import os
from typing import TYPE_CHECKING

from tests.utils import create_fake_venv
from uv_toolbox.settings import UvToolboxEnvironment, UvToolboxSettings
from uv_toolbox.shims import create_shims
from uv_toolbox.utils import _venv_bin_path

if TYPE_CHECKING:
    from pathlib import Path


def _make_settings(
    tmp_path: Path,
    *,
    envs: list[UvToolboxEnvironment],
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
                    'executables': env.executables,
                }
                for env in envs
            ],
        },
    )


def test_create_shims_creates_per_venv_shim_directories(tmp_path: Path) -> None:
    """Creates a `shims/` directory inside each venv that has listed executables."""
    env = UvToolboxEnvironment(
        name='env1',
        requirements='ruff',
        executables=['ruff'],
    )
    settings = _make_settings(tmp_path, envs=[env])
    venv_path = env.venv_path(settings=settings)
    create_fake_venv(venv_path, ['ruff'])

    shim_dirs = create_shims(settings=settings)

    assert len(shim_dirs) == 1
    assert shim_dirs[0] == venv_path / 'shims'
    assert shim_dirs[0].exists()
    assert shim_dirs[0].is_dir()


def test_create_shims_creates_shims_for_listed_executables(
    tmp_path: Path,
) -> None:
    """Only executables in the `executables` field get shims; unlisted executables in the venv are ignored."""
    env = UvToolboxEnvironment(
        name='env1',
        requirements='ruff',
        executables=['ruff', 'black'],
    )
    settings = _make_settings(tmp_path, envs=[env])
    venv_path = env.venv_path(settings=settings)
    create_fake_venv(venv_path, ['ruff', 'black', 'pytest'])

    shim_dirs = create_shims(settings=settings)
    shim_dir = shim_dirs[0]

    if os.name == 'nt':
        assert (shim_dir / 'ruff.bat').exists()
        assert (shim_dir / 'black.bat').exists()
        assert not (shim_dir / 'pytest.bat').exists()
    else:
        assert (shim_dir / 'ruff').exists()
        assert (shim_dir / 'black').exists()
        assert not (shim_dir / 'pytest').exists()
        assert os.access(shim_dir / 'ruff', os.X_OK)
        assert os.access(shim_dir / 'black', os.X_OK)


def test_create_shims_returns_multiple_shim_dirs_in_config_order(
    tmp_path: Path,
) -> None:
    """Returns one shim dir per env in the same order they appear in the config."""
    env1 = UvToolboxEnvironment(
        name='env1',
        requirements='ruff',
        executables=['ruff'],
    )
    env2 = UvToolboxEnvironment(
        name='env2',
        requirements='black',
        executables=['black'],
    )
    settings = _make_settings(tmp_path, envs=[env1, env2])

    create_fake_venv(env1.venv_path(settings=settings), ['ruff'])
    create_fake_venv(env2.venv_path(settings=settings), ['black'])

    shim_dirs = create_shims(settings=settings)

    assert len(shim_dirs) == 2
    assert shim_dirs[0] == env1.venv_path(settings=settings) / 'shims'
    assert shim_dirs[1] == env2.venv_path(settings=settings) / 'shims'


def test_create_shims_allows_duplicate_executables_across_envs(
    tmp_path: Path,
) -> None:
    """Both environments can expose the same executable; config order determines PATH precedence."""
    env1 = UvToolboxEnvironment(
        name='env1',
        requirements='ruff==0.1.0',
        executables=['ruff'],
    )
    env2 = UvToolboxEnvironment(
        name='env2',
        requirements='ruff==0.2.0',
        executables=['ruff'],
    )
    settings = _make_settings(tmp_path, envs=[env1, env2])

    create_fake_venv(env1.venv_path(settings=settings), ['ruff'])
    create_fake_venv(env2.venv_path(settings=settings), ['ruff'])

    shim_dirs = create_shims(settings=settings)

    assert len(shim_dirs) == 2
    if os.name == 'nt':
        assert (shim_dirs[0] / 'ruff.bat').exists()
        assert (shim_dirs[1] / 'ruff.bat').exists()
    else:
        assert (shim_dirs[0] / 'ruff').exists()
        assert (shim_dirs[1] / 'ruff').exists()


def test_create_shims_clears_old_shims(tmp_path: Path) -> None:
    """Re-running create_shims replaces the shim directory contents to match the current executables list."""
    env = UvToolboxEnvironment(
        name='env1',
        requirements='ruff',
        executables=['ruff', 'black'],
    )
    settings = _make_settings(tmp_path, envs=[env])
    venv_path = env.venv_path(settings=settings)
    create_fake_venv(venv_path, ['ruff', 'black', 'mypy'])

    shim_dirs = create_shims(settings=settings)
    shim_dir = shim_dirs[0]

    env.executables = ['ruff', 'mypy']
    settings = _make_settings(tmp_path, envs=[env])

    create_shims(settings=settings)

    if os.name == 'nt':
        assert not (shim_dir / 'black.bat').exists()
        assert (shim_dir / 'mypy.bat').exists()
        assert (shim_dir / 'ruff.bat').exists()
    else:
        assert not (shim_dir / 'black').exists()
        assert (shim_dir / 'mypy').exists()
        assert (shim_dir / 'ruff').exists()


def test_create_shims_skips_nonexistent_venvs(tmp_path: Path) -> None:
    """Returns an empty list and creates no directories when the venv does not exist yet."""
    env = UvToolboxEnvironment(
        name='env1',
        requirements='ruff',
        executables=['ruff'],
    )
    settings = _make_settings(tmp_path, envs=[env])

    shim_dirs = create_shims(settings=settings)

    assert len(shim_dirs) == 0


def test_create_shims_skips_envs_with_empty_executables(tmp_path: Path) -> None:
    """Returns an empty list for envs with no executables listed, even when the venv exists."""
    env = UvToolboxEnvironment(name='env1', requirements='ruff', executables=[])
    settings = _make_settings(tmp_path, envs=[env])
    venv_path = env.venv_path(settings=settings)
    create_fake_venv(venv_path, ['ruff', 'black'])

    shim_dirs = create_shims(settings=settings)

    assert len(shim_dirs) == 0


def test_create_shims_skips_missing_executables(tmp_path: Path) -> None:
    """Silently skips executables listed in config that are not present in the venv's bin directory."""
    env = UvToolboxEnvironment(
        name='env1',
        requirements='ruff',
        executables=['ruff', 'black', 'nonexistent'],
    )
    settings = _make_settings(tmp_path, envs=[env])
    venv_path = env.venv_path(settings=settings)
    create_fake_venv(venv_path, ['ruff', 'black'])

    shim_dirs = create_shims(settings=settings)
    shim_dir = shim_dirs[0]

    if os.name == 'nt':
        assert (shim_dir / 'ruff.bat').exists()
        assert (shim_dir / 'black.bat').exists()
        assert not (shim_dir / 'nonexistent.bat').exists()
    else:
        assert (shim_dir / 'ruff').exists()
        assert (shim_dir / 'black').exists()
        assert not (shim_dir / 'nonexistent').exists()


def test_unix_shim_contains_correct_paths(tmp_path: Path) -> None:
    """Unix shim sets VIRTUAL_ENV, invokes the binary via `uv run --no-project`, and starts with a bash shebang."""
    if os.name == 'nt':
        return

    env = UvToolboxEnvironment(
        name='env1',
        requirements='ruff',
        executables=['ruff'],
    )
    settings = _make_settings(tmp_path, envs=[env])
    venv_path = env.venv_path(settings=settings)
    create_fake_venv(venv_path, ['ruff'])

    shim_dirs = create_shims(settings=settings)
    shim_content = (shim_dirs[0] / 'ruff').read_text()

    assert f'VIRTUAL_ENV="{venv_path}"' in shim_content
    assert f'uv run --no-project --python "{venv_path}/bin/python"' in shim_content
    assert f'"{_venv_bin_path(venv_path)}/ruff"' in shim_content
    assert shim_content.startswith('#!/usr/bin/env bash')


def test_windows_shim_contains_correct_paths(tmp_path: Path) -> None:
    """Windows .bat shim sets VIRTUAL_ENV and invokes the binary via `uv run --no-project`."""
    if os.name != 'nt':
        return

    env = UvToolboxEnvironment(
        name='env1',
        requirements='ruff',
        executables=['ruff'],
    )
    settings = _make_settings(tmp_path, envs=[env])
    venv_path = env.venv_path(settings=settings)

    bin_path = venv_path / 'Scripts'
    bin_path.mkdir(parents=True, exist_ok=True)
    (bin_path / 'ruff.exe').write_text('fake')

    shim_dirs = create_shims(settings=settings)
    shim_content = (shim_dirs[0] / 'ruff.bat').read_text()

    assert f'set VIRTUAL_ENV={venv_path}' in shim_content
    assert 'uv run --no-project --python' in shim_content
    assert f'"{bin_path / "ruff.exe"}"' in shim_content or f'"{bin_path}\\ruff.exe"' in shim_content
    assert shim_content.startswith('@echo off')
