from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from tests.utils import create_fake_venv
from uv_toolbox import cli as cli_module
from uv_toolbox.cli import app
from uv_toolbox.errors import CommandDelimiterRequiredError, UvToolboxError
from uv_toolbox.lockfile import EnvironmentLock, UvToolboxLock, read_lockfile, write_lockfile
from uv_toolbox.migrate import Migration
from uv_toolbox.settings import UvToolboxEnvironment, UvToolboxSettings
from uv_toolbox.utils import _venv_python

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

runner = CliRunner()


def _write_config(
    tmp_path: Path,
    *,
    venv_path: Path,
    envs: list[tuple[str, str]],
) -> Path:
    env_lines = []
    for name, requirements in envs:
        env_lines.extend(
            [
                f'  - name: {name}',
                f'    dependencies: [{requirements!r}]',
            ],
        )
    contents = '\n'.join(
        [
            f'venv_path: {venv_path}',
            'environments:',
            *env_lines,
            '',
        ],
    )
    config_path = tmp_path / 'uvtb.yaml'
    config_path.write_text(contents)
    return config_path


def _fingerprint(name: str, requirement: str) -> str:
    return UvToolboxEnvironment(name=name, dependencies=[requirement]).lock_fingerprint()


def _uv_lock(tag: str) -> str:
    return f'version = 1\nrevision = 3\nrequires-python = ">=3.12"\n# {tag}\n'


def test_exec_runs_uv_command(mocker: MockerFixture, tmp_path: Path) -> None:
    """Runs `uv run --no-project` with the correct args and sets VIRTUAL_ENV to the content-addressed path."""
    venv_root = tmp_path / '.uv-toolbox'
    config_path = _write_config(
        tmp_path,
        venv_path=venv_root,
        envs=[('env1', 'ruff')],
    )

    mocker.patch.object(
        sys,
        'argv',
        ['uvtb', 'exec', '--', 'ruff', '--version'],
    )
    init_mock = mocker.patch('uv_toolbox.cli.initialize_virtualenv')
    run_mock = mocker.patch('uv_toolbox.cli.run_checked')

    result = runner.invoke(
        app,
        [
            '--config',
            str(config_path),
            'exec',
            '--env',
            'env1',
            '--',
            'ruff',
            '--version',
        ],
    )

    assert result.exit_code == 0
    init_mock.assert_called_once()
    run_mock.assert_called_once()
    args = run_mock.call_args.kwargs['args']
    extra_env = run_mock.call_args.kwargs['extra_env']
    assert extra_env['VIRTUAL_ENV'].startswith(str(venv_root))
    venv_python = str(_venv_python(Path(extra_env['VIRTUAL_ENV'])))
    assert args == ['uv', 'run', '--no-project', '--python', venv_python, '--', 'ruff', '--version']


def test_exec_requires_delimiter(mocker: MockerFixture, tmp_path: Path) -> None:
    """The `exec` command raises CommandDelimiterRequiredError when `--` is absent from argv."""
    config_path = _write_config(
        tmp_path,
        venv_path=tmp_path / '.uv-toolbox',
        envs=[('env1', 'ruff')],
    )
    mocker.patch.object(sys, 'argv', ['uvtb', 'exec', '--env', 'env1', 'ruff'])

    result = runner.invoke(
        app,
        ['--config', str(config_path), 'exec', '--env', 'env1', 'ruff'],
    )

    assert result.exit_code != 0
    assert isinstance(result.exception, CommandDelimiterRequiredError)


def test_shim_outputs_paths(tmp_path: Path) -> None:
    """Emits `export PATH=...` shell code containing shim directories for each env with listed executables."""
    venv_root = tmp_path / '.uv-toolbox'
    env_lines = [
        '  - name: env1',
        "    dependencies: ['ruff']",
        '    executables_override: [ruff]',
        '  - name: env2',
        "    dependencies: ['black']",
        '    executables_override: [black]',
    ]
    contents = '\n'.join(
        [
            f'venv_path: {venv_root}',
            'environments:',
            *env_lines,
            '',
        ],
    )
    config_path = tmp_path / 'uvtb.yaml'
    config_path.write_text(contents)

    settings = UvToolboxSettings.model_validate(
        {
            'venv_path': venv_root,
            'environments': [
                {
                    'name': 'env1',
                    'dependencies': ['ruff'],
                    'executables_override': ['ruff'],
                },
                {
                    'name': 'env2',
                    'dependencies': ['black'],
                    'executables_override': ['black'],
                },
            ],
        },
    )

    create_fake_venv(
        settings.environments[0].venv_path(settings=settings),
        ['ruff'],
    )
    create_fake_venv(
        settings.environments[1].venv_path(settings=settings),
        ['black'],
    )

    result = runner.invoke(app, ['--config', str(config_path), 'shim'])

    assert result.exit_code == 0
    output = result.stdout.strip()
    assert output.startswith('export PATH="')
    assert output.endswith(f'{os.pathsep}$PATH"')
    assert 'shims' in output


