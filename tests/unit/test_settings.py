from __future__ import annotations

import json
import warnings
from pathlib import Path

import pytest

from uv_toolbox.errors import (
    EnvironmentNotFoundError,
    MultipleEnvironmentsError,
)
from uv_toolbox.lockfile import EnvironmentLock, UvToolboxLock, write_lockfile
from uv_toolbox.settings import (
    UvSource,
    UvToolboxEnvironment,
    UvToolboxSettings,
    _is_local_requirement,
    locked_packages_digest,
)


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
    'declared',
    [
        {},
        {'requirements': 'ruff', 'requirements_file': Path('requirements.txt')},
        {'dependencies': ['ruff'], 'requirements': 'ruff'},
    ],
    ids=['none', 'both-deprecated', 'dependencies-and-requirements'],
)
@pytest.mark.filterwarnings('ignore::FutureWarning')
def test_environment_requires_exactly_one_way_of_declaring_dependencies(declared: dict[str, object]) -> None:
    """Validation rejects environments declaring no dependencies, or declaring them more than one way."""
    with pytest.raises(ValueError, match='Exactly one of dependencies, requirements or requirements_file'):
        UvToolboxEnvironment.model_validate({'name': 'env1', **declared})


def test_environment_paths_from_settings(tmp_path: Path) -> None:
    """Venv path uses content-addressed storage: a 12-char hash directory under the configured venv_path."""
    settings = _make_settings(
        venv_path=tmp_path / '.uv-toolbox',
        envs=[{'name': 'env1', 'dependencies': ['ruff']}],
    )
    env = settings.environments[0]

    venv_path = env.venv_path(settings=settings)
    assert venv_path.parent == tmp_path / '.uv-toolbox'
    assert len(venv_path.name) == 12


def test_environment_executable_configuration_defaults_to_auto_discovery() -> None:
    """Executable discovery defaults to automatic mode with no omissions."""
    env = UvToolboxEnvironment(name='env1', dependencies=['ruff'])

    assert env.executables_override is None
    assert env.omit_executables == []


def test_environment_warns_for_legacy_executables_alias() -> None:
    """The former executables key warns and remains an alias for the override."""
    with pytest.warns(
        FutureWarning,
        match=r"'executables'.*deprecated.*removed in uv-toolbox 1\.0",
    ):
        env = UvToolboxEnvironment.model_validate(
            {'name': 'env1', 'dependencies': ['ruff'], 'executables': ['ruff']},
        )

    assert env.executables_override == ['ruff']


def test_environment_executables_override_does_not_warn() -> None:
    """The replacement executable override key does not emit a warning."""
    with warnings.catch_warnings():
        warnings.simplefilter('error')
        env = UvToolboxEnvironment.model_validate(
            {
                'name': 'env1',
                'dependencies': ['ruff'],
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
                'dependencies': ['ruff'],
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
                'dependencies': ['ruff'],
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
                {'name': 'env1', 'dependencies': ['ruff']},
                {'name': 'env1', 'dependencies': ['black']},
            ],
        )


def test_settings_reject_invalid_default_environment() -> None:
    """Settings validation rejects a default_environment that doesn't match any configured environment name."""
    with pytest.raises(ValueError, match='Default environment'):
        _make_settings(
            envs=[{'name': 'env1', 'dependencies': ['ruff']}],
            default_environment='env2',
        )


def test_select_environment_by_name() -> None:
    """select_environment returns the environment whose name matches the given argument."""
    settings = _make_settings(
        envs=[
            {'name': 'env1', 'dependencies': ['ruff']},
            {'name': 'env2', 'dependencies': ['black']},
        ],
    )

    env = settings.select_environment('env2')

    assert env.name == 'env2'


def test_select_environment_uses_default() -> None:
    """select_environment falls back to the configured default_environment when no name is supplied."""
    settings = _make_settings(
        envs=[
            {'name': 'env1', 'dependencies': ['ruff']},
            {'name': 'env2', 'dependencies': ['black']},
        ],
        default_environment='env1',
    )

    env = settings.select_environment(None)

    assert env.name == 'env1'


