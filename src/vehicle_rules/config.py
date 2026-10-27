"""Configuration management for the vehicle rules assistant."""

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

# Top-level section of project_config.yml with the settings of this project, shared by all environments.
PROJECT_SECTION = "vehicle_rules"


class Regulation(BaseModel):
    """A Dutch law or regulation in the official BWB repository (wetten.overheid.nl)."""

    bwb_id: str = Field(..., description="BWB identifier, such as BWBR0025798")
    title: str = Field(..., description="Official short title")


class ProjectConfig(BaseModel):
    """Project configuration model.

    The usage policy comes from the top level of project_config.yml, the course resources from the
    environment's section, the project settings from its vehicle_rules section.
    """

    usage_policy_id: str = Field(..., description="Usage policy to set on every serverless job, endpoint and pipeline")
    catalog: str = Field(..., description="Unity Catalog name")
    db_schema: str = Field(..., description="Schema name", alias="schema")
    volume: str = Field(..., description="Volume name for the downloaded legal texts")
    llm_endpoint: str = Field(..., description="AI Gateway chat model service name")
    embedding_endpoint: str = Field(..., description="Embedding model endpoint, the same for documents and queries")
    experiment_path: str = Field(..., description="MLflow experiment path for this environment")
    warehouse_id: str = Field(..., description="Warehouse ID")
    lakebase_instance: str = Field(..., description="Lakebase project ID")
    genie_space_id: str | None = Field(None, description="Genie space ID for MCP integration")
    regulations: list[Regulation] = Field(..., description="Legal texts the assistant answers from")
    system_prompt: str = Field(
        default=(
            "You are a helpful AI assistant that explains which Dutch vehicle rules apply to a specific "
            "vehicle, what they mean in practice, and whether the APK checks them."
        ),
        description="System prompt for the agent",
    )

    model_config = {"populate_by_name": True}

    @classmethod
    def from_yaml(cls, config_path: str, env: str = "dev") -> "ProjectConfig":
        """Load configuration from YAML file.

        Args:
            config_path: Path to the YAML configuration file
            env: Environment name (dev, acc, prd)

        Returns:
            ProjectConfig instance

        """
        if env not in ["prd", "acc", "dev"]:
            raise ValueError(f"Invalid environment: {env}. Expected 'prd', 'acc', or 'dev'")

        with open(config_path) as f:
            config_data = yaml.safe_load(f)

        if env not in config_data:
            raise ValueError(f"Environment '{env}' not found in config file")
        if PROJECT_SECTION not in config_data:
            raise ValueError(f"Section '{PROJECT_SECTION}' not found in config file")

        # The project section wins, so its volume replaces the course's suggested arxiv_files.
        return cls(
            **{
                "usage_policy_id": config_data.get("usage_policy_id"),
                **config_data[env],
                **config_data[PROJECT_SECTION],
            }
        )

    @property
    def schema(self) -> str:
        """Alias for db_schema for backward compatibility."""
        return self.db_schema

    @property
    def full_schema_name(self) -> str:
        """Get fully qualified schema name."""
        return f"{self.catalog}.{self.db_schema}"

    @property
    def full_volume_path(self) -> str:
        """Get fully qualified volume path."""
        return f"{self.catalog}.{self.schema}.{self.volume}"


def load_config(config_path: str = "project_config.yml", env: str = "dev") -> ProjectConfig:
    """Load project configuration.

    Args:
        config_path: Path to configuration file
        env: Environment name

    Returns:
        ProjectConfig instance

    """
    # Handle relative paths from notebooks
    if not Path(config_path).is_absolute():
        # Try to find config in parent directories
        current = Path.cwd()
        for _ in range(3):  # Search up to 3 levels
            candidate = current / config_path
            if candidate.exists():
                config_path = str(candidate)
                break
            current = current.parent

    return ProjectConfig.from_yaml(config_path, env)


def get_env() -> str:
    """Get current environment from environment variable.

    Returns:
        Environment name (dev, acc, or prd)

    """
    return os.getenv("ENV", "dev")
