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
    slack_api_url: Optional[str] = Field(default=None, description="Override Slack API base URL (for testing)")
    openai_api_key: Optional[str] = Field(default=None)
    openai_api_base: Optional[str] = Field(default=None)
    openai_api_version: Optional[str] = Field(default=None)
    azure_deployment: Optional[str] = Field(default=None)
    langchain_model_name: str = Field(default="gpt-4o-mini")
    k8s_cluster_name: str = Field(default="ci-cluster")
    rollout_timeout_seconds: int = Field(default=15 * 60)
    
    # Watch behavior
    watch_all_namespaces: bool = Field(
        default=False,
        description="If True, watch all deployments regardless of labels/annotations. If False, require opt-in via labels."
    )
    namespace_label_enabled: bool = Field(
        default=True,
        description="If True, allow namespace-level project-fyr/enabled annotation to enable watching all deployments in that namespace."
    )

    # Namespace Monitoring
    namespace_monitoring_enabled: bool = Field(
        default=True,
        description="Enable namespace-level incident detection and investigation"
    )
    namespace_monitoring_interval_seconds: int = Field(
        default=300,
        description="How often to check for namespace issues (stuck terminating, quota violations, etc.)"
    )
    namespace_terminating_threshold_minutes: int = Field(
        default=5,
        description="Consider namespace stuck if in Terminating state for this many minutes"
    )
    namespace_eviction_threshold: int = Field(
        default=5,
        description="Trigger investigation if this many pods evicted in window"
    )
    namespace_eviction_window_minutes: int = Field(
        default=5,
        description="Time window for counting pod evictions"
    )
    namespace_restart_threshold: int = Field(
        default=10,
        description="Trigger investigation if this many container restarts in window"
    )
    namespace_restart_window_minutes: int = Field(
        default=5,
        description="Time window for counting container restarts"
    )
    
    # Rate Limiting
    max_investigations_per_namespace_per_hour: int = Field(
        default=2,
        description="Maximum investigations (rollouts + namespace incidents) per namespace per hour"
    )
    max_investigations_per_cluster_per_hour: int = Field(
        default=20,
        description="Maximum investigations cluster-wide per hour"
    )

    # Alert Webhook & Correlation
    alert_webhook_secret: Optional[str] = Field(default=None)
    alert_correlation_window_seconds: int = Field(default=300)
    alert_batch_min_count: int = Field(default=1)

    slack_mock_log_file: Optional[str] = Field(default=None)
    
    prometheus_url: Optional[str] = Field(default=None, description="Prometheus server URL")

    class Config:
        env_prefix = "PROJECT_FYR_"
        case_sensitive = False


settings = Settings()
