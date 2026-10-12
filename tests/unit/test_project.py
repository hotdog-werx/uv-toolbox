from __future__ import annotations

import tomllib
from datetime import date
from pathlib import Path
from textwrap import dedent
from typing import TYPE_CHECKING

import pytest
import yaml
from packaging.version import Version

from uv_toolbox import project
from uv_toolbox.errors import ExternalCommandError, UnsupportedRequirementError, UvVersionError
from uv_toolbox.project import (
    ProjectSpec,
    _index_env,
    _legacy_pins,
    check_environment,
    lock_environment,
    logical_requirement_lines,
    merge_uv_settings,
    parse_requirements,
    render_pyproject,
    resolve_requires_python,
    sync_environment,
)
from uv_toolbox.project import ensure_uv_version as real_ensure_uv_version
from uv_toolbox.settings import UvToolboxSettings

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pytest_mock import MockerFixture

_UV_LOCK = 'version = 1\nrevision = 3\nrequires-python = ">=3.12"\n'


def _settings(tmp_path: Path, **env: object) -> UvToolboxSettings:
    return UvToolboxSettings.model_validate(
        {
            'config_file': tmp_path / 'uv-toolbox.yaml',
            'venv_path': tmp_path / '.venvs',
            'environments': [{'name': 'tools', **(env or {'dependencies': ['ruff']})}],
        },
    )


def _fake_uv(mocker: MockerFixture, *, lock_content: str = _UV_LOCK, fail: Sequence[str] = ()):  # noqa: ANN202
    """Mock run_checked: `uv lock` writes uv.lock in its cwd; commands matching `fail` raise."""

    def run(args: Sequence[str], **kwargs: object) -> str:
        joined = ' '.join(args)
        if any(pattern in joined for pattern in fail):
            raise ExternalCommandError(cmd_args=args, returncode=1, stderr=f'{joined} failed')
        if list(args[:2]) == ['uv', 'lock'] and '--check' not in args:
            cwd = kwargs['cwd']
            assert isinstance(cwd, Path)
            (cwd / 'uv.lock').write_text(lock_content)
        if list(args[:3]) == ['uv', 'python', 'find']:
            return '3.13.3'
        return ''

    return mocker.patch('uv_toolbox.project.run_checked', side_effect=run)


def _commands(run_mock: object) -> list[list[str]]:
    return [list(call.kwargs['args']) for call in run_mock.call_args_list]  # ty: ignore[unresolved-attribute]


# ── requirements parsing ─────────────────────────────────────────────────────


def test_logical_requirement_lines_handles_comments_continuations_and_trailing_content() -> None:
    """Joins continuations and drops whole-line and trailing comments, keeping URL fragments."""
    content = '# comment\n\nroot-tool \\\n        >=1  # pinned\ngit+https://x/y.git@v1#subdirectory=pkg\ntrailing \\'

    assert list(logical_requirement_lines(content)) == [
        'root-tool >=1',
        'git+https://x/y.git@v1#subdirectory=pkg',
        'trailing',
    ]


def test_logical_requirement_lines_ends_continuation_at_blank_line() -> None:
    """A dangling continuation is closed by a blank or comment line."""
    assert list(logical_requirement_lines('first \\\n\n# note\nsecond\n')) == ['first', 'second']


def test_index_env_mirrors_converted_index_settings() -> None:
    """Converted index options are also passed to `uv add` through uv's environment variables."""
    env = _index_env(
        {
            'index': [{'url': 'https://a', 'default': True}, {'url': 'https://b'}, {'url': 'https://c'}],
            'find-links': ['https://f1', 'https://f2'],
            'no-index': True,
        },
    )

    assert env == {
        'UV_DEFAULT_INDEX': 'https://a',
        'UV_INDEX': 'https://b https://c',
        'UV_FIND_LINKS': 'https://f1,https://f2',
        'UV_NO_INDEX': '1',
    }