def test_select_environment_single_env_without_name() -> None:
    """select_environment returns the only environment when there is exactly one, with no name required."""
    settings = _make_settings(envs=[{'name': 'env1', 'dependencies': ['ruff']}])

    env = settings.select_environment(None)

    assert env.name == 'env1'


def test_select_environment_missing_name() -> None:
    """select_environment raises EnvironmentNotFoundError when the given name does not match any environment."""
    settings = _make_settings(envs=[{'name': 'env1', 'dependencies': ['ruff']}])

    with pytest.raises(EnvironmentNotFoundError):
        settings.select_environment('missing')


def test_select_environment_requires_name_with_multiple_envs() -> None:
    """select_environment raises MultipleEnvironmentsError when multiple envs exist and no name is given."""
    settings = _make_settings(
        envs=[
            {'name': 'env1', 'dependencies': ['ruff']},
            {'name': 'env2', 'dependencies': ['black']},
        ],
    )

    with pytest.raises(MultipleEnvironmentsError):
        settings.select_environment(None)


# ── lockfile_path ─────────────────────────────────────────────────────────────


def test_lockfile_path_is_none_without_config_file() -> None:
    """lockfile_path is None when settings were not loaded from a config file on disk."""
    settings = _make_settings(envs=[{'name': 'env1', 'dependencies': ['ruff']}])
    assert settings.lockfile_path is None


def test_lockfile_path_is_sibling_of_config_file(tmp_path: Path) -> None:
    """lockfile_path resolves to `uv-toolbox.lock` next to the config file that produced the settings."""
    config = tmp_path / 'uv-toolbox.yaml'
    config.write_text('environments:\n  - name: env1\n    dependencies: [ruff]\n', encoding='utf-8')
    settings = UvToolboxSettings.model_validate(
        {
            'config_file': config,
            'environments': [{'name': 'env1', 'dependencies': ['ruff']}],
        },
    )
    assert settings.lockfile_path == tmp_path / 'uv-toolbox.lock'


# ── new configuration format ─────────────────────────────────────────────────


def test_environment_accepts_structured_configuration() -> None:
    """Dependencies, sources, requires_python and the uv passthrough validate (kebab-case keys too)."""
    with warnings.catch_warnings():
        warnings.simplefilter('error')
        env = UvToolboxEnvironment.model_validate(
            {
                'name': 'env1',
                'requires-python': '>=3.12',
                'dependencies': ['tool>=1', 'other @ git+https://example.com/other.git@v1'],
                'sources': {
                    'tool': {'git': 'https://example.com/tool.git', 'tag': 'v1', 'subdirectory': 'pkg'},
                    'multi': [
                        {'index': 'internal', 'marker': "sys_platform == 'linux'"},
                        {'path': 'vendor/multi'},
                    ],
                },
                'uv': {'constraint-dependencies': ['pydantic<3']},
            },
        )

    assert env.requires_python == '>=3.12'
    assert not env.uses_deprecated_requirements
    assert env.sources_data() == {
        'tool': {'git': 'https://example.com/tool.git', 'tag': 'v1', 'subdirectory': 'pkg'},
        'multi': [{'index': 'internal', 'marker': "sys_platform == 'linux'"}, {'path': 'vendor/multi'}],
    }


@pytest.mark.parametrize(
    ('field', 'value', 'message'),
    [
        ('dependencies', ['not a requirement !!'], 'Invalid dependency'),
        ('requires_python', 'three twelve', 'Invalid requires_python'),
        ('uv', {'package': True}, "'package' key cannot be set"),
        ('uv', {'sources': {}}, "top-level 'sources' key"),
    ],
)
def test_environment_rejects_invalid_structured_values(field: str, value: object, message: str) -> None:
    """Invalid dependencies, specifiers and managed passthrough keys are rejected with clear errors."""
    payload: dict[str, object] = {'name': 'env1', 'dependencies': ['ruff'], field: value}

    with pytest.raises(ValueError, match=message):
        UvToolboxEnvironment.model_validate(payload)


