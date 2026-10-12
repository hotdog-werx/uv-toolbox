# Migrating to uv Project Mode

uv-toolbox now builds every environment as a uv project and locks it with
`uv lock` instead of `uv pip compile`. Most of this happens automatically, but a
few things change.

## What happens automatically

- **The lockfile is upgraded.** The first `uvtb install` or `uvtb exec` re-locks
  every environment and rewrites `uv-toolbox.lock` in the new `version: 2`
  format, which embeds each environment's `uv.lock`. Existing version pins are
  carried over wherever they still satisfy your config. Git dependencies are the
  exception: the old format didn't record the ref they came from, so they
  resolve to the commit the ref points to now. Commit the rewritten lockfile.
- **Venvs move.** Venv addresses are now derived from the locked packages, so
  the first install creates new venvs. Old ones in `~/.cache/uv-toolbox/` can be
  deleted.
- **A `.uv-toolbox/` directory appears next to your config file.** It holds the
  generated projects and ignores itself in git. See
  [Locking](locking.md#the-uv-toolbox-working-directory).

`uvtb lock --check` fails on a `version: 1` lockfile until you run `uvtb lock`
once, so update the lockfile before your CI checks it.

## `uvtb lock` keeps pins

Plain `uvtb lock` used to re-resolve everything, like `uv lock --upgrade`. It
now keeps existing pins, like `uv lock`. To pick up new versions:

```bash
uvtb lock --upgrade                 # everything
uvtb lock --upgrade-package ruff    # just ruff (repeatable)
```

## Converting `requirements` / `requirements_file`

The `requirements` and `requirements_file` keys still work, with a deprecation
warning, and will be removed in uv-toolbox 1.0. Convert them to `dependencies`
and `sources` with:

```bash
uvtb migrate-config
```

It converts each deprecated environment using the same `uv add` conversion
uv-toolbox runs when locking, prints a diff, and asks before writing (`--yes`
skips the prompt). For example:

```yaml
# before
environments:
  - name: repolish
    requirements: |
      repolish>=1.4.0,<2
      git+https://github.com/hotdog-werx/devkit.git@v0#subdirectory=packages/devkit-python
```

```yaml
# after
environments:
  - name: repolish
    dependencies:
      - hotdogwerx-devkit-python
      - repolish>=1.4.0,<2
    sources:
      hotdogwerx-devkit-python:
        git: https://github.com/hotdog-werx/devkit.git
        subdirectory: packages/devkit-python
        rev: v0
```

Some things to know:

- The config file is rewritten from its parsed contents, so **comments and
  formatting are not preserved**. Review the diff, and re-add comments (or tidy
  `rev:` into `tag:`) by hand if you like.
- `pyproject.toml` configs are never rewritten. Instead, the command prints the
  replacement `[[tool.uv-toolbox.environments]]` tables for you to paste in.
- `requirements_file` files are left on disk.
- Index and constraint options become `uv` settings; see
  [Configuration](configuration.md#deprecated-requirements-and-requirements_file).

Run `uvtb lock` afterwards. The fingerprints change, but the locked packages
stay the same.

## uv version

uv-toolbox now needs uv 0.8.0 or newer.
