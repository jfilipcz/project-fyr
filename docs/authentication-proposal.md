# Fyr Dashboard Authentication Proposal

## Overview

This proposal outlines an authentication system for the Fyr dashboard that supports both:
1. **SSO Authentication** (Microsoft Entra ID / Azure AD by default)
2. **Local Authentication** (username/password fallback)
3. **Pluggable SSO Providers** (allow other deployments to use different SSO providers)

The design is based on the authentication pattern used in kvarn, with added flexibility for multi-provider support and local auth fallback.

## Architecture

### Authentication Flow

```
User Request
    ↓
AuthenticationMiddleware
    ↓
Check excluded paths (health, static files, etc.)
    ↓
Extract Bearer token from Authorization header
    ↓
Determine authentication method:
    ├─ SSO Token → Validate with configured SSO provider
    ├─ Local Token → Validate with JWT + local user DB
    └─ Session Cookie → Validate session
    ↓
Set user in request.state
    ↓
Continue to endpoint
```

### Component Structure

```
project_fyr/
├── auth/
│   ├── __init__.py              # Exports main auth components
│   ├── base.py                  # Abstract base classes for auth providers
│   ├── middleware.py            # FastAPI middleware for auth
│   ├── local_auth.py            # Local username/password auth
│   ├── providers/
│   │   ├── __init__.py
│   │   ├── entra.py            # Microsoft Entra ID (Azure AD) provider
│   │   ├── okta.py             # Okta provider (example)
│   │   └── generic_oidc.py     # Generic OIDC provider
│   ├── models.py                # User, Session models for DB
│   └── utils.py                 # Token generation, password hashing
├── dashboard.py                 # Updated with auth integration
└── config.py                    # Updated with auth settings
```

## Configuration

### Environment Variables

```bash
# Authentication Mode
PROJECT_FYR_AUTH_ENABLED=true
PROJECT_FYR_AUTH_MODE=hybrid  # Options: sso, local, hybrid

# SSO Provider Configuration
PROJECT_FYR_SSO_PROVIDER=entra  # Options: entra, okta, generic_oidc, custom
PROJECT_FYR_SSO_TENANT_ID=<tenant-id>
PROJECT_FYR_SSO_CLIENT_ID=<client-id>
PROJECT_FYR_SSO_CLIENT_SECRET=<client-secret>  # For OIDC flows

# Generic OIDC (for custom providers)
PROJECT_FYR_OIDC_ISSUER=https://your-provider.com
PROJECT_FYR_OIDC_JWKS_URI=https://your-provider.com/.well-known/jwks.json
PROJECT_FYR_OIDC_AUDIENCE=api://your-app

# Local Authentication
PROJECT_FYR_LOCAL_AUTH_ENABLED=true
PROJECT_FYR_LOCAL_AUTH_JWT_SECRET=<random-secret>
PROJECT_FYR_LOCAL_AUTH_JWT_EXPIRY_HOURS=24
PROJECT_FYR_LOCAL_AUTH_SESSION_EXPIRY_HOURS=168  # 7 days

# Default Admin User (created on startup if doesn't exist)
PROJECT_FYR_ADMIN_USERNAME=admin
PROJECT_FYR_ADMIN_PASSWORD=<secure-password>
PROJECT_FYR_ADMIN_EMAIL=admin@example.com

# Excluded Paths (no auth required)
PROJECT_FYR_AUTH_EXCLUDE_PATHS=/health,/metrics,/static,/api/webhook
```

### config.py Updates

```python
class Settings(BaseSettings):
    # ... existing settings ...
    
    # Authentication
    auth_enabled: bool = Field(default=False, description="Enable authentication for dashboard")
    auth_mode: str = Field(default="hybrid", description="Auth mode: sso, local, or hybrid")
    
    # SSO Configuration
    sso_provider: str = Field(default="entra", description="SSO provider: entra, okta, generic_oidc")
    sso_tenant_id: Optional[str] = Field(default=None)
    sso_client_id: Optional[str] = Field(default=None)
    sso_client_secret: Optional[str] = Field(default=None)
    
    # Generic OIDC
    oidc_issuer: Optional[str] = Field(default=None)
    oidc_jwks_uri: Optional[str] = Field(default=None)
    oidc_audience: Optional[str] = Field(default=None)
    
    # Local Auth
    local_auth_enabled: bool = Field(default=True, description="Enable local username/password auth")
    local_auth_jwt_secret: str = Field(default="change-me-in-production")
    local_auth_jwt_expiry_hours: int = Field(default=24)
    local_auth_session_expiry_hours: int = Field(default=168)
    
    # Default Admin
    admin_username: str = Field(default="admin")
    admin_password: Optional[str] = Field(default=None)
    admin_email: str = Field(default="admin@example.com")
    
    # Excluded paths
    auth_exclude_paths: str = Field(
        default="/health,/metrics,/static,/api/webhook",
        description="Comma-separated list of path prefixes to exclude from auth"
    )
```

