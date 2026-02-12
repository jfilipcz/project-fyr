## ADDED Requirements

### Requirement: Deployment opt-in rules
The watcher SHALL monitor deployments when any of the following are true: the deployment label `project-fyr/enabled=true` is set, the namespace annotation `project-fyr/enabled=true` is set (and namespace labels are enabled), or global watch-all is enabled. The watcher SHALL skip configured system namespaces.

#### Scenario: Deployment opt-in by label
- **WHEN** a deployment has label `project-fyr/enabled=true` in a non-system namespace
- **THEN** the watcher records rollout events for that deployment

#### Scenario: Namespace opt-in
- **WHEN** a namespace has annotation `project-fyr/enabled=true` and namespace label monitoring is enabled
- **THEN** all deployments in that namespace are monitored even without the deployment label

#### Scenario: System namespace exclusion
- **WHEN** a deployment is in a namespace listed as system namespaces
- **THEN** the watcher skips monitoring that deployment

### Requirement: Rollout record creation
For monitored deployments, the system SHALL create or update a rollout record with cluster, namespace, deployment name, generation, status, timestamps, and namespace metadata (team, slack channel, annotations).

#### Scenario: New rollout detected
- **WHEN** a new deployment generation is observed
- **THEN** a rollout record is created with status `ROLLING_OUT` and metadata from the namespace annotations

### Requirement: Rollout status evaluation
The system SHALL transition rollout status to `SUCCESS` when the deployment is stable, and to `FAILED` when progress conditions indicate failure, a timeout is exceeded, or a majority of pods are failing with CrashLoop or image pull errors.

#### Scenario: Deployment becomes stable
- **WHEN** available replicas meet desired replicas
- **THEN** the rollout status is updated to `SUCCESS`

#### Scenario: Progress deadline exceeded
- **WHEN** the deployment reports progressing status `False`
- **THEN** the rollout status is updated to `FAILED`

#### Scenario: Early failure by pod signals
- **WHEN** at least half of pods are in CrashLoopBackOff or image pull failure
- **THEN** the rollout status is updated to `FAILED` before timeout

### Requirement: Pending investigation threshold
The analyzer SHALL treat rollouts stuck in `PENDING` longer than the configured threshold as investigation candidates.

#### Scenario: Stuck pending rollout
- **WHEN** a rollout remains `PENDING` beyond the configured threshold
- **THEN** the analyzer includes it in the investigation queue
