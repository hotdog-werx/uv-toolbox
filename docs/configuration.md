# Configuration

uv-toolbox reads its configuration from the first of these it finds, walking up
from the current directory to the git root:

1. `uv-toolbox.yaml`
2. `uv-toolbox.json`
3. `uv-toolbox.toml`
4. `pyproject.toml`, under `[tool.uv-toolbox]`

Pass `--config` (or set `UV_TOOLBOX_CONFIG_FILE`) to use a specific file. Keys
can be written in `snake_case` or `kebab-case`.

## Top-level settings

| Key                   | Default               | Meaning                                                          |
| --------------------- | --------------------- | ---------------------------------------------------------------- |
| `environments`        | (required)            | The environments to manage; see below.                           |
| `venv_path`           | `~/.cache/uv-toolbox` | Where venvs are stored. Relative paths start at the config file. |
| `default_environment` | none                  | The environment `exec` uses when `--env` is not given.           |
| `show_commands`       | `false`               | Print each command uv-toolbox runs.                              |

## Environments

Each environment is turned into a uv project (see [Locking](locking.md)). Its
dependency keys map directly onto that project:

| Key               | Maps to                     |
| ----------------- | --------------------------- |
| `dependencies`    | `[project] dependencies`    |
| `sources`         | `[tool.uv.sources]`         |
| `requires_python` | `[project] requires-python` |
| `uv`              | `[tool.uv]`                 |

### `dependencies`

PEP 508 requirement strings, as in a `pyproject.toml`:

```yaml
environments:
  - name: formatting
    dependencies:
      - ruff>=0.14
      - black[jupyter]==25.1.0
      - tool @ git+https://github.com/example/tool.git@v1
```

Direct references such as the last line work, but `sources` is usually clearer
for anything not on an index.

### `sources`

Where to get named dependencies from, with the same schema as uv's
[`[tool.uv.sources]`](https://docs.astral.sh/uv/concepts/projects/dependencies/#dependency-sources).
Each source sets exactly one of `git`, `path`, `url` or `index`:

```yaml
environments:
  - name: repolish
    requires_python: '>=3.12'
    dependencies:
      - repolish>=1.4.0,<2
      - hotdogwerx-devkit-python
      - hotdogwerx-devkit-releez
      - my-plugin
    sources:
      hotdogwerx-devkit-python: &devkit
        git: https://github.com/hotdog-werx/devkit.git
        tag: v0
        subdirectory: packages/devkit-python
      hotdogwerx-devkit-releez:
        <<: *devkit
        subdirectory: packages/devkit-releez
      my-plugin:
        path: plugins/my-plugin
        editable: true
```

- `git` sources take one of `tag`, `branch` or `rev`, plus an optional
  `subdirectory`. The lock records both the ref and the commit it resolved to,
  so re-locking never looks the ref up again; `uvtb lock --upgrade` (or
  `--upgrade-package`) moves it.
- `path` sources are relative to the config file.
- A source may also be a list of entries with `marker`s, as in uv.

YAML anchors and merge keys (`&devkit` / `<<: *devkit` above) keep repeated git
sources short.

### `requires_python`

The Python versions the environment is locked for, e.g. `'>=3.12'`. When it's
not set, uv-toolbox keeps the range recorded in the existing lock, and a first
lock uses the minor version of the interpreter uv finds. Set it explicitly when
your team uses different Python versions. See
[Locking](locking.md#python-version).

### `uv`

Settings copied verbatim into the generated project's `[tool.uv]` table, using
uv's own (kebab-case) names. Anything uv supports there is available, for
example:

```yaml
environments:
  - name: tools
    dependencies: [internal-tool, ruff]
    sources:
      internal-tool: { index: internal }
    uv:
      constraint-dependencies: ['pydantic<3']
      override-dependencies: ['urllib3>=2']
      index:
        - name: internal
          url: https://pypi.internal.example/simple
          explicit: true
      exclude-newer: '2026-01-01T00:00:00Z'
```

uv-toolbox doesn't validate these settings itself; uv reports any problems when
locking. Two keys are managed by uv-toolbox and rejected here: `package` (the
generated project is never a package) and `sources` (use the environment's
`sources` key). Changing the `uv` settings makes the lock stale, just like
changing dependencies.

### `environment`

Environment variables set for every uv command and for `exec` / shims. `$VAR`
references are expanded when used:

```yaml
environments:
  - name: testing
    dependencies: [pytest]
    environment:
      PYTEST_ADDOPTS: '-v --tb=short'
      UV_INDEX_INTERNAL_PASSWORD: '$INTERNAL_TOKEN'
```

### Executables

Shims expose the console scripts declared by the environment's `dependencies`.
Scripts of transitive dependencies are excluded. Use `omit_executables` to hide
some of them, or `executables_override` for an exact list (`[]` exposes
nothing):

```yaml
environments:
  - name: formatting
    dependencies: [ruff, black]
    omit_executables: [black]
  - name: no-shims
    dependencies: [pytest]
    executables_override: []
```

The legacy `executables` key is an alias for `executables_override`. It emits a
deprecation warning and will be removed in uv-toolbox 1.0.

## Deprecated: `requirements` and `requirements_file`

Environments can still be declared with pip requirements, inline or from a file:

```yaml
environments:
  - name: formatting
    requirements: |
      ruff
      git+https://github.com/example/tool.git@v1
  - name: testing
    requirements_file: requirements-test.txt # relative to the config file
```

These keys emit a deprecation warning and will be removed in uv-toolbox 1.0.
When locking, uv-toolbox converts them into the generated project with `uv add`,
which names unnamed URLs and moves direct references into `[tool.uv.sources]`.
Requirement lines, `-r` includes and `-e` lines are converted; `-c` constraints
files, `--index-url`, `--extra-index-url`, `--find-links`, `--no-index` and
`--pre` become the equivalent `uv` settings. Any other option fails with an
error naming the line.

Run `uvtb migrate-config` to convert them for good; see
[Migrating](migrating.md).
