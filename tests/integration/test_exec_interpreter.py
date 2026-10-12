"""`uvtb exec` always runs the toolbox venv's interpreter."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from textwrap import dedent

import pytest


def _uvtb(cwd: Path, *args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(  # noqa: S603
        ['uvtb', *args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, f'stdout:\n{result.stdout}\nstderr:\n{result.stderr}'
    return result


@pytest.mark.parametrize('redirect', ['python-version', 'UV_PYTHON'])
def test_exec_ignores_conflicting_interpreter_requests(tmp_path: Path, redirect: str) -> None:
    """A `.python-version` or UV_PYTHON naming another interpreter can't redirect `exec`."""
    (tmp_path / '.git').mkdir()
    (tmp_path / 'uv-toolbox.yaml').write_text(
        dedent("""\
            venv_path: .venvs
            environments:
              - name: tools
                requires_python: '>=3.11'
                dependencies: []
        """),
    )
    env: dict[str, str] = {**os.environ, 'UV_PYTHON_DOWNLOADS': 'never'}
    env.pop('VIRTUAL_ENV', None)
    env.pop('UV_PYTHON', None)
    _uvtb(tmp_path, 'install', env=env)

    # No such interpreter exists, so uv fails if it honours the request.
    if redirect == 'python-version':
        (tmp_path / '.python-version').write_text('3.99\n')
    else:
        env['UV_PYTHON'] = '3.99'
    result = _uvtb(tmp_path, 'exec', '--', 'python', '-c', 'import sys; print(sys.prefix)', env=env)

    venvs = list((tmp_path / '.venvs').iterdir())
    venv = next(path for path in venvs if path.is_dir())
    assert Path(result.stdout.strip()).resolve() == venv.resolve()
