from __future__ import annotations

from os.path import expandvars
from pathlib import Path
from typing import Annotated

from pydantic import (
    AliasChoices,
    AliasGenerator,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)
from pydantic_settings import (
    BaseSettings,
    JsonConfigSettingsSource,
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

    def venv_path(self, settings: UvToolboxSettings) -> Path:
        """Get the path to the virtual environment for this environment."""
        return settings.venv_path / self.name

    def process_env(self, settings: UvToolboxSettings) -> dict[str, str]:
        """Environment variables for processes run in this environment."""
        return {
            **{k: expandvars(v) for k, v in self.environment.items()},
            'VIRTUAL_ENV': str(self.venv_path(settings=settings)),
        }


class UvToolboxSettings(BaseSettings):
    """Settings loaded from config files.

    Precedence (highest first):
      1. CLI args (passed to initialization)
      2. Environment variables (with `UV_TOOLBOX_` prefix)
      3. Configuration files, in order:
        1. uv-toolbox.yaml
        2. uv-toolbox.json
        3. uv-toolbox.toml
        4. pyproject.toml ([tool.uv-toolbox])
    """

    venv_path: Path = Path.cwd() / '.uv-toolbox'
    default_environment: str | None = None
    environments: Annotated[
        list[UvToolboxEnvironment],
        Field(min_length=1),
    ]

    model_config = SettingsConfigDict(
        env_prefix='UV_TOOLBOX_',
        env_nested_delimiter='__',
        extra='forbid',
        pyproject_toml_table_header=('tool', 'uv-toolbox'),
        alias_generator=_ALIASES,
        populate_by_name=True,
    )

    def get_environment(self, env_name: str) -> UvToolboxEnvironment:
        """Get an environment by name.

        Args:
            env_name: The name of the environment to get.

        Returns:
            The matching environment.

        Raises:
            ValueError: If no environment with the given name exists.
        """
        for env in self.environments:
            if env.name == env_name:
                return env
        msg = (
            f'No environment found with name {env_name!r}. Available '
            'environments: {[env.name for env in self.environments]}'
        )
        raise ValueError(msg)

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

    @model_validator(mode='after')
    def ensure_valid_default_environment(
        self,
    ) -> UvToolboxSettings:
        """Validate that the default environment name exists."""
        if self.default_environment is not None:
            env_names = {env.name for env in self.environments}
            if self.default_environment not in env_names:
                msg = f'Default environment {self.default_environment!r} not found in environments.'
                raise ValueError(msg)
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,  # noqa: ARG003
        file_secret_settings: PydanticBaseSettingsSource,  # noqa: ARG003
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Customize settings sources for UV Toolbox."""
        uv_toolbox_yaml = YamlConfigSettingsSource(
            settings_cls,
            yaml_file='uv-toolbox.yaml',
        )
        uv_toolbox_json = JsonConfigSettingsSource(
            settings_cls,
            json_file='uv-toolbox.json',
        )
        uv_toolbox_toml = TomlConfigSettingsSource(
            settings_cls,
            toml_file='uv-toolbox.toml',
        )
        pyproject_toml = PyprojectTomlConfigSettingsSource(settings_cls)
        return (
            init_settings,
            env_settings,
            uv_toolbox_yaml,
            uv_toolbox_json,
            uv_toolbox_toml,
            pyproject_toml,
        )