## Implementation Details

### 1. Base Authentication Provider (auth/base.py)

```python
from abc import ABC, abstractmethod
from typing import Dict, Optional

class AuthProvider(ABC):
    """Abstract base class for authentication providers."""
    
    @abstractmethod
    async def validate_token(self, token: str) -> Dict:
        """
        Validate an authentication token.
        
        Returns:
            Dict with user info: {
                "user_id": str,
                "email": str,
                "name": str,
                "roles": List[str],
                "provider": str
            }
        
        Raises:
            HTTPException(401) if validation fails
        """
        pass
    
    @abstractmethod
    def get_provider_name(self) -> str:
        """Return the provider name (e.g., 'entra', 'okta')."""
        pass
```

### 2. Entra ID Provider (auth/providers/entra.py)

```python
# Based on kvarn's implementation
from ..base import AuthProvider
from fastapi import HTTPException
import jwt
import requests
from functools import lru_cache

class EntraIDProvider(AuthProvider):
    def __init__(self, tenant_id: str, client_id: str):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.jwks_uri_v1 = f"https://login.microsoftonline.com/{tenant_id}/discovery/keys"
        self.jwks_uri_v2 = f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
    
    @lru_cache(maxsize=3)
    def get_jwks(self, token_version: str):
        """Cache JWKS keys for token validation"""
        jwks_uri = self.jwks_uri_v1 if token_version == '1.0' else self.jwks_uri_v2
        response = requests.get(jwks_uri, timeout=10)
        response.raise_for_status()
        return response.json()
    
    async def validate_token(self, token: str) -> Dict:
        """Verify Entra ID JWT token"""
        try:
            unverified_payload = jwt.decode(token, options={"verify_signature": False})
            unverified_header = jwt.get_unverified_header(token)
            
            # Get and verify with public key
            kid = unverified_header.get('kid')
            token_version = unverified_payload.get('ver', '2.0')
            jwks_data = self.get_jwks(token_version)
            
            # Find matching key and verify
            public_key = None
            for key_data in jwks_data['keys']:
                if key_data.get('kid') == kid:
                    public_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(key_data))
                    break
            
            if not public_key:
                raise HTTPException(status_code=401, detail="Token key ID not found")
            
            decoded = jwt.decode(
                jwt=token,
                key=public_key,
                algorithms=['RS256'],
                audience=f"api://{self.client_id}",
                options={"verify_signature": True, "verify_exp": True}
            )
            
            return {
                "user_id": decoded.get("oid"),
                "email": decoded.get("preferred_username") or decoded.get("upn"),
                "name": decoded.get("name"),
                "roles": decoded.get("roles", []),
                "provider": "entra"
            }
            
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Token expired")
        except jwt.InvalidTokenError as e:
            raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")
    
    def get_provider_name(self) -> str:
        return "entra"
```

### 3. Local Authentication (auth/local_auth.py)