def test_install_initializes_all_envs(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    """The `install` command calls initialize_virtualenv for every environment in config order."""
    venv_root = tmp_path / '.uv-toolbox'
    config_path = _write_config(
        tmp_path,
        venv_path=venv_root,
        envs=[('env1', 'ruff'), ('env2', 'black')],
    )

    init_mock = mocker.patch('uv_toolbox.cli.initialize_virtualenv')

    result = runner.invoke(app, ['--config', str(config_path), 'install'])

    assert result.exit_code == 0
    assert init_mock.call_count == 2
    names = [call.kwargs['env'].name for call in init_mock.call_args_list]
    assert names == ['env1', 'env2']


def test_main_invokes_app(mocker: MockerFixture) -> None:
    """The main() entry point calls the Typer app."""
    app_mock = mocker.patch('uv_toolbox.cli.app')

    cli_module.main()

    app_mock.assert_called_once_with()


def test_install_handles_uv_toolbox_error(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    """The `install` command exits with code 1 and prints the error message when initialize_virtualenv raises."""
    venv_root = tmp_path / '.uv-toolbox'
    config_path = _write_config(
        tmp_path,
        venv_path=venv_root,
        envs=[('env1', 'ruff')],
    )

    mocker.patch(
        'uv_toolbox.cli.initialize_virtualenv',
        side_effect=UvToolboxError('Test error'),
    )

    result = runner.invoke(app, ['--config', str(config_path), 'install'])

    assert result.exit_code == 1
    assert 'Test error' in result.stderr


def test_exec_handles_uv_toolbox_error(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    """The `exec` command exits with code 1 and prints the error message on UvToolboxError."""
    venv_root = tmp_path / '.uv-toolbox'
    config_path = _write_config(
        tmp_path,
        venv_path=venv_root,
        envs=[('env1', 'ruff')],
    )

    mocker.patch.object(sys, 'argv', ['uvtb', 'exec', '--', 'ruff'])
    mocker.patch(
        'uv_toolbox.cli.initialize_virtualenv',
        side_effect=UvToolboxError('Test error'),
    )

    result = runner.invoke(
        app,
        ['--config', str(config_path), 'exec', '--env', 'env1', '--', 'ruff'],
    )

    assert result.exit_code == 1
    assert 'Test error' in result.stderr


def test_shim_list_paths_prints_one_dir_per_line(tmp_path: Path) -> None:
    """Passing `--list-paths` prints each shim directory as a plain path per line, not shell export syntax."""
    venv_root = tmp_path / '.uv-toolbox'
    env_lines = [
        '  - name: env1',
        "    dependencies: ['ruff']",
        '    executables_override: [ruff]',
    ]
    config_path = tmp_path / 'uvtb.yaml'
    config_path.write_text(
        '\n'.join([f'venv_path: {venv_root}', 'environments:', *env_lines, '']),
    )

    settings = UvToolboxSettings.model_validate(
        {
            'venv_path': venv_root,
            'environments': [
                {
                    'name': 'env1',
                    'dependencies': ['ruff'],
                    'executables_override': ['ruff'],
                },
            ],
        },
    )
    create_fake_venv(
        settings.environments[0].venv_path(settings=settings),
        ['ruff'],
    )

    result = runner.invoke(
        app,
        ['--config', str(config_path), 'shim', '--list-paths'],
    )

    assert result.exit_code == 0
    lines = result.stdout.strip().splitlines()
    assert len(lines) == 1
    assert lines[0].endswith('shims')


def test_shim_outputs_comment_when_no_shims(tmp_path: Path) -> None:
    """The `shim` command emits a shell comment when no venvs exist and no shims are created."""
    venv_root = tmp_path / '.uv-toolbox'
    config_path = _write_config(
        tmp_path,
        venv_path=venv_root,
        envs=[('env1', 'ruff')],
    )

    result = runner.invoke(app, ['--config', str(config_path), 'shim'])

    assert result.exit_code == 0
    output = result.stdout.strip()
    assert output == '# No shims to add to PATH'


def test_shim_handles_uv_toolbox_error(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    """The `shim` command exits with code 1 and prints the error message when create_shims raises."""
    venv_root = tmp_path / '.uv-toolbox'
    config_path = _write_config(
        tmp_path,
        venv_path=venv_root,
        envs=[('env1', 'ruff')],
    )

    mocker.patch(
        'uv_toolbox.cli.create_shims',
        side_effect=UvToolboxError('Test error'),
    )

    result = runner.invoke(app, ['--config', str(config_path), 'shim'])

    assert result.exit_code == 1
    assert 'Test error' in result.stderr


# ── lock command ─────────────────────────────────────────────────────────────


def _write_lock(tmp_path: Path, **entries: EnvironmentLock) -> Path:
    lock_path = tmp_path / 'uv-toolbox.lock'
    write_lockfile(UvToolboxLock(environments=entries), lock_path)
    return lock_path


def test_lock_writes_lockfile(mocker: MockerFixture, tmp_path: Path) -> None:
    """`lock` locks every environment and writes `<config-dir>/uv-toolbox.lock`."""
    config_path = _write_config(tmp_path, venv_path=tmp_path / '.venvs', envs=[('env1', 'ruff')])
    lock_mock = mocker.patch('uv_toolbox.lock.lock_environment', return_value=_uv_lock('new'))

    result = runner.invoke(app, ['--config', str(config_path), 'lock'])

    assert result.exit_code == 0, result.output
    assert 'Wrote' in result.stdout
    assert lock_mock.call_args.kwargs['uv_lock'] is None
    written = read_lockfile(tmp_path / 'uv-toolbox.lock')
    assert written.environments['env1'] == EnvironmentLock(
        uv_lock=_uv_lock('new'), fingerprint=_fingerprint('env1', 'ruff')
    )


def test_lock_keeps_existing_pins_and_skips_unchanged_write(mocker: MockerFixture, tmp_path: Path) -> None:
    """Plain `lock` starts from the committed uv.lock and leaves the file alone when nothing changed."""
    config_path = _write_config(tmp_path, venv_path=tmp_path / '.venvs', envs=[('env1', 'ruff')])
    current = EnvironmentLock(uv_lock=_uv_lock('current'), fingerprint=_fingerprint('env1', 'ruff'))
    lock_path = _write_lock(tmp_path, env1=current)
    lock_mock = mocker.patch('uv_toolbox.lock.lock_environment', return_value=_uv_lock('current'))
    write_mock = mocker.patch('uv_toolbox.cli.write_lockfile')

    result = runner.invoke(app, ['--config', str(config_path), 'lock'])

    assert result.exit_code == 0, result.output
    assert lock_mock.call_args.kwargs['uv_lock'] == _uv_lock('current')
    assert 'Lockfile is up to date' in result.stdout
    write_mock.assert_not_called()
    assert lock_path.exists()


@pytest.mark.parametrize(
    ('flags', 'expected'),
    [
        (['--refresh'], {'refresh': True, 'upgrade': False, 'upgrade_packages': []}),
        (['--upgrade'], {'refresh': False, 'upgrade': True, 'upgrade_packages': []}),
        (
            ['-P', 'ruff', '--upgrade-package', 'click'],
            {'refresh': False, 'upgrade': False, 'upgrade_packages': ['ruff', 'click']},
        ),
    ],
    ids=['refresh', 'upgrade', 'upgrade-package'],
)
def test_lock_forwards_flags(
    mocker: MockerFixture, tmp_path: Path, flags: list[str], expected: dict[str, object]
) -> None:
    """`lock` forwards --refresh, --upgrade and --upgrade-package to `uv lock`."""
    config_path = _write_config(tmp_path, venv_path=tmp_path / '.venvs', envs=[('env1', 'ruff')])
    lock_mock = mocker.patch('uv_toolbox.lock.lock_environment', return_value=_uv_lock('new'))

    result = runner.invoke(app, ['--config', str(config_path), 'lock', *flags])

    assert result.exit_code == 0, result.output
    for key, value in expected.items():
        assert lock_mock.call_args.kwargs[key] == value


def test_lock_errors_without_config_file(mocker: MockerFixture) -> None:
    """The `lock` command exits with code 1 when settings has no lockfile_path (no config file known)."""
    mocker.patch(
        'uv_toolbox.cli.UvToolboxSettings.from_context',
        return_value=UvToolboxSettings.model_validate(
            {'environments': [{'name': 'env1', 'dependencies': ['ruff']}]},
        ),
    )

    result = runner.invoke(app, ['lock'])

    assert result.exit_code == 1
    assert 'Cannot locate lockfile' in result.stderr


def test_lock_handles_uv_toolbox_error(mocker: MockerFixture, tmp_path: Path) -> None:
    """The `lock` command exits with code 1 and prints the error message when locking fails."""
    config_path = _write_config(tmp_path, venv_path=tmp_path / '.venvs', envs=[('env1', 'ruff')])
    mocker.patch('uv_toolbox.lock.lock_environment', side_effect=UvToolboxError('lock failed'))

    result = runner.invoke(app, ['--config', str(config_path), 'lock'])

    assert result.exit_code == 1
    assert 'lock failed' in result.stderr


# ── lock --check ─────────────────────────────────────────────────────────────


def test_lock_check_succeeds_without_writing(mocker: MockerFixture, tmp_path: Path) -> None:
    """`lock --check` runs `uv lock --check` per environment and never writes or re-locks."""
    config_path = _write_config(tmp_path, venv_path=tmp_path / '.venvs', envs=[('env1', 'ruff')])
    lock_path = _write_lock(
        tmp_path,
        env1=EnvironmentLock(uv_lock=_uv_lock('current'), fingerprint=_fingerprint('env1', 'ruff')),
    )
    original = lock_path.read_text()
    check_mock = mocker.patch('uv_toolbox.lock.check_environment', return_value=None)
    lock_mock = mocker.patch('uv_toolbox.lock.lock_environment')

    result = runner.invoke(app, ['--config', str(config_path), 'lock', '--check'])

    assert result.exit_code == 0, result.output
    assert 'Lockfile is up to date' in result.stdout
    assert check_mock.call_args.kwargs['uv_lock'] == _uv_lock('current')
    lock_mock.assert_not_called()
    assert lock_path.read_text() == original


def test_lock_check_reports_uv_lock_check_failures(mocker: MockerFixture, tmp_path: Path) -> None:
    """`lock --check` fails with uv's explanation when a uv.lock no longer satisfies its project."""
    config_path = _write_config(tmp_path, venv_path=tmp_path / '.venvs', envs=[('env1', 'ruff')])
    _write_lock(tmp_path, env1=EnvironmentLock(uv_lock=_uv_lock('current'), fingerprint=_fingerprint('env1', 'ruff')))
    mocker.patch('uv_toolbox.lock.check_environment', return_value='The lockfile needs to be updated')

    result = runner.invoke(app, ['--config', str(config_path), 'lock', '--check'])

    assert result.exit_code == 1
    assert 'Lockfile is out of date' in result.stderr
    assert 'env1: The lockfile needs to be updated' in result.stderr


@pytest.mark.parametrize(
    'fingerprint',
    [None, _fingerprint('env1', 'black')],
    ids=['missing', 'mismatched'],
)
def test_lock_check_fails_on_stale_fingerprint(mocker: MockerFixture, tmp_path: Path, fingerprint: str | None) -> None:
    """`lock --check` fails without running uv when an entry's fingerprint is missing or stale."""
    config_path = _write_config(tmp_path, venv_path=tmp_path / '.venvs', envs=[('env1', 'ruff')])
    _write_lock(tmp_path, env1=EnvironmentLock(uv_lock=_uv_lock('current'), fingerprint=fingerprint))
    check_mock = mocker.patch('uv_toolbox.lock.check_environment')

    result = runner.invoke(app, ['--config', str(config_path), 'lock', '--check'])

    assert result.exit_code == 1
    assert 'Configuration changed for: env1' in result.stderr
    check_mock.assert_not_called()


def test_lock_check_rejects_version_1_lockfile(mocker: MockerFixture, tmp_path: Path) -> None:
    """A version 1 lockfile fails `--check` with a pointer to `uv-toolbox lock`."""
    config_path = _write_config(tmp_path, venv_path=tmp_path / '.venvs', envs=[('env1', 'ruff')])
    (tmp_path / 'uv-toolbox.lock').write_text('version: 1\nenvironments:\n  env1:\n    requirements: ruff==1\n')
    check_mock = mocker.patch('uv_toolbox.lock.check_environment')

    result = runner.invoke(app, ['--config', str(config_path), 'lock', '--check'])

    assert result.exit_code == 1
    assert 'Lockfile format is outdated' in result.stderr
    check_mock.assert_not_called()


def test_lock_check_reports_missing_lock(mocker: MockerFixture, tmp_path: Path) -> None:
    """`lock --check` exits nonzero when the configuration has no lockfile."""
    config_path = _write_config(tmp_path, venv_path=tmp_path / '.venvs', envs=[('env1', 'ruff')])
    check_mock = mocker.patch('uv_toolbox.lock.check_environment')

    result = runner.invoke(app, ['--config', str(config_path), 'lock', '--check'])

    assert result.exit_code == 1
    assert 'Lockfile is missing' in result.stderr
    check_mock.assert_not_called()


def test_lock_check_rejects_upgrade_flags(tmp_path: Path) -> None:
    """`--check` never upgrades, so combining it with upgrade flags is an error."""
    config_path = _write_config(tmp_path, venv_path=tmp_path / '.venvs', envs=[('env1', 'ruff')])
    _write_lock(tmp_path, env1=EnvironmentLock(uv_lock=_uv_lock('current'), fingerprint=_fingerprint('env1', 'ruff')))

    result = runner.invoke(app, ['--config', str(config_path), 'lock', '--check', '--upgrade-package', 'ruff'])

    assert result.exit_code == 1
    assert '--check cannot be combined with --upgrade' in result.stderr


# ── automatic re-locking ─────────────────────────────────────────────────────


def test_install_relocks_stale_environments(mocker: MockerFixture, tmp_path: Path) -> None:
    """`install` re-locks only stale environments from their old uv.lock and installs the fresh lock."""
    config_path = _write_config(
        tmp_path,
        venv_path=tmp_path / '.venvs',
        envs=[('current', 'ruff'), ('changed', 'black>=25')],
    )
    lock_path = _write_lock(
        tmp_path,
        current=EnvironmentLock(uv_lock=_uv_lock('current'), fingerprint=_fingerprint('current', 'ruff')),
        changed=EnvironmentLock(uv_lock=_uv_lock('changed-old'), fingerprint=_fingerprint('changed', 'black')),
    )
    lock_mock = mocker.patch('uv_toolbox.lock.lock_environment', return_value=_uv_lock('changed-new'))
    init_mock = mocker.patch('uv_toolbox.cli.initialize_virtualenv')

    result = runner.invoke(app, ['--config', str(config_path), 'install'])

    assert result.exit_code == 0, result.output
    assert 're-locking: changed' in result.stderr
    lock_mock.assert_called_once()
    assert lock_mock.call_args.args[0].name == 'changed'
    assert lock_mock.call_args.kwargs['uv_lock'] == _uv_lock('changed-old')

    updated = read_lockfile(lock_path)
    assert updated.environments['current'].uv_lock == _uv_lock('current')
    assert updated.environments['changed'] == EnvironmentLock(
        uv_lock=_uv_lock('changed-new'),
        fingerprint=_fingerprint('changed', 'black>=25'),
    )
    installed = {call.kwargs['env'].name: call.kwargs['env'] for call in init_mock.call_args_list}
    assert installed['changed'].locked_uv_lock == _uv_lock('changed-new')


def test_install_migrates_version_1_lockfile(mocker: MockerFixture, tmp_path: Path) -> None:
    """A version 1 lockfile is re-locked (seeded with its pins) and rewritten as version 2."""
    config_path = _write_config(tmp_path, venv_path=tmp_path / '.venvs', envs=[('env1', 'ruff')])
    lock_path = tmp_path / 'uv-toolbox.lock'
    lock_path.write_text(
        'version: 1\nenvironments:\n  env1:\n    fingerprint: sha256:x\n    requirements: |\n      ruff==0.14.14\n',
    )
    lock_mock = mocker.patch('uv_toolbox.lock.lock_environment', return_value=_uv_lock('v2'))
    init_mock = mocker.patch('uv_toolbox.cli.initialize_virtualenv')

    result = runner.invoke(app, ['--config', str(config_path), 'install'])

    assert result.exit_code == 0, result.output
    assert 'Lockfile format is outdated; re-locking' in result.stderr
    assert lock_mock.call_args.kwargs['legacy_requirements'] == 'ruff==0.14.14\n'
    migrated = read_lockfile(lock_path)
    assert not migrated.outdated_format
    assert migrated.environments['env1'].fingerprint == _fingerprint('env1', 'ruff')
    init_mock.assert_called_once()


def test_install_leaves_current_lockfile_untouched(mocker: MockerFixture, tmp_path: Path) -> None:
    """`install` neither locks nor rewrites a lockfile whose fingerprints all match."""
    config_path = _write_config(tmp_path, venv_path=tmp_path / '.venvs', envs=[('env1', 'ruff')])
    lock_path = _write_lock(
        tmp_path,
        env1=EnvironmentLock(uv_lock=_uv_lock('current'), fingerprint=_fingerprint('env1', 'ruff')),
    )
    original = lock_path.read_text()
    lock_mock = mocker.patch('uv_toolbox.lock.lock_environment')
    mocker.patch('uv_toolbox.cli.initialize_virtualenv')

    result = runner.invoke(app, ['--config', str(config_path), 'install'])

    assert result.exit_code == 0, result.output
    lock_mock.assert_not_called()
    assert lock_path.read_text() == original
    assert 'Updated' not in result.stderr


def test_install_drops_removed_environments_from_lockfile(mocker: MockerFixture, tmp_path: Path) -> None:
    """Entries for environments removed from the config are pruned without re-locking the rest."""
    config_path = _write_config(tmp_path, venv_path=tmp_path / '.venvs', envs=[('env1', 'ruff')])
    lock_path = _write_lock(
        tmp_path,
        env1=EnvironmentLock(uv_lock=_uv_lock('current'), fingerprint=_fingerprint('env1', 'ruff')),
        removed=EnvironmentLock(uv_lock=_uv_lock('removed'), fingerprint=_fingerprint('removed', 'black')),
    )
    lock_mock = mocker.patch('uv_toolbox.lock.lock_environment')
    mocker.patch('uv_toolbox.cli.initialize_virtualenv')

    result = runner.invoke(app, ['--config', str(config_path), 'install'])

    assert result.exit_code == 0, result.output
    lock_mock.assert_not_called()
    assert set(read_lockfile(lock_path).environments) == {'env1'}


def test_install_handles_relock_error(mocker: MockerFixture, tmp_path: Path) -> None:
    """A locking failure while re-locking exits with code 1 and installs nothing."""
    config_path = _write_config(tmp_path, venv_path=tmp_path / '.venvs', envs=[('env1', 'ruff')])
    _write_lock(tmp_path, env1=EnvironmentLock(uv_lock=_uv_lock('old')))
    mocker.patch('uv_toolbox.lock.lock_environment', side_effect=UvToolboxError('lock failed'))
    init_mock = mocker.patch('uv_toolbox.cli.initialize_virtualenv')

    result = runner.invoke(app, ['--config', str(config_path), 'install'])

    assert result.exit_code == 1
    assert 'lock failed' in result.stderr
    init_mock.assert_not_called()


@pytest.mark.parametrize(
    'fingerprint',
    [None, _fingerprint('env1', 'ruff')],
    ids=['missing', 'mismatched'],
)
def test_exec_relocks_stale_environment(mocker: MockerFixture, tmp_path: Path, fingerprint: str | None) -> None:
    """`exec` re-locks an environment with a missing or stale fingerprint and runs against the fresh lock."""
    config_path = _write_config(tmp_path, venv_path=tmp_path / '.venvs', envs=[('env1', 'ruff>=0.15')])
    lock_path = _write_lock(tmp_path, env1=EnvironmentLock(uv_lock=_uv_lock('old'), fingerprint=fingerprint))
    mocker.patch.object(sys, 'argv', ['uvtb', 'exec', '--', 'ruff'])
    mocker.patch('uv_toolbox.lock.lock_environment', return_value=_uv_lock('new'))
    init_mock = mocker.patch('uv_toolbox.cli.initialize_virtualenv')
    mocker.patch('uv_toolbox.cli.run_checked')

    result = runner.invoke(app, ['--config', str(config_path), 'exec', '--', 'ruff'])

    assert result.exit_code == 0, result.output
    updated = read_lockfile(lock_path).environments['env1']
    assert updated == EnvironmentLock(uv_lock=_uv_lock('new'), fingerprint=_fingerprint('env1', 'ruff>=0.15'))
    assert init_mock.call_args.kwargs['env'].locked_uv_lock == _uv_lock('new')


# ── migrate-config ───────────────────────────────────────────────────────────


def _write_deprecated_config(tmp_path: Path) -> Path:
    config_path = tmp_path / 'uv-toolbox.yaml'
    config_path.write_text('environments:\n  - name: env1\n    requirements: ruff\n')
    return config_path


def test_migrate_config_shows_diff_and_writes_after_confirmation(mocker: MockerFixture, tmp_path: Path) -> None:
    """migrate-config prints a diff, asks before writing, and points at `uv-toolbox lock`."""
    config_path = _write_deprecated_config(tmp_path)
    mocker.patch(
        'uv_toolbox.cli.migrate_config',
        return_value=Migration(['env1'], 'environments:\n  - name: env1\n    dependencies: [ruff]\n', rewritable=True),
    )

    result = runner.invoke(app, ['--config', str(config_path), 'migrate-config'], input='y\n')

    assert result.exit_code == 0, result.output
    assert '-    requirements: ruff' in result.stdout
    assert '+    dependencies: [ruff]' in result.stdout
    assert 'Comments and formatting' in result.stderr
    assert 'dependencies: [ruff]' in config_path.read_text()
    assert 'Run `uv-toolbox lock`' in result.stdout


def test_migrate_config_declined_leaves_file(mocker: MockerFixture, tmp_path: Path) -> None:
    """Declining the confirmation leaves the config untouched and exits nonzero."""
    config_path = _write_deprecated_config(tmp_path)
    original = config_path.read_text()
    mocker.patch('uv_toolbox.cli.migrate_config', return_value=Migration(['env1'], 'new\n', rewritable=True))

    result = runner.invoke(app, ['--config', str(config_path), 'migrate-config'], input='n\n')

    assert result.exit_code == 1
    assert config_path.read_text() == original


def test_migrate_config_yes_skips_prompt(mocker: MockerFixture, tmp_path: Path) -> None:
    """--yes writes without asking."""
    config_path = _write_deprecated_config(tmp_path)
    mocker.patch('uv_toolbox.cli.migrate_config', return_value=Migration(['env1'], 'new\n', rewritable=True))

    result = runner.invoke(app, ['--config', str(config_path), 'migrate-config', '--yes'])

    assert result.exit_code == 0, result.output
    assert config_path.read_text() == 'new\n'


def test_migrate_config_prints_pyproject_tables(mocker: MockerFixture, tmp_path: Path) -> None:
    """Configs that cannot be rewritten get the replacement tables printed instead."""
    config_path = _write_deprecated_config(tmp_path)
    original = config_path.read_text()
    mocker.patch(
        'uv_toolbox.cli.migrate_config',
        return_value=Migration(['env1'], '[[tool.uv-toolbox.environments]]\n', rewritable=False),
    )

    result = runner.invoke(app, ['--config', str(config_path), 'migrate-config'])

    assert result.exit_code == 0, result.output
    assert 'cannot be rewritten automatically' in result.stdout
    assert '[[tool.uv-toolbox.environments]]' in result.stdout
    assert config_path.read_text() == original


def test_migrate_config_nothing_to_do(tmp_path: Path) -> None:
    """A config without deprecated environments is reported as already migrated."""
    config_path = _write_config(tmp_path, venv_path=tmp_path / '.venvs', envs=[('env1', 'ruff')])

    result = runner.invoke(app, ['--config', str(config_path), 'migrate-config'])

    assert result.exit_code == 0, result.output
    assert 'nothing to migrate' in result.stdout


def test_migrate_config_handles_conversion_error(mocker: MockerFixture, tmp_path: Path) -> None:
    """Conversion failures exit with code 1 and print the error."""
    config_path = _write_deprecated_config(tmp_path)
    mocker.patch('uv_toolbox.cli.migrate_config', side_effect=UvToolboxError('cannot convert'))

    result = runner.invoke(app, ['--config', str(config_path), 'migrate-config'])

    assert result.exit_code == 1
    assert 'cannot convert' in result.stderr


def test_cli_prints_config_deprecations_without_source_locations(tmp_path: Path) -> None:
    """Deprecation warnings are shown as plain messages rather than Python warning tracebacks."""
    config_path = _write_deprecated_config(tmp_path)

    result = runner.invoke(app, ['--config', str(config_path), 'shim', '--list-paths'])

    assert result.exit_code == 0, result.output
    assert result.stderr.startswith('FutureWarning: Environment ')
    assert 'pydantic' not in result.stderr
