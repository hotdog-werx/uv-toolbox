import shutil
import tempfile
from pathlib import Path

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
    )


def install_requirements(
    env: UvToolboxEnvironment,
    settings: UvToolboxSettings,
) -> None:
    """Install the requirements for the given environment into its virtualenv.

    Args:
        env: The UV toolbox environment to install requirements for.
        settings: The UV toolbox settings.
    """
    temp_dir: Path | None = None

    if env.requirements_file is not None:
        reqs_arg = ['-r', str(env.requirements_file)]
    elif env.requirements is not None:
        temp_dir = Path(tempfile.mkdtemp())
        temp_req_file = temp_dir / f'requirements_{env.name}.txt'
        temp_req_file.write_text(env.requirements)
        reqs_arg = ['-r', str(temp_req_file)]

    if reqs_arg:
        run_checked(
            args=[
                'uv',
                'pip',
                'install',
                *reqs_arg,
                '--exact',
            ],
            extra_env=env.process_env(settings=settings),
        )

    if temp_dir is not None:
        shutil.rmtree(temp_dir)


def initialize_virtualenv(
    env: UvToolboxEnvironment,
    settings: UvToolboxSettings,
    *,
    force: bool = False,
) -> None:
    """Create and set up the virtual environment for the given environment.

    Args:
        env: The UV toolbox environment to initialize.
        settings: The UV toolbox settings.
        force: If True, recreate the virtual environment even if it exists.
    """
    venv_path = env.venv_path(settings=settings)

    # Only create venv if it doesn't exist or force is True
    if not venv_path.exists() or force:
        # Use --clear flag when forcing recreation of existing venv
        clear = force and venv_path.exists()
        create_virtualenv(env=env, settings=settings, clear=clear)

    install_requirements(env=env, settings=settings)
