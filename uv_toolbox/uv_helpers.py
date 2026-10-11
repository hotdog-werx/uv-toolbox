from pathlib import Path

from uv_toolbox.project import lock_environment, sync_environment
from uv_toolbox.settings import UvToolboxEnvironment, UvToolboxSettings


def _machine_lock_path(venv_path: Path) -> Path:
    """Path for the machine lockfile (a uv.lock), stored as a sibling of the venv directory.

    Kept outside the venv so clearing the venv does not delete it.
    """
    return venv_path.parent / f'{venv_path.name}.uv.lock'


def _machine_uv_lock(
    env: UvToolboxEnvironment,
    settings: UvToolboxSettings,
    *,
    upgrade: bool,
) -> str:
    """Return the machine lock for an environment without a repo lockfile entry.

    The first install (or an upgrade) locks the environment online and keeps
    the resulting uv.lock next to the venv, so later installs can sync from it
    without resolving again.
    """
    machine_lock = _machine_lock_path(env.venv_path(settings=settings))
    if machine_lock.exists() and not upgrade:
        return machine_lock.read_text()
    uv_lock = lock_environment(env, settings, upgrade=upgrade)
    machine_lock.parent.mkdir(parents=True, exist_ok=True)
    machine_lock.write_text(uv_lock)
    return uv_lock


def initialize_virtualenv(
    env: UvToolboxEnvironment,
    settings: UvToolboxSettings,
    *,
    clear: bool = False,
    upgrade: bool = False,
) -> None:
    """Create or update the virtual environment for the given environment.

    Installs from the environment's uv.lock in the repo lockfile when there is
    one, otherwise from a machine lock next to the venv.

    Args:
        env: The UV toolbox environment to initialize.
        settings: The UV toolbox settings.
        clear: If True, clear and recreate the virtual environment.
        upgrade: If True and there is no repo lockfile entry, re-resolve
            dependencies and refresh the machine lock.
    """
    uv_lock = env.locked_uv_lock
    if uv_lock is None:
        uv_lock = _machine_uv_lock(env, settings, upgrade=upgrade)
    sync_environment(env, settings, uv_lock=uv_lock, clear=clear)