```python
from typing import Optional, Dict
from datetime import datetime, timedelta
from passlib.context import CryptContext
import jwt
from fastapi import HTTPException
from sqlalchemy.orm import Session
from .models import User, Session as UserSession
from .base import AuthProvider

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

class LocalAuthProvider(AuthProvider):
    def __init__(self, jwt_secret: str, jwt_expiry_hours: int = 24):
        self.jwt_secret = jwt_secret
        self.jwt_expiry_hours = jwt_expiry_hours
    
    def hash_password(self, password: str) -> str:
        return pwd_context.hash(password)
    
    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        return pwd_context.verify(plain_password, hashed_password)
    
    def create_access_token(self, user_id: int, email: str, name: str) -> str:
        """Create JWT access token."""
        expires = datetime.utcnow() + timedelta(hours=self.jwt_expiry_hours)
        payload = {
            "user_id": user_id,
            "email": email,
            "name": name,
            "exp": expires,
            "iat": datetime.utcnow(),
            "provider": "local"
        }
        return jwt.encode(payload, self.jwt_secret, algorithm="HS256")
    
    async def validate_token(self, token: str) -> Dict:
        """Validate local JWT token."""
        try:
            payload = jwt.decode(
                token,
                self.jwt_secret,
                algorithms=["HS256"],
                options={"verify_signature": True, "verify_exp": True}
            )
            
            return {
                "user_id": payload.get("user_id"),
                "email": payload.get("email"),
                "name": payload.get("name"),
                "roles": ["user"],  # Can be extended
                "provider": "local"
            }
            
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Token expired")
        except jwt.InvalidTokenError as e:
            raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")
    
    def authenticate_user(self, session: Session, username: str, password: str) -> Optional[User]:
        """Authenticate user with username and password."""
        from sqlalchemy import select
        stmt = select(User).where(User.username == username, User.is_active == True)
        user = session.scalars(stmt).first()
        
        if not user:
            return None
        
        if not self.verify_password(password, user.hashed_password):
            return None
        
        return user
    
    def get_provider_name(self) -> str:
        return "local"
```

### 4. Authentication Middleware (auth/middleware.py)

```python
from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from typing import List, Optional
import logging

logger = logging.getLogger(__name__)

class AuthenticationMiddleware(BaseHTTPMiddleware):
    def __init__(
        self, 
        app,
        sso_provider: Optional[AuthProvider] = None,
        local_provider: Optional[AuthProvider] = None,
        exclude_paths: List[str] = None,
        redirect_to_login: bool = True
    ):
        super().__init__(app)
        self.sso_provider = sso_provider
        self.local_provider = local_provider
        self.exclude_paths = exclude_paths or []
        self.redirect_to_login = redirect_to_login
    
    async def dispatch(self, request: Request, call_next):
        # Skip auth for excluded paths
        for path in self.exclude_paths:
            if request.url.path.startswith(path):
                return await call_next(request)
        
        # Skip for OPTIONS (CORS preflight)
        if request.method == "OPTIONS":
            return await call_next(request)
        
        # Try to get token from Authorization header or cookie
        token = None
        authorization = request.headers.get("Authorization")
        
        if authorization and authorization.startswith("Bearer "):
            token = authorization.split(" ")[1]
        else:
            # Try session cookie
            token = request.cookies.get("fyr_session")
        
        if not token:
            return self._unauthorized_response(request)
        
        # Try SSO provider first, then local
        user_info = None
        
        if self.sso_provider:
            try:
                user_info = await self.sso_provider.validate_token(token)
            except Exception as e:
                logger.debug(f"SSO validation failed: {e}")
        
        if not user_info and self.local_provider:
            try:
                user_info = await self.local_provider.validate_token(token)
            except Exception as e:
                logger.debug(f"Local validation failed: {e}")
        
        if not user_info:
            logger.warning(f"Authentication failed for {request.url.path}")
            return self._unauthorized_response(request)
        
        # Add user info to request state
        request.state.user = user_info
        
        response = await call_next(request)
        return response
    
    def _unauthorized_response(self, request: Request):
        """Return appropriate unauthorized response."""
        # For API requests, return JSON
        if request.url.path.startswith("/api/"):
            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication required"}
            )
        
        # For browser requests, redirect to login
        if self.redirect_to_login:
            return RedirectResponse(url="/login", status_code=302)
        
        return JSONResponse(
            status_code=401,
            content={"detail": "Authentication required"}
        )
```

### 5. Database Models (auth/models.py)

```python
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True)
    username = Column(String(255), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255))
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login = Column(DateTime, nullable=True)

class Session(Base):
    __tablename__ = "user_sessions"
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    session_token = Column(String(255), unique=True, nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    user_agent = Column(Text, nullable=True)
    ip_address = Column(String(45), nullable=True)
```

### 6. Login Endpoints (auth/endpoints.py)