@pytest.mark.parametrize(
    ('source', 'message'),
    [
        ({}, 'exactly one of git, path, url or index'),
        ({'git': 'https://example.com/x.git', 'path': 'x'}, 'exactly one of git, path, url or index'),
        ({'path': 'x', 'tag': 'v1'}, "'tag' is only valid for git sources"),
        ({'git': 'https://example.com/x.git', 'tag': 'v1', 'rev': 'abc'}, 'only one of tag, branch or rev'),
        ({'git': 'https://example.com/x.git', 'unknown': 1}, 'Extra inputs are not permitted'),
    ],
)
def test_source_validation(source: dict[str, object], message: str) -> None:
    """Sources must name exactly one location, and refs only apply to git sources."""
    with pytest.raises(ValueError, match=message):
        UvSource.model_validate(source)


@pytest.mark.filterwarnings('ignore::FutureWarning')
def test_sources_require_dependencies() -> None:
    """Sources cannot be combined with the deprecated requirements keys."""
    with pytest.raises(ValueError, match='sources can only be used with dependencies'):
        UvToolboxEnvironment.model_validate(
            {'name': 'env1', 'requirements': 'tool', 'sources': {'tool': {'path': 'tool'}}},
        )


@pytest.mark.parametrize(
    'declared',
    [{'requirements': 'ruff'}, {'requirements-file': 'requirements.txt'}],
    ids=['requirements', 'requirements_file'],
)
def test_deprecated_requirements_warn(declared: dict[str, object]) -> None:
    """The deprecated keys still validate but warn, pointing at the replacement and migrate-config."""
    with pytest.warns(FutureWarning, match=r"'dependencies' and 'sources'.*uvtb migrate-config"):
        env = UvToolboxEnvironment.model_validate({'name': 'env1', **declared})

    assert env.uses_deprecated_requirements


@pytest.mark.filterwarnings('ignore::FutureWarning')
def test_relative_requirements_file_resolves_against_config_dir(tmp_path: Path) -> None:
    """A relative requirements_file is read from the config file's directory, whatever the cwd."""
    (tmp_path / 'requirements.txt').write_text('ruff\n', encoding='utf-8')
    settings = UvToolboxSettings.model_validate(
        {
            'config_file': tmp_path / 'uv-toolbox.yaml',
            'environments': [{'name': 'env1', 'requirements_file': 'requirements.txt'}],
        },
    )

    assert settings.environments[0].requirements_file == tmp_path / 'requirements.txt'
    assert settings.environments[0].requirements_text() == 'ruff\n'


def test_config_dir_defaults_to_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a known config file, relative paths resolve against the working directory."""
    monkeypatch.chdir(tmp_path)

    assert _make_settings(envs=[{'name': 'env1', 'dependencies': ['ruff']}]).config_dir == tmp_path


@pytest.mark.parametrize('suffix', ['.yaml', '.json'])
def test_config_files_are_read_as_utf8(tmp_path: Path, suffix: str) -> None:
    """Non-ASCII config content survives on platforms whose default encoding isn't UTF-8."""
    value = 'café — ✓'
    config = tmp_path / f'uv-toolbox{suffix}'
    if suffix == '.yaml':
        config.write_text(
            f'# Tools — formatting\nenvironments:\n  - name: env1\n    dependencies: [ruff]\n'
            f'    environment:\n      NOTE: "{value}"\n',
            encoding='utf-8',
        )
    else:
        config.write_text(
            json.dumps(
                {'environments': [{'name': 'env1', 'dependencies': ['ruff'], 'environment': {'NOTE': value}}]},
                ensure_ascii=False,
            ),
            encoding='utf-8',
        )

    settings = UvToolboxSettings.model_validate({'config_file': config})

    assert settings.environments[0].environment == {'NOTE': value}


def test_project_dir_is_next_to_config_file(tmp_path: Path) -> None:
    """Each environment's generated project lives under .uv-toolbox/projects with a filesystem-safe name."""
    settings = UvToolboxSettings.model_validate(
        {
            'config_file': tmp_path / 'uv-toolbox.yaml',
            'environments': [{'name': 'lint/fmt tools', 'dependencies': ['ruff']}],
        },
    )

    assert settings.project_dir(settings.environments[0]) == tmp_path / '.uv-toolbox' / 'projects' / 'lint_fmt_tools'


