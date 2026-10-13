"""Convert deprecated `requirements` / `requirements_file` environments to `dependencies`."""

from __future__ import annotations

import json
import tomllib
import typing
from dataclasses import dataclass

import yaml

from uv_toolbox.project import convert_requirements, resolve_requires_python, toml_key, toml_value

if typing.TYPE_CHECKING:  # pragma: no cover
    from pathlib import Path

    from uv_toolbox.settings import UvToolboxSettings

_DEPRECATED_KEYS = ('requirements', 'requirements_file', 'requirements-file')


class _IndentedDumper(yaml.SafeDumper):
    """Safe dumper that indents block sequences under their parent key."""

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:  # noqa: FBT001, FBT002, ARG002
        """Never emit indentless sequences."""
        super().increase_indent(flow=flow, indentless=False)


@dataclass
class Migration:
    """The result of converting a config file.

    Attributes:
        migrated: Names of the converted environments.
        content: The new config file content, or for pyproject.toml (which
            cannot be rewritten without losing unrelated content) the
            replacement `[[tool.uv-toolbox.environments]]` tables.
        rewritable: Whether content replaces the whole config file.
    """

    migrated: list[str]
    content: str
    rewritable: bool


def _load(config_file: Path) -> tuple[dict[str, typing.Any], dict[str, typing.Any]]:
    """Return the parsed config file and its uv-toolbox table."""
    text = config_file.read_text(encoding='utf-8')
    suffix = config_file.suffix.lower()
    if suffix in {'.yaml', '.yml'}:
        document = yaml.safe_load(text) or {}
    elif suffix == '.json':
        document = json.loads(text)
    else:
        document = tomllib.loads(text)
    if config_file.name == 'pyproject.toml':
        return document, document['tool']['uv-toolbox']
    return document, document


def _converted_environment(
    raw_env: dict[str, typing.Any],
    converted: dict[str, typing.Any],
) -> dict[str, typing.Any]:
    """Replace the deprecated keys of raw_env with the converted ones, in place order."""
    result: dict[str, typing.Any] = {}
    for key, value in raw_env.items():
        if key in _DEPRECATED_KEYS:
            result.update(converted)
        elif key != 'uv':
            result[key] = value
    return result


def _toml_tables(data: dict[str, typing.Any], header: str) -> list[str]:
    """Render a table's scalar keys, followed by its dict values as sub-tables."""
    lines = [f'{toml_key(key)} = {toml_value(value)}' for key, value in data.items() if not isinstance(value, dict)]
    for key, value in data.items():
        if isinstance(value, dict):
            lines.extend(['', f'[{header}.{toml_key(key)}]'])
            lines.extend(f'{toml_key(k)} = {toml_value(v)}' for k, v in value.items())
    return lines


def _render_toml_environments(environments: list[dict[str, typing.Any]], header: str) -> list[str]:
    lines: list[str] = []
    for env in environments:
        lines.extend(['', f'[[{header}]]', *_toml_tables(env, header)])
    return lines


def _render(config_file: Path, document: dict[str, typing.Any], migrated: list[dict[str, typing.Any]]) -> Migration:
    names = [env['name'] for env in migrated]
    suffix = config_file.suffix.lower()
    if config_file.name == 'pyproject.toml':
        content = '\n'.join(_render_toml_environments(migrated, 'tool.uv-toolbox.environments')).lstrip('\n') + '\n'
        return Migration(names, content, rewritable=False)
    if suffix in {'.yaml', '.yml'}:
        content = yaml.dump(
            document,
            Dumper=_IndentedDumper,
            sort_keys=False,
            default_flow_style=False,
            allow_unicode=True,
        )
    elif suffix == '.json':
        content = json.dumps(document, indent=2) + '\n'
    else:
        top = {key: value for key, value in document.items() if key != 'environments'}
        lines = _toml_tables(top, '')
        lines.extend(_render_toml_environments(document.get('environments', []), 'environments'))
        content = '\n'.join(lines).lstrip('\n') + '\n'
    return Migration(names, content, rewritable=True)


def migrate_config(settings: UvToolboxSettings) -> Migration:
    """Convert the deprecated environments of the active config file.

    Each `requirements` / `requirements_file` environment is converted with
    the same `uv add` conversion used when locking, into `dependencies`,
    `sources` and (for index and constraint options) the `uv` passthrough.

    Args:
        settings: UV toolbox settings loaded from the config file.

    Returns:
        The converted config.

    Raises:
        ValueError: If no config file is known.
    """
    config_file = settings.config_file
    if config_file is None:  # pragma: no cover - CLI always resolves a config file
        msg = 'No config file to migrate.'
        raise ValueError(msg)
    document, table = _load(config_file)
    envs_by_name = {env.name: env for env in settings.environments}
    migrated = []
    for index, raw_env in enumerate(table.get('environments', [])):
        if not any(key in raw_env for key in _DEPRECATED_KEYS):
            continue
        env = envs_by_name[raw_env['name']]
        requires_python = resolve_requires_python(env, settings, env.locked_uv_lock)
        spec = convert_requirements(env, settings, requires_python=requires_python)
        converted: dict[str, typing.Any] = {'dependencies': spec.dependencies}
        if spec.sources:
            converted['sources'] = spec.sources
        if spec.uv_settings:
            converted['uv'] = spec.uv_settings
        new_env = _converted_environment(raw_env, converted)
        table['environments'][index] = new_env
        migrated.append(new_env)
    return _render(config_file, document, migrated)