```python
from fastapi import APIRouter, Depends, HTTPException, Response, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from .local_auth import LocalAuthProvider
from .models import User
from ..db import get_db_session

router = APIRouter(prefix="/auth", tags=["authentication"])

class LoginRequest(BaseModel):
    username: str
    password: str

class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict

@router.post("/login")
async def login(
    credentials: LoginRequest,
    response: Response,
    db: Session = Depends(get_db_session)
):
    """Local username/password login."""
    from ..config import settings
    
    if not settings.local_auth_enabled:
        raise HTTPException(status_code=403, detail="Local authentication is disabled")
    
    local_auth = LocalAuthProvider(
        jwt_secret=settings.local_auth_jwt_secret,
        jwt_expiry_hours=settings.local_auth_jwt_expiry_hours
    )
    
    user = local_auth.authenticate_user(db, credentials.username, credentials.password)
    
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    # Update last login
    user.last_login = datetime.utcnow()
    db.commit()
    
    # Create token
    token = local_auth.create_access_token(
        user_id=user.id,
        email=user.email,
        name=user.full_name or user.username
    )
    
    # Set cookie for browser
    response.set_cookie(
        key="fyr_session",
        value=token,
        httponly=True,
        secure=True,  # Use in production with HTTPS
        samesite="lax",
        max_age=settings.local_auth_jwt_expiry_hours * 3600
    )
    
    return LoginResponse(
        access_token=token,
        user={
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "name": user.full_name
        }
    )

@router.post("/logout")
async def logout(response: Response):
    """Logout user."""
    response.delete_cookie("fyr_session")
    return {"message": "Logged out successfully"}

@router.get("/me")
async def get_current_user(request: Request):
    """Get current authenticated user."""
    if not hasattr(request.state, "user"):
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    return request.state.user
```

## Dashboard Integration

### Updated dashboard.py

```python
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from .auth.middleware import AuthenticationMiddleware
from .auth.providers.entra import EntraIDProvider
from .auth.local_auth import LocalAuthProvider
from .auth.endpoints import router as auth_router
from .config import settings

app = FastAPI(title="Project Fyr Dashboard")

# Setup authentication if enabled
if settings.auth_enabled:
    # Initialize providers based on configuration
    sso_provider = None
    local_provider = None
    
    if settings.auth_mode in ["sso", "hybrid"] and settings.sso_provider == "entra":
        if settings.sso_tenant_id and settings.sso_client_id:
            sso_provider = EntraIDProvider(
                tenant_id=settings.sso_tenant_id,
                client_id=settings.sso_client_id
            )
    
    if settings.auth_mode in ["local", "hybrid"] and settings.local_auth_enabled:
        local_provider = LocalAuthProvider(
            jwt_secret=settings.local_auth_jwt_secret,
            jwt_expiry_hours=settings.local_auth_jwt_expiry_hours
        )
    
    # Add authentication middleware
    exclude_paths = settings.auth_exclude_paths.split(",")
    exclude_paths.extend(["/auth/login", "/auth/logout", "/login"])
    
    app.add_middleware(
        AuthenticationMiddleware,
        sso_provider=sso_provider,
        local_provider=local_provider,
        exclude_paths=exclude_paths,
        redirect_to_login=True
    )
    
    # Include auth endpoints
    app.include_router(auth_router)

# ... rest of existing dashboard code ...
```

## Login UI

### templates/login.html

