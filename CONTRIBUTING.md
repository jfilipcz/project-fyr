# Contributing to Project Fyr

Thank you for your interest in contributing to Project Fyr! This guide will help you get started.

## Development Setup

```bash
# Clone the repository
git clone https://github.com/jfilipcz/project-fyr.git
cd project-fyr

# Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install in editable mode with dev dependencies
pip install -e '.[dev]'
```

## Running Tests

```bash
# Run the full test suite
pytest tests/ -v

# Run a specific test file
pytest tests/test_triage.py -v

# Run with coverage (install pytest-cov first)
pip install pytest-cov
pytest tests/ --cov=project_fyr --cov-report=term-missing
```

## Code Style

- We use [ruff](https://docs.astral.sh/ruff/) for linting.
- Line length: 120 characters.
- Target Python version: 3.11+.

Run the linter:
```bash
ruff check project_fyr/ tests/
```

## Making Changes

1. **Fork** the repository and create a feature branch from `main`.
2. **Write tests** for any new functionality or bug fixes.
3. **Run the test suite** to ensure nothing is broken.
4. **Run the linter** to ensure code style compliance.
5. **Commit** with clear, descriptive messages.
6. **Open a Pull Request** against `main`.

## Pull Request Guidelines

- Keep PRs focused — one logical change per PR.
- Include tests for new features and bug fixes.
- Update documentation if your change affects user-facing behaviour.
- Ensure CI passes before requesting review.

## Architecture Overview

Project Fyr consists of three services sharing a common codebase:

| Service | Entry Point | Purpose |
|---------|-------------|---------|
| Watcher | `python -m project_fyr.watcher_service` | Monitors K8s deployments and namespaces |
| Analyzer | `python -m project_fyr.analyzer_service` | Investigates failures using LLM agent |
| Dashboard | `python -m project_fyr.dashboard` | Web UI for browsing and investigating |

Key modules:
- `project_fyr/llm.py` — LLM factory (supports OpenAI, Azure OpenAI, and compatible endpoints)
- `project_fyr/agent.py` — LangChain-based Investigator Agent
- `project_fyr/triage.py` — Keyword-based failure triage
- `project_fyr/slack.py` / `slack_blocks.py` — Slack notification system
- `project_fyr/auth/` — Authentication subsystem (local JWT, OIDC)

## Reporting Issues

- Use GitHub Issues for bug reports and feature requests.
- Include reproduction steps, expected vs. actual behaviour, and relevant logs.
- For security issues, see [SECURITY.md](SECURITY.md).

## License

By contributing, you agree that your contributions will be licensed under the [AGPL-3.0 License](LICENSE).
