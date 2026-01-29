# uv-toolbox

`uv-toolbox` is a CLI tool for managing Python tool environments. It will help
you create multiple virtual environments and manage their dependencies through a
declarative configuration file.

Here is an example in YAML format:

```yaml
environments:
  - name: env1
    requirements: |
      ruff==0.13.0
      black
  - name: env2
    requirements: |
      isort
      flake8
```