# ── injected locks ───────────────────────────────────────────────────────────


_UV_LOCK = """version = 1
revision = 3
requires-python = ">=3.12"

[[package]]
name = "ruff"
version = "0.14.14"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "uv-toolbox-env"
version = "0.0.0"
source = { virtual = "." }
dependencies = [{ name = "ruff" }]
"""


def _settings_with_lock(
    tmp_path: Path, lock: UvToolboxLock, dependencies: list[str] | None = None
) -> UvToolboxSettings:
    write_lockfile(lock, tmp_path / 'uv-toolbox.lock')
    return UvToolboxSettings.model_validate(
        {
            'config_file': tmp_path / 'uv-toolbox.yaml',
            'venv_path': tmp_path / '.venvs',
            'environments': [{'name': 'env1', 'dependencies': dependencies or ['ruff']}],
        },
    )


def test_inject_locked_environments_populates_env(tmp_path: Path) -> None:
    """Settings loaded next to a repo lockfile carry each environment's uv.lock."""
    settings = _settings_with_lock(tmp_path, UvToolboxLock(environments={'env1': EnvironmentLock(uv_lock=_UV_LOCK)}))

    assert settings.environments[0].locked_uv_lock == _UV_LOCK


def test_inject_locked_environments_skips_missing_entries(tmp_path: Path) -> None:
    """Environments absent from the lockfile, or without a lockfile at all, have no injected lock."""
    settings = _settings_with_lock(tmp_path, UvToolboxLock(environments={'other': EnvironmentLock(uv_lock=_UV_LOCK)}))
    assert settings.environments[0].locked_uv_lock is None

    (tmp_path / 'uv-toolbox.lock').unlink()
    settings = UvToolboxSettings.model_validate(
        {'config_file': tmp_path / 'uv-toolbox.yaml', 'environments': [{'name': 'env1', 'dependencies': ['ruff']}]},
    )
    assert settings.environments[0].locked_uv_lock is None


def test_inject_locked_environments_applies_stale_entries(tmp_path: Path) -> None:
    """Entries whose fingerprint no longer matches are still applied, so read-only commands keep working."""
    settings = _settings_with_lock(
        tmp_path,
        UvToolboxLock(environments={'env1': EnvironmentLock(uv_lock=_UV_LOCK, fingerprint='sha256:old')}),
        dependencies=['ruff>=0.15'],
    )

    assert settings.environments[0].locked_uv_lock == _UV_LOCK


def test_apply_lock_replaces_and_clears_locked_environments() -> None:
    """apply_lock sets each environment's uv.lock, clearing those absent from the lock."""
    settings = _make_settings(
        envs=[
            {'name': 'env1', 'dependencies': ['ruff']},
            {'name': 'env2', 'dependencies': ['black']},
        ],
    )
    settings.environments[1]._locked_uv_lock = _UV_LOCK

    settings.apply_lock(UvToolboxLock(environments={'env1': EnvironmentLock(uv_lock=_UV_LOCK)}))

    assert settings.environments[0].locked_uv_lock == _UV_LOCK
    assert settings.environments[1].locked_uv_lock is None


# ── CAS addressing ───────────────────────────────────────────────────────────


def test_venv_path_changes_when_lock_is_injected(tmp_path: Path) -> None:
    """The content address moves from the declared inputs to the locked packages once a lock exists."""
    settings_no_lock = _make_settings(venv_path=tmp_path / '.venvs', envs=[{'name': 'env1', 'dependencies': ['ruff']}])
    settings_with_lock = _settings_with_lock(
        tmp_path,
        UvToolboxLock(environments={'env1': EnvironmentLock(uv_lock=_UV_LOCK)}),
    )

    assert settings_no_lock.environments[0].venv_path(settings_no_lock) != settings_with_lock.environments[
        0
    ].venv_path(settings_with_lock)


