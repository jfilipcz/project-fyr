import { test, expect } from '@playwright/test';

/**
 * On-Demand Investigation Tests
 * Covers triggering investigations from the dashboard
 */

test.describe('On-Demand Investigation', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
  });

  test('should navigate to investigate page', async ({ page }) => {
    // Click investigate in nav or menu
    await page.click('[data-testid="nav-investigate"]');
    
    // Should be on investigate page
    await expect(page).toHaveURL(/\/investigate/);
    
    // Should show investigation form
    await expect(page.locator('[data-testid="investigate-form"]')).toBeVisible();
  });

  test('should show namespace and deployment inputs', async ({ page }) => {
    await page.goto('/investigate');
    
    // Check for input fields
    await expect(page.locator('[data-testid="namespace-input"]')).toBeVisible();
    await expect(page.locator('[data-testid="deployment-input"]')).toBeVisible();
    
    // Optional question field
    await expect(page.locator('[data-testid="question-input"]')).toBeVisible();
  });

  test('should validate required fields', async ({ page }) => {
    await page.goto('/investigate');
    
    // Click investigate without filling fields
    await page.click('[data-testid="investigate-submit"]');
    
    // Should show validation errors
    await expect(page.locator('.error, .invalid-feedback')).toBeVisible();
  });

  test('should trigger investigation with namespace and deployment', async ({ page }) => {
    await page.goto('/investigate');
    
    // Fill in form
    await page.fill('[data-testid="namespace-input"]', 'fyr-testing');
    await page.fill('[data-testid="deployment-input"]', 'test-app');
    
    // Submit
    await page.click('[data-testid="investigate-submit"]');
    
    // Should show loading state
    await expect(page.locator('[data-testid="investigation-loading"]')).toBeVisible();
    
    // Should show progress indicator
    await expect(page.locator('[data-testid="investigation-progress"]')).toBeVisible();
  });

  test('should trigger investigation with specific question', async ({ page }) => {
    await page.goto('/investigate');
    
    // Fill in form with question
    await page.fill('[data-testid="namespace-input"]', 'fyr-testing');
    await page.fill('[data-testid="deployment-input"]', 'test-app');
    await page.fill('[data-testid="question-input"]', 'Why did the pods crash?');
    
    // Submit
    await page.click('[data-testid="investigate-submit"]');
    
    // Should show loading with question context
    await expect(page.locator('text=/Investigating.*Why did the pods crash/')).toBeVisible();
  });

  test('should display investigation results', async ({ page }) => {
    await page.goto('/investigate');
    
    // Trigger investigation
    await page.fill('[data-testid="namespace-input"]', 'fyr-testing');
    await page.fill('[data-testid="deployment-input"]', 'test-app');
    await page.click('[data-testid="investigate-submit"]');
    
    // Wait for results (AI can take time)
    await page.waitForSelector('[data-testid="investigation-results"]', { timeout: 60000 });
    
    // Should show analysis
    await expect(page.locator('[data-testid="root-cause"]')).toBeVisible();
    await expect(page.locator('[data-testid="recommendations"]')).toBeVisible();
  });

  test('should show error on investigation failure', async ({ page }) => {
    await page.goto('/investigate');
    
    // Use non-existent deployment
    await page.fill('[data-testid="namespace-input"]', 'non-existent-namespace');
    await page.fill('[data-testid="deployment-input"]', 'non-existent-deployment');
    await page.click('[data-testid="investigate-submit"]');
    
    // Should show error message
    await expect(page.locator('[data-testid="investigation-error"]')).toBeVisible({ timeout: 30000 });
  });

  test('should allow retry on failure', async ({ page }) => {
    await page.goto('/investigate');
    
    // Trigger investigation that might fail
    await page.fill('[data-testid="namespace-input"]', 'test-namespace');
    await page.fill('[data-testid="deployment-input"]', 'test-deployment');
    await page.click('[data-testid="investigate-submit"]');
    
    // Wait for potential error
    const retryButton = page.locator('[data-testid="retry-investigation"]');
    
    if (await retryButton.isVisible({ timeout: 60000 })) {
      // Click retry
      await retryButton.click();
      
      // Should restart investigation
      await expect(page.locator('[data-testid="investigation-loading"]')).toBeVisible();
    }
  });

  test('should show investigation history', async ({ page }) => {
    await page.goto('/investigate');
    
    // Check for history section
    const history = page.locator('[data-testid="investigation-history"]');
    
    if (await history.isVisible()) {
      // Should list previous investigations
      const items = history.locator('.history-item');
      const count = await items.count();
      
      if (count > 0) {
        // Items should show namespace, deployment, timestamp
        await expect(items.first().locator('.namespace')).toBeVisible();
        await expect(items.first().locator('.deployment')).toBeVisible();
        await expect(items.first().locator('.timestamp')).toBeVisible();
      }
    }
  });

  test('should reload investigation from history', async ({ page }) => {
    await page.goto('/investigate');
    
    const history = page.locator('[data-testid="investigation-history"]');
    
    if (await history.isVisible()) {
      const items = history.locator('.history-item');
      const count = await items.count();
      
      if (count > 0) {
        // Click first history item
        await items.first().click();
        
        // Should populate form
        const namespace = await page.locator('[data-testid="namespace-input"]').inputValue();
        const deployment = await page.locator('[data-testid="deployment-input"]').inputValue();
        
        expect(namespace).not.toBe('');
        expect(deployment).not.toBe('');
      }
    }
  });
});

test.describe('Namespace Investigation', () => {
  test('should investigate namespace-level issues', async ({ page }) => {
    await page.goto('/investigate');
    
    // Fill only namespace (no deployment)
    await page.fill('[data-testid="namespace-input"]', 'test-namespace');
    
    // Ask namespace-level question
    await page.fill('[data-testid="question-input"]', 'When was this namespace created?');
    
    // Submit
    await page.click('[data-testid="investigate-submit"]');
    
    // Should show namespace analysis
    await page.waitForSelector('[data-testid="investigation-results"]', { timeout: 60000 });
    
    // Results should contain namespace metadata
    await expect(page.locator('[data-testid="namespace-details"]')).toBeVisible();
  });
});
