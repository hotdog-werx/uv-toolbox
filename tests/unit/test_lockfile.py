from __future__ import annotations

from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from pathlib import Path

from uv_toolbox.lockfile import (
    EnvironmentLock,
    UvToolboxLock,
    lockfiles_equal,
    read_lockfile,
    write_lockfile,
)

_COMPILED = (
    'ruff==0.14.14 \\\n    --hash=sha256:aaaa \\\n    --hash=sha256:bbbb\nclick==8.3.1 \\\n    --hash=sha256:cccc'
)


def test_write_and_read_lockfile_round_trip(tmp_path: Path) -> None:
    """Writing then reading a lockfile preserves the version and environment requirements content."""
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
    """Requirements are serialized using YAML's `|` block scalar style for readable multi-line output."""
    lock = UvToolboxLock(
        environments={'fmt': EnvironmentLock(requirements=_COMPILED)},
    )
    path = tmp_path / 'uv-toolbox.lock'
    write_lockfile(lock, path)
    raw = path.read_text()

    assert 'requirements: |' in raw
    assert 'ruff==0.14.14' in raw


def test_write_lockfile_version_comes_before_environments(
    tmp_path: Path,
) -> None:
    """The `version` key is serialized before `environments` for a stable, readable lockfile layout."""
    lock = UvToolboxLock(
        environments={'fmt': EnvironmentLock(requirements=_COMPILED)},
    )
    path = tmp_path / 'uv-toolbox.lock'
    write_lockfile(lock, path)
    raw = path.read_text()

    assert raw.index('version:') < raw.index('environments:')


def test_write_lockfile_multiple_environments(tmp_path: Path) -> None:
    """A lockfile with multiple environments round-trips each environment's requirements independently."""
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
    """Looking up an environment name absent from the lockfile returns None instead of raising."""
    lock = UvToolboxLock(
        environments={'fmt': EnvironmentLock(requirements='ruff==0.14.14')},
    )
    path = tmp_path / 'uv-toolbox.lock'
    write_lockfile(lock, path)
    loaded = read_lockfile(path)

    assert loaded.environments.get('missing') is None


def test_read_lockfile_trailing_newline_from_block_scalar(
    tmp_path: Path,
) -> None:
    """Reading a lockfile preserves the trailing newline that PyYAML's `|` block scalar adds on write."""
    lock = UvToolboxLock(
        environments={'fmt': EnvironmentLock(requirements='ruff==0.14.14')},
    )
    path = tmp_path / 'uv-toolbox.lock'
    write_lockfile(lock, path)
    loaded = read_lockfile(path)

    # PyYAML preserves the trailing newline from | block scalars
    assert loaded.environments['fmt'].requirements.endswith('\n')


def test_write_lockfile_does_not_use_global_representer(tmp_path: Path) -> None:
    """Confirms the lockfile's block-scalar representer stays scoped and doesn't leak into the global yaml dumper."""
    # Confirm _LiteralStr doesn't leak into the global yaml dumper
    plain_str = 'hello\nworld'
    result = yaml.dump({'key': plain_str})
    assert '|' not in result


def test_lockfiles_equal_ignores_serialization_newline() -> None:
    """Generated and YAML-loaded requirements compare equal despite the block-scalar newline."""
    generated = UvToolboxLock(
        environments={'fmt': EnvironmentLock(requirements='ruff==1')},
    )
    loaded = UvToolboxLock(
        environments={'fmt': EnvironmentLock(requirements='ruff==1\n')},
    )

    assert lockfiles_equal(generated, loaded)
    loaded.environments['fmt'].requirements = 'ruff==2\n'
    assert not lockfiles_equal(generated, loaded)
