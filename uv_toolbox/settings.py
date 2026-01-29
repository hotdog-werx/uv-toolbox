from __future__ import annotations

from pathlib import Path

from pydantic import (
    AliasChoices,
    AliasGenerator,
    BaseModel,
    ConfigDict,
    model_validator,
)
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    PyprojectTomlConfigSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
    YamlConfigSettingsSource,
)


def _to_kebab(name: str) -> str:
    """Convert snake_case to kebab-case."""
    return name.replace('_', '-')


def _validation_alias(name: str) -> AliasChoices:
    """Accept both snake_case and kebab-case for settings keys.

    `AliasChoices` order matters: we list the snake_case field name first so if both
    variants are present (e.g. env var + pyproject), the env var wins.
    """
    return AliasChoices(name, _to_kebab(name))


_ALIASES = AliasGenerator(
    validation_alias=_validation_alias,
    serialization_alias=_to_kebab,
)


class UvToolboxEnvironment(BaseModel):
    """Definition of a UV toolbox environment.

    Attributes:
        requirements: A pip requirements string (mutually exclusive with
            requirements_file).
        requirements_file: A path to a pip requirements file (mutually
            exclusive with requirements).
    """

    name: str
    requirements: str | None = None
    requirements_file: Path | None = None
    environment: dict[str, str] = {}

    model_config = ConfigDict(
        alias_generator=_ALIASES,
        populate_by_name=True,
    )

    @model_validator(mode='after')
    def check_requirements(self) -> UvToolboxEnvironment:
        """Validate that only one of requirements or requirements_file is set."""
        if not (bool(self.requirements) ^ bool(self.requirements_file)):
            msg = 'Exactly one of requirements or requirements_file must be set.'
            raise ValueError(msg)
        return self

    @property
    def venv_path(self) -> Path:
        """Get the path to the virtual environment for this environment."""
        settings = UvToolboxSettings.model_validate({})
        return settings.venv_path / self.name

    @property
    def process_env(self) -> dict[str, str]:
        """Environment variables for processes run in this environment."""
        return {
            **self.environment,
            'VIRTUAL_ENV': str(self.venv_path),
        }


class UvToolboxSettings(BaseSettings):
    """Settings loaded from config files.

    Precedence (highest first):
      1. uv-toolbox.yaml
      2. uv-toolbox.toml
      3. pyproject.toml ([tool.uv-toolbox])
    """

    venv_path: Path = Path.cwd() / '.uv-toolbox'
    environments: list[UvToolboxEnvironment]

    model_config = SettingsConfigDict(
        env_prefix='UV_TOOLBOX_',
        env_nested_delimiter='__',
        extra='forbid',
        pyproject_toml_table_header=('tool', 'uv-toolbox'),
        alias_generator=_ALIASES,
        populate_by_name=True,
    )

    @model_validator(mode='after')
    def ensure_unique_env_names(
        self,
    ) -> UvToolboxSettings:
        """Validate that environment names are unique."""
        all_env_names = [env.name for env in self.environments]
        duplicate_names = {name for name in all_env_names if all_env_names.count(name) > 1}
        if duplicate_names:
            msg = f'Duplicate environment names found: {", ".join(sorted(duplicate_names))}'
            raise ValueError(msg)
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,  # noqa: ARG003
        env_settings: PydanticBaseSettingsSource,  # noqa: ARG003
        dotenv_settings: PydanticBaseSettingsSource,  # noqa: ARG003
        file_secret_settings: PydanticBaseSettingsSource,  # noqa: ARG003
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Customize settings sources for UV Toolbox."""
        uv_toolbox_yaml = YamlConfigSettingsSource(
            settings_cls,
            yaml_file='uv-toolbox.yaml',
        )
        uv_toolbox_toml = TomlConfigSettingsSource(
            settings_cls,
            toml_file='uv-toolbox.toml',
        )
        pyproject_toml = PyprojectTomlConfigSettingsSource(settings_cls)
        return (
            uv_toolbox_yaml,
            uv_toolbox_toml,
            pyproject_toml,
        )
