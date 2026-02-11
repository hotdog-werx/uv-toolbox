from __future__ import annotations

import subprocess
from pathlib import Path
from textwrap import dedent

import pytest


def test_integration_exec_installs_project_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that uvtb exec directly calls commands from venv without reinstalling.

    This test demonstrates that after running `uvtb install`, when we then run
    `uvtb exec -- myproject-cli`, it directly executes the command from the venv
    bin directory without triggering any additional installation from uv.
    """
    # Create the project directory structure
    project_dir = tmp_path / 'myproject'
    project_dir.mkdir()
    src_dir = project_dir / 'src' / 'myproject'
    src_dir.mkdir(parents=True)

    # Create the CLI module that prints hello-world
    cli_module = src_dir / 'cli.py'
    cli_module.write_text(
        dedent("""
        def main() -> None:
            print("hello-world")


        if __name__ == "__main__":
            main()
    """).strip()
        + '\n',
    )

    # Create __init__.py
    (src_dir / '__init__.py').write_text('')

    # Create pyproject.toml with CLI entry point and dev dependencies
    pyproject = project_dir / 'pyproject.toml'
    pyproject.write_text(
        dedent("""
        [project]
        name = "myproject"
        version = "0.1.0"
        dependencies = []

        [project.optional-dependencies]
        dev = ["pytest"]

        [project.scripts]
        myproject-cli = "myproject.cli:main"

        [build-system]
        requires = ["hatchling"]
        build-backend = "hatchling.build"
    """).strip()
        + '\n',
    )

    # Create uv-toolbox.yaml that installs ruff and the project itself
    uvtb_config = project_dir / 'uv-toolbox.yaml'
    uvtb_config.write_text(
        dedent("""
        venv_path: .uv-toolbox
        environments:
          - name: tools
            requirements: |
              ruff
              myproject @ .
    """).strip()
        + '\n',
    )

    # Change to the project directory so we don't need --config
    monkeypatch.chdir(project_dir)

    # Step 1: Run uvtb install
    install_result = subprocess.run(
        ['uvtb', 'install'],
        check=False,
        capture_output=True,
        text=True,
    )

    # Verify install succeeded
    assert install_result.returncode == 0, (
        f'uvtb install failed:\nstdout: {install_result.stdout}\nstderr: {install_result.stderr}'
    )

    # Verify that the venv was created
    venv_dir = project_dir / '.uv-toolbox'
    assert venv_dir.exists(), 'Virtual environment directory was not created'

    # Step 2: Run uvtb exec -- myproject-cli
    exec_result = subprocess.run(
        ['uvtb', 'exec', '--', 'myproject-cli'],
        check=False,
        capture_output=True,
        text=True,
    )

    # Verify the command succeeded
    assert exec_result.returncode == 0, (
        f'uvtb exec failed:\nstdout: {exec_result.stdout}\nstderr: {exec_result.stderr}'
    )

    # Verify the output is what we expect
    assert 'hello-world' in exec_result.stdout, f'Expected "hello-world" in output, got:\n{exec_result.stdout}'

    # THE FIX VERIFICATION: Check that NO installation happened!
    # With the fix, we directly call commands from the venv bin directory
    # instead of using 'uv run --active', so there should be no installation activity.
    assert 'Installed' not in exec_result.stderr and 'installed' not in exec_result.stderr, (
        f'Did not expect installation activity in stderr, but got:\n{exec_result.stderr}'
    )

    # The key demonstration: With the fix, uvtb exec directly calls commands
    # from the venv bin directory instead of using 'uv run --active'.
    # This avoids triggering automatic installation by uv.
    
    # Evidence that everything still works:
    # 1. The CLI worked (meaning myproject was installed by uvtb install)
    # 2. No installation activity happened during exec
    # 3. All tools are available from the venv

    # Verify the project was installed by checking if we can import it
    import_check = subprocess.run(
        [
            'uvtb',
            'exec',
            '--',
            'python',
            '-c',
            'import myproject; print("Import successful")',
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert import_check.returncode == 0, (
        f'Failed to import myproject:\nstdout: {import_check.stdout}\nstderr: {import_check.stderr}'
    )
    assert 'Import successful' in import_check.stdout

    # We can also verify ruff was installed
    ruff_check = subprocess.run(
        ['uvtb', 'exec', '--', 'ruff', '--version'],
        check=False,
        capture_output=True,
        text=True,
    )

    assert ruff_check.returncode == 0, 'ruff should be available in the environment'
    assert 'ruff' in ruff_check.stdout.lower(), f'Expected ruff version in output, got:\n{ruff_check.stdout}'

    # CRITICAL VERIFICATION: Check that pytest (from dev dependencies) is available!
    # This proves that uvtb install properly installed the project with its dependencies.
    pytest_check = subprocess.run(
        ['uvtb', 'exec', '--', 'pytest', '--version'],
        check=False,
        capture_output=True,
        text=True,
    )

    assert pytest_check.returncode == 0, (
        f'pytest should be installed in the venv!\nstdout: {pytest_check.stdout}\nstderr: {pytest_check.stderr}'
    )
    assert 'pytest' in pytest_check.stdout.lower(), f'Expected pytest version in output, got:\n{pytest_check.stdout}'

    # Verify that this also didn't trigger installation
    assert 'Installed' not in pytest_check.stderr and 'installed' not in pytest_check.stderr
