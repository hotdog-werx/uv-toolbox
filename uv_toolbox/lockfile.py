from __future__ import annotations

import typing
from dataclasses import dataclass, field

import yaml

if typing.TYPE_CHECKING:
    from pathlib import Path


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
    """Resolved, pinned requirements for a single environment."""

    requirements: str


@dataclass
class UvToolboxLock:
    """In-memory representation of a uv-toolbox.lock file."""

    version: int = 1
    environments: dict[str, EnvironmentLock] = field(default_factory=dict)


def write_lockfile(lock: UvToolboxLock, path: Path) -> None:
    """Serialize a UvToolboxLock to a YAML file at path.

    Requirements strings are written as YAML literal block scalars (|) for
    human readability. sort_keys=False preserves dict insertion order so
    'version' always appears before 'environments'.

    Args:
        lock: The lockfile data to write.
        path: Destination path (created or overwritten).
    """
    data = {
        'version': lock.version,
        'environments': {
            name: {
                'requirements': _LiteralStr(
                    env_lock.requirements if env_lock.requirements.endswith('\n') else env_lock.requirements + '\n',
                ),
            }
            for name, env_lock in lock.environments.items()
        },
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

    PyYAML includes a trailing newline in | block scalar values; callers that
    use requirements for hashing should normalize via
    UvToolboxEnvironment._normalize_resolved_requirements.

    Args:
        path: Path to the lockfile.

    Returns:
        Parsed UvToolboxLock.
    """
    data = yaml.safe_load(path.read_text())
    version = int(data.get('version', 1))
    environments = {
        name: EnvironmentLock(requirements=env_data['requirements'])
        for name, env_data in (data.get('environments') or {}).items()
    }
    return UvToolboxLock(version=version, environments=environments)
