# uv-toolbox

**Declarative Python tool environment management with uv.**

Stop juggling multiple tool installations and conflicting dependencies.
`uv-toolbox` lets you define all your Python development tools (linters,
formatters, testing tools) in a single configuration file, each with their own
isolated environment and pinned versions.

## Why use uv-toolbox?

- **One config, multiple environments**: Define all your tools in YAML or TOML
  with exact versions
- **Zero conflicts**: Each tool gets its own isolated virtual environment
  powered by uv's blazing-fast resolver
- **Automatic deduplication**: Identical requirements across projects share the
  same venv via content-addressed storage
- **Fast repeated installs**: After the first install, subsequent runs sync from
  a local pinned lockfile using uv's package cache — no network, no
  re-resolution
- **Reproducible across teams**: Commit `uv-toolbox.lock` to lock down exact
  versions with hashes for all platform variants via `uvtb lock`
- **Works from subdirectories**: Run commands from anywhere in your project
- **Simple execution**: Run tools with `uv-toolbox exec`, add them to PATH with
  `eval "$(uv-toolbox shim)"`, or use the [mise plugin](mise.md) for zero-config
  automatic PATH management
- **Leverage uv speed**: Built on top of [uv](https://github.com/astral-sh/uv),
  the ultra-fast Python package installer

Perfect for projects that need consistent tooling without polluting your global
Python installation or dealing with dependency conflicts between tools.

## Quick Example

```yaml
# uv-toolbox.yaml
environments:
  - name: formatting
    requirements: |
      ruff==0.13.0
      black==24.1.0
    executables: [ruff, black]
  - name: testing
    requirements: |
      pytest==8.0.0
      pytest-cov==4.0.0
    executables: [pytest]
```

```bash
# Install all tool environments
uv-toolbox install

# Run a tool
uv-toolbox exec --env formatting -- ruff check .

# Or add all tools to PATH
eval "$(uv-toolbox shim)"
ruff check .  # now available directly
```

[Get Started →](quick-start.md)

## Documentation

- [Quick Start](quick-start.md)
- [Locking](locking.md) — offline-first installs, `uvtb lock`, and reproducible
  team setups
- [mise Integration](mise.md) — automatic PATH management via the mise plugin
- [Content-Addressed Storage](content-addressing.md)
