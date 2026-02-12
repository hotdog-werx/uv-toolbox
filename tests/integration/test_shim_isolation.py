from __future__ import annotations

import os
import subprocess
from textwrap import dedent
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


# ruff: noqa: S603, S607


def test_integration_shim_runs_in_correct_venv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that shims execute commands in the correct isolated venv.

    This test verifies that when running a shim, it uses the venv's Python
    interpreter and doesn't leak into other environments or the system Python.
    """
    # Create the project directory structure
    project_dir = tmp_path / 'myproject'
    project_dir.mkdir()

    # Create uv-toolbox.yaml with a simple requirement
    uvtb_config = project_dir / 'uv-toolbox.yaml'
    uvtb_config.write_text(
        dedent("""
        venv_path: .uv-toolbox
        environments:
          - name: tools
            requirements: |
              ruff
            executables: [python]
    """).strip()
        + '\n',
    )

    # Change to the project directory
    monkeypatch.chdir(project_dir)

    # Step 1: Run uvtb install to create the venv
    install_result = subprocess.run(
        ['uvtb', 'install'],
        check=False,
        capture_output=True,
        text=True,
    )

    assert install_result.returncode == 0, (
        f'uvtb install failed:\nstdout: {install_result.stdout}\nstderr: {install_result.stderr}'
    )

    # Step 2: Run uvtb shim to create shims
    shim_result = subprocess.run(
        ['uvtb', 'shim'],
        check=False,
        capture_output=True,
        text=True,
    )

    assert shim_result.returncode == 0, (
        f'uvtb shim failed:\nstdout: {shim_result.stdout}\nstderr: {shim_result.stderr}'
    )

    # Extract the shim directory from the output
    # Output format: export PATH="/path/to/shims:$PATH" (Unix) or export PATH="C:\path\to\shims;$PATH" (Windows)

    shim_path_line = shim_result.stdout.strip()
    assert shim_path_line.startswith('export PATH="')

    # Parse out the shim directory (handle both : and ; separators)
    path_value = shim_path_line.split('"')[1]
    shim_dir = path_value.split(os.pathsep)[0]

    # Step 3: Execute the python shim and verify it's using the venv's Python
    # On Windows, shims are .bat files
    python_shim = project_dir / shim_dir / 'python.bat' if os.name == 'nt' else project_dir / shim_dir / 'python'
    assert python_shim.exists(), f'Python shim not found at {python_shim}'

    # Run a command through the shim to check sys.executable and import ruff
    check_result = subprocess.run(
        [
            str(python_shim),
            '-c',
            'import sys; print(sys.executable); import ruff; print("ruff imported successfully")',
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert check_result.returncode == 0, (
        f'Shim execution failed:\nstdout: {check_result.stdout}\nstderr: {check_result.stderr}'
    )

    # Verify the Python executable is from the venv
    python_exe = check_result.stdout.split('\n')[0]
    assert '.uv-toolbox' in python_exe, f'Expected Python from .uv-toolbox venv, got: {python_exe}'

    # Verify ruff was successfully imported
    assert 'ruff imported successfully' in check_result.stdout, (
        f'Expected to import ruff, but got:\n{check_result.stdout}'
    )

    # Step 4: Verify isolation - try to import a package NOT in the venv
    # pytest should NOT be available since it's not in requirements
    pytest_check = subprocess.run(
        [str(python_shim), '-c', 'import pytest'],
        check=False,
        capture_output=True,
        text=True,
    )

    assert pytest_check.returncode != 0, (
        f'pytest should NOT be available in the venv!\nstdout: {pytest_check.stdout}\nstderr: {pytest_check.stderr}'
    )
    assert 'ModuleNotFoundError' in pytest_check.stderr or 'No module named' in pytest_check.stderr, (
        f'Expected ModuleNotFoundError for pytest, got:\n{pytest_check.stderr}'
    )
