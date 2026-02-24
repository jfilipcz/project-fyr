# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Project Fyr Contributors
"""Settings for the Project Fyr service."""

from typing import Optional, List
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
    pending_investigation_threshold_seconds: int = Field(
        default=300,
        description="Seconds to wait before investigating a PENDING rollout (default: 5 minutes)"
    )
    speculative_analysis_grace_seconds: int = Field(
        default=300,
        description="Seconds to wait before starting speculative analysis on ROLLING_OUT deployments (default: 5 minutes)"
    )

    # Annotation prefix for Kubernetes labels/annotations
    annotation_prefix: str = Field(
        default="project-fyr.io",
        description="Domain prefix for custom Kubernetes annotations (e.g. project-fyr.io/requestor-email)"
    )

    # Watch behavior
    watch_all_namespaces: bool = Field(
        default=False,
        description="If True, watch all deployments regardless of labels/annotations. If False, require opt-in via labels."
    )
    namespace_label_enabled: bool = Field(
        default=True,
        description="If True, allow namespace-level project-fyr/enabled annotation to enable watching all deployments in that namespace."
    )

    # System Namespaces - Filtered from monitoring and display
    # Stored as comma-separated string to avoid JSON parsing issues from env vars
    system_namespaces_str: str = Field(
        default="kube-system,kube-public,kube-node-lease,default,monitoring,logging,ingress-nginx,cert-manager,flux-system,argocd,project-fyr,istio-system,elastic-system",
        description="System namespaces to exclude from monitoring and display (comma-separated)"
    )

    @property
    def system_namespaces(self) -> List[str]:
        """Get system_namespaces as a list."""
        if not self.system_namespaces_str:
            return []
        return [ns.strip() for ns in self.system_namespaces_str.split(',') if ns.strip()]

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

    # Rate Limiting (configurable via env: MAX_INVESTIGATIONS_PER_NAMESPACE_PER_HOUR, MAX_INVESTIGATIONS_PER_CLUSTER_PER_HOUR)
    max_investigations_per_namespace_per_hour: int = Field(
        default=10,
        description="Maximum investigations (rollouts + namespace incidents) per namespace per hour"
    )
    max_investigations_per_cluster_per_hour: int = Field(
        default=40,
        description="Maximum investigations cluster-wide per hour"
    )

    # Alert Webhook & Correlation
    alert_webhook_secret: Optional[str] = Field(default=None)
    alert_correlation_window_seconds: int = Field(default=300)
    alert_batch_min_count: int = Field(default=1)

    slack_mock_log_file: Optional[str] = Field(default=None)

    # Slack Socket Mode (for internal apps without public URL)
    slack_app_token: Optional[str] = Field(
        default=None,
        description="Slack App-Level Token (xapp-...) for Socket Mode connection"
    )
    slack_socket_mode_enabled: bool = Field(
        default=False,
        description="Enable Slack Socket Mode for slash commands and interactivity"
    )
    slack_signing_secret: Optional[str] = Field(
        default=None,
        description="Slack signing secret for request verification"
    )

    # Slack routing toggles
    enable_requestor_dm: bool = Field(
        default=False,
        description="Enable requestor DM routing based on namespace annotation"
    )
    enable_owner_channel: bool = Field(
        default=False,
        description="Enable owner channel routing based on deployment label"
    )
    slack_routing_cache_ttl_seconds: int = Field(
        default=3600,
        description="TTL for Slack user/channel routing cache"
    )

    # Fyr Dashboard URL (for deep links in Slack messages)
    dashboard_base_url: Optional[str] = Field(
        default=None,
        description="Base URL for Fyr dashboard (e.g., https://fyr.example.com)"
    )

    prometheus_url: Optional[str] = Field(default=None, description="Prometheus server URL")

    # Overview Insights Cache
    insights_cache_ttl_minutes: int = Field(
        default=60,
        description="TTL for cached aggregated insights in minutes"
    )

    overview_show_ai_summary: bool = Field(
        default=False,
        description="Show AI summary text on the overview dashboard"
    )

    show_triage_in_slack: bool = Field(
        default=False,
        description="Show triage assignment block in Slack notifications"
    )

    # Authentication
    auth_enabled: bool = Field(
        default=False,
        description="Enable authentication for dashboard access"
    )
    auth_mode: str = Field(
        default="hybrid",
        description="Authentication mode: 'sso', 'local', or 'hybrid'"
    )

    # SSO Configuration
    sso_provider: str = Field(
        default="none",
        description="SSO provider: 'none', 'entra', 'generic_oidc'"
    )
    sso_tenant_id: Optional[str] = Field(
        default=None,
        description="Azure AD / Entra ID tenant ID"
    )
    sso_client_id: Optional[str] = Field(
        default=None,
        description="SSO application client ID"
    )
    sso_client_secret: Optional[str] = Field(
        default=None,
        description="SSO client secret (for OIDC flows)"
    )

    # Generic OIDC Configuration
    oidc_issuer: Optional[str] = Field(
        default=None,
        description="OIDC issuer URL"
    )
    oidc_jwks_uri: Optional[str] = Field(
        default=None,
        description="OIDC JWKS URI for token validation"
    )
    oidc_audience: Optional[str] = Field(
        default=None,
        description="OIDC audience (e.g., api://your-app)"
    )

    # Local Authentication
    local_auth_enabled: bool = Field(
        default=True,
        description="Enable local username/password authentication"
    )
    local_auth_jwt_secret: str = Field(
        default="change-me-in-production",
        description="JWT secret for local auth tokens"
    )
    local_auth_jwt_expiry_hours: int = Field(
        default=24,
        description="JWT token expiry time in hours"
    )
    local_auth_session_expiry_hours: int = Field(
        default=168,
        description="Session expiry time in hours (7 days default)"
    )

    # Default Admin User
    admin_username: str = Field(
        default="admin",
        description="Default admin username"
    )
    admin_password: Optional[str] = Field(
        default=None,
        description="Default admin password (created on first startup)"
    )
    admin_email: str = Field(
        default="admin@example.com",
        description="Default admin email"
    )

    # Authentication Excluded Paths
    auth_exclude_paths: str = Field(
        default="/health,/metrics,/static,/api/webhook",
        description="Comma-separated list of path prefixes to exclude from authentication"
    )

    class Config:
        env_prefix = "PROJECT_FYR_"
        case_sensitive = False


settings = Settings()