def test_venv_path_ignores_uv_lock_format_changes() -> None:
    """Changing uv's lock revision, wheel metadata or the virtual root package keeps the same venv."""
    reformatted = (
        _UV_LOCK.replace('revision = 3', 'revision = 4')
        .replace('dependencies = [{ name = "ruff" }]', 'dependencies = [{ name = "ruff" }]\n\n[package.metadata]')
        .replace(
            'source = { registry = "https://pypi.org/simple" }',
            'source = { registry = "https://pypi.org/simple" }\nwheels = [{ url = "https://x/ruff.whl" }]',
        )
    )

    assert locked_packages_digest(reformatted) == locked_packages_digest(_UV_LOCK)
    assert locked_packages_digest(_UV_LOCK.replace('0.14.14', '0.15.0')) != locked_packages_digest(_UV_LOCK)


def test_identical_locks_share_a_venv(tmp_path: Path) -> None:
    """Environments locked to the same packages share a content address, whatever their name or config."""
    first = UvToolboxEnvironment(name='a', dependencies=['ruff'])
    second = UvToolboxEnvironment(name='b', dependencies=['ruff>=0.14'])
    first._locked_uv_lock = _UV_LOCK
    second._locked_uv_lock = _UV_LOCK
    settings = _make_settings(venv_path=tmp_path, envs=[{'name': 'a', 'dependencies': ['ruff']}])

    assert first.venv_path(settings) == second.venv_path(settings)


def _checkout(root: Path, env: dict[str, object], uv_lock: str | None = None) -> Path:
    """Return the venv path an environment gets in a checkout at root (shared venv root)."""
    root.mkdir(parents=True, exist_ok=True)
    if uv_lock is not None:
        write_lockfile(
            UvToolboxLock(environments={'env1': EnvironmentLock(uv_lock=uv_lock)}), root / 'uv-toolbox.lock'
        )
    settings = UvToolboxSettings.model_validate(
        {
            'config_file': root / 'uv-toolbox.yaml',
            'venv_path': root.parent / 'shared-venvs',
            'environments': [{'name': 'env1', **env}],
        },
    )
    return settings.environments[0].venv_path(settings)


_LOCAL_UV_LOCK = _UV_LOCK.replace(
    'source = { registry = "https://pypi.org/simple" }',
    'source = { editable = "../../../plugins/ruff" }',
)


@pytest.mark.filterwarnings('ignore::FutureWarning')
@pytest.mark.parametrize(
    ('env', 'uv_lock'),
    [
        ({'dependencies': ['tool'], 'sources': {'tool': {'path': 'plugins/tool', 'editable': True}}}, None),
        ({'dependencies': ['ruff']}, _LOCAL_UV_LOCK),
        ({'requirements': '-e ./plugins/tool\n'}, None),
    ],
    ids=['declared-path-source', 'locked-editable', 'deprecated-editable'],
)
def test_local_paths_give_each_checkout_its_own_venv(
    tmp_path: Path,
    env: dict[str, object],
    uv_lock: str | None,
) -> None:
    """Relative local paths read the same in every clone or worktree, but must not share a venv."""
    first = _checkout(tmp_path / 'checkout-a', env, uv_lock)
    second = _checkout(tmp_path / 'checkout-b', env, uv_lock)

    assert first != second


@pytest.mark.filterwarnings('ignore::FutureWarning')
@pytest.mark.parametrize(
    ('env', 'uv_lock'),
    [
        ({'dependencies': ['tool'], 'sources': {'tool': {'git': 'https://example.com/tool.git', 'tag': 'v1'}}}, None),
        ({'dependencies': ['ruff']}, _UV_LOCK),
        ({'requirements': 'ruff\n'}, None),
    ],
    ids=['declared-git-source', 'locked-registry', 'deprecated-registry'],
)
def test_environments_without_local_paths_share_venvs_across_projects(
    tmp_path: Path,
    env: dict[str, object],
    uv_lock: str | None,
) -> None:
    """Without local paths, identical environments in different projects keep sharing one venv."""
    assert _checkout(tmp_path / 'project-a', env, uv_lock) == _checkout(tmp_path / 'project-b', env, uv_lock)


