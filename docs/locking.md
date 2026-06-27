# Locking

`uv-toolbox` has two complementary locking mechanisms: a **machine lockfile** that
speeds up repeated installs automatically, and a **repo lockfile** that you commit
to version control for reproducible, hash-verified installs across the team.

## Machine Lockfile (Automatic)

The first time you run `uvtb install`, uv resolves your requirements online and
writes a pinned machine lockfile next to the venv directory:

```
~/.cache/uv-toolbox/
  ├── a3f7e2d4c1b9/        # venv
  └── a3f7e2d4c1b9.lock    # machine lockfile (auto-generated)
```

On every subsequent install, `uvtb` syncs from this lockfile using uv's local
package cache — **no network, no re-resolution**:

```bash
uvtb install  # warm cache: completes in milliseconds
```

If the cache is cold (e.g. a fresh machine), `uvtb` falls back to an online sync
automatically, then the cache is warm for next time.

The machine lockfile is a sibling of the venv directory so `--clear` never deletes
it.

### Upgrading

To re-resolve dependencies and refresh pinned versions:

```bash
uvtb install --upgrade   # or -u
```

This deletes the machine lockfile and runs a full online resolution, capturing
the latest versions that satisfy your requirements.

## Repo Lockfile (`uv-toolbox.lock`)

For teams that need reproducible installs across machines, you can generate a
committed lockfile with exact pinned versions **and hashes** for all platform
wheel variants:

```bash
uvtb lock
```

This generates `uv-toolbox.lock` next to your config file using
`uv pip compile --generate-hashes --universal` — the same cross-platform,
hash-verified format used by uv's own lockfile.

### What it looks like

```yaml
version: 1
environments:
  formatting:
    requirements: |
      ruff==0.14.14 \
          --hash=sha256:aaaa... \
          --hash=sha256:bbbb...
  testing:
    requirements: |
      pytest==9.0.2 \
          --hash=sha256:cccc...
```

One file covers all environments. Each environment's requirements block contains
pinned versions with hashes for every supported platform wheel variant.

### Benefits

- **Security**: Hash verification catches tampered or corrupted packages
- **Reproducibility**: Everyone installs exactly the same packages, everywhere
- **Deliberate upgrades**: Version changes require an explicit `uvtb lock` run and
  a visible diff in version control
- **Cross-platform**: Hashes for all platform variants are pre-computed, so any
  machine can verify installs without network access to fetch metadata

### Workflow

```bash
# Initial setup or after changing requirements
uvtb lock    # resolve and write uv-toolbox.lock
git add uv-toolbox.lock
git commit -m "chore: update tool lockfile"

# Everyone else
uvtb install  # installs from the committed lockfile
```

When `uv-toolbox.lock` is present, `uvtb install` uses the pre-resolved,
hash-verified requirements from the lockfile. The machine lockfile is still
written after the first install, so subsequent runs remain fast.

### Updating the lockfile

Re-run `uvtb lock` whenever you want to pick up new versions:

```bash
uvtb lock    # re-resolves from current requirements
```

This replaces `uv-toolbox.lock` with freshly resolved, pinned content. Review
the diff, commit when satisfied.

## How the Two Lockfiles Interact

| Scenario | What happens |
|---|---|
| No lockfiles exist | Online resolve → write machine lockfile |
| Machine lockfile exists | Offline-first sync from machine lockfile |
| Repo lockfile exists, no machine lockfile | Install from repo lockfile → write machine lockfile |
| Both exist | Offline-first sync from machine lockfile |
| `--upgrade` flag | Delete machine lockfile → online re-resolve |

## Effect on Content-Addressed Storage

When `uv-toolbox.lock` is present, the CAS hash used to locate each venv is
derived from the **resolved lockfile content**, not the raw requirements. This
means running `uvtb lock` (which changes package versions) followed by
`uvtb install` will use a **new venv** — the old one is untouched and can be
cleaned up manually.

See [Content-Addressed Storage](content-addressing.md) for details.
