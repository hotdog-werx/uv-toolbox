from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from uv_toolbox.errors import (
    EnvironmentNotFoundError,
    MultipleEnvironmentsError,
)
from uv_toolbox.lockfile import EnvironmentLock, UvToolboxLock, write_lockfile
from uv_toolbox.settings import UvToolboxEnvironment, UvToolboxSettings


def _make_settings(
    *,
    envs: list[dict[str, object]],
    venv_path: Path | None = None,
    default_environment: str | None = None,
) -> UvToolboxSettings:
    payload: dict[str, object] = {
        'environments': envs,
    }
    if venv_path is not None:
        payload['venv_path'] = venv_path
    if default_environment is not None:
        payload['default_environment'] = default_environment
    return UvToolboxSettings.model_validate(payload)


@pytest.mark.parametrize(
    ('requirements', 'requirements_file'),
    [
        (None, None),
        ('ruff', Path('requirements.txt')),
    ],
)
def test_environment_requires_exactly_one_requirements(
    requirements: str | None,
    requirements_file: Path | None,
) -> None:
    """Validation rejects both the neither-set and both-set combinations for requirements/requirements_file."""
    with pytest.raises(ValueError, match='Exactly one of requirements'):
        UvToolboxEnvironment(
            name='env1',
            requirements=requirements,
            requirements_file=requirements_file,
        )


def test_environment_paths_from_settings(tmp_path: Path) -> None:
    """Venv path uses content-addressed storage: a 12-char hash directory under the configured venv_path."""
    settings = _make_settings(
        venv_path=tmp_path / '.uv-toolbox',
        envs=[{'name': 'env1', 'requirements': 'ruff'}],
    )
    env = settings.environments[0]

    venv_path = env.venv_path(settings=settings)
    assert venv_path.parent == tmp_path / '.uv-toolbox'
    assert len(venv_path.name) == 12


def test_environment_executable_configuration_defaults_to_auto_discovery() -> None:
    """Executable discovery defaults to automatic mode with no omissions."""
    env = UvToolboxEnvironment(name='env1', requirements='ruff')

    assert env.executables_override is None
    assert env.omit_executables == []


def test_environment_warns_for_legacy_executables_alias() -> None:
    """The former executables key warns and remains an alias for the override."""
    with pytest.warns(
        FutureWarning,
        match=r"'executables'.*deprecated.*removed in uv-toolbox 1\.0",
    ):
        env = UvToolboxEnvironment.model_validate(
            {'name': 'env1', 'requirements': 'ruff', 'executables': ['ruff']},
        )

    assert env.executables_override == ['ruff']


def test_environment_executables_override_does_not_warn() -> None:
    """The replacement executable override key does not emit a warning."""
    with warnings.catch_warnings():
        warnings.simplefilter('error')
        env = UvToolboxEnvironment.model_validate(
            {
                'name': 'env1',
                'requirements': 'ruff',
                'executables_override': ['ruff'],
            },
        )

    assert env.executables_override == ['ruff']


