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

## Usage

Install environments:

```bash
uv-toolbox install
```

Run a command inside an environment (uses the configured default if set, otherwise pass `--env`):

```bash
uv-toolbox exec --env env1 -- ruff --version
```

Emit a POSIX shell snippet that prepends all environment bin paths to `PATH`:

```bash
eval "$(uv-toolbox shim)"
```
