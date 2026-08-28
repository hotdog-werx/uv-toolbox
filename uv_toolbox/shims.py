"""Shim generation for UV Toolbox."""

from __future__ import annotations

import os
import stat
import typing
from importlib.metadata import Distribution, distributions
from json import JSONDecodeError, loads
from urllib.parse import parse_qs, urlsplit, urlunsplit

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from uv_toolbox.utils import _venv_bin_path

if typing.TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from uv_toolbox.settings import UvToolboxEnvironment, UvToolboxSettings


def _site_packages_paths(venv_path: Path) -> list[Path]:
    """Return site-packages directories belonging to a virtual environment."""
    if os.name == 'nt':
        candidates = [venv_path / 'Lib' / 'site-packages']
    else:
        candidates = sorted((venv_path / 'lib').glob('python*/site-packages'))
    return [path for path in candidates if path.is_dir()]


def _logical_requirement_lines(content: str) -> Iterator[str]:
    """Yield logical, non-comment lines from requirements-file content."""
    pending = ''
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        pending += line
        if pending.endswith('\\'):
            pending = pending[:-1].rstrip() + ' '
            continue
        yield pending
        pending = ''
    if pending:
        yield pending


def _requirement_specs(env: UvToolboxEnvironment) -> Iterator[str]:
    """Yield the environment's first-order requirement specifications."""
    content = env.requirements_file.read_text() if env.requirements_file is not None else env.requirements or ''
    yield from _logical_requirement_lines(content)


def _bare_vcs_key(spec: str) -> tuple[str, str | None] | None:
    """Return a comparable URL/subdirectory key for a bare VCS requirement."""
    editable_prefixes = ('-e ', '--editable ')
    for prefix in editable_prefixes:
        if spec.startswith(prefix):
            spec = spec.removeprefix(prefix).strip()
            break
    if not spec.startswith('git+'):
        return None

    parsed = urlsplit(spec.removeprefix('git+'))
    path = parsed.path
    git_suffix = path.rfind('.git@')
    if git_suffix >= 0:
        path = path[: git_suffix + len('.git')]
    url = urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, '')).rstrip('/')
    subdirectory = parse_qs(parsed.fragment).get('subdirectory', [None])[0]
    return url, subdirectory


def _distribution_direct_url(distribution: Distribution) -> tuple[str, str | None] | None:
    """Read a distribution's PEP 610 direct URL metadata."""
    content = distribution.read_text('direct_url.json')
    if content is None:
        return None
    try:
        data = loads(content)
    except JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    url = data.get('url')
    if not isinstance(url, str):
        return None
    subdirectory = data.get('subdirectory')
    return url.rstrip('/'), subdirectory if isinstance(subdirectory, str) else None


def _first_order_package_names(env: UvToolboxEnvironment) -> tuple[set[str], set[tuple[str, str | None]]]:
    """Return named and bare-VCS first-order requirements."""
    names: set[str] = set()
    vcs_keys: set[tuple[str, str | None]] = set()
    for spec in _requirement_specs(env):
        if spec.startswith(('-r ', '--requirement ', '-c ', '--constraint ', '--')):
            continue
        try:
            names.add(canonicalize_name(Requirement(spec).name))
        except InvalidRequirement:
            vcs_key = _bare_vcs_key(spec)
            if vcs_key is not None:
                vcs_keys.add(vcs_key)
    return names, vcs_keys


def _auto_executables(env: UvToolboxEnvironment, venv_path: Path) -> list[str]:
    """Discover console scripts declared by first-order installed packages."""
    package_names, vcs_keys = _first_order_package_names(env)
    omitted = set(env.omit_executables)
    executable_names: set[str] = set()
    for distribution in distributions(path=[str(path) for path in _site_packages_paths(venv_path)]):
        distribution_name = distribution.metadata['Name']
        is_first_order = (
            bool(distribution_name and canonicalize_name(distribution_name) in package_names)
            or _distribution_direct_url(distribution) in vcs_keys
        )
        if not is_first_order:
            continue
        executable_names.update(
            entry_point.name
            for entry_point in distribution.entry_points
            if entry_point.group == 'console_scripts' and entry_point.name not in omitted
        )
    return sorted(executable_names)


def _executables(env: UvToolboxEnvironment, venv_path: Path) -> list[str]:
    """Return the exact executable names to expose for an environment."""
    if env.executables_override is not None:
        return env.executables_override
    return _auto_executables(env, venv_path)


