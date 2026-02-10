# Content-Addressed Storage

UV Toolbox uses **content-addressed storage** for virtual environments, similar
to how Docker and `uv tool` work. This provides automatic deduplication and
eliminates naming conflicts.

## Overview

Each virtual environment's location is determined by hashing its **normalized
requirements**, not by user-defined names.

```yaml
environments:
  - name: formatting
    requirements: ruff==0.13.0 black==24.0.0
  - name: testing
    requirements: pytest==8.0.0
```

Results in:

```
~/.cache/uv-toolbox/
  ├── a3f7e2d4c1b9/  # hash of "black==24.0.0\nruff==0.13.0"
  └── 9c8b7f6e5d4a/  # hash of "pytest==8.0.0"
```

**Users still reference by name:**

```bash
uvtb exec --env formatting -- ruff check
```

The tool internally maps environment name → requirements hash → venv path.

## Benefits

### Automatic Deduplication

**Same requirements = same venv across projects:**

```yaml
# Project A - web app
environments:
  - name: dev
    requirements: ruff==0.13.0

# Project B - CLI tool
environments:
  - name: formatting
    requirements: ruff==0.13.0
```

Both projects automatically share `~/.cache/uv-toolbox/acadbba99747/` - **saving
disk space and setup time!**

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
    requirements: ruff pytest
```

### Works from Subdirectories

Config files are discovered by walking up the directory tree, so you can run
commands from anywhere in your project.

## How It Works

### Normalization

Requirements are normalized before hashing to ensure consistency:

```python
# These all hash to the same value:
"ruff==0.13.0\npytest==8.0.0"
"pytest==8.0.0\nruff==0.13.0"  # Different order
"  ruff==0.13.0  \n  pytest==8.0.0  "  # Extra whitespace
"# comment\nruff==0.13.0\npytest==8.0.0"  # Comments

# After normalization (sorted, stripped, comments removed):
"pytest==8.0.0\nruff==0.13.0"
```

### Hash Generation

- **Algorithm**: SHA-256 (truncated to 12 hex characters)
- **Format**: `a3f7e2d4c1b9` (12 chars = 48 bits)
- **Collision probability**: Negligible (~16M venvs needed for 1% collision
  chance)

### Requirements Files

For `requirements_file`, the **file contents** are hashed, not the path:

```yaml
environments:
  - name: dev
    requirements_file: requirements-dev.txt
```

- ✅ Portable across machines
- ✅ Changes to file update the hash
- ✅ Different files with same content share venv

## Configuration

### Default: Centralized Storage

By default, venvs are stored in `~/.cache/uv-toolbox/`:

```yaml
# No venv_path needed - uses default
environments:
  - name: dev
    requirements: ruff pytest
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
    requirements: ruff pytest
```

This stores venvs in `.uv-toolbox/<hash>/` within your project.

## Edge Cases

### Manual Package Installation

If you manually install packages:

```bash
uvtb exec --env dev -- pip install extra-package
```

The venv is modified but the hash doesn't change. This is **by design**:

- Hash is based on declared requirements
- Manual changes don't break the mapping
- You're responsible for undeclared dependencies

### Order Independence

Package order doesn't matter:

```yaml
# These create the SAME venv:
requirements: ruff black
requirements: black ruff
```

### Whitespace & Comments

Comments and whitespace are normalized away:

```yaml
requirements: |
  # Formatting tools
  ruff==0.13.0

  black==24.0.0  # code formatter
```

Hashes the same as:

```yaml
requirements: ruff==0.13.0 black==24.0.0
```

## Migration from Previous Versions

**Breaking change** in v0.1.0:

- **Old**: Venvs stored at `venv_path/<env-name>/`
- **New**: Venvs stored at `venv_path/<content-hash>/`

**To migrate:**

1. Upgrade to v0.1.0+
2. Run `uvtb install` to recreate venvs with new hashing
3. Old venvs in `.uv-toolbox/` can be safely deleted

## Technical Details

For more implementation details, see the source code:

- [Hashing logic](https://github.com/yourusername/uv-toolbox/blob/main/uv_toolbox/settings.py#L106-L124)
- [Path resolution](https://github.com/yourusername/uv-toolbox/blob/main/uv_toolbox/settings.py#L126-L134)
