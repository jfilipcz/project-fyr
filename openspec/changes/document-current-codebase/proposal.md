## Why

The current system behavior is documented across README and design notes but lacks a single, enforceable spec. Capturing the existing behavior as specs creates a durable contract for maintenance, onboarding, and future changes.

## What Changes

- Create baseline specs that describe the current runtime behavior of Project Fyr’s core capabilities.
- Document the current architecture and decision points in design.md for future change alignment.
- Establish a spec-driven artifact set for future evolution (without changing runtime behavior).

## Capabilities

### New Capabilities
- `deployment-rollout-monitoring`: How the watcher detects, records, and evaluates deployment rollouts, including opt-in mechanisms and status transitions.
- `namespace-incident-monitoring`: Namespace-level incident detection and investigation triggers (terminating stuck, quota, eviction, restarts).
- `investigation-agent`: LLM-driven investigation workflow, tool usage, and analysis output structure.
- `alert-webhook-processing`: Alert webhook ingestion, batching, and investigation triggering for alert batches.
- `slack-integration`: Slack notification delivery, Socket Mode interactivity, and command-driven workflows.
- `dashboard-and-auth`: Dashboard UI behavior, investigation views, and authentication modes (local/SSO/hybrid).

### Modified Capabilities
- (none)

## Impact

- Documentation/spec artifacts under `openspec/changes/document-current-codebase/`.
- No runtime code changes, no API changes, no dependency changes.
- Improves future change control and onboarding by establishing a baseline contract.
