import shutil
import tempfile
from pathlib import Path

from uv_toolbox.process import run_checked
from uv_toolbox.settings import UvToolboxEnvironment


def create_virtualenv(env: UvToolboxEnvironment) -> None:
    """Create a Python virtual environment at the specified path.

    Args:
        env: The UV toolbox environment definition to create a virtual
            environment for.
    """
    run_checked(
        args=['uv', 'venv', str(env.venv_path)],
        extra_env=env.process_env,
    )


def install_requirements(
    env: UvToolboxEnvironment,
) -> None:
    """Install the requirements for the given environment into its virtualenv.

    Args:
        env: The UV toolbox environment definition.
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
            extra_env=env.process_env,
        )

    if temp_dir is not None:
        shutil.rmtree(temp_dir)


def initialize_virtualenv(env: UvToolboxEnvironment) -> None:
    """Create and set up the virtual environment for the given environment.

    Args:
        env: The UV toolbox environment definition.
    """
    create_virtualenv(env)
    install_requirements(env)
