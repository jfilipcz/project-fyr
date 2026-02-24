# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in Project Fyr, **please do not open a public issue**.

Instead, report it privately by emailing the maintainers or using GitHub's [private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability) feature on this repository.

We will acknowledge your report within 48 hours and aim to provide a fix or mitigation plan within 7 days.

## Supported Versions

| Version | Supported |
|---------|-----------|
| main (HEAD) | ✅ |
| < 1.0.0 | ⚠️ Best-effort |

## Security Considerations

### Authentication

- The dashboard supports local JWT authentication and generic OIDC (Okta, Keycloak, Auth0, etc.).
- **Always** set `PROJECT_FYR_LOCAL_AUTH_JWT_SECRET` to a strong random value in production. The application will refuse to start with the default placeholder.
- When using OIDC, configure `PROJECT_FYR_OIDC_ISSUER` and `PROJECT_FYR_OIDC_AUDIENCE` to match your identity provider.

### Webhook Security

- The alert webhook endpoint (`/webhook/alert`) requires a shared secret via `PROJECT_FYR_ALERT_WEBHOOK_SECRET`.
- If no secret is configured, the endpoint rejects all requests (fail-closed).

### Secrets Management

- Never commit secrets to the repository.
- Use Kubernetes Secrets, External Secrets Operator, or environment variables.
- The Helm chart supports `secrets.existingSecret` for injecting credentials.

### LLM API Keys

- API keys for OpenAI or Azure OpenAI should be injected via environment variables, not stored in configuration files.
- The LLM factory does not log or expose API keys.
