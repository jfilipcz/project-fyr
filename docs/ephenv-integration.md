# Ephemeral Environment (Ephenv) Integration

## Overview

Project Fyr now supports automatic failure detection and direct notifications for ephemeral environments created by the Ephenv operator. When deployments fail in ephemeral namespaces, the requestor receives a direct Slack DM with the analysis.

## How It Works

### Ephenv Operator
- Creates ephemeral development environments via `EphemeralEnvironment` CRDs
- Stamps namespace with `requestor` annotation containing the user's email
- Deploys Helm charts into uniquely named namespaces

### Project Fyr Integration
- Watches for deployment failures across all namespaces
- Extracts requestor email from namespace annotations (supports both `requestor` and `example.com/requestor-email`)
- Sends direct Slack DM to requestor when deployment fails
- Falls back to team channels if DM fails

## Configuration

### Enable Requestor DMs

To enable direct messages to ephemeral environment requestors:

```bash
# In values-ci.yaml or values-dev.yaml
env:
  - name: PROJECT_FYR_ENABLE_REQUESTOR_DM
    value: "true"
```

### Annotation Support

Project Fyr recognizes two annotation formats for requestor email:

1. **Ephenv style** (primary): `requestor: user@example.com`
2. **Legacy style**: `example.com/requestor-email: user@example.com`

## User Experience

### Ephemeral Environment Creator

1. Create environment via Ephenv Portal or CRD
2. Ephenv operator provisions namespace with `requestor` annotation
3. If deployment fails, Project Fyr:
   - Analyzes the failure using AI
   - Looks up your Slack user by email
   - Sends you a direct message with:
     - Root cause analysis
     - Relevant logs and events
     - Suggested remediation steps
     - Link to detailed dashboard view

### Notification Flow

```
Deployment Failure
    ↓
Project Fyr Watcher detects failure
    ↓
Extract requestor email from namespace annotation
    ↓
AI analyzes logs, events, and resource states
    ↓
Look up Slack user by email
    ↓
Send DM to requestor
    ↓
Fallback to team channel if DM fails
```

## Ephenv Namespace Lifecycle

### Automatic Cleanup

Project Fyr handles ephemeral namespace cleanup gracefully:

- When ephemeral namespace is deleted (after TTL or manual cleanup)
- Rollouts from deleted namespaces are automatically marked as `DISCARDED`
- No notifications are sent for legitimately deleted environments
- Audit trail is maintained in `discard_reason` metadata

### Example Ephenv Environment

```yaml
apiVersion: ephenv.platform.example.com/v1alpha1
kind: EphemeralEnvironment
metadata:
  name: test-nginx
spec:
  requestor:
    email: developer@example.com  # ← Project Fyr uses this
  chart:
    name: nginx
    source: acr
    version: "1.0.0"
  namespace:
    name: test-nginx-env
  ttlSeconds: 3600
```

The resulting namespace will have: `annotations: {requestor: "developer@example.com"}`

## Testing

### Verify Annotation Detection

Check if Project Fyr can read ephenv namespace annotations:

```bash
# Create test ephenv environment
kubectl apply -f test-env.yaml

# Check namespace annotation
kubectl get namespace <ephenv-namespace> -o jsonpath='{.metadata.annotations.requestor}'

# Trigger failure and check Project Fyr logs
kubectl logs -n project-fyr -l app=project-fyr-analyzer | grep "requestor_email"
```

### Verify DM Delivery

1. Create ephemeral environment with your email
2. Cause deployment failure (e.g., invalid image)
3. Check Slack for DM from Project Fyr bot
4. Verify fallback to default channel if DM fails

## Monitoring

### Check Requestor DM Statistics

```sql
-- Count rollouts with requestor email
SELECT COUNT(*) as with_requestor
FROM rollouts 
WHERE JSON_EXTRACT(metadata, '$.example.com/requestor-email') IS NOT NULL;

-- Check DM delivery status
SELECT 
  namespace,
  deployment,
  JSON_EXTRACT(metadata, '$.example.com/requestor-email') as requestor,
  notify_status
FROM rollouts 
WHERE JSON_EXTRACT(metadata, '$.example.com/requestor-email') IS NOT NULL
ORDER BY id DESC LIMIT 20;
```

### Ephenv-Specific Queries

```bash
# List active ephenv environments
kubectl get ephemeralenvironments

# Check specific environment status
kubectl get ephenv <name> -o yaml

# List namespaces created by ephenv
kubectl get namespaces -l ephenv.platform.example.com/managed-by=ephenv-operator
```

## Troubleshooting

### DM Not Received

1. **Verify annotation is set**:
   ```bash
   kubectl get namespace <ns> -o jsonpath='{.metadata.annotations}'
   ```

2. **Check feature is enabled**:
   ```bash
   kubectl exec -n project-fyr deployment/project-fyr-analyzer -- env | grep ENABLE_REQUESTOR_DM
   ```

3. **Verify Slack user exists**:
   - Email must match your Slack account email
   - Bot must have `users:read.email` permission

4. **Check Project Fyr logs**:
   ```bash
   kubectl logs -n project-fyr -l app=project-fyr-analyzer | grep -A5 "requestor"
   ```

### Deployment Not Detected

1. **Check namespace is not in system namespaces list**:
   - Ephenv namespaces should NOT be in `PROJECT_FYR_SYSTEM_NAMESPACES`

2. **Verify deployment failure was captured**:
   ```bash
   kubectl exec -n project-fyr project-fyr-mysql-0 -- mysql -u root -p<password> projectfyr \
     -e "SELECT * FROM rollouts WHERE namespace='<ephenv-namespace>' ORDER BY id DESC LIMIT 5;"
   ```

3. **Check watcher logs for errors**:
   ```bash
   kubectl logs -n project-fyr -l app=project-fyr-watcher
   ```

## Related Documentation

- [Slack DM Feature Spec](../agents-slack-dm-feature.md)
- [Ephenv Operator README](/home/jfilipczak/dev/ephenv/docs/README.md)
- [Project Fyr Architecture](../architecture.md)

## Rollout Plan

### Phase 1: Testing (Current)
- [x] Add support for `requestor` annotation
- [x] Test with ephenv CI environment
- [ ] Enable `enable_requestor_dm=true` in CI cluster

### Phase 2: Production
- [ ] Document user-facing behavior
- [ ] Enable in production clusters
- [ ] Monitor DM delivery metrics
- [ ] Gather user feedback

## Future Enhancements

- **Proactive monitoring**: Notify requestor when environment is about to expire
- **Health checks**: Regular status updates for long-running ephemeral environments
- **Cost tracking**: Alert requestor of resource usage/costs
- **Auto-remediation suggestions**: Interactive buttons for common fixes
