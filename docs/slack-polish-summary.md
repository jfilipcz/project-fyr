# Slack Polish Summary

## Overview
This update improves Slack UX and interactivity across rollout failures, alert batches, and thread follow-ups. The main outcomes are:
- Enhanced Block Kit messages now appear for rollouts (buttons + deep links).
- Alert batch notifications use a dedicated alert layout.
- Slack actions use stable IDs with values, improving acknowledgements.
- Thread replies now run investigations and return results inline.
- Acknowledge timestamps render correctly in Slack.

## Behavior Changes
- Rollout notifications now include `rollout_id` and metadata, enabling the richer failure blocks with actions (View in Fyr, Investigate Further, etc.).
- Alert batch notifications are rendered using the alert-specific block builder instead of generic analysis blocks.
- Slack action IDs for rollouts are normalized to `view_rollout` with a `value` payload, avoiding dynamic action ID matching issues.
- “Investigate Further” starts a tracked thread context and subsequent thread questions trigger an agent investigation.
- Alert acknowledgements now use UNIX epoch timestamps so Slack date formatting renders correctly.

## Implementation Notes
- Added a dedicated `send_alert_batch(...)` method to the Slack notifier for alert batch formatting and mock logging.
- Added minimal thread context tracking in the Slack handler to route follow-up questions to the correct namespace/deployment.
- Added optional `question` support in the investigator to bias responses toward user thread questions.

## Files Touched
- `project_fyr/slack.py` (new alert batch sender, shared post helper, mock logging tweaks)
- `project_fyr/service.py` (rollout Slack metadata + rollout_id; alert batch notification uses new alert blocks)
- `project_fyr/slack_handler.py` (thread context, action IDs, timestamp fix, thread Q&A investigations)
- `project_fyr/slack_commands.py` (stable `view_rollout` action IDs)
- `project_fyr/agent.py` (optional `question` argument for targeted answers)

## Quick Verification Ideas
- Send a rollout failure notification and confirm Slack buttons show up.
- Click “Investigate Further”, then ask a question in the thread and confirm a result is posted.
- Trigger an alert batch and confirm the alert-specific block layout.
- Click an “Acknowledge” button and confirm the timestamp renders correctly.
