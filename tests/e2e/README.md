# Project Fyr E2E Tests

End-to-end tests for Project Fyr using Playwright.

## Setup

```bash
cd tests/e2e
npm install
npx playwright install
```

## Running Tests

### All tests
```bash
npm test
```

### Specific test suite
```bash
npm run test:auth          # Authentication tests
npm run test:dashboard     # Dashboard tests
npm run test:rollout       # Rollout detail tests
npm run test:investigate   # Investigation tests
npm run test:slack         # Slack integration tests
```

### Headed mode (with browser UI)
```bash
npm run test:headed
```

### Debug mode
```bash
npm run test:debug
```

### Interactive UI mode
```bash
npm run test:ui
```

### Specific browser
```bash
npm run test:chromium
npm run test:firefox
npm run test:webkit
```

## Environment Variables

Set these before running tests:

```bash
# Dashboard URL
export FYR_DASHBOARD_URL=https://fyr.example.com

# SSO Credentials (for auth tests)
export TEST_SSO_EMAIL=test@example.com
export TEST_SSO_PASSWORD=yourpassword

# Local Auth Credentials (if enabled)
export TEST_LOCAL_USERNAME=admin
export TEST_LOCAL_PASSWORD=admin

# Slack Mock Server (for Slack tests)
export SLACK_MOCK_URL=http://localhost:8081
```

## Test Structure

- `tests/auth.spec.ts` - Authentication and authorization
- `tests/dashboard-overview.spec.ts` - Dashboard overview page
- `tests/rollout-detail.spec.ts` - Rollout detail pages
- `tests/investigate.spec.ts` - On-demand investigation
- `tests/slack-integration.spec.ts` - Slack notifications and interactivity

## CI Integration

Tests run automatically in CI with:
- Retry on failure (2 retries)
- Parallel execution disabled for consistency
- JUnit XML reports for integration
- Screenshots and videos on failure

## Writing New Tests

1. Create a new `.spec.ts` file in `tests/`
2. Use test fixtures for authentication
3. Add `data-testid` attributes to UI elements
4. Follow existing test patterns

Example:
```typescript
import { test, expect } from '@playwright/test';

test.describe('My Feature', () => {
  test('should do something', async ({ page }) => {
    await page.goto('/my-feature');
    await expect(page.locator('[data-testid="my-element"]')).toBeVisible();
  });
});
```

## Best Practices

1. Use `data-testid` attributes for selectors
2. Wait for network requests to complete
3. Handle timeouts for AI operations (60s+)
4. Mock external services in CI
5. Use fixtures for common setup
6. Keep tests independent
7. Clean up test data after runs

## Debugging Tips

- Use `await page.pause()` to pause execution
- Run with `--headed` to see the browser
- Use `--debug` for step-by-step debugging
- Check `test-results/` for screenshots/videos
- Use `playwright codegen` to generate test code

## Reports

View test reports:
```bash
npm run report
```

Reports include:
- Test execution timeline
- Screenshots on failure
- Video recordings
- Network logs
- Console logs
