# Locking

Every environment is backed by a small uv project that uv-toolbox generates from
your config, so locking and installing use uv's own `uv lock` and `uv sync`. You
get uv's lock semantics: pins are kept until you ask to upgrade them, git
dependencies record which tag a commit came from, and checking a lock needs no
network.

There are two places a lock can live:

- the **repo lockfile**, `uv-toolbox.lock`, which you commit for reproducible
  installs across a team and in CI;
- a **machine lock** next to each venv, used when there is no repo lockfile.

## Repo Lockfile (`uv-toolbox.lock`)

```bash
uvtb lock
```

This locks every environment and writes `uv-toolbox.lock` next to your config
file. Commit it.

### What it looks like

One file covers all environments. Each environment embeds the `uv.lock` uv
produced for it:

```yaml
version: 2
environments:
  formatting:
    fingerprint: sha256:3b1f...
    uv_lock: |
      version = 1
      revision = 3
      requires-python = ">=3.12"

      [[package]]
      name = "ruff"
      version = "0.14.14"
      source = { registry = "https://pypi.org/simple" }
      sdist = { url = "https://files.pythonhosted.org/...", hash = "sha256:...", size = 4499326 }
      wheels = [
          { url = "https://files.pythonhosted.org/...", hash = "sha256:...", size = 10766236 },
          ...
      ]
```

The embedded `uv.lock` is exactly what uv wrote, including hashes for every
platform's wheels, so diffs in code review read like any other `uv.lock` diff.

Git dependencies record both the ref you asked for and the commit it resolved
to:

```toml
source = { git = "https://github.com/example/tools.git?subdirectory=packages%2Ftool&tag=v1#3e005e37785ae8f70c5777fe5cf76ad1cb01bcc1" }
```

The `fingerprint` is a digest of the inputs the environment was locked from:
`dependencies`, `sources`, `requires_python`, the `uv` settings and the
`environment` variables (or, for environments still using the deprecated
`requirements` / `requirements_file`, the normalized requirements). Environment
variables are fingerprinted before `$VAR` expansion, so machine-specific values
such as `$HOME` never trigger a re-lock.

### Workflow

```bash
# After changing an environment
uvtb lock          # lock and write uv-toolbox.lock
uvtb lock --check  # verify without writing; suitable for CI
git add uv-toolbox.lock
git commit -m "chore: update tool lockfile"

# Everyone else
uvtb install       # installs exactly what uv-toolbox.lock lists
```

### Upgrading

`uvtb lock` keeps existing pins wherever they still satisfy the config, like
`uv lock`. Running it twice in a row changes nothing, and a newly published
release (or a tag that moved upstream) is not picked up until you ask:

```bash
uvtb lock --upgrade                 # re-resolve every package
uvtb lock --upgrade-package ruff    # re-resolve only ruff (repeatable, or -P)
uvtb lock --refresh                 # ignore cached package metadata
```

Review the diff and commit when satisfied.

### Checking in CI

`uvtb lock --check` exits nonzero, without writing anything, when:

- `uv-toolbox.lock` is missing, or was written in the old `version: 1` format;
- an environment's fingerprint doesn't match its configuration (the config
  changed without re-locking);
- `uv lock --check` reports that an environment's lock no longer satisfies its
  dependencies.

uv validates an existing lock without contacting indexes or git remotes, so the
check is fast and only fails when your configuration changed. New upstream
releases and moved tags never fail it. (The exception is an environment still
using the deprecated `requirements` with a git URL that has no package name:
converting it means fetching the URL to learn the name. Run
`uvtb migrate-config` to avoid this.)

### Automatic re-locking

`uvtb install` and `uvtb exec` compare each environment's fingerprint with its
current configuration before installing. If you change an environment without
running `uvtb lock`, that environment is re-locked automatically and
`uv-toolbox.lock` is rewritten:

```text
Lockfile is out of date; re-locking: formatting
Updated /path/to/uv-toolbox.lock
```

