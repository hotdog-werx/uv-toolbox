"""Shim generation for UV Toolbox.

Creates wrapper scripts for explicitly listed executables in virtual environments.
"""

from __future__ import annotations

import os
import stat
import typing

from uv_toolbox.utils import _venv_bin_path

if typing.TYPE_CHECKING:
    from pathlib import Path

    from uv_toolbox.settings import UvToolboxEnvironment, UvToolboxSettings


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
    script = f"""#!/usr/bin/env bash
export VIRTUAL_ENV="{venv_path}"
exec "{target_path}" "$@"
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
    script = f"""@echo off
set VIRTUAL_ENV={venv_path}
"{target_path}" %*
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

    # Skip if no executables listed
    if not env.executables:
        return None

    # Shim directory is inside the venv
    shim_dir = venv_path / 'shims'
    shim_dir.mkdir(parents=True, exist_ok=True)

    # Clear existing shims in this venv
    for shim_file in shim_dir.iterdir():
        if shim_file.is_file():
            shim_file.unlink()

    # Create shims for this environment's executables
    bin_path = _venv_bin_path(venv_path)
    for exe_name in env.executables:
        _create_shim_for_executable(exe_name, venv_path, bin_path, shim_dir)

    return shim_dir


def create_shims(settings: UvToolboxSettings) -> list[Path]:
    """Create per-venv shim scripts for explicitly listed executables.

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
