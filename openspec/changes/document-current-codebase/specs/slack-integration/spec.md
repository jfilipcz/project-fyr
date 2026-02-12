## ADDED Requirements

### Requirement: Slack notification delivery
The system SHALL send Slack Block Kit notifications for failed rollouts, namespace incidents, and alert batches to the configured channel or namespace-specific channel when available.

#### Scenario: Rollout failure notification
- **WHEN** a rollout investigation completes for a failed deployment
- **THEN** a Slack message is posted to the rollout’s target channel

### Requirement: Socket Mode interactivity
When Socket Mode is enabled, the system SHALL support slash commands and interactive actions using the Slack Bolt handler.

#### Scenario: Slash command handled
- **WHEN** a user issues `/fyr status`
- **THEN** the system responds with cluster status in Slack

### Requirement: Interactive investigation triggers
The Slack message actions SHALL allow users to trigger investigations or deep-link into the dashboard.

#### Scenario: Investigate Further button
- **WHEN** a user clicks “Investigate Further”
- **THEN** the system starts a threaded investigation context for that rollout

### Requirement: Threaded follow-up investigations
The system SHALL respond to relevant thread questions in an investigation thread by running the agent and posting results.

#### Scenario: Thread question asked
- **WHEN** a user asks a question in an active investigation thread
- **THEN** the system posts an investigation result in the same thread
