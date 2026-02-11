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
    requirements: |
      ruff==0.13.0
      black
  - name: testing
    requirements: |
      pytest
      pytest-cov
```

## Usage

### Install environments

```bash
uv-toolbox install
```

This creates virtual environments for each defined environment. By default,
venvs are stored in `~/.cache/uv-toolbox/` using content-addressed storage.

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
    requirements: ruff black
```

Then run without `--env`:

```bash
uv-toolbox exec -- ruff check .
```

### Add to PATH

To make tools available directly in your PATH, you need to:

1. Specify which executables to expose in your config:

```yaml
environments:
  - name: formatting
    requirements: |
      ruff==0.13.0
      black
    executables: [ruff, black]  # List executables to expose
  - name: testing
    requirements: pytest
    executables: [pytest]
```

2. Add shims to your PATH:

```bash
eval "$(uv-toolbox shim)"
```

Now tools are available directly:

```bash
ruff check .
black .
pytest
```

**Note**: Only executables listed in the `executables` field will be added to PATH. This prevents Python/pip from the venv from polluting your PATH.

## Configuration Options

### Virtual Environment Location

By default, venvs are stored in `~/.cache/uv-toolbox/` using
[content-addressed storage](content-addressing.md):

```yaml
# Default - no venv_path needed
environments:
  - name: dev
    requirements: ruff pytest
```

For project-local storage:

```yaml
venv_path: .uv-toolbox # Relative to config file
environments:
  - name: dev
    requirements: ruff pytest
```

### Requirements Files

Use a requirements file instead of inline requirements:

```yaml
environments:
  - name: dev
    requirements_file: requirements-dev.txt
```

### Environment Variables

Set environment variables for specific environments:

```yaml
environments:
  - name: testing
    requirements: pytest
    executables: [pytest]
    environment:
      PYTEST_ADDOPTS: '-v --tb=short'
```

### Executables

Specify which executables to expose via shims:

```yaml
environments:
  - name: formatting
    requirements: |
      ruff==0.13.0
      black
    executables: [ruff, black]  # Only these will be available via shims
```

This field is optional and only needed if you plan to use `uv-toolbox shim`. It gives you explicit control over which tools are added to PATH, preventing Python/pip from the venv from polluting your environment.

## Next Steps

- Learn about [content-addressed storage](content-addressing.md)
- See the [full configuration reference](configuration.md) (coming soon)
- Check out [advanced usage patterns](advanced.md) (coming soon)
