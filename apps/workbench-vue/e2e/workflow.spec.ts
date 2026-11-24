import { test, expect } from '@playwright/test'

test('reference workflow navigation shell', async ({ page }) => {
  await page.goto('/workspace?reference=1&fixture=overview')
  await expect(page.locator('html')).toHaveAttribute('data-visual-ready', 'true', { timeout: 30_000 })
  await page.getByRole('button', { name: '创建任务' }).click()
  await expect(page).toHaveURL(/\/tasks\/new/)
  await expect(page.locator('[data-page-id="02-new-task"]')).toBeVisible()
})