```html
<!DOCTYPE html>
<html>
<head>
    <title>Fyr - Login</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
            margin: 0;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        }
        .login-container {
            background: white;
            padding: 2rem;
            border-radius: 10px;
            box-shadow: 0 10px 25px rgba(0,0,0,0.2);
            width: 100%;
            max-width: 400px;
        }
        h1 {
            text-align: center;
            color: #333;
            margin-bottom: 1.5rem;
        }
        .form-group {
            margin-bottom: 1rem;
        }
        label {
            display: block;
            margin-bottom: 0.5rem;
            color: #555;
        }
        input {
            width: 100%;
            padding: 0.75rem;
            border: 1px solid #ddd;
            border-radius: 5px;
            font-size: 1rem;
        }
        button {
            width: 100%;
            padding: 0.75rem;
            background: #667eea;
            color: white;
            border: none;
            border-radius: 5px;
            font-size: 1rem;
            cursor: pointer;
            margin-top: 1rem;
        }
        button:hover {
            background: #5568d3;
        }
        .sso-button {
            background: #0078d4;
            margin-top: 1rem;
        }
        .sso-button:hover {
            background: #106ebe;
        }
        .divider {
            text-align: center;
            margin: 1.5rem 0;
            color: #999;
        }
        .error {
            color: red;
            margin-top: 0.5rem;
            font-size: 0.9rem;
        }
    </style>
</head>
<body>
    <div class="login-container">
        <h1>🔥 Fyr</h1>
        
        {% if local_auth_enabled %}
        <form id="loginForm">
            <div class="form-group">
                <label for="username">Username</label>
                <input type="text" id="username" name="username" required>
            </div>
            <div class="form-group">
                <label for="password">Password</label>
                <input type="password" id="password" name="password" required>
            </div>
            <div id="error" class="error" style="display:none;"></div>
            <button type="submit">Login</button>
        </form>
        {% endif %}
        
        {% if local_auth_enabled and sso_enabled %}
        <div class="divider">- OR -</div>
        {% endif %}
        
        {% if sso_enabled %}
        <button class="sso-button" onclick="ssoLogin()">
            Sign in with Microsoft
        </button>
        {% endif %}
    </div>
    
    <script>
        document.getElementById('loginForm')?.addEventListener('submit', async (e) => {
            e.preventDefault();
            const username = document.getElementById('username').value;
            const password = document.getElementById('password').value;
            const errorDiv = document.getElementById('error');
            
            try {
                const response = await fetch('/auth/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username, password })
                });
                
                if (response.ok) {
                    window.location.href = '/';
                } else {
                    const data = await response.json();
                    errorDiv.textContent = data.detail || 'Login failed';
                    errorDiv.style.display = 'block';
                }
            } catch (error) {
                errorDiv.textContent = 'Network error';
                errorDiv.style.display = 'block';
            }
        });
        
        function ssoLogin() {
            // Implement SSO redirect flow (MSAL, OIDC, etc.)
            window.location.href = '/auth/sso/login';
        }
    </script>
</body>
</html>
```

## Deployment Considerations

### Helm Chart Updates

```yaml
# values.yaml
auth:
  enabled: false
  mode: hybrid  # sso, local, or hybrid
  
  sso:
    provider: entra  # entra, okta, generic_oidc
    tenantId: ""
    clientId: ""
    clientSecret: ""  # Use secret
    
  local:
    enabled: true
    jwtSecret: ""  # Generate securely
    
  admin:
    username: admin
    password: ""  # Set via secret
    email: admin@example.com

# Secret for sensitive values
---
apiVersion: v1
kind: Secret
metadata:
  name: fyr-auth-secrets
type: Opaque
stringData:
  sso-client-secret: "{{ .Values.auth.sso.clientSecret }}"
  local-jwt-secret: "{{ .Values.auth.local.jwtSecret }}"
  admin-password: "{{ .Values.auth.admin.password }}"
```

## Migration Path

1. **Phase 1: Core Implementation** (Week 1)
   - Implement base auth provider interface
   - Add Entra ID provider (based on kvarn)
   - Add local auth provider
   - Create database models

2. **Phase 2: Middleware & Integration** (Week 2)
   - Implement authentication middleware
   - Add login endpoints
   - Create login UI
   - Update dashboard.py

3. **Phase 3: Testing & Documentation** (Week 3)
   - Unit tests for auth providers
   - Integration tests
   - Update deployment documentation
   - Create admin user management CLI

4. **Phase 4: Additional Providers** (Week 4)
   - Add generic OIDC provider
   - Add Okta provider (optional)
   - Documentation for custom providers

## Security Considerations

1. **Secrets Management**
   - Never commit JWT secrets or SSO client secrets to Git
   - Use Kubernetes secrets in production
   - Rotate secrets regularly

2. **Token Security**
   - Use HTTPS in production
   - Set HttpOnly and Secure flags on cookies
   - Implement token expiry and refresh logic

3. **Password Security**
   - Use bcrypt for password hashing
   - Enforce strong password policies
   - Implement rate limiting on login endpoint

4. **Session Management**
   - Store session tokens in database
   - Implement session revocation
   - Clean up expired sessions

## Benefits

1. **Flexible Authentication**: Support both SSO and local auth
2. **Based on Proven Pattern**: Uses kvarn's working Entra ID implementation
3. **Pluggable Providers**: Easy to add new SSO providers
4. **Backward Compatible**: Auth can be disabled (default)
5. **Security First**: Industry-standard practices
6. **User-Friendly**: Simple login UI for local auth
7. **Enterprise Ready**: SSO support for corporate deployments

## Next Steps

1. Review and approve this proposal
2. Create implementation tickets
3. Set up development environment with auth enabled
4. Implement Phase 1 components
5. Test with your Entra ID tenant
