# mise Integration

`uv-toolbox` ships a [mise](https://mise.jdx.dev) env plugin that automatically
adds your shims to `PATH` on every shell activation — no `eval` in your shell
rc required.

## Setup

Add the plugin to your project's `mise.toml`:

```toml
[plugins]
uv-toolbox = "https://github.com/hotdog-werx/uv-toolbox"
```

Then wire up installation so shims exist before mise tries to expose them:

```toml
[hooks]
postinstall = "uv-toolbox install"
```

Now a single command sets everything up:

```bash
mise install
```

On the next shell activation, mise calls the plugin, which runs
`uv-toolbox shim --list-paths` and prepends the shim directories to `PATH`.
Your tools are available directly:

```bash
ruff check .
pytest
```

## How it works

The plugin consists of two Lua hooks in `mise-plugin/`:

- `mise_env.lua` — required by mise; returns no extra environment variables
- `mise_path.lua` — returns the list of shim directories for mise to prepend to
  `PATH`

Every time mise activates (new shell, `cd`), it calls `mise_path.lua`, which
recreates shims and returns their directories. If no venvs have been installed
yet, the hook returns nothing and `PATH` is left unchanged.

## Custom config path

If your `uv-toolbox.yaml` is not in the default location, pass the path via the
plugin config:

```toml
[plugins]
uv-toolbox = "https://github.com/hotdog-werx/uv-toolbox"

[env]
_.uv-toolbox.config = "./path/to/uv-toolbox.yaml"
```
