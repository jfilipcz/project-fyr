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

import asyncio
import logging
import signal
import sys
from typing import Optional

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from .config import settings
from .slack_commands import register_commands
from .db import init_db, RolloutRepo, AlertRepo

logger = logging.getLogger(__name__)


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
    
    def _register_actions(self) -> None:
        """Register interactive component handlers."""
        
        @self.app.action("investigate_further")
        def handle_investigate_further(ack, action, respond, client, body):
            """Handle 'Investigate Further' button click - start AI chat in thread."""
            ack()
            
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
                except:
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
                                "text": f"👀 *Acknowledged* by <@{user_id}> at <!date^{int(asyncio.get_event_loop().time())}^{{time}}|now>"
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
        @self.app.action("view_alert_batch")
        @self.app.action("view_namespace")
        @self.app.action("view_pipeline")
        @self.app.action("open_dashboard")
        def handle_link_button(ack, action, body):
            """Handle URL button clicks (just acknowledge, browser opens URL)."""
            ack()
            logger.debug(f"Link button clicked: {action.get('action_id')}")
        
        # Catch-all for view_rollout_* buttons
        @self.app.action({"action_id": "view_rollout_"})
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
            # Only respond to thread replies that mention the bot
            if "thread_ts" not in event:
                return
            
            text = event.get("text", "")
            user = event.get("user")
            channel = event.get("channel")
            thread_ts = event.get("thread_ts")
            
            # Check if this is a thread where we're doing investigation
            # For now, respond to any thread message in channels where Fyr is present
            # In production, track which threads have active investigations
            
            # Skip bot's own messages
            if event.get("bot_id"):
                return
            
            # Simple check - if user is asking a question in a thread
            if "?" in text or any(kw in text.lower() for kw in ["what", "why", "how", "show", "tell"]):
                logger.info(f"Received question in thread: {text[:50]}...")
                
                # This would trigger the AI agent - for now just acknowledge
                # Full implementation would use InvestigatorAgent with the thread context
                say(
                    thread_ts=thread_ts,
                    text="🔍 Let me investigate that for you..."
                )
                
                # TODO: Implement full AI chat integration
                # This requires passing the question to InvestigatorAgent
                # and posting the response back to the thread
    
    def _build_app_home_view(self) -> dict:
        """Build the App Home tab view."""
        try:
            engine = init_db(settings.database_url)
            repo = RolloutRepo(engine)
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
                        "action_id": f"view_rollout_{f.id}"
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