def test_parse_requirements_maps_pip_options(tmp_path: Path) -> None:
    """Requirements, includes and editables go to uv add; other options map onto [tool.uv] settings."""
    (tmp_path / 'constraints.txt').write_text('# pins\nidna<3.8\n')
    content = dedent("""\
        ruff>=0.14
        -r base.txt
        --requirement=more.txt
        -e ./plugin
        -c constraints.txt
        --index-url https://primary.example/simple
        --extra-index-url=https://extra.example/simple
        -f https://wheels.example/
        --no-index
        --pre
    """)

    parsed = parse_requirements(content, base_dir=tmp_path)

    assert parsed.requirements == ['ruff>=0.14']
    assert parsed.requirement_files == [tmp_path / 'base.txt', tmp_path / 'more.txt']
    assert parsed.editables == ['./plugin']
    assert parsed.uv_settings == {
        'constraint-dependencies': ['idna<3.8'],
        'index': [
            {'url': 'https://primary.example/simple', 'default': True},
            {'url': 'https://extra.example/simple'},
        ],
        'find-links': ['https://wheels.example/'],
        'no-index': True,
        'prerelease': 'allow',
    }


@pytest.mark.parametrize(
    ('content', 'files'),
    [
        ('--only-binary :all:', {}),
        ('-r', {}),
        ('-c constraints.txt', {'constraints.txt': '-r other.txt\n'}),
    ],
    ids=['unknown-option', 'missing-value', 'option-in-constraints'],
)
def test_parse_requirements_rejects_unconvertible_lines(tmp_path: Path, content: str, files: dict[str, str]) -> None:
    """Options with no uv project equivalent fail with an error naming the line."""
    for name, text in files.items():
        (tmp_path / name).write_text(text)

    with pytest.raises(UnsupportedRequirementError, match='Cannot convert requirements line'):
        parse_requirements(content, base_dir=tmp_path)


def test_merge_uv_settings_concatenates_lists_and_lets_later_layers_win() -> None:
    """Lists from every layer are kept; scalar settings come from the last layer that sets them."""
    merged = merge_uv_settings(
        {'index': [{'url': 'a'}], 'prerelease': 'allow'},
        {'index': [{'url': 'b'}], 'prerelease': 'explicit', 'resolution': 'lowest'},
    )

    assert merged == {'index': [{'url': 'a'}, {'url': 'b'}], 'prerelease': 'explicit', 'resolution': 'lowest'}


def test_legacy_pins_reads_version_1_entries() -> None:
    """Exact pins (with markers) are extracted from `uv pip compile` output; URLs and hashes are skipped."""
    requirements = (
        'click==8.3.1 \\\n    --hash=sha256:aaaa\n'
        "colorama==0.4.6 ; sys_platform == 'win32' \\\n    --hash=sha256:bbbb\n"
        'tool @ git+https://example.com/tool.git@abc\n'
        'not a requirement\n'
        '--index-url https://pypi.org/simple\n'
    )

    assert _legacy_pins(requirements) == ['click==8.3.1', 'colorama==0.4.6; sys_platform == "win32"']


# ── pyproject rendering ──────────────────────────────────────────────────────


def test_render_pyproject_is_valid_toml_with_passthrough_and_rebased_sources(tmp_path: Path) -> None:
    """The generated project copies the passthrough verbatim and makes source paths project-relative."""
    absolute = (tmp_path / 'elsewhere' / 'absolute').as_posix()
    spec = ProjectSpec(
        dependencies=['tool', 'plugin', 'quoted "name" >=1'],
        sources={
            'tool': {'git': 'https://example.com/tool.git', 'tag': 'v1'},
            'plugin': [{'path': 'plugins/plugin', 'editable': True, 'marker': "os_name == 'posix'"}],
            'absolute': {'path': absolute},
        },
        uv_settings={
            'constraint-dependencies': ['pydantic<3'],
            'index': [{'name': 'internal', 'url': 'https://pypi.example/simple', 'explicit': True}],
            'exclude-newer': date(2026, 1, 1),
            'compile-bytecode': False,
            'concurrent-downloads': 4,
            'pip': {},
        },
    )
    project_dir = tmp_path / '.uv-toolbox' / 'projects' / 'tools'

    data = tomllib.loads(
        render_pyproject(spec, requires_python='>=3.12', project_dir=project_dir, config_dir=tmp_path),
    )

    assert data['project'] == {
        'name': 'uv-toolbox-env',
        'version': '0.0.0',
        'requires-python': '>=3.12',
        'dependencies': spec.dependencies,
    }
    assert data['tool']['uv'].pop('sources') == {
        'tool': {'git': 'https://example.com/tool.git', 'tag': 'v1'},
        'plugin': [{'path': '../../../plugins/plugin', 'editable': True, 'marker': "os_name == 'posix'"}],
        'absolute': {'path': absolute},
    }
    assert data['tool']['uv'] == {'package': False, **spec.uv_settings, 'exclude-newer': '2026-01-01'}


