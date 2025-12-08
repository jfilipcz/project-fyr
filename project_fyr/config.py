"""Settings for the Project Fyr service."""

from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = Field(
        default="sqlite:///./project_fyr.db",
        description="SQLAlchemy database URL",
    )
    slack_bot_token: Optional[str] = Field(default=None)
    slack_default_channel: Optional[str] = Field(default=None)
    openai_api_key: Optional[str] = Field(default=None)
    openai_api_base: Optional[str] = Field(default=None)
    openai_api_version: str = Field(default=None)
    azure_deployment: Optional[str] = Field(default=None)
    langchain_model_name: str = Field(default="gpt-4o-mini")
    k8s_cluster_name: str = Field(default="ci-cluster")
    rollout_timeout_seconds: int = Field(default=15 * 60)

    slack_mock_log_file: Optional[str] = Field(default=None)
    
    prometheus_url: Optional[str] = Field(default=None, description="Prometheus server URL")

    class Config:
        env_prefix = "PROJECT_FYR_"
        case_sensitive = False


settings = Settings()
