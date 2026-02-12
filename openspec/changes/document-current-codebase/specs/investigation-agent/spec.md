## ADDED Requirements

### Requirement: Agent-driven investigation
The analyzer SHALL invoke an LLM agent to investigate failing rollouts or alert batches using read-only Kubernetes tools and produce a structured analysis.

#### Scenario: Failed rollout investigation
- **WHEN** a rollout is marked `FAILED`
- **THEN** the agent runs a Kubernetes investigation and returns an analysis with summary, likely cause, recommended steps, and severity

### Requirement: Disabled or mock agent behavior
If the agent is disabled due to missing API keys, the analyzer SHALL return a low-severity analysis indicating the agent is disabled. If the model is configured as `mock`, the analyzer SHALL return a mock analysis.

#### Scenario: Missing API key
- **WHEN** the analyzer is configured without an API key
- **THEN** the analysis indicates the agent is disabled and severity is `low`

#### Scenario: Mock model enabled
- **WHEN** the model name is set to `mock`
- **THEN** the analyzer returns a mock investigation result

### Requirement: Alert context integration
For alert batch investigations, the analyzer SHALL include alert context (summary and active alerts) in the agent prompt.

#### Scenario: Alert batch investigation
- **WHEN** an alert batch triggers an investigation
- **THEN** the agent is invoked with alert context data for prioritization

### Requirement: Triage classification for rollouts
For rollout investigations, the system SHALL compute a triage team and reason and attach them to the analysis metadata.

#### Scenario: Triage applied
- **WHEN** a rollout investigation completes
- **THEN** the analysis includes triage team and triage reason metadata
