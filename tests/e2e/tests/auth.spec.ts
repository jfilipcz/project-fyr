import { test, expect } from '@playwright/test';

/**
 * Authentication & Authorization Tests
 * Covers SSO login, session management, and access control
 */

test.describe('Authentication', () => {
  test('should redirect unauthenticated users to login', async ({ page }) => {
    await page.goto('/');
    
    // Should redirect to SSO login or show login page
    await expect(page).toHaveURL(/\/(login|auth)/);
  });

  test('should login via SSO (Entra ID)', async ({ page }) => {
    // Note: This test requires environment variables for SSO credentials
    const ssoEmail = process.env.TEST_SSO_EMAIL;
    const ssoPassword = process.env.TEST_SSO_PASSWORD;
    
    if (!ssoEmail || !ssoPassword) {
      test.skip('SSO credentials not provided');
    }

    await page.goto('/');
    
    // Wait for redirect to Microsoft login
    await page.waitForURL(/login\.microsoftonline\.com/);
    
    // Fill in email
    await page.fill('input[type="email"]', ssoEmail);
    await page.click('input[type="submit"]');
    
    // Fill in password
    await page.fill('input[type="password"]', ssoPassword);
    await page.click('input[type="submit"]');
    
    // Handle "Stay signed in?" prompt
    await page.click('input[value="Yes"]').catch(() => {
      // Button might not appear
    });
    
    // Should redirect back to dashboard
    await expect(page).toHaveURL('/');
    
    // Should see user info or logout button
    await expect(page.locator('[data-testid="user-menu"]')).toBeVisible();
  });

  test('should persist session after page reload', async ({ page, context }) => {
    // Assuming we have an authenticated session
    await page.goto('/');
    
    // Store cookies
    const cookies = await context.cookies();
    
    // Reload page
    await page.reload();
    
    // Should still be authenticated
    await expect(page).toHaveURL('/');
    await expect(page.locator('[data-testid="user-menu"]')).toBeVisible();
  });

  test('should logout successfully', async ({ page }) => {
    await page.goto('/');
    
    // Click user menu
    await page.click('[data-testid="user-menu"]');
    
    // Click logout
    await page.click('[data-testid="logout-button"]');
    
    // Should redirect to login
    await expect(page).toHaveURL(/\/(login|auth)/);
  });

  test('should deny access to protected endpoints without auth', async ({ page }) => {
    // Clear cookies
    await page.context().clearCookies();
    
    // Try to access API endpoint
    const response = await page.goto('/api/rollouts');
    
    // Should return 401 or redirect
    expect([401, 302, 303]).toContain(response?.status() || 0);
  });
});

test.describe('Local Authentication (if enabled)', () => {
  test('should login with username and password', async ({ page }) => {
    const username = process.env.TEST_LOCAL_USERNAME || 'admin';
    const password = process.env.TEST_LOCAL_PASSWORD || 'admin';
    
    await page.goto('/login');
    
    // Fill in credentials
    await page.fill('input[name="username"]', username);
    await page.fill('input[name="password"]', password);
    
    // Submit form
    await page.click('button[type="submit"]');
    
    // Should redirect to dashboard
    await expect(page).toHaveURL('/');
    await expect(page.locator('[data-testid="user-menu"]')).toBeVisible();
  });

  test('should show error on invalid credentials', async ({ page }) => {
    await page.goto('/login');
    
    await page.fill('input[name="username"]', 'invalid');
    await page.fill('input[name="password"]', 'wrongpassword');
    await page.click('button[type="submit"]');
    
    // Should show error message
    await expect(page.locator('.error, .alert-danger, [role="alert"]')).toBeVisible();
    
    // Should stay on login page
    await expect(page).toHaveURL(/\/login/);
  });
});
