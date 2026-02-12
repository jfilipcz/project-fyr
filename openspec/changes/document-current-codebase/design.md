## Context

Project Fyr’s behavior is currently described across README, architecture notes, and code. There is no single spec baseline, which makes future changes hard to verify. This change documents the current system as formal specs without altering runtime behavior.

## Goals / Non-Goals

**Goals:**
- Capture current runtime behavior as baseline specs for core capabilities.
- Provide a clear contract for future changes and verification.
- Keep specs scoped to user-facing or system-observable behavior.

**Non-Goals:**
- Changing implementation or behavior.
- Exhaustive internal design documentation of every module.
- Retrofitting historical decisions that are not reflected in current behavior.

## Decisions

- **Use spec-driven OpenSpec schema**: Aligns with repository’s workflow and enables a consistent proposal → specs → design → tasks progression.
- **Treat current behavior as ADDED requirements**: Since there are no prior specs, all baseline behaviors are introduced as new requirements.
- **Capability segmentation**: Document the system as a small set of cohesive capabilities (deployment monitoring, namespace incidents, investigation agent, alert webhook, Slack integration, dashboard/auth). This reduces spec size while keeping boundaries meaningful.
- **Specs backed by code and existing docs**: Use README, architecture.md, and key modules (`project_fyr/`) as the authoritative source for requirements.

## Risks / Trade-offs

- **Risk:** Baseline specs may miss edge cases not obvious in code.
  → **Mitigation:** Review with maintainers and refine incrementally.
- **Risk:** Specs become stale if future changes don’t update them.
  → **Mitigation:** Require spec updates in future OpenSpec changes.
- **Trade-off:** Higher-level capability grouping may hide some subsystem detail.
  → **Mitigation:** Add follow-up specs if teams need deeper granularity.

## Migration Plan

- No runtime migration. This change only adds documentation artifacts under `openspec/changes/`.
- After review, archive the change to promote specs into `openspec/specs/`.

## Open Questions

- Are there additional capabilities that should be broken out (e.g., database schema, metrics/telemetry)?
- Which teams should review each capability spec for accuracy?
