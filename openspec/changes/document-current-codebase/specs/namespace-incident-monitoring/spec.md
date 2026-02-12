## ADDED Requirements

### Requirement: Namespace monitoring opt-in
The namespace monitor SHALL only evaluate namespaces that have the annotation `project-fyr/enabled=true` and SHALL skip system namespaces.

#### Scenario: Namespace monitoring enabled
- **WHEN** a namespace includes `project-fyr/enabled=true`
- **THEN** the namespace is eligible for incident checks

### Requirement: Stuck terminating detection
The system SHALL create a namespace incident when a namespace has been in `Terminating` longer than the configured threshold and no active incident exists for that namespace and incident type.

#### Scenario: Terminating namespace exceeds threshold
- **WHEN** a namespace is in `Terminating` longer than the configured threshold
- **THEN** a namespace incident is created and queued for investigation

### Requirement: Rate limiting for incidents
The system SHALL enforce per-namespace and per-cluster investigation rate limits for namespace incidents.

#### Scenario: Namespace rate limit exceeded
- **WHEN** a namespace has reached the hourly investigation limit
- **THEN** new incidents for that namespace are not created

### Requirement: Incident metadata capture
The system SHALL capture namespace metadata (team, slack channel, finalizers, deletion timestamp) with each incident record.

#### Scenario: Incident metadata stored
- **WHEN** a namespace incident is created
- **THEN** the incident metadata includes finalizers and deletion timestamp if present
