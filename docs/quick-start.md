# Quick Start

`uv-toolbox` is a CLI tool for managing Python tool environments. Create
multiple virtual environments and manage their dependencies through a
declarative configuration file.

## Installation

```bash
pipx install uv-toolbox
```

## Basic Example

Create a `uv-toolbox.yaml` file in your project:

```yaml
environments:
  - name: formatting
    dependencies:
      - ruff==0.13.0
      - black
  - name: testing
    dependencies: [pytest, pytest-cov]
```

`dependencies` takes PEP 508 requirement strings, exactly like a
`pyproject.toml`. Packages that aren't on PyPI go in `sources`; see
[Configuration](configuration.md).

## Usage

### Install environments

```bash
uv-toolbox install
```

This locks each environment with uv and installs it into its own virtual
environment. By default, venvs are stored in `~/.cache/uv-toolbox/` using
content-addressed storage.

The lock is kept next to each venv, so later installs sync from it using uv's
local package cache without resolving again or touching the network.

To re-resolve and pick up newer versions:

```bash
uv-toolbox install --upgrade  # or -u
```

### Run commands

Run a command inside a specific environment:

```bash
uv-toolbox exec --env formatting -- ruff check .
```

Or set a default environment in your config:

```yaml
default_environment: formatting
environments:
  - name: formatting
    dependencies: [ruff, black]
```

Then run without `--env`:

```bash
uv-toolbox exec -- ruff check .
```

### Add to PATH

The packages listed in `dependencies` automatically expose their declared
console scripts:

```yaml
environments:
  - name: formatting
    dependencies: [ruff==0.13.0, black]
  - name: testing
    dependencies: [pytest]
```

Add their shims to your PATH:

```bash
eval "$(uv-toolbox shim)"
```

Now tools are available directly:

```bash
ruff check .
black .
pytest
```

Only scripts declared by the packages in `dependencies` are added. Scripts from
transitive dependencies and the venv's Python/pip executables are excluded.

**Tip**: If you use [mise](https://mise.jdx.dev), the [mise plugin](mise.md)
handles this automatically on every shell activation — no `eval` needed.

## Configuration Options

### Virtual Environment Location

By default, venvs are stored in `~/.cache/uv-toolbox/` using
[content-addressed storage](content-addressing.md). For project-local storage:

```yaml
venv_path: .uv-toolbox # Relative to config file
environments:
  - name: dev
    dependencies: [ruff, pytest]
```

### Git and local sources

```yaml
environments:
  - name: tools
    dependencies: [my-tool, my-plugin]
    sources:
      my-tool:
        git: https://github.com/example/my-tool.git
        tag: v1.2.0
      my-plugin:
        path: plugins/my-plugin # relative to the config file
        editable: true
```

### Environment Variables

Set environment variables for specific environments:

```yaml
environments:
  - name: testing
    dependencies: [pytest]
    environment:
      PYTEST_ADDOPTS: '-v --tb=short'
```

### Executables

Use `omit_executables` to hide some automatically exposed scripts, or
`executables_override` for an exact list (`[]` disables shims):

```yaml
environments:
  - name: formatting
    dependencies: [ruff==0.13.0, black]
    omit_executables: [black]
  - name: testing
    dependencies: [pytest]
    executables_override: []
```

See [Configuration](configuration.md) for everything else, including
`requires_python` and the `uv` settings passthrough.

### Generate a repo lockfile

For reproducible installs across the team, commit a lockfile:

```bash
uv-toolbox lock          # write uv-toolbox.lock
uv-toolbox lock --check  # verify it in CI, without writing
```

`uv-toolbox.lock` embeds the `uv.lock` of every environment. `uvtb install`
installs exactly what it lists, and `uvtb lock` keeps existing pins until you
pass `--upgrade` or `--upgrade-package`. See [Locking](locking.md) for the full
workflow.

### Coming from `requirements`?

Environments declared with `requirements` or `requirements_file` still work, but
those keys are deprecated. `uvtb migrate-config` converts them; see
[Migrating](migrating.md).

## Next Steps

- Read the full [configuration reference](configuration.md)
- Learn about [locking](locking.md): offline installs, team lockfiles, and
  upgrades
- Set up the [mise plugin](mise.md) for automatic PATH management
- Learn about [content-addressed storage](content-addressing.md)