def test_render_pyproject_rejects_unrenderable_values() -> None:
    """Values with no TOML representation fail loudly instead of producing a broken project."""
    with pytest.raises(TypeError, match='Cannot render'):
        render_pyproject(ProjectSpec(uv_settings={'bad': object()}), requires_python='>=3.12')


# ── requires-python ──────────────────────────────────────────────────────────


def test_resolve_requires_python_prefers_config_then_lock_then_interpreter(
    tmp_path: Path,
    mocker: MockerFixture,
) -> None:
    """A configured value wins; otherwise the locked value is kept; a first lock uses the interpreter."""
    run_mock = _fake_uv(mocker)
    configured = _settings(tmp_path, dependencies=['ruff'], requires_python='>=3.11')
    assert resolve_requires_python(configured.environments[0], configured, _UV_LOCK) == '>=3.11'

    settings = _settings(tmp_path)
    env = settings.environments[0]
    assert resolve_requires_python(env, settings, _UV_LOCK) == '>=3.12'
    run_mock.assert_not_called()
    assert resolve_requires_python(env, settings, None) == '>=3.13'
    assert _commands(run_mock) == [['uv', 'python', 'find', '--no-project', '--show-version']]


# ── uv version ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ('output', 'ok'),
    [
        ('uv 0.12.24 (5411378eb 2026-10-08 aarch64-apple-darwin)', True),
        ('uv 0.9.27 (b5797b2ab 2026-01-26)', True),
        ('uv 0.8.0', True),
        ('uv 0.7.22 (abc 2025-06-01)', False),
        ('uv 1.2.3+++', False),
        ('something unexpected', False),
    ],
)
def test_ensure_uv_version(mocker: MockerFixture, output: str, *, ok: bool) -> None:
    """Uv older than the supported minimum (or unparseable output) fails with a clear error."""
    mocker.patch('uv_toolbox.project.run_checked', return_value=output)
    real_ensure_uv_version.cache_clear()

    if ok:
        real_ensure_uv_version()
    else:
        with pytest.raises(UvVersionError, match=r'requires uv >= 0\.8\.0'):
            real_ensure_uv_version()
    real_ensure_uv_version.cache_clear()


# ── lock / check / sync ──────────────────────────────────────────────────────


