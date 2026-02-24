# Changelog

All notable changes to Project Fyr will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- **LLM Factory** (`project_fyr/llm.py`) — centralised LLM creation supporting OpenAI, Azure OpenAI, and any OpenAI-compatible endpoint (Ollama, vLLM, etc.)
- **Generic OIDC Provider** (`project_fyr/auth/providers/oidc.py`) — authenticate users via any standards-compliant OIDC provider (Okta, Keycloak, Auth0, Google Workspace)
- **Configurable annotation prefix** — the Kubernetes annotation prefix (default `project-fyr.io`) is now configurable via `PROJECT_FYR_ANNOTATION_PREFIX`
- **GitHub Actions CI** — automated testing on Python 3.11 and 3.12 with ruff linting
- **Comprehensive test suite** — 115 tests covering LLM factory, auth providers, triage, Slack blocks, webhook, aggregator, dashboard, and service logic
- **OSS scaffolding** — CONTRIBUTING.md, SECURITY.md, CHANGELOG.md, CODE_OF_CONDUCT.md

### Changed
- Refactored `agent.py` and `aggregator.py` to use centralised `create_llm()` factory instead of inline Azure/OpenAI branching
- JWT authentication now **fails closed** — raises `ValueError` on startup if the default placeholder secret is used
- Webhook endpoint now **rejects requests** when no secret is configured (previously allowed unauthenticated access)
- Dashboard error responses no longer expose internal exception details to clients
- Database engine is now a singleton (shared across requests) instead of being created per-request
- Replaced all `datetime.utcnow()` calls with timezone-aware `utcnow()` helper
- Replaced all bare `except:` clauses with `except Exception:`


### Security
- Fixed JWT default secret bypass — application now refuses to start with insecure default
- Fixed webhook authentication bypass — endpoint rejects when secret is not configured
- Genericised error messages to prevent information leakage through API responses
