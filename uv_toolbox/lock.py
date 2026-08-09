from __future__ import annotations

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
    *,
    existing_requirements: str | None = None,
    refresh: bool = False,
    upgrade: bool = False,
) -> str:
    """Compile pinned, hash-verified requirements for one environment.

    Runs `uv pip compile --generate-hashes --universal` to produce a
    platform-agnostic requirements file with hashes for every wheel variant.
    VIRTUAL_ENV is intentionally not set so resolution is not bound to any
    specific venv's Python.

    Args:
        env: The environment to compile requirements for.
        settings: UV toolbox settings (used for show_commands and requirement source).
        existing_requirements: Previously locked requirements whose compatible
            pins should be preserved during validation.
        refresh: If True, forward `--refresh` to `uv pip compile` so cached
            package metadata is ignored and the latest compatible versions
            are considered.
        upgrade: If True, forward `--upgrade` to `uv pip compile` so existing
            pins (direct and transitive) are disregarded and every package is
            re-resolved to its latest compatible version.

    Returns:
        The compiled requirements text (stripped, no trailing newline).
    """
    with tempfile.TemporaryDirectory() as temp_dir_raw:
        temp_dir = Path(temp_dir_raw)
        output_path = temp_dir / 'compiled-requirements.txt'
        if existing_requirements is not None:
            # uv treats an existing output file as the preferred set of pins.
            # This lets `lock --check` validate compatibility without turning
            # every newly published transitive dependency into lockfile drift.
            output_path.write_text(existing_requirements)
        if env.requirements_file is not None:
            req_source = str(env.requirements_file)
        else:
            temp_req_file = temp_dir / f'requirements_{env.name}.txt'
            # Always resolve the declared source. ``resolved_requirements``
            # may contain content injected from an existing lockfile, which
            # would prevent `lock` from refreshing stale dependencies.
            if env.requirements is None:  # pragma: no cover
                msg = 'requirements is None — model validation guarantees this is impossible'
                raise RuntimeError(msg)
            temp_req_file.write_text(env.requirements)
            req_source = str(temp_req_file)

        run_checked(
            args=[
                'uv',
                'pip',
                'compile',
                '--generate-hashes',
                '--universal',
                '--no-header',
                '--no-annotate',
                *(['--refresh'] if refresh else []),
                *(['--upgrade'] if upgrade else []),
                '-o',
                str(output_path),
                req_source,
            ],
            capture_stdout=True,
            capture_stderr=False,
            show_command=settings.show_commands,
        )
        return output_path.read_text().strip()


def generate_lock(
    settings: UvToolboxSettings,
    *,
    existing_lock: UvToolboxLock | None = None,
    refresh: bool = False,
    upgrade: bool = False,
) -> UvToolboxLock:
    """Compile a lockfile for all configured environments.

    Args:
        settings: UV toolbox settings.
        existing_lock: Existing lock whose compatible pins should be preserved.
        refresh: If True, forward `--refresh` to `uv pip compile` for every
            environment so the latest compatible versions are considered.
        upgrade: If True, forward `--upgrade` to `uv pip compile` for every
            environment so existing pins are disregarded and re-resolved to
            their latest compatible version.

    Returns:
        A UvToolboxLock containing compiled, hash-bearing requirements for
        every environment.
    """
    lock = UvToolboxLock()
    for env in settings.environments:
        existing_environment = existing_lock.environments.get(env.name) if existing_lock is not None else None
        compiled = generate_environment_lock(
            env=env,
            settings=settings,
            existing_requirements=(existing_environment.requirements if existing_environment is not None else None),
            refresh=refresh,
            upgrade=upgrade,
        )
        lock.environments[env.name] = EnvironmentLock(requirements=compiled)
    return lock
