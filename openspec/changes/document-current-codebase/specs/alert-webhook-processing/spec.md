## ADDED Requirements

### Requirement: Alert webhook ingestion
The system SHALL accept alert payloads at `/api/webhook/alert` and optionally authenticate requests using a shared header token when configured.

#### Scenario: Authenticated alert webhook
- **WHEN** a request includes the configured alert token
- **THEN** the system accepts and processes the alert payload

#### Scenario: Missing or invalid token
- **WHEN** a request lacks a required alert token
- **THEN** the system responds with HTTP 401

### Requirement: Alert persistence and state tracking
The system SHALL persist incoming alerts and maintain per-fingerprint state for throttling and re-trigger decisions.

#### Scenario: New alert persisted
- **WHEN** an alert payload is received
- **THEN** the alert is stored with labels, annotations, and timestamps

### Requirement: Investigation throttling
The system SHALL avoid re-triggering investigations for the same alert fingerprint within the configured throttle window.

#### Scenario: Throttled alert
- **WHEN** an alert fingerprint was investigated recently
- **THEN** the alert is stored but marked as skipped for batching

### Requirement: Alert batching and job creation
The system SHALL batch alerts by namespace/service within the correlation window and create investigation jobs for batches that meet the minimum count.

#### Scenario: Batch meets minimum count
- **WHEN** a batch has at least the configured minimum alert count
- **THEN** a new investigation job is created for that alert batch
