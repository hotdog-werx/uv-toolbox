import shutil
import tempfile
from pathlib import Path

from uv_toolbox.errors import ExternalCommandError
from uv_toolbox.process import run_checked
from uv_toolbox.settings import UvToolboxEnvironment, UvToolboxSettings


def create_virtualenv(
    env: UvToolboxEnvironment,
    settings: UvToolboxSettings,
    *,
    clear: bool = False,
) -> None:
    """Create a Python virtual environment at the specified path.

    Args:
        env: The UV toolbox environment to create the virtualenv for.
        settings: The UV toolbox settings.
        clear: If True, clear the existing venv if it already exists.
    """
    args = ['uv', 'venv', str(env.venv_path(settings=settings))]
    if clear:
        args.append('--clear')

    run_checked(
        args=args,
        extra_env=env.process_env(settings=settings),
        capture_stdout=False,
        capture_stderr=False,
        show_command=settings.show_commands,
    )


def _lockfile_path(venv_path: Path) -> Path:
    """Path for the requirements lockfile, stored as a sibling of the venv directory.

    Kept outside the venv so `uv venv --clear` does not delete it.
    """
    return venv_path.parent / f'{venv_path.name}.lock'


def _sync_from_lockfile(
    env: UvToolboxEnvironment,
    settings: UvToolboxSettings,
    lockfile: Path,
) -> None:
    """Sync the environment from a lockfile, trying the uv cache first.

    Attempts an offline sync (no network, no re-resolution) and falls back to
    an online sync if the cache does not contain all required packages.

    Args:
        env: The UV toolbox environment.
        settings: The UV toolbox settings.
        lockfile: Path to the pinned requirements lockfile.
    """
    try:
        run_checked(
            args=['uv', 'pip', 'sync', str(lockfile), '--offline'],
            extra_env=env.process_env(settings=settings),
            capture_stdout=False,
            capture_stderr=False,
            show_command=settings.show_commands,
        )
    except ExternalCommandError:
        run_checked(
            args=['uv', 'pip', 'sync', str(lockfile)],
            extra_env=env.process_env(settings=settings),
            capture_stdout=False,
            capture_stderr=False,
            show_command=settings.show_commands,
        )


def _install_from_resolved(
    env: UvToolboxEnvironment,
    settings: UvToolboxSettings,
    lockfile: Path,
) -> None:
    """Install from repo-lockfile resolved requirements and write machine lockfile.

    Uses the pre-compiled, hash-bearing requirements injected from uv-toolbox.lock.
    uv pip sync auto-enables --require-hashes when the file contains --hash= lines.
    The machine lockfile is written with the same resolved content so the next
    install can use the offline-first path without re-reading the repo lockfile.

    Args:
        env: The UV toolbox environment.
        settings: The UV toolbox settings.
        lockfile: Path where the machine lockfile should be written.
    """
    if env._resolved_requirements is None:
        msg = '_install_from_resolved called without resolved requirements'
        raise RuntimeError(msg)
    temp_dir = Path(tempfile.mkdtemp())
    try:
        temp_req_file = temp_dir / f'requirements_{env.name}.txt'
        temp_req_file.write_text(env._resolved_requirements)

        run_checked(
            args=['uv', 'pip', 'sync', str(temp_req_file)],
            extra_env=env.process_env(settings=settings),
            capture_stdout=False,
            capture_stderr=False,
            show_command=settings.show_commands,
        )

        lockfile.parent.mkdir(parents=True, exist_ok=True)
        lockfile.write_text(env._resolved_requirements)
    finally:
        shutil.rmtree(temp_dir)


def _initial_install(
    env: UvToolboxEnvironment,
    settings: UvToolboxSettings,
    lockfile: Path,
) -> None:
    """Install from the configured requirements source and export a lockfile.

    Performs a full online sync, then freezes the resolved packages into a
    lockfile so future installs can use the offline-first path.

    Args:
        env: The UV toolbox environment.
        settings: The UV toolbox settings.
        lockfile: Path where the generated lockfile should be written.
    """
    temp_dir: Path | None = None

    try:
        if env.requirements_file is not None:
            req_source = str(env.requirements_file)
        else:
            if env.requirements is None:
                msg = 'env.requirements must be set when requirements_file is None'
                raise RuntimeError(msg)
            temp_dir = Path(tempfile.mkdtemp())
            temp_req_file = temp_dir / f'requirements_{env.name}.txt'
            temp_req_file.write_text(env.requirements)
            req_source = str(temp_req_file)

        run_checked(
            args=['uv', 'pip', 'sync', req_source],
            extra_env=env.process_env(settings=settings),
            capture_stdout=False,
            capture_stderr=False,
            show_command=settings.show_commands,
        )

        frozen = run_checked(
            args=['uv', 'pip', 'freeze'],
            extra_env=env.process_env(settings=settings),
            capture_stdout=True,
            capture_stderr=False,
            show_command=settings.show_commands,
        )
        lockfile.parent.mkdir(parents=True, exist_ok=True)
        lockfile.write_text(frozen)
    finally:
        if temp_dir is not None:
            shutil.rmtree(temp_dir)


def install_requirements(
    env: UvToolboxEnvironment,
    settings: UvToolboxSettings,
    *,
    upgrade: bool = False,
) -> None:
    """Install the requirements for the given environment into its virtualenv.

    On the first install (or when upgrade=True), resolves packages online and
    writes a pinned lockfile. On subsequent installs, syncs from the lockfile
    using the uv package cache (offline-first, with an online fallback if the
    cache is cold).

    Args:
        env: The UV toolbox environment to install requirements for.
        settings: The UV toolbox settings.
        upgrade: If True, delete the existing lockfile and re-resolve from
            scratch, refreshing pinned versions and their transitive deps.
    """
    venv_path = env.venv_path(settings=settings)
    lockfile = _lockfile_path(venv_path)

    if upgrade:
        lockfile.unlink(missing_ok=True)

    if lockfile.exists():
        _sync_from_lockfile(env=env, settings=settings, lockfile=lockfile)
    elif env._resolved_requirements is not None:
        _install_from_resolved(env=env, settings=settings, lockfile=lockfile)
    else:
        _initial_install(env=env, settings=settings, lockfile=lockfile)


def initialize_virtualenv(
    env: UvToolboxEnvironment,
    settings: UvToolboxSettings,
    *,
    clear: bool = False,
    upgrade: bool = False,
) -> None:
    """Create and set up the virtual environment for the given environment.

    Args:
        env: The UV toolbox environment to initialize.
        settings: The UV toolbox settings.
        clear: If True, clear and recreate the virtual environment.
        upgrade: If True, re-resolve dependencies and refresh the lockfile.
    """
    venv_path = env.venv_path(settings=settings)

    # Only create venv if it doesn't exist or clear is True
    if not venv_path.exists() or clear:
        create_virtualenv(env=env, settings=settings, clear=True)

    install_requirements(env=env, settings=settings, upgrade=upgrade)
