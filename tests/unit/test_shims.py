from __future__ import annotations

import os
from typing import TYPE_CHECKING

from tests.utils import create_fake_distribution, create_fake_venv
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
                    'executables_override': env.executables_override,
                    'omit_executables': env.omit_executables,
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
        executables_override=['ruff'],
    )
    settings = _make_settings(tmp_path, envs=[env])
    venv_path = env.venv_path(settings=settings)
    create_fake_venv(venv_path, ['ruff'])

    shim_dirs = create_shims(settings=settings)

    assert len(shim_dirs) == 1
    assert shim_dirs[0] == venv_path / 'shims'
    assert shim_dirs[0].exists()
    assert shim_dirs[0].is_dir()


def test_create_shims_discovers_scripts_from_first_order_packages(tmp_path: Path) -> None:
    """Automatic mode exposes first-order package scripts but not transitive package scripts."""
    env = UvToolboxEnvironment(name='env1', requirements='root-tool>=1')
    settings = _make_settings(tmp_path, envs=[env])
    venv_path = env.venv_path(settings=settings)
    create_fake_venv(venv_path, ['root', 'transitive'])
    create_fake_distribution(venv_path, 'root-tool', ['root'])
    create_fake_distribution(venv_path, 'transitive-tool', ['transitive'])

    shim_dirs = create_shims(settings=settings)

    assert len(shim_dirs) == 1
    suffix = '.bat' if os.name == 'nt' else ''
    assert (shim_dirs[0] / f'root{suffix}').exists()
    assert not (shim_dirs[0] / f'transitive{suffix}').exists()


def test_create_shims_matches_bare_vcs_requirements_to_direct_url_metadata(
    tmp_path: Path,
) -> None:
    """Bare VCS requirements are matched using installed PEP 610 metadata."""
    requirement = 'git+https://github.com/example/tools.git@v1#subdirectory=packages/workspace'
    env = UvToolboxEnvironment(name='env1', requirements=requirement)
    settings = _make_settings(tmp_path, envs=[env])
    venv_path = env.venv_path(settings=settings)
    create_fake_venv(venv_path, ['workspace-link'])
    create_fake_distribution(
        venv_path,
        'example-workspace',
        ['workspace-link'],
        direct_url={
            'url': 'https://github.com/example/tools.git',
            'subdirectory': 'packages/workspace',
            'vcs_info': {'vcs': 'git', 'commit_id': 'abc123'},
        },
    )

    shim_dirs = create_shims(settings=settings)

    suffix = '.bat' if os.name == 'nt' else ''
    assert (shim_dirs[0] / f'workspace-link{suffix}').exists()


def test_create_shims_omits_selected_auto_discovered_executables(tmp_path: Path) -> None:
    """omit_executables subtracts selected scripts from automatic discovery."""
    env = UvToolboxEnvironment(
        name='env1',
        requirements='root-tool',
        omit_executables=['root-admin'],
    )
    settings = _make_settings(tmp_path, envs=[env])
    venv_path = env.venv_path(settings=settings)
    create_fake_venv(venv_path, ['root', 'root-admin'])
    create_fake_distribution(venv_path, 'root-tool', ['root', 'root-admin'])

    shim_dirs = create_shims(settings=settings)

    suffix = '.bat' if os.name == 'nt' else ''
    assert (shim_dirs[0] / f'root{suffix}').exists()
    assert not (shim_dirs[0] / f'root-admin{suffix}').exists()


def test_create_shims_creates_shims_for_listed_executables(
    tmp_path: Path,
) -> None:
    """Only executables in the `executables_override` field get shims; unlisted executables in the venv are ignored."""
    env = UvToolboxEnvironment(
        name='env1',
        requirements='ruff',
        executables_override=['ruff', 'black'],
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
        executables_override=['ruff'],
    )
    env2 = UvToolboxEnvironment(
        name='env2',
        requirements='black',
        executables_override=['black'],
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
        executables_override=['ruff'],
    )
    env2 = UvToolboxEnvironment(
        name='env2',
        requirements='ruff==0.2.0',
        executables_override=['ruff'],
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
    """Re-running create_shims replaces the shim directory contents to match the current executable override."""
    env = UvToolboxEnvironment(
        name='env1',
        requirements='ruff',
        executables_override=['ruff', 'black'],
    )
    settings = _make_settings(tmp_path, envs=[env])
    venv_path = env.venv_path(settings=settings)
    create_fake_venv(venv_path, ['ruff', 'black', 'mypy'])

    shim_dirs = create_shims(settings=settings)
    shim_dir = shim_dirs[0]

    env.executables_override = ['ruff', 'mypy']
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
        executables_override=['ruff'],
    )
    settings = _make_settings(tmp_path, envs=[env])

    shim_dirs = create_shims(settings=settings)

    assert len(shim_dirs) == 0


def test_create_shims_skips_envs_with_empty_executables(tmp_path: Path) -> None:
    """An empty executable override exposes nothing, even when the venv has tools."""
    env = UvToolboxEnvironment(name='env1', requirements='ruff', executables_override=[])
    settings = _make_settings(tmp_path, envs=[env])
    venv_path = env.venv_path(settings=settings)
    create_fake_venv(venv_path, ['ruff', 'black'])

    shim_dirs = create_shims(settings=settings)

    assert len(shim_dirs) == 0


def test_empty_override_clears_existing_auto_discovered_shims(tmp_path: Path) -> None:
    """Switching to an empty override removes shims created by automatic mode."""
    env = UvToolboxEnvironment(name='env1', requirements='root-tool')
    settings = _make_settings(tmp_path, envs=[env])
    venv_path = env.venv_path(settings=settings)
    create_fake_venv(venv_path, ['root'])
    create_fake_distribution(venv_path, 'root-tool', ['root'])
    shim_dir = create_shims(settings=settings)[0]

    env.executables_override = []
    settings = _make_settings(tmp_path, envs=[env])
    shim_dirs = create_shims(settings=settings)

    assert shim_dirs == []
    assert list(shim_dir.iterdir()) == []


def test_create_shims_skips_missing_executables(tmp_path: Path) -> None:
    """Silently skip overridden executable names absent from the venv bin directory."""
    env = UvToolboxEnvironment(
        name='env1',
        requirements='ruff',
        executables_override=['ruff', 'black', 'nonexistent'],
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
        executables_override=['ruff'],
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
        executables_override=['ruff'],
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
