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
    executables: [ruff, black]
  - name: env2
    requirements: |
      isort
      flake8
    executables: [isort, flake8]
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

The `executables` field controls which tools are exposed via shims:

```yaml
environments:
  - name: formatting
    requirements: ruff==0.13.0
    executables: [ruff] # Only ruff will be available in PATH via shims
```

- **Optional**: Only needed if you want to use `uv-toolbox shim`
- **Explicit control**: List exactly which executables to expose
- **Prevents PATH pollution**: Python/pip from the venv won't be added to PATH

## Usage

Install environments:

```bash
uv-toolbox install
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

This creates wrapper scripts for executables listed in the `executables` field
of each environment. Only explicitly listed executables are exposed, preventing
Python/pip from polluting your PATH.
