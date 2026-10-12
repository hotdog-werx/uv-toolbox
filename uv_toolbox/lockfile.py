from __future__ import annotations

import typing
from dataclasses import dataclass, field

import yaml

if typing.TYPE_CHECKING:
    from pathlib import Path

# The lockfile format written by this version. Version 1 stored
# `uv pip compile` output; version 2 embeds each environment's uv.lock.
LOCKFILE_VERSION = 2


class _LiteralStr(str):
    """Marker subclass to force PyYAML block scalar (|) output style."""

    __slots__ = ()


class _LiteralDumper(yaml.Dumper):
    """Custom YAML dumper that renders _LiteralStr values as block scalars.

    Subclassing rather than mutating the global representer registry avoids
    side effects on other PyYAML callers in the same process.
    """


def _literal_representer(
    dumper: yaml.Dumper,
    data: _LiteralStr,
) -> yaml.ScalarNode:
    return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='|')


_LiteralDumper.add_representer(_LiteralStr, _literal_representer)


@dataclass
class EnvironmentLock:
    """Locked state of a single environment.

    Attributes:
        uv_lock: The environment's uv.lock content. None for entries read
            from a version 1 lockfile, which must be re-locked.
        fingerprint: Digest of the declared inputs the environment was
            locked from. None for lockfiles written before fingerprints were
            recorded, which are always treated as out of date.
        legacy_requirements: The `uv pip compile` output of a version 1
            entry. Only used to seed the first uv.lock with the same pins.
    """

    uv_lock: str | None = None
    fingerprint: str | None = None
    legacy_requirements: str | None = None


@dataclass
class UvToolboxLock:
    """In-memory representation of a uv-toolbox.lock file."""

    version: int = LOCKFILE_VERSION
    environments: dict[str, EnvironmentLock] = field(default_factory=dict)

    @property
    def outdated_format(self) -> bool:
        """Whether this lock was written in an older format and must be re-locked."""
        return self.version != LOCKFILE_VERSION


def lockfiles_equal(left: UvToolboxLock, right: UvToolboxLock) -> bool:
    """Return whether two lockfiles contain equivalent locked environments."""
    if left.version != right.version or left.environments.keys() != right.environments.keys():
        return False
    return all(
        left.environments[name].fingerprint == right.environments[name].fingerprint
        and left.environments[name].uv_lock == right.environments[name].uv_lock
        for name in left.environments
    )


def _environment_data(env_lock: EnvironmentLock) -> dict[str, str]:
    """Build the serializable mapping for one environment's lock entry."""
    if env_lock.uv_lock is None:
        msg = 'Cannot write an environment lock without uv.lock content.'
        raise ValueError(msg)
    data: dict[str, str] = {}
    if env_lock.fingerprint is not None:
        data['fingerprint'] = env_lock.fingerprint
    data['uv_lock'] = _LiteralStr(env_lock.uv_lock)
    return data


def write_lockfile(lock: UvToolboxLock, path: Path) -> None:
    """Serialize a UvToolboxLock to a YAML file at path.

    uv.lock content is written as a YAML literal block scalar (|), so the
    embedded TOML stays readable and diffs stay line-oriented. sort_keys=False
    preserves dict insertion order so 'version' always appears before
    'environments'.

    Args:
        lock: The lockfile data to write.
        path: Destination path (created or overwritten).
    """
    data = {
        'version': LOCKFILE_VERSION,
        'environments': {name: _environment_data(env_lock) for name, env_lock in lock.environments.items()},
    }
    path.write_text(
        yaml.dump(
            data,
            Dumper=_LiteralDumper,
            default_flow_style=False,
            sort_keys=False,
        ),
    )


def read_lockfile(path: Path) -> UvToolboxLock:
    """Load a uv-toolbox.lock YAML file into a UvToolboxLock.

    Version 1 entries are read with their compiled requirements kept as
    `legacy_requirements` and no uv.lock content.

    Args:
        path: Path to the lockfile.

    Returns:
        Parsed UvToolboxLock.
    """
    data = yaml.safe_load(path.read_text()) or {}
    version = int(data.get('version', 1))
    environments = {
        name: EnvironmentLock(
            uv_lock=env_data.get('uv_lock') if version == LOCKFILE_VERSION else None,
            fingerprint=env_data.get('fingerprint'),
            legacy_requirements=env_data.get('requirements'),
        )
        for name, env_data in (data.get('environments') or {}).items()
    }
    return UvToolboxLock(version=version, environments=environments)
