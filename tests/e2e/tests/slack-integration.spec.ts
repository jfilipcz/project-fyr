import { test, expect } from '@playwright/test';

/**
 * Slack Integration Tests
 * These tests verify the Slack notification flow and interactive features
 * Note: These require Slack API mocking or a test workspace
 */

test.describe('Slack Notifications', () => {
  test.skip('requires Slack mock server or test workspace');

  test('should send failure notification to Slack', async ({ request }) => {
    // This test would verify Slack webhook/API integration
    // In CI, this should use a mock Slack server
    
    const slackMockUrl = process.env.SLACK_MOCK_URL || 'http://localhost:8081';
    
    // Trigger a deployment failure (or use existing failed rollout)
    // The watcher should send a Slack notification
    
    // Check mock server received notification
    const response = await request.get(`${slackMockUrl}/messages`);
    expect(response.ok()).toBeTruthy();
    
    const messages = await response.json();
    expect(messages.length).toBeGreaterThan(0);
    
    // Verify message structure
    const lastMessage = messages[messages.length - 1];
    expect(lastMessage).toHaveProperty('channel');
    expect(lastMessage).toHaveProperty('blocks');
    
    // Should have "Investigate Further" button
    const blocks = lastMessage.blocks;
    const actionBlock = blocks.find((b: any) => b.type === 'actions');
    expect(actionBlock).toBeDefined();
    
    const investigateButton = actionBlock.elements.find(
      (e: any) => e.action_id === 'investigate_further'
    );
    expect(investigateButton).toBeDefined();
  });

  test('should format Markdown correctly in Slack', async ({ request }) => {
    const slackMockUrl = process.env.SLACK_MOCK_URL || 'http://localhost:8081';
    
    // Get recent Slack message
    const response = await request.get(`${slackMockUrl}/messages`);
    const messages = await response.json();
    
    if (messages.length > 0) {
      const lastMessage = messages[messages.length - 1];
      const text = JSON.stringify(lastMessage);
      
      // Should NOT have raw Markdown symbols
      expect(text).not.toMatch(/##\s+/); // No ## headers
      expect(text).not.toMatch(/\*\*\w+\*\*/); // No **bold**
      
      // Should have Slack mrkdwn format
      expect(text).toMatch(/\*\w+\*/); // Slack *bold*
    }
  });
});

test.describe('Slack Interactive Features', () => {
  test.skip('requires Slack Socket Mode or webhook simulation');

  test('should handle "Investigate Further" button click', async ({ request }) => {
    // This would simulate clicking the button in Slack
    // In real implementation, this would be triggered via Slack's API
    
    const slackMockUrl = process.env.SLACK_MOCK_URL || 'http://localhost:8081';
    
    // Simulate button click
    const response = await request.post(`${slackMockUrl}/interactions`, {
      data: {
        type: 'block_actions',
        actions: [{
          action_id: 'investigate_further',
          value: 'test-namespace/test-deployment',
        }],
      },
    });
    
    expect(response.ok()).toBeTruthy();
    
    // Should post a thread reply
    const messages = await (await request.get(`${slackMockUrl}/messages`)).json();
    const threadReplies = messages.filter((m: any) => m.thread_ts);
    
    expect(threadReplies.length).toBeGreaterThan(0);
    
    // Thread should contain investigation context
    const reply = threadReplies[threadReplies.length - 1];
    expect(reply.text).toMatch(/ready to help investigate/i);
  });

  test('should handle threaded questions', async ({ request }) => {
    const slackMockUrl = process.env.SLACK_MOCK_URL || 'http://localhost:8081';
    
    // Simulate user asking a question in thread
    await request.post(`${slackMockUrl}/messages`, {
      data: {
        channel: 'C0XXXXXXXXXXX',
        thread_ts: '1234567890.123456',
        text: 'Why did this fail?',
        user: 'U05UFAG2WPR',
      },
    });
    
    // Should trigger AI investigation
    // Check for response in thread
    await new Promise(resolve => setTimeout(resolve, 5000)); // Wait for AI
    
    const messages = await (await request.get(`${slackMockUrl}/messages`)).json();
    const responses = messages.filter(
      (m: any) => m.thread_ts === '1234567890.123456' && m.user === 'bot'
    );
    
    expect(responses.length).toBeGreaterThan(0);
    
    // Response should contain analysis
    const botResponse = responses[responses.length - 1];
    expect(botResponse.text.length).toBeGreaterThan(50); // Substantial response
  });

  test('should handle "View in Fyr" link', async ({ page, request }) => {
    const slackMockUrl = process.env.SLACK_MOCK_URL || 'http://localhost:8081';
    
    // Get recent message
    const messages = await (await request.get(`${slackMockUrl}/messages`)).json();
    
    if (messages.length > 0) {
      const lastMessage = messages[messages.length - 1];
      
      // Extract "View in Fyr" link
      const blocks = lastMessage.blocks;
      const actionBlock = blocks.find((b: any) => b.type === 'actions');
      
      if (actionBlock) {
        const viewButton = actionBlock.elements.find(
          (e: any) => e.action_id === 'view_in_fyr'
        );
        
        if (viewButton && viewButton.url) {
          // Navigate to the URL
          await page.goto(viewButton.url);
          
          // Should be on rollout detail page
          await expect(page).toHaveURL(/\/rollouts\/\d+/);
          
          // Should show rollout details
          await expect(page.locator('[data-testid="rollout-detail"]')).toBeVisible();
        }
      }
    }
  });
});

test.describe('Slack App Home', () => {
  test.skip('requires Slack App Home API access');

  test('should display App Home with statistics', async ({ request }) => {
    // This would verify the App Home view published to Slack
    const slackMockUrl = process.env.SLACK_MOCK_URL || 'http://localhost:8081';
    
    // Get App Home view
    const response = await request.get(`${slackMockUrl}/app_home`);
    expect(response.ok()).toBeTruthy();
    
    const appHome = await response.json();
    
    // Should have blocks with statistics
    expect(appHome.blocks).toBeDefined();
    expect(appHome.blocks.length).toBeGreaterThan(0);
    
    // Should show failure statistics
    const textContent = JSON.stringify(appHome);
    expect(textContent).toMatch(/\d+.*failed/i);
  });
});
