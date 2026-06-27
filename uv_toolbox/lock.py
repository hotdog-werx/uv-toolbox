from __future__ import annotations

import shutil
import tempfile
import typing
from pathlib import Path

from uv_toolbox.lockfile import EnvironmentLock, UvToolboxLock
from uv_toolbox.process import run_checked

if typing.TYPE_CHECKING:  # pragma: no cover
    from uv_toolbox.settings import UvToolboxEnvironment, UvToolboxSettings


def generate_environment_lock(
    env: UvToolboxEnvironment,
    settings: UvToolboxSettings,
) -> str:
    """Compile pinned, hash-verified requirements for one environment.

    Runs `uv pip compile --generate-hashes --universal` to produce a
    platform-agnostic requirements file with hashes for every wheel variant.
    VIRTUAL_ENV is intentionally not set so resolution is not bound to any
    specific venv's Python.

    Args:
        env: The environment to compile requirements for.
        settings: UV toolbox settings (used for show_commands and requirement source).

    Returns:
        The compiled requirements text (stripped, no trailing newline).
    """
    temp_dir: Path | None = None

    try:
        if env.requirements_file is not None:
            req_source = str(env.requirements_file)
        else:
            temp_dir = Path(tempfile.mkdtemp())
            temp_req_file = temp_dir / f'requirements_{env.name}.txt'
            temp_req_file.write_text(env.resolved_requirements)
            req_source = str(temp_req_file)

        return run_checked(
            args=[
                'uv',
                'pip',
                'compile',
                '--generate-hashes',
                '--universal',
                '--no-header',
                '--no-annotate',
                '-o',
                '-',
                req_source,
            ],
            capture_stdout=True,
            capture_stderr=False,
            show_command=settings.show_commands,
        )
    finally:
        if temp_dir is not None:
            shutil.rmtree(temp_dir)


def generate_lock(settings: UvToolboxSettings) -> UvToolboxLock:
    """Compile a lockfile for all configured environments.

    Args:
        settings: UV toolbox settings.

    Returns:
        A UvToolboxLock containing compiled, hash-bearing requirements for
        every environment.
    """
    lock = UvToolboxLock()
    for env in settings.environments:
        compiled = generate_environment_lock(env=env, settings=settings)
        lock.environments[env.name] = EnvironmentLock(requirements=compiled)
    return lock
