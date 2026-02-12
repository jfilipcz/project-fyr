import { test, expect } from '@playwright/test';

/**
 * Dashboard Overview Tests
 * Covers the main dashboard page with rollout statistics and lists
 */

test.describe('Dashboard Overview', () => {
  test.beforeEach(async ({ page }) => {
    // Assume authenticated - implement auth fixture in real tests
    await page.goto('/');
  });

  test('should display overview statistics', async ({ page }) => {
    // Check for key metrics
    await expect(page.locator('[data-testid="total-rollouts"]')).toBeVisible();
    await expect(page.locator('[data-testid="failed-rollouts"]')).toBeVisible();
    await expect(page.locator('[data-testid="success-rate"]')).toBeVisible();
    
    // Metrics should have numeric values
    const totalRollouts = await page.locator('[data-testid="total-rollouts"]').textContent();
    expect(totalRollouts).toMatch(/\d+/);
  });

  test('should display recent rollouts table', async ({ page }) => {
    // Check table headers
    await expect(page.locator('th:has-text("Namespace")')).toBeVisible();
    await expect(page.locator('th:has-text("Deployment")')).toBeVisible();
    await expect(page.locator('th:has-text("Status")')).toBeVisible();
    await expect(page.locator('th:has-text("Started")')).toBeVisible();
    
    // Should have at least some rows (if data exists)
    const rows = page.locator('tbody tr');
    const count = await rows.count();
    
    if (count > 0) {
      // First row should have namespace and deployment
      await expect(rows.first().locator('td').first()).not.toBeEmpty();
    }
  });

  test('should filter rollouts by status', async ({ page }) => {
    // Click "Failed" filter
    await page.click('[data-testid="filter-failed"]');
    
    // All visible rollouts should be failed
    const statusCells = page.locator('tbody tr td:has([data-status])');
    const count = await statusCells.count();
    
    for (let i = 0; i < count; i++) {
      const status = await statusCells.nth(i).getAttribute('data-status');
      expect(['FAILED', 'TIMED_OUT']).toContain(status || '');
    }
  });

  test('should filter rollouts by namespace', async ({ page }) => {
    // Get first namespace from the table
    const firstNamespace = await page.locator('tbody tr td:nth-child(1)').first().textContent();
    
    if (!firstNamespace) {
      test.skip('No rollouts available');
    }
    
    // Type in namespace filter
    await page.fill('[data-testid="namespace-filter"]', firstNamespace);
    
    // Wait for filtering
    await page.waitForTimeout(300);
    
    // All visible rows should match the namespace
    const namespaceCells = page.locator('tbody tr td:nth-child(1)');
    const count = await namespaceCells.count();
    
    for (let i = 0; i < count; i++) {
      const ns = await namespaceCells.nth(i).textContent();
      expect(ns).toBe(firstNamespace);
    }
  });

  test('should paginate rollouts list', async ({ page }) => {
    // Check if pagination exists
    const nextButton = page.locator('[data-testid="pagination-next"]');
    
    if (!(await nextButton.isVisible())) {
      test.skip('Not enough data for pagination');
    }
    
    // Get first row before pagination
    const firstRowBefore = await page.locator('tbody tr').first().textContent();
    
    // Click next page
    await nextButton.click();
    
    // First row should be different
    const firstRowAfter = await page.locator('tbody tr').first().textContent();
    expect(firstRowAfter).not.toBe(firstRowBefore);
  });

  test('should navigate to rollout detail on row click', async ({ page }) => {
    // Click first rollout row
    await page.click('tbody tr:first-child');
    
    // Should navigate to detail page
    await expect(page).toHaveURL(/\/rollouts\/\d+/);
  });

  test('should refresh data on manual refresh', async ({ page }) => {
    // Click refresh button
    await page.click('[data-testid="refresh-button"]');
    
    // Should show loading state
    await expect(page.locator('[data-testid="loading-spinner"]')).toBeVisible();
    
    // Wait for data to load
    await expect(page.locator('[data-testid="loading-spinner"]')).not.toBeVisible({ timeout: 5000 });
  });

  test('should auto-refresh data periodically', async ({ page }) => {
    // Wait for auto-refresh (if implemented)
    const initialTimestamp = await page.locator('[data-testid="last-updated"]').textContent();
    
    // Wait for refresh interval (e.g., 30 seconds)
    await page.waitForTimeout(31000);
    
    const newTimestamp = await page.locator('[data-testid="last-updated"]').textContent();
    expect(newTimestamp).not.toBe(initialTimestamp);
  });

  test('should display empty state when no rollouts', async ({ page }) => {
    // Apply filters that return no results
    await page.fill('[data-testid="namespace-filter"]', 'non-existent-namespace-xyz');
    await page.waitForTimeout(300);
    
    // Should show empty state
    await expect(page.locator('[data-testid="empty-state"]')).toBeVisible();
    await expect(page.locator('text=/No rollouts found|No data available/')).toBeVisible();
  });
});

test.describe('Dashboard Navigation', () => {
  test('should navigate between pages', async ({ page }) => {
    await page.goto('/');
    
    // Navigate to Alerts page
    await page.click('[data-testid="nav-alerts"]');
    await expect(page).toHaveURL(/\/alerts/);
    
    // Navigate back to Overview
    await page.click('[data-testid="nav-overview"]');
    await expect(page).toHaveURL('/');
  });

  test('should show active navigation state', async ({ page }) => {
    await page.goto('/');
    
    // Overview should be active
    await expect(page.locator('[data-testid="nav-overview"]')).toHaveClass(/active/);
    
    // Navigate to Alerts
    await page.click('[data-testid="nav-alerts"]');
    
    // Alerts should be active
    await expect(page.locator('[data-testid="nav-alerts"]')).toHaveClass(/active/);
  });
});
