# Content-Addressed Storage

UV Toolbox uses **content-addressed storage** for virtual environments, similar
to how Docker and `uv tool` work. This provides automatic deduplication and
eliminates naming conflicts.

## Overview

Each virtual environment's location is determined by hashing **what it
installs**, not by user-defined names. With a repo lockfile, that's the locked
packages; without one, it's the environment's declared inputs.

```yaml
environments:
  - name: formatting
    dependencies: [ruff==0.13.0, black==24.0.0]
  - name: testing
    dependencies: [pytest==8.0.0]
```

Results in:

```
~/.cache/uv-toolbox/
  ├── a3f7e2d4c1b9/  # formatting
  └── 9c8b7f6e5d4a/  # testing
```

**Users still reference by name:**

```bash
uvtb exec --env formatting -- ruff check
```

The tool internally maps environment name → content hash → venv path.

## Benefits

### Automatic Deduplication

**Same packages = same venv across projects:**

```yaml
# Project A - web app
environments:
  - name: dev
    dependencies: [ruff==0.13.0]

# Project B - CLI tool
environments:
  - name: formatting
    dependencies: [ruff==0.13.0]
```

Once both are locked to the same packages, they share one venv in
`~/.cache/uv-toolbox/`, **saving disk space and setup time!**

### No Naming Conflicts

Multiple projects can have environments with the same name without collision:

- Project A's "dev" environment
- Project B's "dev" environment
- Both work perfectly because they're stored by content hash, not name

### Zero Configuration

No manual `project_name` needed - the hash provides automatic isolation:

```yaml
# Just define your environments - it works!
environments:
  - name: dev
    dependencies: [ruff, pytest]
```

### Works from Subdirectories

Config files are discovered by walking up the directory tree, so you can run
commands from anywhere in your project.

## How It Works

### With a repo lockfile

When `uv-toolbox.lock` has an entry for the environment, the hash covers the
**locked packages**: each package's name, version and source, taken from the
embedded `uv.lock`. Everything else in the lock is ignored, including wheel URLs
and upload times, uv's lock format `revision`, and the generated project's own
entry. As a result:

- Environments locked to the same packages share a venv, whatever their names or
  how their dependencies were written.
- A uv upgrade that only changes the lock format doesn't move environments to
  new venvs.

### Without a repo lockfile

The hash covers the environment's **declared inputs**: `dependencies`,
`sources`, `requires_python` and the `uv` settings. For environments using the
deprecated `requirements` / `requirements_file`, the requirements are normalized
first (comments and blank lines removed, lines stripped and sorted), and a
`requirements_file` is hashed by its contents, not its path.

The first install locks the environment and keeps that lock next to the venv
(see [Locking](locking.md#machine-lock)).

### Hash Generation

- **Algorithm**: SHA-256 (truncated to 12 hex characters)
- **Format**: `a3f7e2d4c1b9` (12 chars = 48 bits)
- **Collision probability**: Negligible (~2.4M venvs needed for a 1% collision
  chance)

## Configuration

### Default: Centralized Storage

By default, venvs are stored in `~/.cache/uv-toolbox/`:

```yaml
# No venv_path needed - uses default
environments:
  - name: dev
    dependencies: [ruff, pytest]
```

**Platform-specific defaults:**

- Linux: `~/.cache/uv-toolbox/`
- macOS: `~/.cache/uv-toolbox/`
- Windows: `C:\Users\<username>\.cache\uv-toolbox\`

### Local Storage (Optional)

You can still use project-local storage:

```yaml
venv_path: .uv-toolbox # Relative to config file
environments:
  - name: dev
    dependencies: [ruff, pytest]
```

This stores venvs in `.uv-toolbox/<hash>/` within your project.

## Interaction with the Repo Lockfile

Because the hash follows the locked packages:

- Running `uvtb lock` in a way that changes package versions changes the hash
- The next `uvtb install` after such a `uvtb lock` creates a **new venv** at the
  new hash address
- The old venv is left untouched and can be cleaned up manually

This is intentional: the venv address reflects what's actually installed, not
just what was asked for.

## Edge Cases

### Manual Package Installation

If you manually install packages:

```bash
uvtb exec --env dev -- uv pip install extra-package
```

The venv is modified but the hash doesn't change. This is **by design**:

- The hash is based on the lock (or the declared inputs)
- Manual changes don't break the mapping
- You're responsible for undeclared dependencies
- The next `uvtb install` syncs the venv back to exactly what the lock lists,
  removing the extra package

## Migration from Previous Versions

Moving to uv project mode changed how hashes are computed, so the first install
after upgrading creates new venvs; see [Migrating](migrating.md).

**Breaking change** in v0.1.0:

- **Old**: Venvs stored at `venv_path/<env-name>/`
- **New**: Venvs stored at `venv_path/<content-hash>/`

**To migrate:**

1. Upgrade to v0.1.0+
2. Run `uvtb install` to recreate venvs with new hashing
3. Old venvs in `.uv-toolbox/` can be safely deleted

## Technical Details

For more implementation details, see the source code:

- [`uv_toolbox/settings.py`](https://github.com/hotdog-werx/uv-toolbox/blob/master/uv_toolbox/settings.py)
  — `UvToolboxEnvironment._get_requirements_hash()` and
  `locked_packages_digest()` (hashing) and `UvToolboxEnvironment.venv_path()`
  (path resolution)