def _create_unix_shim(
    shim_path: Path,
    target_path: Path,
    venv_path: Path,
) -> None:
    """Create a Unix shell script shim.

    Args:
        shim_path: Path where the shim script should be created.
        target_path: Path to the actual executable.
        venv_path: Path to the virtual environment.
    """
    python_exe = venv_path / 'bin' / 'python'
    script = f"""#!/usr/bin/env bash
VIRTUAL_ENV="{venv_path}"
exec uv run --no-project --python "{python_exe}" -- "{target_path}" "$@"
"""
    shim_path.write_text(script)
    # Make executable
    shim_path.chmod(
        shim_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH,
    )


def _create_windows_shim(
    shim_path: Path,
    target_path: Path,
    venv_path: Path,
) -> None:
    """Create a Windows batch script shim.

    Args:
        shim_path: Path where the shim script should be created (without extension).
        target_path: Path to the actual executable.
        venv_path: Path to the virtual environment.
    """
    # Create .bat file
    bat_path = shim_path.with_suffix('.bat')
    python_exe = venv_path / 'Scripts' / 'python.exe'
    script = f"""@echo off
set VIRTUAL_ENV={venv_path}
uv run --no-project --python "{python_exe}" -- "{target_path}" %*
"""
    bat_path.write_text(script)


def _find_windows_executable(bin_path: Path, exe_name: str) -> Path | None:
    """Find an executable in a Windows venv Scripts directory.

    Args:
        bin_path: Path to the venv Scripts directory.
        exe_name: Name of the executable to find.

    Returns:
        Path to the executable if found, None otherwise.
    """
    for ext in ['.exe', '.bat', '.cmd']:
        exe_path = bin_path / f'{exe_name}{ext}'
        if exe_path.exists():
            return exe_path
    return None


def _find_unix_executable(bin_path: Path, exe_name: str) -> Path | None:
    """Find an executable in a Unix venv bin directory.

    Args:
        bin_path: Path to the venv bin directory.
        exe_name: Name of the executable to find.

    Returns:
        Path to the executable if found, None otherwise.
    """
    exe_path = bin_path / exe_name
    if exe_path.exists() and os.access(exe_path, os.X_OK):
        return exe_path
    return None


def _find_executable(bin_path: Path, exe_name: str) -> Path | None:
    """Find an executable in a venv bin directory.

    Args:
        bin_path: Path to the venv bin directory.
        exe_name: Name of the executable to find.

    Returns:
        Path to the executable if found, None otherwise.
    """
    if os.name == 'nt':
        return _find_windows_executable(bin_path, exe_name)
    return _find_unix_executable(bin_path, exe_name)


def _create_shim_for_executable(
    exe_name: str,
    venv_path: Path,
    bin_path: Path,
    shim_dir: Path,
) -> None:
    """Create a shim for a single executable.

    Args:
        exe_name: Name of the executable.
        venv_path: Path to the virtual environment.
        bin_path: Path to the venv bin directory.
        shim_dir: Directory where the shim should be created.
    """
    # Find the executable in the venv
    target_path = _find_executable(bin_path, exe_name)
    if target_path is None:
        # Executable not found - skip silently
        return

    # Create shim path
    shim_path = shim_dir / exe_name

    # Create platform-specific shim
    if os.name == 'nt':
        _create_windows_shim(shim_path, target_path, venv_path)
    else:
        _create_unix_shim(shim_path, target_path, venv_path)


def _create_shims_for_environment(
    env: UvToolboxEnvironment,
    settings: UvToolboxSettings,
) -> Path | None:
    """Create shim directory for a single environment.

    Args:
        env: The environment to create shims for.
        settings: The UV toolbox settings.

    Returns:
        Path to the shim directory if created, None otherwise.
    """
    venv_path = env.venv_path(settings=settings)

    # Skip if venv doesn't exist yet
    if not venv_path.exists():
        return None

    executable_names = _executables(env, venv_path)
    shim_dir = venv_path / 'shims'
    if not executable_names and not shim_dir.exists():
        return None
    shim_dir.mkdir(parents=True, exist_ok=True)

    # Clear existing shims in this venv
    for shim_file in shim_dir.iterdir():
        if shim_file.is_file():
            shim_file.unlink()

    # Create shims for this environment's executables
    bin_path = _venv_bin_path(venv_path)
    for exe_name in executable_names:
        _create_shim_for_executable(exe_name, venv_path, bin_path, shim_dir)

    return shim_dir if executable_names else None


def create_shims(settings: UvToolboxSettings) -> list[Path]:
    """Create per-venv shim scripts for configured environments.

    Args:
        settings: The UV toolbox settings.

    Returns:
        List of shim directories in config order (for PATH precedence).
    """
    shim_dirs: list[Path] = []

    for env in settings.environments:
        shim_dir = _create_shims_for_environment(env, settings)
        if shim_dir is not None:
            shim_dirs.append(shim_dir)

    return shim_dirs
