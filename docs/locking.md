# Locking

`uv-toolbox` has two complementary locking mechanisms: a **machine lockfile**
that speeds up repeated installs automatically, and a **repo lockfile** that you
commit to version control for reproducible, hash-verified installs across the
team.

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

The machine lockfile is a sibling of the venv directory so `--clear` never
deletes it.

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
    fingerprint: sha256:3b1f...
    requirements: |
      ruff==0.14.14 \
          --hash=sha256:aaaa... \
          --hash=sha256:bbbb...
  testing:
    fingerprint: sha256:9c0e...
    requirements: |
      pytest==9.0.2 \
          --hash=sha256:cccc...
```

One file covers all environments. Each environment's requirements block contains
pinned versions with hashes for every supported platform wheel variant.

The `fingerprint` is a digest of the inputs the environment was locked from: its
declared requirements (inline, or the contents of its `requirements_file`) and
its configured `environment` variables. Reordering requirements or adding
comments and blank lines does not change it. Environment variables are
fingerprinted before `$VAR` expansion, so machine-specific values such as
`$HOME` never trigger a re-lock.

### Benefits

- **Security**: Hash verification catches tampered or corrupted packages
- **Reproducibility**: Everyone installs exactly the same packages, everywhere
- **Deliberate upgrades**: Version changes require an explicit `uvtb lock` run
  and a visible diff in version control
- **Cross-platform**: Hashes for all platform variants are pre-computed, so any
  machine can verify installs without network access to fetch metadata

### Workflow

```bash
# Initial setup or after changing requirements
uvtb lock    # resolve and write uv-toolbox.lock
uvtb lock --check  # verify without writing; suitable for CI
git add uv-toolbox.lock
git commit -m "chore: update tool lockfile"

# Everyone else
uvtb install  # installs from the committed lockfile
```

When `uv-toolbox.lock` is present, `uvtb install` uses the pre-resolved,
hash-verified requirements from the lockfile. The machine lockfile is still
written after the first install, so subsequent runs remain fast.

### Automatic re-locking

`uvtb install` and `uvtb exec` compare each environment's fingerprint with its
current configuration before installing. If you change an environment's
requirements (or its `environment` variables) without running `uvtb lock`, that
environment is re-locked automatically and `uv-toolbox.lock` is rewritten:

```text
Lockfile is out of date; re-locking: formatting
Updated /path/to/uv-toolbox.lock
```

Only changed environments are re-resolved, and their existing pins are kept
wherever they still satisfy the new requirements, so the diff contains just what
your change required. Environments you haven't touched are copied unchanged,
environments added to the config are locked, and entries for environments
removed from the config are dropped. Commit the updated lockfile along with the
config change.

Lockfiles written by older uv-toolbox versions have no fingerprints. They are
treated as out of date and re-locked on the next `uvtb install`, which adds the
fingerprints. `uvtb shim` never re-locks; it keeps pointing at the existing
venvs until the next install.

Files pulled in from a `requirements_file` with `-r` or `-c` are not
fingerprinted. Run `uvtb lock` after editing those.

### Updating the lockfile

Re-run `uvtb lock` whenever you want to pick up new versions:

```bash
uvtb lock    # re-resolves from current requirements
```

This replaces `uv-toolbox.lock` with freshly resolved, pinned content. Review
the diff, commit when satisfied.

`uvtb lock --check` seeds resolution with the committed pins and exits nonzero
when the lockfile is missing or no longer satisfies the configured requirements.
It also fails, without resolving anything, when an environment's fingerprint is
missing or doesn't match its configuration. Unlike `install`, it never re-locks,
so CI catches config changes committed without an updated lockfile, as well as
lockfiles written before fingerprints existed. Run `uvtb lock` once to add them.
Compatible pins are preserved, so publishing a newer transitive dependency does
not create unrelated CI drift. It never rewrites or upgrades the file; run plain
`uvtb lock` when you deliberately want the latest compatible versions.

## How the Two Lockfiles Interact

| Scenario                                  | What happens                                        |
| ----------------------------------------- | --------------------------------------------------- |
| Repo lockfile stale for an environment    | Re-lock that environment → rewrite repo lockfile    |
| No lockfiles exist                        | Online resolve → write machine lockfile             |
| Machine lockfile exists                   | Offline-first sync from machine lockfile            |
| Repo lockfile exists, no machine lockfile | Install from repo lockfile → write machine lockfile |
| Both exist                                | Offline-first sync from machine lockfile            |
| `--upgrade` flag                          | Delete machine lockfile → online re-resolve         |

## CI Caching

In CI, two caches are worth preserving between runs:

| Cache path             | Key                       | What it buys                           |
| ---------------------- | ------------------------- | -------------------------------------- |
| `~/.cache/uv/`         | hash of `uv-toolbox.lock` | Skips downloading wheels (biggest win) |
| `~/.cache/uv-toolbox/` | hash of `uv-toolbox.lock` | Skips venv creation and sync entirely  |

Both caches should bust when `uv-toolbox.lock` changes. If your project also
uses uv to manage its own dependencies (i.e. you have a `uv.lock` alongside
`uv-toolbox.lock`), keep them as **separate cache entries** with separate keys —
mixing them causes spurious cache misses when only one file changes.

### GitHub Actions example

```yaml
- uses: actions/cache@v6.1.0
  with:
    path: |
      ~/.cache/uv
      ~/.cache/uv-toolbox
    key: uv-${{ hashFiles('uv.lock', 'uv-toolbox.lock') }}
    restore-keys: |
      uv-
```

`~/.cache/uv` is uv's package cache and is shared between uv-toolbox and any
uv-managed project venvs, so both paths belong in the same cache entry. The key
covers both lockfiles — `hashFiles` silently ignores any that don't exist, so
this works whether or not your project also has a `uv.lock`.

On a cache hit, `uvtb install` completes offline in milliseconds. On a miss
(first run or after `uvtb lock`), it downloads and resolves normally and primes
the cache for the next run.

## Effect on Content-Addressed Storage

When `uv-toolbox.lock` is present, the CAS hash used to locate each venv is
derived from the **resolved lockfile content**, not the raw requirements. This
means running `uvtb lock` (which changes package versions) followed by
`uvtb install` will use a **new venv** — the old one is untouched and can be
cleaned up manually.

See [Content-Addressed Storage](content-addressing.md) for details.
