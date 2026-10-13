from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
import yaml

from uv_toolbox.lockfile import (
    LOCKFILE_VERSION,
    EnvironmentLock,
    UvToolboxLock,
    lockfiles_equal,
    read_lockfile,
    write_lockfile,
)

_REAL_UV_LOCK = (Path(__file__).parent.parent / 'fixtures' / 'repolish.uv.lock').read_text(encoding='utf-8')

_SMALL_UV_LOCK = dedent("""\
    version = 1
    revision = 3
    requires-python = ">=3.12"

    [[package]]
    name = "ruff"
    version = "0.14.14"
    source = { registry = "https://pypi.org/simple" }
""")


def test_real_uv_lock_round_trips_byte_for_byte_in_block_style(tmp_path: Path) -> None:
    """A real uv.lock survives a write/read cycle unchanged and is embedded as a `|` block."""
    lock = UvToolboxLock(
        environments={'repolish': EnvironmentLock(uv_lock=_REAL_UV_LOCK, fingerprint='sha256:abc')},
    )
    path = tmp_path / 'uv-toolbox.lock'
    write_lockfile(lock, path)

    raw = path.read_text(encoding='utf-8')
    assert '    uv_lock: |\n      version = 1\n' in raw
    assert read_lockfile(path).environments['repolish'].uv_lock == _REAL_UV_LOCK


def test_trailing_spaces_still_round_trip(tmp_path: Path) -> None:
    """Content PyYAML cannot emit as a block scalar still round-trips exactly, just in quoted style."""
    content = 'name = "x"   \nversion = "1"\n'
    lock = UvToolboxLock(environments={'env': EnvironmentLock(uv_lock=content)})
    path = tmp_path / 'uv-toolbox.lock'
    write_lockfile(lock, path)

    assert read_lockfile(path).environments['env'].uv_lock == content


def test_write_lockfile_layout(tmp_path: Path) -> None:
    """Version precedes environments, and each entry lists its fingerprint before its uv.lock."""
    lock = UvToolboxLock(
        environments={
            'formatting': EnvironmentLock(uv_lock=_SMALL_UV_LOCK, fingerprint='sha256:abc'),
            'testing': EnvironmentLock(uv_lock=_SMALL_UV_LOCK),
        },
    )
    path = tmp_path / 'uv-toolbox.lock'
    write_lockfile(lock, path)
    raw = path.read_text(encoding='utf-8')

    assert raw.startswith(f'version: {LOCKFILE_VERSION}\nenvironments:\n')
    assert raw.index('fingerprint: sha256:abc') < raw.index('uv_lock: |')
    loaded = read_lockfile(path)
    assert loaded.version == LOCKFILE_VERSION
    assert loaded.environments['formatting'].fingerprint == 'sha256:abc'
    assert loaded.environments['testing'].fingerprint is None
    assert not loaded.outdated_format


def test_write_lockfile_rejects_entry_without_uv_lock(tmp_path: Path) -> None:
    """Only fully locked entries can be written."""
    lock = UvToolboxLock(environments={'env': EnvironmentLock(legacy_requirements='ruff==1')})

    with pytest.raises(ValueError, match=r'without uv\.lock content'):
        write_lockfile(lock, tmp_path / 'uv-toolbox.lock')


def test_write_lockfile_does_not_use_global_representer(tmp_path: Path) -> None:
    """The literal block style is scoped to the lockfile dumper."""
    lock = UvToolboxLock(environments={'env': EnvironmentLock(uv_lock=_SMALL_UV_LOCK)})
    write_lockfile(lock, tmp_path / 'uv-toolbox.lock')

    assert yaml.dump({'key': 'a\nb\n'}) != 'key: |\n  a\n  b\n'


def test_read_version_1_lockfile_keeps_legacy_requirements(tmp_path: Path) -> None:
    """Version 1 entries have no uv.lock content and keep their compiled pins for seeding."""
    path = tmp_path / 'uv-toolbox.lock'
    path.write_text(
        dedent("""\
            version: 1
            environments:
              formatting:
                fingerprint: sha256:old
                requirements: |
                  ruff==0.14.14 \\
                      --hash=sha256:aaaa
        """),
        encoding='utf-8',
    )

    loaded = read_lockfile(path)

    assert loaded.outdated_format
    entry = loaded.environments['formatting']
    assert entry.uv_lock is None
    assert entry.fingerprint == 'sha256:old'
    assert entry.legacy_requirements is not None
    assert entry.legacy_requirements.startswith('ruff==0.14.14')


def test_read_lockfile_without_version_is_version_1(tmp_path: Path) -> None:
    """Lockfiles predating the version key are treated as version 1."""
    path = tmp_path / 'uv-toolbox.lock'
    path.write_text('environments:\n  fmt:\n    requirements: ruff==1\n', encoding='utf-8')

    assert read_lockfile(path).version == 1


def test_read_empty_lockfile(tmp_path: Path) -> None:
    """An empty file reads as a version 1 lock without environments."""
    path = tmp_path / 'uv-toolbox.lock'
    path.write_text('', encoding='utf-8')

    loaded = read_lockfile(path)

    assert loaded.environments == {}
    assert loaded.outdated_format


def test_lockfiles_equal_compares_uv_lock_and_fingerprint() -> None:
    """Locks are equal only with the same environments, fingerprints and uv.lock content."""
    base = UvToolboxLock(environments={'env': EnvironmentLock(uv_lock=_SMALL_UV_LOCK, fingerprint='a')})

    assert lockfiles_equal(base, UvToolboxLock(environments={'env': EnvironmentLock(_SMALL_UV_LOCK, 'a')}))
    assert not lockfiles_equal(base, UvToolboxLock(environments={'env': EnvironmentLock(_SMALL_UV_LOCK, 'b')}))
    assert not lockfiles_equal(base, UvToolboxLock(environments={'env': EnvironmentLock('other', 'a')}))
    assert not lockfiles_equal(base, UvToolboxLock(environments={'other': EnvironmentLock(_SMALL_UV_LOCK, 'a')}))
    assert not lockfiles_equal(base, UvToolboxLock(version=1, environments=base.environments))


def test_non_ascii_uv_lock_round_trips_in_block_style(tmp_path: Path) -> None:
    """Non-ASCII content (e.g. a local path) stays a readable UTF-8 block scalar."""
    content = _SMALL_UV_LOCK + 'source = { editable = "../../../plugins/café" }\n'
    lock = UvToolboxLock(environments={'env': EnvironmentLock(uv_lock=content)})
    path = tmp_path / 'uv-toolbox.lock'
    write_lockfile(lock, path)

    assert 'plugins/café' in path.read_text(encoding='utf-8')
    assert 'uv_lock: |' in path.read_text(encoding='utf-8')
    assert read_lockfile(path).environments['env'].uv_lock == content
