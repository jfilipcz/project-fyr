## ADDED Requirements

### Requirement: Dashboard views
The dashboard SHALL provide views for rollouts, rollout details with analysis, alerts, and on-demand investigations.

#### Scenario: Rollout detail view
- **WHEN** a user opens a rollout detail URL
- **THEN** the dashboard displays rollout metadata and analysis content

### Requirement: Overview insights
The dashboard SHALL provide an overview page with rollout statistics and aggregated insights over a recent time window.

#### Scenario: Overview shows recent stats
- **WHEN** a user opens the overview page
- **THEN** the dashboard displays counts of successful, failed, and in-progress rollouts

### Requirement: Authentication modes
When authentication is enabled, the dashboard SHALL support local auth, SSO, or hybrid modes and redirect unauthenticated users to a login page.

#### Scenario: Auth enabled
- **WHEN** authentication is enabled
- **THEN** unauthenticated access to protected pages redirects to `/login`

### Requirement: Local user management
The system SHALL provide a CLI to manage local users, including create, list, password reset, admin toggle, and delete.

#### Scenario: Create admin user
- **WHEN** an operator runs the manage-users CLI to create an admin user
- **THEN** the user is created with admin privileges
