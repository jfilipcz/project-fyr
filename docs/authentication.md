# Fyr Authentication System

## Overview

The Fyr authentication system provides flexible, secure access control for the dashboard. It supports both SSO (Single Sign-On) and local username/password authentication, with the ability to use them independently or together in hybrid mode.

## Features

- **Multiple Authentication Methods**: SSO, local, or hybrid mode
- **SSO Providers**: Microsoft Entra ID (Azure AD) built-in, extensible for others
- **Local Authentication**: Username/password with JWT tokens
- **Flexible Configuration**: Enable/disable auth entirely or choose specific methods
- **Secure by Default**: BCrypt password hashing, JWT tokens, HTTP-only cookies
- **User Management CLI**: Command-line tool for managing local users

## Quick Start

### 1. Install Dependencies

```bash
pip install -e .
```

### 2. Configure Authentication

Set environment variables to enable and configure auth:

```bash
# Enable authentication
export PROJECT_FYR_AUTH_ENABLED=true
export PROJECT_FYR_AUTH_MODE=hybrid  # or 'sso' or 'local'

# Local auth configuration
export PROJECT_FYR_LOCAL_AUTH_ENABLED=true
export PROJECT_FYR_LOCAL_AUTH_JWT_SECRET="your-secret-key-here"

# SSO configuration (for Entra ID)
export PROJECT_FYR_SSO_PROVIDER=entra
export PROJECT_FYR_SSO_TENANT_ID="your-tenant-id"
export PROJECT_FYR_SSO_CLIENT_ID="your-client-id"
```

### 3. Initialize Database

The auth system requires database tables for users. Run database migrations:

```bash
# This will create the users and user_sessions tables
python -m alembic upgrade head
```

### 4. Create Admin User

```bash
python scripts/manage_users.py create admin admin@example.com --admin
# You'll be prompted for a password
```

### 5. Start Dashboard

```bash
python -m project_fyr.dashboard_service
```

Visit http://localhost:8000/login to sign in.

## Configuration Reference

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PROJECT_FYR_AUTH_ENABLED` | `false` | Enable authentication system |
| `PROJECT_FYR_AUTH_MODE` | `hybrid` | Auth mode: `sso`, `local`, or `hybrid` |
| `PROJECT_FYR_LOCAL_AUTH_ENABLED` | `true` | Enable local username/password auth |
| `PROJECT_FYR_LOCAL_AUTH_JWT_SECRET` | `change-me-in-production` | JWT signing secret (⚠️ change in production!) |
| `PROJECT_FYR_LOCAL_AUTH_JWT_EXPIRY_HOURS` | `24` | JWT token expiry time |
| `PROJECT_FYR_SSO_PROVIDER` | `entra` | SSO provider (`entra`, `okta`, `generic_oidc`) |
| `PROJECT_FYR_SSO_TENANT_ID` | - | Azure AD tenant ID |
| `PROJECT_FYR_SSO_CLIENT_ID` | - | SSO application client ID |
| `PROJECT_FYR_ADMIN_USERNAME` | `admin` | Default admin username |
| `PROJECT_FYR_ADMIN_PASSWORD` | - | Default admin password (optional) |
| `PROJECT_FYR_AUTH_EXCLUDE_PATHS` | `/health,/metrics,/static,/api/webhook` | Paths excluded from auth |

## User Management

### CLI Tool

The `manage_users.py` script provides user management:

```bash
# Create a user
python scripts/manage_users.py create <username> <email> [--admin] [--name "Full Name"]

# List all users
python scripts/manage_users.py list

# Change password
python scripts/manage_users.py passwd <username>

# Toggle admin privileges
python scripts/manage_users.py toggle-admin <username>

# Delete user
python scripts/manage_users.py delete <username>
```

### Examples

```bash
# Create regular user
python scripts/manage_users.py create john john@example.com --name "John Doe"

# Create admin user
python scripts/manage_users.py create admin admin@example.com --admin

# List all users
python scripts/manage_users.py list

# Make user an admin
python scripts/manage_users.py toggle-admin john

