# uv-toolbox

[![CI](https://github.com/hotdog-werx/uv-toolbox/actions/workflows/ci-checks.yaml/badge.svg)](https://github.com/hotdog-werx/uv-toolbox/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/uv-toolbox.svg)](https://pypi.org/project/uv-toolbox/)
[![Python Version](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![codecov](https://codecov.io/gh/hotdog-werx/uv-toolbox/branch/master/graph/badge.svg)](https://codecov.io/gh/hotdog-werx/uv-toolbox)

`uv-toolbox` is a CLI tool for managing Python tool environments. It will help
you create multiple virtual environments and manage their dependencies through a
declarative configuration file.

Here is an example in YAML format:

```yaml
environments:
  - name: env1
    dependencies: [ruff==0.13.0, black]
  - name: env2
    dependencies: [isort, flake8]
    sources:
      flake8:
        git: https://github.com/PyCQA/flake8.git
        tag: 7.3.0
```

Each environment is a small uv project: `dependencies`, `sources`,
`requires_python` and a `uv` passthrough for `[tool.uv]` settings work as they
do in a `pyproject.toml`, and locking uses `uv lock`. See the
[configuration docs](docs/configuration.md).

### Configuration Options

**Virtual Environment Location:**

By default, virtual environments are stored in `~/.cache/uv-toolbox/` using
**content-addressed storage**. This means:

- **Automatic deduplication**: Identically locked environments across projects
  share the same venv
- **No naming conflicts**: Venvs are organized by content hash, not names
- **Works from subdirectories**: Run commands from anywhere in your project

```yaml
# Default: centralized, content-addressed storage
# (no venv_path needed - defaults to ~/.cache/uv-toolbox)
environments:
  - name: formatting
    dependencies: [ruff==0.13.0]
  - name: testing
    dependencies: [pytest==8.0.0]

# Optional: local storage (per-project)
venv_path: .uv-toolbox
```

**How it works:**

- Each environment's venv location is determined by hashing its locked packages
- Projects with identically locked environments automatically share the same
  venv
- Config files are discovered by walking up the directory tree

**Executables:**

By default, shims expose the `console_scripts` declared by the packages in
`dependencies`. Scripts belonging only to transitive dependencies are excluded.
Use `omit_executables` to hide selected scripts, or `executables_override` for
an exact allowlist:

```yaml
environments:
  - name: formatting
    dependencies: [ruff==0.13.0, black]
    omit_executables: [black]
  - name: no-shims
    dependencies: [pytest]
    executables_override: []
```

- **Automatic**: First-order package scripts are exposed without duplication
- **Selective omission**: Remove individual automatically discovered scripts
- **Exact override**: Use any list, including `[]` to expose nothing

The legacy `executables` key emits a deprecation warning and will be removed in
uv-toolbox 1.0. Use `executables_override` instead.

The `requirements` and `requirements_file` keys are deprecated in the same way.
Run `uv-toolbox migrate-config` to convert them to `dependencies` and `sources`
(see [migrating](docs/migrating.md)).

## Usage

Install environments:

```bash
uv-toolbox install
```

Each environment is locked with `uv lock` and installed with `uv sync --frozen`.
Later installs sync from the lock via uv's local cache, with no network and no
re-resolution.

Commit a repo lockfile, which embeds every environment's `uv.lock`:

```bash
uv-toolbox lock                         # writes uv-toolbox.lock, keeping existing pins
uv-toolbox lock --upgrade               # re-resolve everything
uv-toolbox lock --upgrade-package ruff  # re-resolve one package
uv-toolbox lock --check                 # verify in CI, offline, without writing
```

Run a command inside an environment (uses the configured default if set,
otherwise pass `--env`):

```bash
uv-toolbox exec --env env1 -- ruff --version
```

Add shim scripts to your PATH for direct tool access:

```bash
eval "$(uv-toolbox shim)"
```

This creates wrapper scripts for the console scripts declared by each
environment's `dependencies`. Transitive package scripts and the venv's
Python/pip executables are not exposed.

If you use [mise](https://mise.jdx.dev), the mise plugin handles PATH management
automatically on every shell activation — see the
[mise integration docs](docs/mise.md).