def test_environment_configured_env_expands_vars(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Configured env expands shell variable references without adding VIRTUAL_ENV."""
    monkeypatch.setenv('TEST_ROOT', str(tmp_path))
    settings = _make_settings(
        venv_path=tmp_path / '.uv-toolbox',
        envs=[
            {
                'name': 'env1',
                'requirements': 'ruff',
                'environment': {'TOOLS': '$TEST_ROOT/tools'},
            },
        ],
    )
    env = settings.environments[0]

    configured_env = env.configured_env()

    assert Path(configured_env['TOOLS']) == tmp_path / 'tools'
    assert 'VIRTUAL_ENV' not in configured_env


def test_environment_process_env_includes_configured_env_and_virtual_env(tmp_path: Path) -> None:
    """Process env combines configured variables with the materialized venv path."""
    settings = _make_settings(
        venv_path=tmp_path / '.uv-toolbox',
        envs=[
            {
                'name': 'env1',
                'requirements': 'ruff',
                'environment': {'TOOLS': 'tools'},
            },
        ],
    )
    env = settings.environments[0]

    process_env = env.process_env(settings=settings)

    assert process_env['TOOLS'] == 'tools'
    virtual_env = Path(process_env['VIRTUAL_ENV'])
    assert virtual_env.parent == tmp_path / '.uv-toolbox'
    assert len(virtual_env.name) == 12


def test_settings_reject_duplicate_env_names() -> None:
    """Settings validation rejects configs with two environments sharing the same name."""
    with pytest.raises(ValueError, match='Duplicate environment names'):
        _make_settings(
            envs=[
                {'name': 'env1', 'requirements': 'ruff'},
                {'name': 'env1', 'requirements': 'black'},
            ],
        )


def test_settings_reject_invalid_default_environment() -> None:
    """Settings validation rejects a default_environment that doesn't match any configured environment name."""
    with pytest.raises(ValueError, match='Default environment'):
        _make_settings(
            envs=[{'name': 'env1', 'requirements': 'ruff'}],
            default_environment='env2',
        )


def test_select_environment_by_name() -> None:
    """select_environment returns the environment whose name matches the given argument."""
    settings = _make_settings(
        envs=[
            {'name': 'env1', 'requirements': 'ruff'},
            {'name': 'env2', 'requirements': 'black'},
        ],
    )

    env = settings.select_environment('env2')

    assert env.name == 'env2'


def test_select_environment_uses_default() -> None:
    """select_environment falls back to the configured default_environment when no name is supplied."""
    settings = _make_settings(
        envs=[
            {'name': 'env1', 'requirements': 'ruff'},
            {'name': 'env2', 'requirements': 'black'},
        ],
        default_environment='env1',
    )

    env = settings.select_environment(None)

    assert env.name == 'env1'


def test_select_environment_single_env_without_name() -> None:
    """select_environment returns the only environment when there is exactly one, with no name required."""
    settings = _make_settings(envs=[{'name': 'env1', 'requirements': 'ruff'}])

    env = settings.select_environment(None)

    assert env.name == 'env1'


def test_select_environment_missing_name() -> None:
    """select_environment raises EnvironmentNotFoundError when the given name does not match any environment."""
    settings = _make_settings(envs=[{'name': 'env1', 'requirements': 'ruff'}])

    with pytest.raises(EnvironmentNotFoundError):
        settings.select_environment('missing')


def test_select_environment_requires_name_with_multiple_envs() -> None:
    """select_environment raises MultipleEnvironmentsError when multiple envs exist and no name is given."""
    settings = _make_settings(
        envs=[
            {'name': 'env1', 'requirements': 'ruff'},
            {'name': 'env2', 'requirements': 'black'},
        ],
    )

    with pytest.raises(MultipleEnvironmentsError):
        settings.select_environment(None)


# ── lockfile_path ─────────────────────────────────────────────────────────────


def test_lockfile_path_is_none_without_config_file() -> None:
    """lockfile_path is None when settings were not loaded from a config file on disk."""
    settings = _make_settings(envs=[{'name': 'env1', 'requirements': 'ruff'}])
    assert settings.lockfile_path is None


def test_lockfile_path_is_sibling_of_config_file(tmp_path: Path) -> None:
    """lockfile_path resolves to `uv-toolbox.lock` next to the config file that produced the settings."""
    config = tmp_path / 'uv-toolbox.yaml'
    config.write_text('environments:\n  - name: env1\n    requirements: ruff\n')
    settings = UvToolboxSettings.model_validate(
        {
            'config_file': config,
            'environments': [{'name': 'env1', 'requirements': 'ruff'}],
        },
    )
    assert settings.lockfile_path == tmp_path / 'uv-toolbox.lock'


# ── inject_resolved_requirements ──────────────────────────────────────────────


def test_inject_resolved_requirements_populates_env(tmp_path: Path) -> None:
    """Loading settings with a config_file and matching lockfile injects resolved requirements into the environment."""
    lock = UvToolboxLock(
        environments={'env1': EnvironmentLock(requirements='ruff==0.14.14\n')},
    )
    lock_path = tmp_path / 'uv-toolbox.lock'
    write_lockfile(lock, lock_path)

    config = tmp_path / 'uv-toolbox.yaml'
    config.write_text('environments:\n  - name: env1\n    requirements: ruff\n')

    settings = UvToolboxSettings.model_validate(
        {
            'config_file': config,
            'environments': [{'name': 'env1', 'requirements': 'ruff'}],
        },
    )

    env = settings.environments[0]
    assert env._resolved_requirements is not None
    assert 'ruff==0.14.14' in env._resolved_requirements


def test_inject_resolved_requirements_skipped_when_no_lockfile(
    tmp_path: Path,
) -> None:
    """_resolved_requirements stays None when no lockfile exists next to the config file."""
    config = tmp_path / 'uv-toolbox.yaml'
    config.write_text('environments:\n  - name: env1\n    requirements: ruff\n')

    settings = UvToolboxSettings.model_validate(
        {
            'config_file': config,
            'environments': [{'name': 'env1', 'requirements': 'ruff'}],
        },
    )

    env = settings.environments[0]
    assert env._resolved_requirements is None


def test_inject_resolved_requirements_ignores_missing_env_in_lockfile(
    tmp_path: Path,
) -> None:
    """_resolved_requirements stays None when the lockfile exists but has no entry for this environment's name."""
    lock = UvToolboxLock(
        environments={'other': EnvironmentLock(requirements='black==24.0.0\n')},
    )
    lock_path = tmp_path / 'uv-toolbox.lock'
    write_lockfile(lock, lock_path)

    config = tmp_path / 'uv-toolbox.yaml'
    config.write_text('environments:\n  - name: env1\n    requirements: ruff\n')

    settings = UvToolboxSettings.model_validate(
        {
            'config_file': config,
            'environments': [{'name': 'env1', 'requirements': 'ruff'}],
        },
    )

    env = settings.environments[0]
    assert env._resolved_requirements is None


# ── CAS hash with resolved requirements ───────────────────────────────────────


def test_venv_path_changes_when_resolved_requirements_set(
    tmp_path: Path,
) -> None:
    """The content-addressed venv path changes once resolved (locked) requirements are injected, vs. unresolved."""
    settings_no_lock = _make_settings(
        venv_path=tmp_path / '.uv-toolbox',
        envs=[{'name': 'env1', 'requirements': 'ruff'}],
    )
    path_without_lock = settings_no_lock.environments[0].venv_path(
        settings=settings_no_lock,
    )

    lock = UvToolboxLock(
        environments={
            'env1': EnvironmentLock(
                requirements='ruff==0.14.14 \\\n    --hash=sha256:aaaa\n',
            ),
        },
    )
    lock_path = tmp_path / 'uv-toolbox.lock'
    write_lockfile(lock, lock_path)
    config = tmp_path / 'uv-toolbox.yaml'
    config.write_text('environments:\n  - name: env1\n    requirements: ruff\n')

    settings_with_lock = UvToolboxSettings.model_validate(
        {
            'config_file': config,
            'venv_path': tmp_path / '.uv-toolbox',
            'environments': [{'name': 'env1', 'requirements': 'ruff'}],
        },
    )
    path_with_lock = settings_with_lock.environments[0].venv_path(
        settings=settings_with_lock,
    )

    assert path_without_lock != path_with_lock


# ── _normalize_resolved_requirements ─────────────────────────────────────────


def test_normalize_resolved_requirements_strips_comments_and_blanks() -> None:
    """_normalize_resolved_requirements strips `#` comment lines and blank lines from compiled requirements."""
    env = UvToolboxEnvironment(name='e', requirements='ruff')
    raw = '# generated by uv\nruff==0.14.14 \\\n    --hash=sha256:aaaa\n\n'
    result = env._normalize_resolved_requirements(raw)
    assert '# generated' not in result
    assert '' not in result.split('\n')


def test_normalize_resolved_requirements_preserves_hash_continuation_order() -> None:
    """_normalize_resolved_requirements keeps `--hash` continuation lines attached to their package line, in order."""
    env = UvToolboxEnvironment(name='e', requirements='ruff')
    raw = 'ruff==0.14.14 \\\n    --hash=sha256:aaaa \\\n    --hash=sha256:bbbb\n'
    result = env._normalize_resolved_requirements(raw)
    lines = result.splitlines()
    assert lines[0].startswith('ruff==')
    assert lines[1].strip().startswith('--hash=sha256:aaaa')
    assert lines[2].strip().startswith('--hash=sha256:bbbb')


# ── lock_fingerprint ─────────────────────────────────────────────────────────


def test_lock_fingerprint_ignores_order_comments_and_blank_lines() -> None:
    """Cosmetic edits to requirements do not change the fingerprint."""
    plain = UvToolboxEnvironment(name='env1', requirements='ruff\nblack\n')
    cosmetic = UvToolboxEnvironment(name='env1', requirements='# tools\nblack\n\n  ruff\n')

    assert plain.lock_fingerprint() == cosmetic.lock_fingerprint()
    assert plain.lock_fingerprint().startswith('sha256:')


@pytest.mark.parametrize(
    'changed',
    [
        {'requirements': 'ruff>=0.15'},
        {'requirements': 'ruff', 'environment': {'UV_INDEX_URL': 'https://example.invalid'}},
    ],
    ids=['requirements', 'environment'],
)
def test_lock_fingerprint_changes_with_resolution_inputs(changed: dict[str, object]) -> None:
    """Changing requirements or configured environment variables changes the fingerprint."""
    base = UvToolboxEnvironment(name='env1', requirements='ruff')
    modified = UvToolboxEnvironment.model_validate({'name': 'env1', **changed})

    assert modified.lock_fingerprint() != base.lock_fingerprint()


def test_lock_fingerprint_uses_unexpanded_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fingerprints hash the configured variable text, so machine-specific expansions do not cause re-locks."""
    env = UvToolboxEnvironment(
        name='env1',
        requirements='ruff',
        environment={'UV_CACHE_DIR': '$HOME/.cache'},
    )
    monkeypatch.setenv('HOME', '/home/one')
    first = env.lock_fingerprint()
    monkeypatch.setenv('HOME', '/home/two')

    assert env.lock_fingerprint() == first


def test_lock_fingerprint_tracks_requirements_file_contents(tmp_path: Path) -> None:
    """A requirements_file environment's fingerprint follows the file's contents, not its path."""
    req_file = tmp_path / 'requirements.txt'
    req_file.write_text('ruff\n')
    env = UvToolboxEnvironment(name='env1', requirements_file=req_file)
    inline = UvToolboxEnvironment(name='env1', requirements='ruff')

    assert env.lock_fingerprint() == inline.lock_fingerprint()
    req_file.write_text('ruff>=0.15\n')
    assert env.lock_fingerprint() != inline.lock_fingerprint()


def test_apply_lock_replaces_and_clears_resolved_requirements() -> None:
    """apply_lock sets each environment's resolved requirements, clearing those absent from the lock."""
    settings = _make_settings(
        envs=[
            {'name': 'env1', 'requirements': 'ruff'},
            {'name': 'env2', 'requirements': 'black'},
        ],
    )
    settings.environments[1]._resolved_requirements = 'black==24.0.0'

    settings.apply_lock(
        UvToolboxLock(environments={'env1': EnvironmentLock(requirements='ruff==0.15.0')}),
    )

    assert settings.environments[0].resolved_requirements == 'ruff==0.15.0'
    assert settings.environments[1]._resolved_requirements is None


def test_inject_resolved_requirements_applies_stale_entries(tmp_path: Path) -> None:
    """Loading settings still applies entries with a missing fingerprint, so read-only commands keep working."""
    write_lockfile(
        UvToolboxLock(environments={'env1': EnvironmentLock(requirements='ruff==0.14.14\n')}),
        tmp_path / 'uv-toolbox.lock',
    )
    config = tmp_path / 'uv-toolbox.yaml'
    config.write_text('environments:\n  - name: env1\n    requirements: ruff>=0.15\n')

    settings = UvToolboxSettings.model_validate(
        {
            'config_file': config,
            'environments': [{'name': 'env1', 'requirements': 'ruff>=0.15'}],
        },
    )

    assert settings.environments[0].resolved_requirements == 'ruff==0.14.14\n'
