"""End-to-end locking against real uv, using local git repositories as sources.

Local repositories keep these tests independent of the network, and deleting
them proves that a command did not look a git ref up again.
"""

from __future__ import annotations

import os
import subprocess
from textwrap import dedent
from typing import TYPE_CHECKING

import pytest

from uv_toolbox.lockfile import read_lockfile

if TYPE_CHECKING:
    from pathlib import Path

_GIT_ENV = {
    'GIT_AUTHOR_NAME': 'test',
    'GIT_AUTHOR_EMAIL': 'test@example.com',
    'GIT_COMMITTER_NAME': 'test',
    'GIT_COMMITTER_EMAIL': 'test@example.com',
}


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(  # noqa: S603
        ['git', *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **_GIT_ENV},
    )
    return result.stdout.strip()


def _release(repo: Path, name: str, version: str, tag: str) -> str:
    """Commit a static-metadata package version and (re)point `tag` at it; return the commit."""
    (repo / 'pyproject.toml').write_text(
        dedent(f"""\
            [project]
            name = "{name}"
            version = "{version}"

            [build-system]
            requires = ["uv_build>=0.9,<0.10"]
            build-backend = "uv_build"
        """),
    )
    _git(repo, 'add', '-A')
    _git(repo, 'commit', '-qm', version)
    _git(repo, 'tag', '-f', tag)
    return _git(repo, 'rev-parse', 'HEAD')


def _make_repo(root: Path, name: str) -> Path:
    repo = root / name
    repo.mkdir()
    _git(repo, 'init', '-q')
    return repo


def _uvtb(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ['uvtb', *args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )


def _assert_ok(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 0, f'stdout:\n{result.stdout}\nstderr:\n{result.stderr}'


def _locked_commit(project: Path, package: str) -> str:
    uv_lock = read_lockfile(project / 'uv-toolbox.lock').environments['tools'].uv_lock
    assert uv_lock is not None
    marker = f'name = "{package}"\n'
    source_line = uv_lock[uv_lock.index(marker) :].splitlines()[2]
    return source_line.rsplit('#', 1)[1].rstrip('" }')


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A config with two git dependencies pinned to floating tags in local repositories."""
    monkeypatch.setenv('UV_CACHE_DIR', str(tmp_path / 'uv-cache'))
    repos = tmp_path / 'repos'
    repos.mkdir()
    for name in ('tool-a', 'tool-b'):
        _release(_make_repo(repos, name), name, '1.0.0', 'v1')
    project_dir = tmp_path / 'project'
    project_dir.mkdir()
    (project_dir / 'uv-toolbox.yaml').write_text(
        dedent(f"""\
            venv_path: .venvs
            environments:
              - name: tools
                requires_python: '>=3.11'
                dependencies: [tool-a, tool-b]
                sources:
                  tool-a: {{ git: '{(repos / 'tool-a').as_uri()}', tag: v1 }}
                  tool-b: {{ git: '{(repos / 'tool-b').as_uri()}', tag: v1 }}
        """),
    )
    return project_dir


def test_relock_and_check_do_not_look_up_git_refs(project: Path) -> None:
    """Once locked, `lock` and `lock --check` work without reaching the git sources at all."""
    _assert_ok(_uvtb(project, 'lock'))
    lock_text = (project / 'uv-toolbox.lock').read_text()
    assert 'version: 2' in lock_text
    assert 'uv_lock: |' in lock_text
    assert '?tag=v1#' in lock_text

    # Renaming (rather than deleting, which trips over git's read-only files on
    # Windows) makes every source URL unreachable.
    repos = project.parent / 'repos'
    repos.rename(repos.with_name('repos-gone'))

    relock = _uvtb(project, 'lock')
    _assert_ok(relock)
    assert 'Lockfile is up to date' in relock.stdout
    assert 'Updating' not in relock.stderr
    _assert_ok(_uvtb(project, 'lock', '--check'))
    assert (project / 'uv-toolbox.lock').read_text() == lock_text


def test_moved_tag_passes_check_until_upgrade(project: Path) -> None:
    """A tag moving upstream does not fail `--check`; `--upgrade-package` picks it up for that package only."""
    _assert_ok(_uvtb(project, 'lock'))
    old_a = _locked_commit(project, 'tool-a')
    old_b = _locked_commit(project, 'tool-b')
    repos = project.parent / 'repos'
    new_a = _release(repos / 'tool-a', 'tool-a', '1.1.0', 'v1')
    _release(repos / 'tool-b', 'tool-b', '1.1.0', 'v1')

    _assert_ok(_uvtb(project, 'lock', '--check'))
    _assert_ok(_uvtb(project, 'lock'))
    assert _locked_commit(project, 'tool-a') == old_a

    _assert_ok(_uvtb(project, 'lock', '--upgrade-package', 'tool-a'))

    assert _locked_commit(project, 'tool-a') == new_a
    assert _locked_commit(project, 'tool-b') == old_b
    _assert_ok(_uvtb(project, 'lock', '--check'))


def test_config_change_fails_check_until_relocked(project: Path) -> None:
    """Editing the config makes `--check` fail until `lock` re-locks the environment."""
    _assert_ok(_uvtb(project, 'lock'))
    config = project / 'uv-toolbox.yaml'
    config.write_text(config.read_text().replace('dependencies: [tool-a, tool-b]', 'dependencies: [tool-a]'))

    check = _uvtb(project, 'lock', '--check')
    assert check.returncode == 1
    assert 'Configuration changed for: tools' in check.stderr

    _assert_ok(_uvtb(project, 'lock'))
    assert 'name = "tool-b"' not in (read_lockfile(project / 'uv-toolbox.lock').environments['tools'].uv_lock or '')
    _assert_ok(_uvtb(project, 'lock', '--check'))