def test_lock_environment_starts_from_existing_lock(tmp_path: Path, mocker: MockerFixture) -> None:
    """The existing uv.lock is in place for `uv lock`, with upgrade flags forwarded and VIRTUAL_ENV cleared."""
    run_mock = _fake_uv(mocker, lock_content=_UV_LOCK + '# new\n')
    settings = _settings(tmp_path, dependencies=['ruff'], environment={'UV_INDEX_URL': 'https://idx'})
    env = settings.environments[0]
    seen_lock: list[str] = []
    original = run_mock.side_effect

    def capture(args: Sequence[str], **kwargs: object) -> str:
        cwd = kwargs['cwd']
        assert isinstance(cwd, Path)
        seen_lock.append((cwd / 'uv.lock').read_text())
        return original(args, **kwargs)

    run_mock.side_effect = capture

    result = lock_environment(env, settings, uv_lock=_UV_LOCK, upgrade_packages=['ruff', 'click'], refresh=True)

    assert result == _UV_LOCK + '# new\n'
    assert seen_lock == [_UV_LOCK]
    assert _commands(run_mock) == [
        ['uv', 'lock', '--upgrade-package', 'ruff', '--upgrade-package', 'click', '--refresh'],
    ]
    call = run_mock.call_args
    assert call.kwargs['cwd'] == settings.project_dir(env)
    assert call.kwargs['extra_env'] == {
        'UV_INDEX_URL': 'https://idx',
        'VIRTUAL_ENV': None,
        'UV_PROJECT_ENVIRONMENT': None,
    }
    assert (tmp_path / '.uv-toolbox' / '.gitignore').read_text().endswith('*\n')


def test_lock_environment_without_existing_lock_removes_stale_project_lock(
    tmp_path: Path,
    mocker: MockerFixture,
) -> None:
    """A first lock (or --upgrade) never picks up a uv.lock left in the working directory."""
    settings = _settings(tmp_path, dependencies=['ruff'], requires_python='>=3.12')
    env = settings.environments[0]
    project_dir = settings.project_dir(env)
    project_dir.mkdir(parents=True)
    (project_dir / 'uv.lock').write_text('stale')
    run_mock = _fake_uv(mocker)

    def check_absent(args: Sequence[str], **kwargs: object) -> str:
        assert not (project_dir / 'uv.lock').exists()
        (project_dir / 'uv.lock').write_text(_UV_LOCK)
        return ''

    run_mock.side_effect = check_absent

    lock_environment(env, settings, upgrade=True)

    assert _commands(run_mock) == [['uv', 'lock', '--upgrade']]


def test_lock_environment_seeds_from_legacy_pins(tmp_path: Path, mocker: MockerFixture) -> None:
    """Migrating a version 1 entry first locks with its pins as constraints, then locks without them."""
    run_mock = _fake_uv(mocker)
    settings = _settings(tmp_path, dependencies=['ruff'], requires_python='>=3.12')
    env = settings.environments[0]
    pyprojects: list[dict[str, object]] = []
    original = run_mock.side_effect

    def capture(args: Sequence[str], **kwargs: object) -> str:
        cwd = kwargs['cwd']
        assert isinstance(cwd, Path)
        pyprojects.append(tomllib.loads((cwd / 'pyproject.toml').read_text())['tool']['uv'])
        return original(args, **kwargs)

    run_mock.side_effect = capture

    lock_environment(env, settings, legacy_requirements='ruff==0.14.14 \\\n    --hash=sha256:aaaa\n')

    assert _commands(run_mock) == [['uv', 'lock'], ['uv', 'lock']]
    assert pyprojects == [
        {'package': False, 'constraint-dependencies': ['ruff==0.14.14']},
        {'package': False},
    ]


def test_lock_environment_falls_back_when_legacy_pins_conflict(tmp_path: Path, mocker: MockerFixture) -> None:
    """If the old pins no longer satisfy the config, the environment is locked from scratch with a warning."""
    settings = _settings(tmp_path, dependencies=['ruff>=1'], requires_python='>=3.12')
    env = settings.environments[0]
    calls = 0

    def run(args: Sequence[str], **kwargs: object) -> str:
        nonlocal calls
        calls += 1
        cwd = kwargs['cwd']
        assert isinstance(cwd, Path)
        if calls == 1:
            raise ExternalCommandError(cmd_args=args, returncode=1, stderr='unsatisfiable')
        assert not (cwd / 'uv.lock').exists()
        (cwd / 'uv.lock').write_text(_UV_LOCK)
        return ''

    mocker.patch('uv_toolbox.project.run_checked', side_effect=run)
    secho = mocker.patch('uv_toolbox.project.typer.secho')

    assert lock_environment(env, settings, legacy_requirements='ruff==0.14.14\n') == _UV_LOCK
    assert 'locking from scratch' in secho.call_args.args[0]


