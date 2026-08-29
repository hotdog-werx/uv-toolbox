"""End-to-end tests for the mise environment plugin."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import venv
from pathlib import Path
from textwrap import dedent

import pytest

from uv_toolbox.settings import UvToolboxSettings


def _run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    """Run a command and include captured output in any failure."""
    result = subprocess.run(  # noqa: S603
        command,
        cwd=cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f'Command failed: {command!r}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}'
    return result


def _write_consumer_config(project_dir: Path, toolbox_config: Path) -> Path:
    """Write a mise config that activates the linked plugin."""
    mise_config = project_dir / 'mise.toml'
    mise_config.write_text(
        dedent(f"""
        [env]
        _.uv-toolbox = {{ tools = true, config = {json.dumps(str(toolbox_config))} }}
        """).strip()
        + '\n',
    )
    return mise_config


def _write_toolbox_config(project_dir: Path) -> Path:
    """Write a toolbox config with one unique executable."""
    toolbox_config = project_dir / 'uv-toolbox.yaml'
    toolbox_config.write_text(
        dedent(f"""
        venv_path: {json.dumps(str(project_dir / '.uv-toolbox'))}
        environments:
          - name: plugin-test
            requirements: mise-plugin-fixture
            executables_override: [mise-plugin-fixture]
        """).strip()
        + '\n',
    )
    return toolbox_config


def _add_fixture_executable(venv_path: Path) -> None:
    """Add an executable that can only be reached through the plugin's shim path."""
    venv.EnvBuilder(with_pip=False).create(venv_path)
    executable = venv_path / 'bin' / 'mise-plugin-fixture'
    executable.write_text('#!/usr/bin/env sh\nprintf "mise-plugin-local-source\\n"\n')
    executable.chmod(
        executable.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH,
    )


@pytest.mark.skipif(os.name == 'nt', reason='The mise plugin currently uses POSIX shell quoting')
def test_mise_plugin_exposes_toolbox_shims_from_local_source(
    tmp_path: Path,
    pytestconfig: pytest.Config,
) -> None:
    """A locally linked plugin adds uv-toolbox shims to mise's command PATH."""
    mise = shutil.which('mise')
    assert mise is not None, 'mise must be installed to run the plugin E2E test'
    repo_root = pytestconfig.rootpath

    project_dir = tmp_path / 'consumer'
    project_dir.mkdir()
    toolbox_config = _write_toolbox_config(project_dir)
    mise_config = _write_consumer_config(project_dir, toolbox_config)

    executable_dir = str(Path(sys.executable).parent)
    env = os.environ.copy()
    env.update(
        {
            'MISE_CACHE_DIR': str(tmp_path / 'mise-cache'),
            'MISE_CONFIG_DIR': str(tmp_path / 'mise-config'),
            'MISE_DATA_DIR': str(tmp_path / 'mise-data'),
            'MISE_STATE_DIR': str(tmp_path / 'mise-state'),
            'MISE_YES': '1',
            'PATH': os.pathsep.join([executable_dir, env['PATH']]),
            'UV_CACHE_DIR': str(tmp_path / 'uv-cache'),
        },
    )

    uv_toolbox = Path(executable_dir) / 'uv-toolbox'
    assert uv_toolbox.is_file(), f'uv-toolbox executable not found at {uv_toolbox}'

    settings = UvToolboxSettings(config_file=toolbox_config)
    _add_fixture_executable(
        settings.environments[0].venv_path(settings=settings),
    )

    _run([mise, 'trust', '--yes', str(mise_config)], cwd=project_dir, env=env)
    _run(
        [mise, 'plugins', 'link', '--force', 'uv-toolbox', str(repo_root)],
        cwd=project_dir,
        env=env,
    )

    installed_plugin = tmp_path / 'mise-data' / 'plugins' / 'uv-toolbox'
    assert installed_plugin.is_symlink()
    assert installed_plugin.resolve() == repo_root

    result = _run(
        [mise, 'exec', '--', 'sh', '-c', 'command -v mise-plugin-fixture'],
        cwd=project_dir,
        env=env,
    )
    resolved_executable = Path(result.stdout.strip())
    assert resolved_executable.name == 'mise-plugin-fixture'
    assert resolved_executable.parent.name == 'shims'
    assert resolved_executable.is_file()
