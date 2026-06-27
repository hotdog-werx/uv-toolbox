from __future__ import annotations

from pathlib import Path

import yaml

from uv_toolbox.lockfile import (
    EnvironmentLock,
    UvToolboxLock,
    read_lockfile,
    write_lockfile,
)

_COMPILED = (
    'ruff==0.14.14 \\\n'
    '    --hash=sha256:aaaa \\\n'
    '    --hash=sha256:bbbb\n'
    'click==8.3.1 \\\n'
    '    --hash=sha256:cccc'
)


def test_write_and_read_lockfile_round_trip(tmp_path: Path) -> None:
    lock = UvToolboxLock(
        environments={
            'formatting': EnvironmentLock(requirements=_COMPILED),
        },
    )
    path = tmp_path / 'uv-toolbox.lock'
    write_lockfile(lock, path)
    loaded = read_lockfile(path)

    assert loaded.version == 1
    assert set(loaded.environments) == {'formatting'}
    # PyYAML adds a trailing newline to | block scalars; strip for comparison
    assert loaded.environments['formatting'].requirements.strip() == _COMPILED.strip()


def test_write_lockfile_uses_block_scalar_style(tmp_path: Path) -> None:
    lock = UvToolboxLock(
        environments={'fmt': EnvironmentLock(requirements=_COMPILED)},
    )
    path = tmp_path / 'uv-toolbox.lock'
    write_lockfile(lock, path)
    raw = path.read_text()

    assert 'requirements: |' in raw
    assert 'ruff==0.14.14' in raw


def test_write_lockfile_version_comes_before_environments(tmp_path: Path) -> None:
    lock = UvToolboxLock(
        environments={'fmt': EnvironmentLock(requirements=_COMPILED)},
    )
    path = tmp_path / 'uv-toolbox.lock'
    write_lockfile(lock, path)
    raw = path.read_text()

    assert raw.index('version:') < raw.index('environments:')


def test_write_lockfile_multiple_environments(tmp_path: Path) -> None:
    lock = UvToolboxLock(
        environments={
            'formatting': EnvironmentLock(requirements='ruff==0.14.14'),
            'testing': EnvironmentLock(requirements='pytest==9.0.2'),
        },
    )
    path = tmp_path / 'uv-toolbox.lock'
    write_lockfile(lock, path)
    loaded = read_lockfile(path)

    assert set(loaded.environments) == {'formatting', 'testing'}
    assert 'ruff' in loaded.environments['formatting'].requirements
    assert 'pytest' in loaded.environments['testing'].requirements


def test_read_lockfile_missing_environment_returns_none(tmp_path: Path) -> None:
    lock = UvToolboxLock(environments={'fmt': EnvironmentLock(requirements='ruff==0.14.14')})
    path = tmp_path / 'uv-toolbox.lock'
    write_lockfile(lock, path)
    loaded = read_lockfile(path)

    assert loaded.environments.get('missing') is None


def test_read_lockfile_trailing_newline_from_block_scalar(tmp_path: Path) -> None:
    lock = UvToolboxLock(
        environments={'fmt': EnvironmentLock(requirements='ruff==0.14.14')},
    )
    path = tmp_path / 'uv-toolbox.lock'
    write_lockfile(lock, path)
    loaded = read_lockfile(path)

    # PyYAML preserves the trailing newline from | block scalars
    assert loaded.environments['fmt'].requirements.endswith('\n')


def test_write_lockfile_does_not_use_global_representer(tmp_path: Path) -> None:
    # Confirm _LiteralStr doesn't leak into the global yaml dumper
    plain_str = 'hello\nworld'
    result = yaml.dump({'key': plain_str})
    assert '|' not in result