def test_lock_environment_skips_seeding_without_pins_or_when_upgrading(tmp_path: Path, mocker: MockerFixture) -> None:
    """Seeding only happens for a plain lock of a version 1 entry that has exact pins."""
    run_mock = _fake_uv(mocker)
    settings = _settings(tmp_path, dependencies=['ruff'], requires_python='>=3.12')
    env = settings.environments[0]

    lock_environment(env, settings, legacy_requirements='tool @ git+https://example.com/tool.git@abc\n')
    lock_environment(env, settings, legacy_requirements='ruff==1.0\n', upgrade=True)

    assert _commands(run_mock) == [['uv', 'lock'], ['uv', 'lock', '--upgrade']]


def test_check_environment_reports_uv_explanation(tmp_path: Path, mocker: MockerFixture) -> None:
    """`uv lock --check` passing returns None; failing returns uv's stderr."""
    settings = _settings(tmp_path)
    env = settings.environments[0]
    run_mock = _fake_uv(mocker)

    assert check_environment(env, settings, uv_lock=_UV_LOCK) is None
    assert _commands(run_mock) == [['uv', 'lock', '--check']]

    _fake_uv(mocker, fail=['--check'])
    assert check_environment(env, settings, uv_lock=_UV_LOCK) == 'uv lock --check failed'


def test_sync_environment_tries_offline_first(tmp_path: Path, mocker: MockerFixture) -> None:
    """Syncs the frozen lock into the content-addressed venv, offline first, online as a fallback."""
    settings = _settings(tmp_path)
    env = settings.environments[0]
    venv_path = env.venv_path(settings)
    run_mock = _fake_uv(mocker, fail=['--offline'])

    sync_environment(env, settings, uv_lock=_UV_LOCK)

    assert _commands(run_mock) == [
        ['uv', 'sync', '--frozen', '--offline'],
        ['uv', 'sync', '--frozen'],
    ]
    assert run_mock.call_args.kwargs['extra_env']['UV_PROJECT_ENVIRONMENT'] == str(venv_path)
    assert run_mock.call_args.kwargs['extra_env']['VIRTUAL_ENV'] is None
    assert (settings.project_dir(env) / 'uv.lock').read_text() == _UV_LOCK


@pytest.mark.filterwarnings('ignore::FutureWarning')
def test_sync_environment_needs_no_conversion_for_deprecated_requirements(
    tmp_path: Path,
    mocker: MockerFixture,
) -> None:
    """A frozen sync of a deprecated environment never runs `uv add`, but keeps converted index settings."""
    settings = _settings(tmp_path, requirements='git+https://example.com/tool.git\n--extra-index-url https://idx\n')
    env = settings.environments[0]
    run_mock = _fake_uv(mocker)

    sync_environment(env, settings, uv_lock=_UV_LOCK)

    assert _commands(run_mock) == [['uv', 'sync', '--frozen', '--offline']]
    pyproject = tomllib.loads((settings.project_dir(env) / 'pyproject.toml').read_text())
    assert pyproject['project']['dependencies'] == []
    assert pyproject['tool']['uv']['index'] == [{'url': 'https://idx'}]


def test_sync_environment_clear_removes_venv(tmp_path: Path, mocker: MockerFixture) -> None:
    """clear=True deletes the venv so uv recreates it."""
    settings = _settings(tmp_path)
    env = settings.environments[0]
    venv_path = env.venv_path(settings)
    (venv_path / 'bin').mkdir(parents=True)
    _fake_uv(mocker)

    sync_environment(env, settings, uv_lock=_UV_LOCK, clear=True)

    assert not venv_path.exists()


# ── deprecated conversion ────────────────────────────────────────────────────


