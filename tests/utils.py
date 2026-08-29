"""Shared test utilities."""

from __future__ import annotations

import os
import stat
from json import dumps
from typing import TYPE_CHECKING

from uv_toolbox.utils import _venv_bin_path

if TYPE_CHECKING:
    from pathlib import Path


def create_fake_venv(venv_path: Path, tools: list[str]) -> None:
    """Create a fake venv with specified tool executables.

    Args:
        venv_path: Path to the virtual environment.
        tools: List of tool names to create as executables.
    """
    bin_path = _venv_bin_path(venv_path)
    bin_path.mkdir(parents=True, exist_ok=True)

    for tool in tools:
        if os.name == 'nt':
            # On Windows, create .exe files
            tool_path = bin_path / f'{tool}.exe'
            tool_path.write_text('fake executable')
        else:
            # On Unix, create executable files
            tool_path = bin_path / tool
            tool_path.write_text('#!/usr/bin/env python\nprint("fake tool")\n')
            tool_path.chmod(
                tool_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH,
            )


def create_fake_distribution(
    venv_path: Path,
    name: str,
    scripts: list[str],
    *,
    direct_url: dict[str, object] | None = None,
) -> None:
    """Create enough installed-distribution metadata for shim discovery tests."""
    if os.name == 'nt':
        site_packages = venv_path / 'Lib' / 'site-packages'
    else:
        site_packages = venv_path / 'lib' / 'python3.13' / 'site-packages'
    dist_info = site_packages / f'{name.replace("-", "_")}-1.0.0.dist-info'
    dist_info.mkdir(parents=True, exist_ok=True)
    (dist_info / 'METADATA').write_text(f'Metadata-Version: 2.1\nName: {name}\nVersion: 1.0.0\n')
    (dist_info / 'entry_points.txt').write_text(
        '[console_scripts]\n' + ''.join(f'{script} = example:main\n' for script in scripts),
    )
    if direct_url is not None:
        (dist_info / 'direct_url.json').write_text(dumps(direct_url))
