# uv-toolbox

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

Emit a POSIX shell snippet that prepends all environment bin paths to `PATH`:

```bash
eval "$(uv-toolbox shim)"
```
