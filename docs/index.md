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
- **Familiar dependency syntax**: `dependencies`, `sources` and `[tool.uv]`
  settings work just like they do in a uv project, because each environment is
  one
- **Automatic deduplication**: Identically locked environments across projects
  share the same venv via content-addressed storage
- **Fast repeated installs**: Installs sync from a lock using uv's package
  cache, with no network and no re-resolution
- **Reproducible across teams**: Commit `uv-toolbox.lock`, which embeds uv's own
  `uv.lock` for every environment, with hashes for all platforms
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
    dependencies: [ruff==0.13.0, black==24.1.0]
  - name: testing
    dependencies: [pytest==8.0.0, pytest-cov==4.0.0]
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
- [Configuration](configuration.md): dependencies, sources, Python versions and
  uv settings
- [Migrating to uv Project Mode](migrating.md): what changed, and
  `uvtb migrate-config`
- [Locking](locking.md) — offline-first installs, `uvtb lock`, and reproducible
  team setups
- [mise Integration](mise.md) — automatic PATH management via the mise plugin
- [Content-Addressed Storage](content-addressing.md)