def test_locked_packages_digest_makes_local_sources_absolute(tmp_path: Path) -> None:
    """Local lock sources are resolved against the generated project; other sources are untouched."""
    project_dir = tmp_path / 'repo' / '.uv-toolbox' / 'projects' / 'env1'

    digest = locked_packages_digest(_LOCAL_UV_LOCK, project_dir=project_dir)

    assert (tmp_path / 'repo' / 'plugins' / 'ruff').as_posix() in digest
    assert locked_packages_digest(_UV_LOCK, project_dir=project_dir) == locked_packages_digest(_UV_LOCK)


@pytest.mark.parametrize(
    ('line', 'local'),
    [
        ('-e ./plugin', True),
        ('--editable=../plugin', True),
        ('./plugin', True),
        ('/abs/plugin', True),
        ('plugin @ file:///abs/plugin', True),
        ('C:\\plugins\\tool', True),
        ('ruff>=0.14', False),
        ('git+https://example.com/tool.git@v1#subdirectory=pkg', False),
        ('-r requirements.txt', False),
        ('e-tool', False),
    ],
)
def test_is_local_requirement(line: str, *, local: bool) -> None:
    """Deprecated requirement lines that install from local paths are recognized."""
    assert _is_local_requirement(line) is local


# ── lock_fingerprint ─────────────────────────────────────────────────────────


@pytest.mark.filterwarnings('ignore::FutureWarning')
def test_lock_fingerprint_ignores_order_comments_and_blank_lines_in_requirements() -> None:
    """Cosmetic edits to deprecated requirements do not change the fingerprint."""
    plain = UvToolboxEnvironment(name='env1', requirements='ruff\nblack\n')
    cosmetic = UvToolboxEnvironment(name='env1', requirements='# tools\nblack\n\n  ruff\n')

    assert plain.lock_fingerprint() == cosmetic.lock_fingerprint()
    assert plain.lock_fingerprint().startswith('sha256:')


@pytest.mark.parametrize(
    'changed',
    [
        {'dependencies': ['ruff>=0.15']},
        {'sources': {'ruff': {'git': 'https://example.com/ruff.git'}}},
        {'requires_python': '>=3.13'},
        {'uv': {'prerelease': 'allow'}},
        {'environment': {'UV_INDEX_URL': 'https://example.invalid'}},
    ],
    ids=['dependencies', 'sources', 'requires_python', 'uv', 'environment'],
)
def test_lock_fingerprint_changes_with_resolution_inputs(changed: dict[str, object]) -> None:
    """Changing any input that affects resolution changes the fingerprint."""
    base = UvToolboxEnvironment(name='env1', dependencies=['ruff'])
    modified = UvToolboxEnvironment.model_validate({'name': 'env1', 'dependencies': ['ruff'], **changed})

    assert modified.lock_fingerprint() != base.lock_fingerprint()


def test_lock_fingerprint_uses_unexpanded_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fingerprints hash the configured variable text, so machine-specific expansions do not cause re-locks."""
    env = UvToolboxEnvironment(
        name='env1',
        dependencies=['ruff'],
        environment={'UV_CACHE_DIR': '$HOME/.cache'},
    )
    monkeypatch.setenv('HOME', '/home/one')
    first = env.lock_fingerprint()
    monkeypatch.setenv('HOME', '/home/two')

    assert env.lock_fingerprint() == first


@pytest.mark.filterwarnings('ignore::FutureWarning')
def test_lock_fingerprint_tracks_requirements_file_contents(tmp_path: Path) -> None:
    """A requirements_file environment's fingerprint follows the file's contents, not its path."""
    req_file = tmp_path / 'requirements.txt'
    req_file.write_text('ruff\n', encoding='utf-8')
    env = UvToolboxEnvironment(name='env1', requirements_file=req_file)
    inline = UvToolboxEnvironment(name='env1', requirements='ruff')

    assert env.lock_fingerprint() == inline.lock_fingerprint()
    req_file.write_text('ruff>=0.15\n', encoding='utf-8')
    assert env.lock_fingerprint() != inline.lock_fingerprint()
