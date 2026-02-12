import { test, expect } from '@playwright/test';

/**
 * Rollout Detail Page Tests
 * Covers viewing detailed information about a specific rollout
 */

test.describe('Rollout Detail Page', () => {
  test.beforeEach(async ({ page }) => {
    // Navigate to a specific rollout (use first failed rollout)
    await page.goto('/');
    
    // Click first failed rollout
    await page.click('[data-status="FAILED"]:first-of-type');
    
    // Wait for navigation
    await page.waitForURL(/\/rollouts\/\d+/);
  });

  test('should display rollout metadata', async ({ page }) => {
    // Check key information fields
    await expect(page.locator('[data-testid="rollout-namespace"]')).toBeVisible();
    await expect(page.locator('[data-testid="rollout-deployment"]')).toBeVisible();
    await expect(page.locator('[data-testid="rollout-status"]')).toBeVisible();
    await expect(page.locator('[data-testid="rollout-started-at"]')).toBeVisible();
    
    // Status should indicate failure
    const status = await page.locator('[data-testid="rollout-status"]').textContent();
    expect(['FAILED', 'TIMED_OUT']).toContain(status?.trim() || '');
  });

  test('should display AI analysis summary', async ({ page }) => {
    // Check for analysis section
    await expect(page.locator('[data-testid="analysis-summary"]')).toBeVisible();
    
    // Should have root cause
    await expect(page.locator('[data-testid="root-cause"]')).toBeVisible();
    
    // Should have recommendations
    await expect(page.locator('[data-testid="recommendations"]')).toBeVisible();
  });

  test('should display triage information', async ({ page }) => {
    // Check for triage team assignment
    const triageSection = page.locator('[data-testid="triage-info"]');
    await expect(triageSection).toBeVisible();
    
    // Should show assigned team
    await expect(triageSection.locator('[data-testid="triage-team"]')).toBeVisible();
    
    // Should show triage reason
    await expect(triageSection.locator('[data-testid="triage-reason"]')).toBeVisible();
  });

  test('should display pod information', async ({ page }) => {
    // Check for pods section
    await expect(page.locator('[data-testid="pods-section"]')).toBeVisible();
    
    // Should list failing pods
    const podsList = page.locator('[data-testid="failing-pods"] li');
    const count = await podsList.count();
    
    if (count > 0) {
      // First pod should have name
      await expect(podsList.first()).not.toBeEmpty();
    }
  });

  test('should display recent events', async ({ page }) => {
    // Check for events section
    await expect(page.locator('[data-testid="events-section"]')).toBeVisible();
    
    // Should have event list
    const events = page.locator('[data-testid="event-list"] .event-item');
    const count = await events.count();
    
    if (count > 0) {
      // Events should have timestamp and message
      await expect(events.first().locator('.event-time')).toBeVisible();
      await expect(events.first().locator('.event-message')).toBeVisible();
    }
  });

  test('should expand/collapse detailed sections', async ({ page }) => {
    // Find a collapsible section
    const logsToggle = page.locator('[data-testid="toggle-logs"]');
    
    if (await logsToggle.isVisible()) {
      // Click to expand
      await logsToggle.click();
      await expect(page.locator('[data-testid="logs-content"]')).toBeVisible();
      
      // Click to collapse
      await logsToggle.click();
      await expect(page.locator('[data-testid="logs-content"]')).not.toBeVisible();
    }
  });

  test('should link to Slack notification', async ({ page }) => {
    // Check if Slack link exists
    const slackLink = page.locator('[data-testid="slack-thread-link"]');
    
    if (await slackLink.isVisible()) {
      // Link should have proper href
      await expect(slackLink).toHaveAttribute('href', /slack\.com/);
      
      // Link should open in new tab
      await expect(slackLink).toHaveAttribute('target', '_blank');
    }
  });

  test('should trigger on-demand investigation', async ({ page }) => {
    // Click investigate button
    await page.click('[data-testid="investigate-button"]');
    
    // Should show loading state
    await expect(page.locator('[data-testid="investigation-loading"]')).toBeVisible();
    
    // Wait for investigation to complete
    await expect(page.locator('[data-testid="investigation-loading"]')).not.toBeVisible({
      timeout: 60000, // AI investigation can take time
    });
    
    // Should show updated analysis
    await expect(page.locator('[data-testid="analysis-updated"]')).toBeVisible();
  });

  test('should navigate back to overview', async ({ page }) => {
    // Click back button
    await page.click('[data-testid="back-button"]');
    
    // Should return to overview
    await expect(page).toHaveURL('/');
  });

  test('should copy rollout ID to clipboard', async ({ page }) => {
    // Click copy button
    await page.click('[data-testid="copy-rollout-id"]');
    
    // Should show success toast
    await expect(page.locator('.toast, .notification').filter({ hasText: /copied/i })).toBeVisible();
  });

  test('should display analysis timeline', async ({ page }) => {
    // Check for timeline section
    const timeline = page.locator('[data-testid="analysis-timeline"]');
    
    if (await timeline.isVisible()) {
      // Should have timeline entries
      const entries = timeline.locator('.timeline-entry');
      const count = await entries.count();
      
      expect(count).toBeGreaterThan(0);
      
      // Entries should be chronologically ordered
      const timestamps = await entries.locator('.timestamp').allTextContents();
      // Verify descending order (most recent first)
      for (let i = 1; i < timestamps.length; i++) {
        const prev = new Date(timestamps[i - 1]);
        const curr = new Date(timestamps[i]);
        expect(prev.getTime()).toBeGreaterThanOrEqual(curr.getTime());
      }
    }
  });
});

test.describe('Rollout Detail - ArgoCD Integration', () => {
  test('should display ArgoCD application status', async ({ page }) => {
    await page.goto('/rollouts/1'); // Assuming rollout 1 has ArgoCD
    
    const argoSection = page.locator('[data-testid="argocd-section"]');
    
    if (await argoSection.isVisible()) {
      // Should show sync status
      await expect(argoSection.locator('[data-testid="argocd-sync-status"]')).toBeVisible();
      
      // Should show health status
      await expect(argoSection.locator('[data-testid="argocd-health"]')).toBeVisible();
      
      // Should link to ArgoCD UI
      const argoLink = argoSection.locator('a[href*="argocd"]');
      await expect(argoLink).toHaveAttribute('target', '_blank');
    }
  });
});