Re-locking starts from the environment's existing lock, so only the packages
your change affects move. Environments you haven't touched are copied unchanged,
environments added to the config are locked, and entries for removed
environments are dropped. Commit the updated lockfile along with the config
change. `uvtb shim` never re-locks; it keeps pointing at the existing venvs
until the next install.

Files pulled in from a deprecated `requirements_file` with `-r` or `-c` are not
fingerprinted. Run `uvtb lock` after editing those.

## Machine Lock

Without a repo lockfile, the first `uvtb install` locks each environment online
and keeps the resulting `uv.lock` next to its venv:

```
~/.cache/uv-toolbox/
  ├── a3f7e2d4c1b9/          # venv
  └── a3f7e2d4c1b9.uv.lock   # machine lock (auto-generated)
```

Later installs sync from it without resolving again. To re-resolve and pick up
newer versions:

```bash
uvtb install --upgrade   # or -u
```

## Installing

Installing runs `uv sync --frozen` against the environment's lock, with uv's
`UV_PROJECT_ENVIRONMENT` pointing at the content-addressed venv. It tries
`--offline` first, so with a warm uv cache an install needs no network at all,
and falls back to an online sync when the cache is cold.

| Scenario                               | What happens                                        |
| -------------------------------------- | --------------------------------------------------- |
| Repo lockfile current for environment  | Frozen sync from its embedded `uv.lock`             |
| Repo lockfile stale for an environment | Re-lock that environment → rewrite repo lockfile    |
| Repo lockfile in the `version: 1` form | Re-lock every environment → rewrite as `version: 2` |
| No repo lockfile, no machine lock      | Lock online → write machine lock → sync             |
| No repo lockfile, machine lock exists  | Frozen sync from the machine lock                   |
| `--upgrade`, no repo lockfile          | Re-lock from scratch → replace machine lock → sync  |
| `--clear`                              | Delete the venv, then sync it again                 |

## The `.uv-toolbox/` working directory

uv-toolbox writes each environment's generated project to
`.uv-toolbox/projects/<environment>/` next to your config file. It contains a
`pyproject.toml` and the `uv.lock` uv-toolbox is currently working with, which
is handy when you want to see exactly what uv was given. The directory carries
its own `.gitignore`, so it never shows up in `git status`. It's rewritten on
every lock and install and can be deleted at any time.

The location is fixed relative to the config file on purpose: uv records local
`path` sources relative to the project, so this keeps those paths identical on
every machine.

## Python version

A uv lock is made for a range of Python versions (`requires-python`). Set it per
environment with `requires_python`. When you don't, uv-toolbox keeps the range
recorded in the existing lock, and a first lock uses the minor version of the
interpreter uv finds by default (for example `>=3.13`). Set `requires_python`
explicitly if your team uses different Python versions.

## uv version

uv-toolbox needs uv 0.8.0 or newer and checks this before locking or installing.
A committed lock also needs a uv recent enough to read it, so keep the uv
version in step across the team (for example by pinning it with mise).

## CI Caching

In CI, two caches are worth preserving between runs:

| Cache path             | Key                       | What it buys                           |
| ---------------------- | ------------------------- | -------------------------------------- |
| `~/.cache/uv/`         | hash of `uv-toolbox.lock` | Skips downloading wheels (biggest win) |
| `~/.cache/uv-toolbox/` | hash of `uv-toolbox.lock` | Skips venv creation and sync entirely  |

Both caches should bust when `uv-toolbox.lock` changes.

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
covers both lockfiles. `hashFiles` silently ignores any that don't exist, so
this works whether or not your project also has a `uv.lock`.

On a cache hit, `uvtb install` completes offline. On a miss (first run or after
`uvtb lock`), it downloads packages and primes the cache for the next run.

## Effect on Content-Addressed Storage

When `uv-toolbox.lock` is present, the CAS hash used to locate each venv is
derived from the **locked packages** (each package's name, version and source),
not from the configuration. Running `uvtb lock` in a way that changes package
versions, followed by `uvtb install`, therefore uses a **new venv**; the old one
is untouched and can be cleaned up manually. Changes to uv's lock format alone
do not move environments to new venvs.

See [Content-Addressed Storage](content-addressing.md) for details.
