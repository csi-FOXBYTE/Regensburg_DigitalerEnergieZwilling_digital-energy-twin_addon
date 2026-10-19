import { expect, test } from '@playwright/test';
import { ADMIN, MATOMO } from './matomo';

// Clicks through the Matomo web installer; does nothing if already installed.
test('install Matomo', async ({ page }) => {
  test.setTimeout(180_000);
  await page.goto(`${MATOMO}/`);
  if (!(await page.getByText('Installation status').isVisible())) {
    await expect(page.locator('body')).toContainText(/Sign in|Username/i);
    return;
  }

  const next = async () => {
    await page
      .locator('input[type=submit], button[type=submit], a.btn')
      .filter({ hasText: /next/i })
      .first()
      .click();
    await page.waitForLoadState('networkidle');
  };
  await next(); // Welcome
  await next(); // System check
  await next(); // Database setup, prefilled from the container environment
  await next(); // Tables created
  await page.fill('input[name=login]', ADMIN.login);
  await page.fill('input[name=password]', ADMIN.password);
  await page.fill('input[name=password_bis]', ADMIN.password);
  await page.fill('input[name=email]', 'admin@example.invalid');
  await next();

  await expect(
    page.getByRole('heading', { name: 'Set up a Website' }),
  ).toBeVisible();
  await page.fill('input[name=siteName]', 'DEZ Sanierungstool (lokal)');
  await page.fill('input[name=url]', process.env.SITE_URL ?? 'localhost:4329');
  await page
    .locator('select[name=timezone]')
    .selectOption('UTC', { force: true });
  await page.getByRole('button', { name: /Next/ }).click();
  await page.waitForLoadState('networkidle');

  await page.goto(`${MATOMO}/index.php?module=Installation&action=finished`);
  await expect(page.locator('body')).toContainText(/Congratulations/i);
  await page.locator('input[type=submit], button[type=submit]').last().click();
  await page.waitForLoadState('networkidle');
  await expect(page.locator('body')).toContainText(/Sign in|Username/i);
});
