from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from uv_toolbox.errors import MissingConfigFileError
from uv_toolbox.settings import UvToolboxSettings

if TYPE_CHECKING:
    from pathlib import Path

    import typer


def _write_config(path: Path, venv_path: str = '.uv-toolbox') -> None:
    """Write a minimal config file."""
    path.write_text(
        '\n'.join(
            [
                f'venv_path: {venv_path}',
                'environments:',
                '  - name: env1',
                '    requirements: ruff',
                '',
            ],
        ),
    )


def test_config_discovery_from_subdirectory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Config file is found when running from a subdirectory."""
    # Create config in root
    config_path = tmp_path / 'uv-toolbox.yaml'
    _write_config(config_path)

    # Create and change to subdirectory
    subdir = tmp_path / 'src' / 'subdir'
    subdir.mkdir(parents=True)
    monkeypatch.chdir(subdir)

    # Settings should find config in parent directory
    ctx = cast('typer.Context', SimpleNamespace(obj={}))
    settings = UvToolboxSettings.from_context(ctx)

    assert settings.config_file == config_path
    assert settings.environments[0].name == 'env1'


def test_config_discovery_stops_at_git_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Config discovery stops at .git directory."""
    # Create a fake git root with config
    git_root = tmp_path / 'project'
    git_root.mkdir()
    (git_root / '.git').mkdir()
    config_path = git_root / 'uv-toolbox.yaml'
    _write_config(config_path)

    # Change to subdirectory
    subdir = git_root / 'src'
    subdir.mkdir()
    monkeypatch.chdir(subdir)

    # Should find config
    ctx = cast('typer.Context', SimpleNamespace(obj={}))
    settings = UvToolboxSettings.from_context(ctx)

    assert settings.config_file == config_path


def test_relative_venv_path_resolved_from_config_location(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Relative venv_path is resolved from config file location, not cwd."""
    # Create config in root
    config_path = tmp_path / 'uv-toolbox.yaml'
    _write_config(config_path, venv_path='.uv-toolbox')

    # Change to subdirectory
    subdir = tmp_path / 'src' / 'subdir'
    subdir.mkdir(parents=True)
    monkeypatch.chdir(subdir)

    # Load settings
    ctx = cast('typer.Context', SimpleNamespace(obj={}))
    settings = UvToolboxSettings.from_context(ctx)

    # Venv path should be relative to config file (tmp_path), not cwd (subdir)
    # Path should be content-addressed (hash-based), not name-based
    venv_path = settings.environments[0].venv_path(settings)
    assert venv_path.parent == tmp_path / '.uv-toolbox'
    assert len(venv_path.name) == 12  # Hash is 12 characters


def test_absolute_venv_path_used_as_is(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Absolute venv_path is used as-is without modification."""
    venv_root = tmp_path / 'custom-venvs'
    config_path = tmp_path / 'uv-toolbox.yaml'
    _write_config(config_path, venv_path=str(venv_root))

    monkeypatch.chdir(tmp_path)
    ctx = cast('typer.Context', SimpleNamespace(obj={}))
    settings = UvToolboxSettings.from_context(ctx)

    # Absolute path should be used as base, with content-addressed subdirectory
    venv_path = settings.environments[0].venv_path(settings)
    assert venv_path.parent == venv_root
    assert len(venv_path.name) == 12  # Hash is 12 characters


def test_content_hashing_deduplicates_identical_requirements(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Environments with identical requirements share the same venv via content hashing."""
    config_path = tmp_path / 'uv-toolbox.yaml'
    config_path.write_text(
        """\
venv_path: .uv-toolbox
environments:
  - name: env1
    requirements: ruff
  - name: env2
    requirements: ruff
""",
    )

    monkeypatch.chdir(tmp_path)
    ctx = cast('typer.Context', SimpleNamespace(obj={}))
    settings = UvToolboxSettings.from_context(ctx)

    # Both environments should use the same venv path (same hash)
    venv_path_1 = settings.environments[0].venv_path(settings)
    venv_path_2 = settings.environments[1].venv_path(settings)
    assert venv_path_1 == venv_path_2


def test_content_hashing_differs_for_different_requirements(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Environments with different requirements get different venv paths."""
    config_path = tmp_path / 'uv-toolbox.yaml'
    config_path.write_text(
        """\
venv_path: .uv-toolbox
environments:
  - name: env1
    requirements: ruff
  - name: env2
    requirements: black
""",
    )

    monkeypatch.chdir(tmp_path)
    ctx = cast('typer.Context', SimpleNamespace(obj={}))
    settings = UvToolboxSettings.from_context(ctx)

    # Different environments should use different venv paths (different hashes)
    venv_path_1 = settings.environments[0].venv_path(settings)
    venv_path_2 = settings.environments[1].venv_path(settings)
    assert venv_path_1 != venv_path_2


def test_pyproject_toml_discovery_from_subdirectory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """pyproject.toml with [tool.uv-toolbox] is found from subdirectory."""
    pyproject_path = tmp_path / 'pyproject.toml'
    pyproject_path.write_text(
        """\
[tool.uv-toolbox]
venv_path = ".uv-toolbox"
[[tool.uv-toolbox.environments]]
name = "env1"
requirements = "ruff"
""",
    )

    # Change to subdirectory
    subdir = tmp_path / 'tests'
    subdir.mkdir()
    monkeypatch.chdir(subdir)

    # Should find pyproject.toml
    ctx = cast('typer.Context', SimpleNamespace(obj={}))
    settings = UvToolboxSettings.from_context(ctx)

    assert settings.config_file == pyproject_path
    assert settings.environments[0].name == 'env1'


def test_pyproject_toml_as_directory_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """pyproject.toml as a directory (not file) is skipped."""
    # Create pyproject.toml as a directory (weird but possible)
    (tmp_path / 'pyproject.toml').mkdir()

    monkeypatch.chdir(tmp_path)
    ctx = cast('typer.Context', SimpleNamespace(obj={}))

    # Should raise error since no valid config found
    with pytest.raises(MissingConfigFileError):
        UvToolboxSettings.from_context(ctx)


def test_config_search_stops_at_git_without_finding_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Config search stops at .git boundary even without finding config."""
    # Create a git root but NO config file
    git_root = tmp_path / 'project'
    git_root.mkdir()
    (git_root / '.git').mkdir()

    # Create subdirectory
    subdir = git_root / 'src' / 'deep'
    subdir.mkdir(parents=True)
    monkeypatch.chdir(subdir)

    ctx = cast('typer.Context', SimpleNamespace(obj={}))

    # Should raise error and stop at git root (not search beyond)
    with pytest.raises(MissingConfigFileError):
        UvToolboxSettings.from_context(ctx)