# Change password
python scripts/manage_users.py passwd john
```

## Authentication Modes

### Local Auth Only

Best for: Development, small teams, air-gapped environments

```bash
export PROJECT_FYR_AUTH_ENABLED=true
export PROJECT_FYR_AUTH_MODE=local
export PROJECT_FYR_LOCAL_AUTH_ENABLED=true
export PROJECT_FYR_LOCAL_AUTH_JWT_SECRET="your-secret-here"
```

Users authenticate with username/password. Create users with the CLI tool.

### SSO Only (Entra ID)

Best for: Enterprise deployments with existing SSO

```bash
export PROJECT_FYR_AUTH_ENABLED=true
export PROJECT_FYR_AUTH_MODE=sso
export PROJECT_FYR_SSO_PROVIDER=entra
export PROJECT_FYR_SSO_TENANT_ID="your-tenant-id"
export PROJECT_FYR_SSO_CLIENT_ID="your-client-id"
```

Users authenticate via Microsoft Entra ID (Azure AD). No local users needed.

### Hybrid Mode

Best for: Flexibility - both SSO and local auth available

```bash
export PROJECT_FYR_AUTH_ENABLED=true
export PROJECT_FYR_AUTH_MODE=hybrid
export PROJECT_FYR_SSO_PROVIDER=entra
export PROJECT_FYR_SSO_TENANT_ID="your-tenant-id"
export PROJECT_FYR_SSO_CLIENT_ID="your-client-id"
export PROJECT_FYR_LOCAL_AUTH_ENABLED=true
export PROJECT_FYR_LOCAL_AUTH_JWT_SECRET="your-secret-here"
```

Users can authenticate via either SSO or local credentials. Useful for:
- Testing SSO setup while keeping local admin access
- Allowing external users (via SSO) and service accounts (via local)
- Migration periods

## SSO Configuration

### Microsoft Entra ID (Azure AD)

1. **Register Application in Azure Portal**
   - Go to Azure Active Directory → App registrations
   - Click "New registration"
   - Set redirect URI to your Fyr dashboard URL

2. **Configure API Permissions**
   - Add User.Read permission
   - Grant admin consent

3. **Configure Authentication**
   - Enable ID tokens
   - Set audience to `api://<your-client-id>`

4. **Get Configuration Values**
   - Tenant ID: Directory (tenant) ID
   - Client ID: Application (client) ID

5. **Set Environment Variables**
   ```bash
   export PROJECT_FYR_SSO_TENANT_ID="<tenant-id>"
   export PROJECT_FYR_SSO_CLIENT_ID="<client-id>"
   ```

## Security Best Practices

1. **JWT Secret**: Generate a strong random secret for production:
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

2. **HTTPS**: Always use HTTPS in production for:
   - Secure cookie transmission
   - JWT token security
   - SSO redirect flows

3. **Password Policy**: Enforce strong passwords for local users

4. **Regular Audits**: Review user list periodically

5. **Least Privilege**: Don't grant admin unless necessary

## API Authentication

### Using Bearer Tokens

```bash
# Get token via login
TOKEN=$(curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"your-password"}' \
  | jq -r '.access_token')

# Use token in API requests
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/overview/insights
```

### Using Session Cookies

After logging in via the web interface, cookies are automatically sent with requests.

## Troubleshooting

### "Authentication required" on all pages

- Check `PROJECT_FYR_AUTH_ENABLED=true` is set
- Verify you're logged in at `/login`
- Check browser cookies are enabled

### "Invalid credentials" on login

- Verify username exists: `python scripts/manage_users.py list`
- Try resetting password: `python scripts/manage_users.py passwd <username>`
- Check database connection

### SSO token validation fails

- Verify `SSO_TENANT_ID` and `SSO_CLIENT_ID` are correct
- Check Azure AD application configuration
- Ensure token audience matches `api://<client-id>`
- Review logs for detailed error messages

### Can't access /metrics or /health

- These are excluded from auth by default
- Check `PROJECT_FYR_AUTH_EXCLUDE_PATHS` includes them
- Restart dashboard after config changes

## Development

### Running Tests

```bash
pytest tests/test_auth.py
```

### Adding New SSO Providers

1. Create provider class in `project_fyr/auth/providers/your_provider.py`
2. Implement `AuthProvider` interface
3. Update `dashboard.py` to initialize your provider
4. Document configuration in this README

Example structure:
```python
from ..base import AuthProvider

class YourProvider(AuthProvider):
    async def validate_token(self, token: str) -> Dict:
        # Implement token validation
        pass
    
    def get_provider_name(self) -> str:
        return "your_provider"
```

## Architecture

```
project_fyr/auth/
├── __init__.py          # Module exports
├── base.py              # AuthProvider interface
├── local_auth.py        # Local username/password provider
├── middleware.py        # FastAPI authentication middleware
├── models.py            # User and Session database models
├── endpoints.py         # Login/logout API endpoints
├── utils.py             # Password hashing, token utilities
└── providers/
    ├── __init__.py
    └── entra.py         # Microsoft Entra ID provider
```

## Contributing

When contributing authentication features:

1. Follow existing provider patterns
2. Add comprehensive logging
3. Write unit tests
4. Update this documentation
5. Consider security implications

## License

Same as Project Fyr main license.
