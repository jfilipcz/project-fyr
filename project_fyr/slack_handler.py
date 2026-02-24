# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Project Fyr Contributors
"""Slack Socket Mode handler for Project Fyr.

This module provides a Socket Mode handler that connects to Slack via WebSocket,
eliminating the need for a public URL. It handles:
- Slash commands (/fyr)
- Interactive components (buttons, modals)
- App Home events
- Message actions

Usage:
    python -m project_fyr.slack_handler
"""

from __future__ import annotations

import logging
import re
import signal
import sys
import threading
import time

from kubernetes import config as k8s_config
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from .config import settings
from .slack_commands import register_commands
from .db import init_db, RolloutRepo

logger = logging.getLogger(__name__)

# Initialize k8s config at module load time
try:
    k8s_config.load_incluster_config()
    logger.info("Loaded in-cluster k8s config")
except k8s_config.ConfigException:
    try:
        k8s_config.load_kube_config()
        logger.info("Loaded local k8s config")
    except Exception as e:
        logger.warning(f"Could not load k8s config: {e}")


class FyrSlackHandler:
    """Socket Mode handler for Fyr Slack integration."""

    def __init__(self):
        if not settings.slack_bot_token:
            raise ValueError("SLACK_BOT_TOKEN is required for Slack integration")
        if not settings.slack_app_token:
            raise ValueError("SLACK_APP_TOKEN is required for Socket Mode")

        # Initialize Bolt app
        self.app = App(
            token=settings.slack_bot_token,
            signing_secret=settings.slack_signing_secret,
        )

        self._thread_context: dict[str, dict[str, str | None]] = {}
        self._thread_context_lock = threading.Lock()
        self._agent = None

        # Register handlers
        register_commands(self.app)
        self._register_actions()
        self._register_events()

        # Socket Mode handler
        self.handler = SocketModeHandler(
            self.app,
            settings.slack_app_token
        )

        logger.info("Fyr Slack handler initialized")

    def _store_thread_context(self, thread_ts: str, namespace: str, deployment: str | None) -> None:
        with self._thread_context_lock:
            self._thread_context[thread_ts] = {
                "namespace": namespace,
                "deployment": deployment,
                "is_first_investigation": True,  # Track if this is the first investigation
            }

    def _get_thread_context(self, thread_ts: str) -> dict[str, str | None] | None:
        with self._thread_context_lock:
            return self._thread_context.get(thread_ts)

    def _mark_thread_investigated(self, thread_ts: str) -> None:
        """Mark that at least one investigation has been done in this thread."""
        with self._thread_context_lock:
            if thread_ts in self._thread_context:
                self._thread_context[thread_ts]["is_first_investigation"] = False

    def _get_agent(self):
        if self._agent is None:
            from .agent import InvestigatorAgent
            self._agent = InvestigatorAgent(
                model_name=settings.langchain_model_name,
                api_key=settings.openai_api_key,
                api_base=settings.openai_api_base,
                api_version=settings.openai_api_version,
                azure_deployment=settings.azure_deployment,
            )
        return self._agent

    def _register_actions(self) -> None:
        """Register interactive component handlers."""

        @self.app.action("investigate_further")
        def handle_investigate_further(ack, action, respond, client, body):
            """Handle 'Investigate Further' button click - start AI chat in thread."""
            ack()
            logger.info("🔵 investigate_further button clicked")

            value = action.get("value", "")  # namespace/deployment
            parts = value.split("/", 1)
            namespace = parts[0] if parts else "unknown"
            deployment = parts[1] if len(parts) > 1 else None

            channel_id = body.get("channel", {}).get("id")
            message_ts = body.get("message", {}).get("ts")
            user_id = body.get("user", {}).get("id")

            if not channel_id or not message_ts:
                logger.warning("Missing channel or message_ts in investigate_further action")
                return

            # Post in thread
            target = f"{namespace}/{deployment}" if deployment else namespace
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=message_ts,
                text=f"🤖 *Fyr AI Investigation*\n\nHi <@{user_id}>! I'm ready to help investigate `{target}`.\n\nYou can ask me questions like:\n• What caused this failure?\n• Show me the pod logs\n• What's the memory usage?\n• Are there any related events?\n\n_Just reply in this thread and I'll investigate!_"
            )

            self._store_thread_context(message_ts, namespace, deployment)

        @self.app.action("investigate_namespace")
        def handle_investigate_namespace(ack, action, respond, client, body):
            """Handle namespace investigation button."""
            ack()

            namespace = action.get("value", "")
            channel_id = body.get("channel", {}).get("id")

            if not namespace or not channel_id:
                return

            # Post starting message
            client.chat_postMessage(
                channel=channel_id,
                text=f"🔍 Starting AI investigation for namespace `{namespace}`... This may take 30-60 seconds."
            )

            # Run investigation (blocking)
            try:
                from kubernetes import client as k8s_client, config as k8s_config
                from .namespace_analyzer import NamespaceAnalyzer
                from .slack_blocks import build_investigation_result_response
                from .models import Analysis

                try:
                    k8s_config.load_incluster_config()
                except Exception:
                    k8s_config.load_kube_config()

                core_v1 = k8s_client.CoreV1Api()
                apps_v1 = k8s_client.AppsV1Api()

                analyzer = NamespaceAnalyzer(
                    model_name=settings.langchain_model_name,
                    api_key=settings.openai_api_key,
                    api_base=settings.openai_api_base,
                    api_version=settings.openai_api_version,
                    azure_deployment=settings.azure_deployment,
                )

                result = analyzer.analyze_namespace(
                    namespace=namespace,
                    core_v1=core_v1,
                    apps_v1=apps_v1,
                )

                analysis = Analysis(
                    summary=result.get("summary", ""),
                    likely_cause=result.get("likely_cause", ""),
                    recommended_steps=result.get("recommended_steps", []),
                    severity=result.get("severity", "medium"),
                )

                client.chat_postMessage(
                    channel=channel_id,
                    blocks=build_investigation_result_response(
                        namespace=namespace,
                        deployment=None,
                        analysis=analysis,
                    )
                )

            except Exception as e:
                logger.exception(f"Error in investigate_namespace action: {e}")
                client.chat_postMessage(
                    channel=channel_id,
                    text=f"❌ Investigation failed: {str(e)}"
                )

        @self.app.action("acknowledge_alert")
        def handle_acknowledge_alert(ack, action, respond, client, body):
            """Handle alert acknowledgment button."""
            ack()

            batch_id = action.get("value", "")
            user_id = body.get("user", {}).get("id")
            user_name = body.get("user", {}).get("name", user_id)

            try:
                batch_id = int(batch_id)
            except ValueError:
                respond(text="❌ Invalid batch ID")
                return

            # Update message to show acknowledged
            original_blocks = body.get("message", {}).get("blocks", [])

            # Find and update the actions block
            updated_blocks = []
            for block in original_blocks:
                if block.get("type") == "actions":
                    # Replace with acknowledgment context
                    updated_blocks.append({
                        "type": "context",
                        "elements": [
                            {
                                "type": "mrkdwn",
                                "text": f"👀 *Acknowledged* by <@{user_id}> at <!date^{int(time.time())}^{{time}}|now>"
                            }
                        ]
                    })
                else:
                    updated_blocks.append(block)

            # Update the message
            client.chat_update(
                channel=body.get("channel", {}).get("id"),
                ts=body.get("message", {}).get("ts"),
                blocks=updated_blocks
            )

            logger.info(f"Alert batch {batch_id} acknowledged by {user_name}")

        @self.app.action("view_in_fyr")
        @self.app.action("view_rollout")
        @self.app.action("view_alert_batch")
        @self.app.action("view_namespace")
        @self.app.action("view_pipeline")
        @self.app.action("open_dashboard")
        def handle_link_button(ack, action, body):
            """Handle URL button clicks (just acknowledge, browser opens URL)."""
            ack()
            logger.debug(f"Link button clicked: {action.get('action_id')}")

        # Catch-all for view_rollout_* buttons
        @self.app.action(re.compile(r"^view_rollout_"))
        def handle_view_rollout(ack, action, body):
            """Handle rollout view buttons."""
            ack()

    def _register_events(self) -> None:
        """Register event handlers."""

        @self.app.event("app_home_opened")
        def handle_app_home_opened(client, event, logger):
            """Handle App Home tab opened."""
            user_id = event.get("user")

            if not user_id:
                return

            try:
                # Build App Home view
                view = self._build_app_home_view()

                client.views_publish(
                    user_id=user_id,
                    view=view
                )

                logger.info(f"Published App Home for user {user_id}")

            except Exception as e:
                logger.exception(f"Error publishing App Home: {e}")

        @self.app.event("message")
        def handle_message(client, event, say):
            """Handle messages (for threaded AI chat)."""
            logger.info(f"🔵 Message event received: thread={event.get('thread_ts')}, text={event.get('text', '')[:50]}...")

            # Only respond to thread replies
            if "thread_ts" not in event:
                logger.debug("Not a thread message, ignoring")
                return

            text = event.get("text", "")
            channel = event.get("channel")
            thread_ts = event.get("thread_ts")
            subtype = event.get("subtype")

            # Skip bot's own messages
            if event.get("bot_id") or subtype:
                logger.debug(f"Bot message or subtype message, ignoring: bot_id={event.get('bot_id')}, subtype={subtype}")
                return

            # Check if the parent message was posted by THIS bot to avoid CI/Dev clashes
            # Fetch the thread parent message to check who posted it
            try:
                auth_result = client.auth_test()
                our_bot_user_id = auth_result.get("user_id")

                thread_info = client.conversations_replies(
                    channel=channel,
                    ts=thread_ts,
                    limit=1,
                    inclusive=True
                )

                if thread_info and thread_info.get("messages"):
                    parent_message = thread_info["messages"][0]
                    parent_user = parent_message.get("user")

                    # Only respond if WE posted the parent message
                    if parent_user != our_bot_user_id:
                        logger.debug(f"Thread parent posted by {parent_user}, not us ({our_bot_user_id}), ignoring")
                        return

                    logger.info(f"Thread parent confirmed as ours (user_id={our_bot_user_id})")

            except Exception as e:
                logger.warning(f"Could not verify thread ownership: {e}")
                # Fallback to memory-based context check
                context = self._get_thread_context(thread_ts)
                if not context:
                    logger.debug(f"No context found for thread {thread_ts} - not our thread")
                    return

            # Check if we have context (optional, since we already verified ownership)
            context = self._get_thread_context(thread_ts)
            if context:
                logger.info(f"Thread context found: {context}")
            else:
                logger.info("No stored context, but thread is ours - extracting from parent message")
                # Try to extract namespace/deployment from parent message
                try:
                    if thread_info and thread_info.get("messages"):
                        parent_text = thread_info["messages"][0].get("text", "")
                        # Look for patterns like "namespace/deployment" or just "namespace"
                        import re
                        match = re.search(r'`([^/]+)/([^`]+)`', parent_text)
                        if match:
                            context = {
                                "namespace": match.group(1),
                                "deployment": match.group(2),
                                "is_first_investigation": False
                            }
                            self._store_thread_context(thread_ts, context["namespace"], context["deployment"])
                            logger.info(f"Recovered context from parent: {context}")
                        else:
                            match = re.search(r'`([^`]+)`', parent_text)
                            if match:
                                context = {
                                    "namespace": match.group(1),
                                    "deployment": None,
                                    "is_first_investigation": False
                                }
                                self._store_thread_context(thread_ts, context["namespace"], None)
                                logger.info(f"Recovered namespace context from parent: {context}")
                except Exception as e:
                    logger.warning(f"Could not extract context from parent message: {e}")

                if not context:
                    logger.warning("Could not determine namespace/deployment for this thread")
                    say(thread_ts=thread_ts, text="❌ Sorry, I've lost context for this thread. Please start a new investigation.")
                    return

            # In an active investigation thread, respond to any user message
            if text.strip():
                logger.info(f"Received message in thread: {text[:50]}...")

                say(thread_ts=thread_ts, text="🔍 Let me look into that...")

                namespace = context.get("namespace") if context else None
                deployment = context.get("deployment") if context else None

                if not namespace or not channel:
                    return

                try:
                    from .slack_blocks import build_conversational_response

                    if deployment:
                        agent = self._get_agent()
                        analysis = agent.investigate(
                            deployment,
                            namespace,
                            question=text,
                            is_initial_slack_investigation=False,  # Always conversational in threads
                        )

                        # Always use conversational format for thread replies
                        blocks = build_conversational_response(analysis.likely_cause)
                    else:
                        from kubernetes import client as k8s_client, config as k8s_config
                        from .namespace_analyzer import NamespaceAnalyzer

                        try:
                            k8s_config.load_incluster_config()
                        except Exception:
                            k8s_config.load_kube_config()

                        core_v1 = k8s_client.CoreV1Api()
                        apps_v1 = k8s_client.AppsV1Api()

                        analyzer = NamespaceAnalyzer(
                            model_name=settings.langchain_model_name,
                            api_key=settings.openai_api_key,
                            api_base=settings.openai_api_base,
                            api_version=settings.openai_api_version,
                            azure_deployment=settings.azure_deployment,
                        )

                        result = analyzer.analyze_namespace(
                            namespace=namespace,
                            core_v1=core_v1,
                            apps_v1=apps_v1,
                        )

                        # Extract summary data
                        summary = result.get('summary', {})
                        analysis = result.get('analysis')

                        # Build response text from namespace analysis
                        if analysis:
                            # If there's AI analysis, use that
                            response_text = analysis
                        elif summary.get('has_issues'):
                            # Has issues but no AI analysis yet
                            response_text = f"Found {summary.get('failing_deployments', 0)} failing deployments and {summary.get('unhealthy_pods', 0)} unhealthy pods in `{namespace}`."
                        else:
                            # No issues found
                            response_text = f"Namespace `{namespace}` looks healthy! ✅\n\n"
                            response_text += f"• {summary.get('total_deployments', 0)} deployments running\n"
                            response_text += f"• {summary.get('total_pods', 0)} pods healthy\n"

                        blocks = build_conversational_response(response_text)

                    client.chat_postMessage(
                        channel=channel,
                        thread_ts=thread_ts,
                        blocks=blocks,
                    )

                except Exception as e:
                    logger.exception(f"Error handling thread question: {e}")
                    client.chat_postMessage(
                        channel=channel,
                        thread_ts=thread_ts,
                        text=f"❌ Sorry, I ran into an issue: {str(e)}",
                    )

    def _build_app_home_view(self) -> dict:
        """Build the App Home tab view."""
        try:
            engine = init_db(settings.database_url)
            repo = RolloutRepo(engine, annotation_prefix=settings.annotation_prefix)
            stats = repo.get_stats(hours=24, exclude_system=True)

            failures = repo.list_by_status("failed", limit=5, exclude_system=True)
        except Exception as e:
            logger.error(f"Error getting stats for App Home: {e}")
            stats = {"completed": 0, "failed": 0, "in_progress": 0}
            failures = []

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "🔥 Fyr - Kubernetes Intelligence",
                    "emoji": True
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Cluster:* {settings.k8s_cluster_name}"
                }
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Last 24 Hours*"
                }
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"✅ *Successful*\n{stats.get('completed', 0)}"},
                    {"type": "mrkdwn", "text": f"⚠️ *Failed*\n{stats.get('failed', 0)}"},
                    {"type": "mrkdwn", "text": f"🔄 *In Progress*\n{stats.get('in_progress', 0)}"},
                ]
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Recent Failures*"
                }
            },
        ]

        if failures:
            for f in failures:
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"🔴 *{f.deployment}* ({f.namespace})"
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "View",
                            "emoji": True
                        },
                        "url": f"{settings.dashboard_base_url or 'http://localhost:8000'}/rollout/{f.id}",
                        "action_id": "view_rollout",
                        "value": str(f.id),
                    }
                })
        else:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "✨ No recent failures!"
                }
            })

        blocks.extend([
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Quick Actions*"
                }
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "🔍 Open Dashboard",
                            "emoji": True
                        },
                        "url": settings.dashboard_base_url or "http://localhost:8000",
                        "action_id": "open_dashboard"
                    }
                ]
            }
        ])

        return {
            "type": "home",
            "blocks": blocks
        }

    def start(self) -> None:
        """Start the Socket Mode handler (blocking)."""
        logger.info("Starting Fyr Slack Socket Mode handler...")
        logger.info(f"Cluster: {settings.k8s_cluster_name}")
        logger.info(f"Dashboard URL: {settings.dashboard_base_url or 'http://localhost:8000'}")

        self.handler.start()

    def stop(self) -> None:
        """Stop the handler gracefully."""
        logger.info("Stopping Fyr Slack handler...")
        self.handler.close()


def main():
    """Main entry point for slack handler."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # Check required config
    if not settings.slack_socket_mode_enabled:
        logger.error("Slack Socket Mode is not enabled. Set PROJECT_FYR_SLACK_SOCKET_MODE_ENABLED=true")
        sys.exit(1)

    if not settings.slack_bot_token:
        logger.error("SLACK_BOT_TOKEN is required")
        sys.exit(1)

    if not settings.slack_app_token:
        logger.error("SLACK_APP_TOKEN is required for Socket Mode")
        sys.exit(1)

    try:
        handler = FyrSlackHandler()

        # Handle graceful shutdown
        def signal_handler(signum, frame):
            logger.info(f"Received signal {signum}, shutting down...")
            handler.stop()
            sys.exit(0)

        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)

        # Start handler (blocks)
        handler.start()

    except Exception as e:
        logger.exception(f"Failed to start Slack handler: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
