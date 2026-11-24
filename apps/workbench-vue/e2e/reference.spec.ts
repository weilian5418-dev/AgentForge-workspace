import { test, expect } from '@playwright/test'
import { mkdir } from 'node:fs/promises'

const pages = [
  ['01-overview', '/workspace', 'overview'],
  ['02-new-task', '/tasks/new', 'newTask'],
  ['03-execution', '/tasks/demo-order-export', 'executing'],
  ['04-delivery-review', '/tasks/demo-order-export/delivery', 'deliveryReady'],
  ['05-task-library', '/tasks', 'taskLibrary'],
  ['06-inbox', '/inbox', 'approvalPending'],
  ['07-executors', '/executors', 'executors'],
  ['08-harness-profile', '/executors/profiles/demo-frontend', 'profile'],
  ['09-knowledge-library', '/knowledge', 'knowledge'],
  ['10-knowledge-document', '/knowledge/demo-api-contract', 'document'],
  ['11-extensions', '/extensions', 'extensions'],
  ['12-settings-system', '/settings/connection', 'settings'],
] as const

for (const [id, route, fixture] of pages) {
  test(id, async ({ page }) => {
    const errors: string[] = []
    page.on('pageerror', error => errors.push(error.message))
    await page.goto(`${route}?reference=1&fixture=${fixture}`)
    await page.evaluate(async () => { await document.fonts.ready })
    await expect(page.locator('html')).toHaveAttribute('data-visual-ready', 'true', { timeout: 30_000 })
    await expect(page.locator(`[data-page-id="${id}"]`)).toBeVisible()
    const fontsLoaded = await page.evaluate(() =>
      document.fonts.check('18px "AF Sans"', '订单导出') &&
      document.fonts.check('18px "AF Latin"', 'AgentForge'),
    )
    expect(fontsLoaded).toBe(true)
    expect(errors).toEqual([])
    await mkdir('visual/current', { recursive: true })
    await page.screenshot({
      path: `visual/current/${id}.png`,
      animations: 'disabled',
      caret: 'hide',
      fullPage: false,
    })
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)
    expect(overflow).toBe(false)
  })
}
