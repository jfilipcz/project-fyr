# Setting Up Entra ID Authentication for Fyr

This guide walks you through configuring Microsoft Entra ID (formerly Azure AD) authentication for your Fyr deployment.

## Prerequisites

- Azure subscription with permissions to register applications
- Fyr deployed on a Kubernetes cluster
- Access to modify Helm values

## Step 1: Register Application in Azure Portal

1. Go to [Azure Portal](https://portal.azure.com)
2. Navigate to **Azure Active Directory** (or **Microsoft Entra ID**)
3. Select **App registrations** → **New registration**

### Registration Details

- **Name**: `Fyr Dashboard` (or your preferred name)
- **Supported account types**: 
  - Choose "Accounts in this organizational directory only" for single tenant
  - Or "Accounts in any organizational directory" for multi-tenant
- **Redirect URI**: 
  - Select "Web"
  - Enter: `https://your-fyr-domain.com/auth/callback` (replace with your actual domain)
  - If testing locally: `http://localhost:8000/auth/callback`

4. Click **Register**

## Step 2: Configure Application

### Get Application Details

After registration, note these values from the **Overview** page:

- **Application (client) ID**: `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`
- **Directory (tenant) ID**: `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`

### Configure Authentication

1. Go to **Authentication** in the left menu
2. Under **Implicit grant and hybrid flows**:
   - ✅ Check **ID tokens** (for implicit and hybrid flows)
3. Under **Advanced settings**:
   - Set **Allow public client flows** to **No**
4. Click **Save**

### (Optional) Create Client Secret

If you plan to use authorization code flow (recommended for production):

1. Go to **Certificates & secrets**
2. Click **New client secret**
3. Add description: "Fyr Dashboard Secret"
4. Choose expiration period (recommend: 24 months)
5. Click **Add**
6. **IMPORTANT**: Copy the secret value immediately (you won't be able to see it again)

### Configure API Permissions

1. Go to **API permissions**
2. Default permissions should include:
   - Microsoft Graph → User.Read (Delegated)
3. This is sufficient for basic authentication

### Configure Token Configuration (Optional)

To include additional user claims in the token:

1. Go to **Token configuration**
2. Click **Add optional claim**
3. Select **ID**
4. Add claims like: `email`, `preferred_username`, `name`
5. Click **Add**

## Step 3: Update Fyr Helm Values

Generate a JWT secret for local authentication:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Update your `values.yaml`:

```yaml
auth:
  enabled: true
  mode: hybrid  # Supports both SSO and local auth
  
  sso:
    provider: entra
    tenantId: "YOUR-TENANT-ID"  # From Step 2
    clientId: "YOUR-CLIENT-ID"  # From Step 2
    clientSecret: ""  # Leave empty if using external secrets
  
  local:
    enabled: true
    jwtSecret: ""  # Leave empty if using external secrets
    jwtExpiryHours: 24
    sessionExpiryHours: 168
  
  admin:
    username: admin
    password: ""  # Leave empty if using external secrets
    email: admin@example.com
```

## Step 4: Configure Secrets (Recommended)

### Option A: Using Kubernetes Secrets

Create a secret with sensitive values:

```bash
kubectl create secret generic fyr-auth-secrets \
  --namespace project-fyr \
  --from-literal=PROJECT_FYR_SSO_CLIENT_SECRET='your-client-secret' \
  --from-literal=PROJECT_FYR_LOCAL_AUTH_JWT_SECRET='your-jwt-secret' \
  --from-literal=PROJECT_FYR_ADMIN_PASSWORD='your-admin-password'
```

Update `values.yaml` to reference the secret:

```yaml
secrets:
  existingSecret: "fyr-auth-secrets"
```

### Option B: Using External Secrets Operator

If you're using External Secrets Operator (ESO):

1. Store secrets in your secret store (Azure Key Vault, AWS Secrets Manager, etc.)
2. Create an `ExternalSecret` resource:

```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: fyr-auth-secrets
  namespace: project-fyr
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: your-secret-store
    kind: SecretStore
  target:
    name: fyr-auth-secrets
  data:
    - secretKey: PROJECT_FYR_SSO_CLIENT_SECRET
      remoteRef:
        key: fyr-sso-client-secret
    - secretKey: PROJECT_FYR_LOCAL_AUTH_JWT_SECRET
      remoteRef:
        key: fyr-jwt-secret
    - secretKey: PROJECT_FYR_ADMIN_PASSWORD
      remoteRef:
        key: fyr-admin-password
```

## Step 5: Deploy Updated Configuration

```bash
# Update dependencies
helm dependency update ./helm/project-fyr

# Upgrade the release
helm upgrade project-fyr ./helm/project-fyr \
  --namespace project-fyr \
  --values custom-values.yaml
```

## Step 6: Verify Authentication

### Check Dashboard Deployment

```bash
# Verify environment variables are set
kubectl get configmap -n project-fyr project-fyr-project-fyr -o yaml | grep AUTH

# Check dashboard pod logs
kubectl logs -n project-fyr -l app.kubernetes.io/component=dashboard --tail=50
```

You should see log messages about authentication initialization:
```
INFO: Authentication enabled (mode: hybrid)
INFO: Entra ID provider initialized for tenant: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
INFO: Local authentication provider initialized
```

### Access the Dashboard

1. Navigate to your Fyr dashboard URL
2. You should be redirected to `/login`
3. Try logging in with:
   - **SSO**: Click "Sign in with Microsoft"
   - **Local**: Use admin credentials

## Step 7: Create Additional Users (Optional)

For local authentication, create additional users using the management CLI:

```bash
# Connect to the dashboard pod
kubectl exec -it -n project-fyr deployment/project-fyr-dashboard -- bash

# Create a new user
python scripts/manage_users.py create john.doe john.doe@example.com --admin

# List all users
python scripts/manage_users.py list

# Change user password
python scripts/manage_users.py passwd john.doe
```

## Troubleshooting

### Issue: "Invalid token" error

**Possible causes:**
- Tenant ID or Client ID mismatch
- Token expired
- Wrong audience in token

**Solution:**
```bash
# Verify configuration
kubectl get configmap -n project-fyr project-fyr-project-fyr -o yaml | grep -E "(TENANT_ID|CLIENT_ID)"

# Check dashboard logs for detailed error
kubectl logs -n project-fyr -l app.kubernetes.io/component=dashboard --tail=100 | grep -i auth
```

### Issue: Redirect loop

**Possible causes:**
- Excluded paths not configured correctly
- Session cookie not being set

**Solution:**
- Verify `auth.excludePaths` includes `/login`, `/auth/*`
- Check browser console for cookie issues
- Ensure ingress allows cookie forwarding

### Issue: "User not found" after SSO login

**Possible causes:**
- User doesn't exist in local database
- Email claim not in token

**Solution:**
In hybrid mode, SSO users are auto-created on first login. Check:
```bash
# Verify user creation in database
kubectl exec -it -n project-fyr project-fyr-mysql-0 -- mysql -u root -p projectfyr -e "SELECT * FROM users;"
```

### Issue: Client secret expired

**Solution:**
1. Generate new secret in Azure Portal
2. Update Kubernetes secret:
```bash
kubectl patch secret fyr-auth-secrets -n project-fyr \
  --type='json' \
  -p='[{"op": "replace", "path": "/data/PROJECT_FYR_SSO_CLIENT_SECRET", "value": "'$(echo -n 'new-secret' | base64)'"}]'
```
3. Restart dashboard:
```bash
kubectl rollout restart deployment/project-fyr-dashboard -n project-fyr
```

## Security Best Practices

1. **Never commit secrets**: Use Kubernetes Secrets or External Secrets Operator
2. **Rotate secrets regularly**: Set client secret expiration to 12-24 months
3. **Use HTTPS only**: Configure ingress with TLS certificates
4. **Restrict access**: Configure Azure AD conditional access policies if needed
5. **Monitor authentication**: Check dashboard logs for failed login attempts
6. **Disable local auth in production**: If not needed, set `auth.local.enabled: false`

## Configuration Reference

### Minimal Configuration (SSO only)

```yaml
auth:
  enabled: true
  mode: sso
  sso:
    provider: entra
    tenantId: "YOUR-TENANT-ID"
    clientId: "YOUR-CLIENT-ID"
  local:
    enabled: false
```

### Full Configuration (Hybrid)

```yaml
auth:
  enabled: true
  mode: hybrid
  sso:
    provider: entra
    tenantId: "YOUR-TENANT-ID"
    clientId: "YOUR-CLIENT-ID"
    clientSecret: ""
  local:
    enabled: true
    jwtSecret: ""
    jwtExpiryHours: 24
    sessionExpiryHours: 168
  admin:
    username: admin
    password: ""
    email: admin@example.com
  excludePaths: "/health,/metrics,/static,/api/webhook"
```

## Next Steps

- Set up monitoring for authentication events
- Configure Azure AD conditional access policies
- Implement role-based access control (RBAC) if needed
- Set up audit logging for user actions
