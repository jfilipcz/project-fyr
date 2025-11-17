"""Settings for the Project Fyr service."""

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = Field(
        default="sqlite:///./project_fyr.db",
        description="SQLAlchemy database URL",
    )
    slack_bot_token: str | None = Field(default=None)
    slack_default_channel: str | None = Field(default=None)
    openai_api_key: str | None = Field(default=None)
    langchain_model_name: str = Field(default="gpt-4o-mini")
    k8s_cluster_name: str = Field(default="ci-cluster")
    rollout_timeout_seconds: int = Field(default=15 * 60)
    log_tail_seconds: int = Field(default=300)
    max_log_lines: int = Field(default=200)
    reducer_max_events: int = Field(default=20)
    reducer_max_clusters: int = Field(default=8)

    class Config:
        env_prefix = "PROJECT_FYR_"
        case_sensitive = False


settings = Settings()
