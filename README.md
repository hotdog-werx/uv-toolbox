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
    requirements: |
      ruff==0.13.0
      black
  - name: env2
    requirements: |
      isort
      flake8
```

### Configuration Options

**Virtual Environment Location:**

By default, virtual environments are stored in `~/.cache/uv-toolbox/` using
**content-addressed storage**. This means:

- **Automatic deduplication**: Identical requirements across projects share the
  same venv
- **No naming conflicts**: Venvs are organized by content hash, not names
- **Works from subdirectories**: Run commands from anywhere in your project

```yaml
# Default: centralized, content-addressed storage
# (no venv_path needed - defaults to ~/.cache/uv-toolbox)
environments:
  - name: formatting
    requirements: ruff==0.13.0
  - name: testing
    requirements: pytest==8.0.0

# Optional: local storage (per-project)
venv_path: .uv-toolbox
```

**How it works:**

- Each environment's venv location is determined by hashing its requirements
- Projects with identical requirements automatically share the same venv
- Config files are discovered by walking up the directory tree

**Executables:**

By default, shims expose the `console_scripts` declared by first-order
requirements. Scripts belonging only to transitive dependencies are excluded.
Use `omit_executables` to hide selected scripts, or `executables_override` for
an exact allowlist:

```yaml
environments:
  - name: formatting
    requirements: |
      ruff==0.13.0
      black
    omit_executables: [black]
  - name: no-shims
    requirements: pytest
    executables_override: []
```

- **Automatic**: First-order package scripts are exposed without duplication
- **Selective omission**: Remove individual automatically discovered scripts
- **Exact override**: Use any list, including `[]` to expose nothing

The legacy `executables` key emits a deprecation warning and will be removed in
uv-toolbox 1.0. Use `executables_override` instead.

## Usage

Install environments:

```bash
uv-toolbox install
```

After the first install, a pinned lockfile is written alongside each venv.
Subsequent installs sync from it via uv's local cache — no network, no
re-resolution. To upgrade to newer versions:

```bash
uv-toolbox install --upgrade   # re-resolve and refresh the lockfile
```

Generate a committed repo lockfile with hashes for all platform variants:

```bash
uv-toolbox lock   # writes uv-toolbox.lock next to your config file
uv-toolbox lock --check  # verifies existing pins remain valid without upgrading
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
environment's first-order requirements. Transitive package scripts and the
venv's Python/pip executables are not exposed.

If you use [mise](https://mise.jdx.dev), the mise plugin handles PATH management
automatically on every shell activation — see the
[mise integration docs](docs/mise.md).