@pytest.mark.filterwarnings('ignore::FutureWarning')
def test_convert_requirements_runs_uv_add_from_config_dir(tmp_path: Path, mocker: MockerFixture) -> None:
    """Requirements and includes go to one `uv add`, editables to another, and the result is read back."""
    (tmp_path / 'reqs').mkdir()
    req_file = tmp_path / 'reqs' / 'requirements.txt'
    req_file.write_text('ruff\n-r base.txt\n-e ./plugin\n--extra-index-url https://idx\n')
    settings = _settings(tmp_path, requirements_file=str(req_file), uv={'prerelease': 'allow'})
    env = settings.environments[0]
    adds: list[list[str]] = []

    def run(args: Sequence[str], **kwargs: object) -> str:
        adds.append(list(args))
        assert kwargs['cwd'] == settings.config_dir
        scratch = Path(args[args.index('--project') + 1])
        assert scratch.parent == tmp_path / '.uv-toolbox'
        if '--editable' in args:
            assert kwargs['extra_env']['UV_INDEX'] == 'https://idx'  # ty: ignore[not-subscriptable]
            (scratch / 'pyproject.toml').write_text(
                '[project]\ndependencies = ["plugin", "ruff"]\n'
                '[tool.uv.sources]\nplugin = { path = "../../plugin", editable = true }\n',
            )
        return ''

    mocker.patch('uv_toolbox.project.run_checked', side_effect=run)

    spec = project.convert_requirements(env, settings, requires_python='>=3.12')

    assert adds[0][-3:] == ['ruff', '-r', str(tmp_path / 'reqs' / 'base.txt')]
    assert adds[1][-2:] == ['--editable', './plugin']
    assert spec == ProjectSpec(
        dependencies=['plugin', 'ruff'],
        sources={'plugin': {'path': 'plugin', 'editable': True}},
        uv_settings={'index': [{'url': 'https://idx'}], 'prerelease': 'allow'},
    )
    assert list((tmp_path / '.uv-toolbox').glob('convert-*')) == []


@pytest.mark.filterwarnings('ignore::FutureWarning')
def test_lock_environment_converts_deprecated_requirements(tmp_path: Path, mocker: MockerFixture) -> None:
    """Deprecated environments are converted with `uv add` before locking; the result shapes the pyproject."""
    settings = _settings(tmp_path, requirements='tool', requires_python='>=3.12')
    env = settings.environments[0]
    convert = mocker.patch(
        'uv_toolbox.project.convert_requirements',
        return_value=ProjectSpec(dependencies=['tool'], sources={'tool': {'path': 'vendor/tool'}}),
    )
    _fake_uv(mocker)

    lock_environment(env, settings)

    convert.assert_called_once_with(env, settings, requires_python='>=3.12')
    pyproject = tomllib.loads((settings.project_dir(env) / 'pyproject.toml').read_text())
    assert pyproject['project']['dependencies'] == ['tool']
    assert pyproject['tool']['uv']['sources'] == {'tool': {'path': '../../../vendor/tool'}}


def test_sync_environment_without_locked_requires_python(tmp_path: Path, mocker: MockerFixture) -> None:
    """A lock without requires-python (never written by uv, but possible by hand) falls back to resolution."""
    settings = _settings(tmp_path, dependencies=['ruff'], requires_python='>=3.11')
    env = settings.environments[0]
    _fake_uv(mocker)

    sync_environment(env, settings, uv_lock='version = 1\n')

    pyproject = tomllib.loads((settings.project_dir(env) / 'pyproject.toml').read_text())
    assert pyproject['project']['requires-python'] == '>=3.11'


def test_ci_uv_matrix_spans_supported_versions() -> None:
    """CI tests the minimum supported uv minor and the uv version pinned for development."""
    root = Path(__file__).parents[2]
    workflow = yaml.safe_load((root / '.github' / 'workflows' / 'ci-checks.yaml').read_text())
    tested = [Version(v) for v in workflow['jobs']['uv-compatibility']['strategy']['matrix']['uv']]
    pinned = Version(tomllib.loads((root / 'mise.toml').read_text())['tools']['uv'])

    assert min(tested).release[:2] == project.MIN_UV_VERSION.release[:2]
    assert max(tested) == pinned
