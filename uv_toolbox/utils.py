import os
from pathlib import Path
from typing import Any


def _filter_nulls(d: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of the dictionary with all None values removed.

    Args:
        d: The dictionary to filter.

    Returns:
        A new dictionary with all None values removed.
    """
    return {k: v for k, v in d.items() if v is not None}


def _venv_bin_path(venv_path: Path) -> Path:
    """Return the path to the 'bin' (or 'Scripts' on Windows) directory of the virtualenv.

    Args:
        venv_path: The path to the virtual environment.

    Returns:
        The path to the 'bin' or 'Scripts' directory within the virtual
            environment.
    """
    return venv_path / ('Scripts' if os.name == 'nt' else 'bin')
