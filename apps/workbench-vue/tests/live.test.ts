import { afterEach, describe, expect, it, vi } from 'vitest'
import { createLiveRepository } from '../src/data/repositories/live'
import { createApiClient, HttpError } from '../src/data/http'

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', 'X-Request-ID': 'integration-request' } })
afterEach(() => vi.unstubAllGlobals())

describe('live backend failures', () => {
  it('keeps failed inventory, tools and approval requests visible instead of inventing empty lists', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ error: { message: 'Database unavailable' } }, 503)))
    const repository = createLiveRepository(() => 'memory-only-token')
    await expect(repository.loadExecutors()).rejects.toThrow('Database unavailable')
    await expect(repository.loadTools()).rejects.toThrow('Database unavailable')
    await expect(repository.loadInbox()).rejects.toThrow('Database unavailable')
  })
  it('does not infer database readiness from a healthy HTTP server', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => url.endsWith('/health') ? json({ status: 'ok' }) : json({ status: 'not_ready', acceptingWork: false, database: { status: 'unavailable', message: 'Database offline' }, reason: 'Database offline' }, 503)))
    const settings = await createLiveRepository(() => 'token').loadSettings()
    expect(settings.services.find(service => service.id === 'api')?.status).toBe('ok')
    expect(settings.services.find(service => service.id === 'database')?.status).toBe('warn')
    expect(settings.services.find(service => service.id === 'admission')?.status).toBe('warn')
    expect(settings.alerts).toContain('Database offline')
  })
  it('refuses to send a message without an actual recipient', async () => {
    const fetch = vi.fn()
    vi.stubGlobal('fetch', fetch)
    await expect(createLiveRepository(() => 'token').sendMessage({ runId: 'run', text: 'hello', idempotencyKey: 'key' })).rejects.toThrow('接收者')
    expect(fetch).not.toHaveBeenCalled()
  })
  it('distinguishes no plan yet from a broken plan endpoint', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(json({}, 404)).mockResolvedValueOnce(json({ error: { message: 'Storage failure' } }, 500))
    vi.stubGlobal('fetch', fetch)
    const repository = createLiveRepository(() => 'token')
    expect(await repository.loadPlan!('run')).toBeNull()
    await expect(repository.loadPlan!('run')).rejects.toThrow('Storage failure')
  })
  it('preserves the backend error and request id for a rejected assignment', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ error: { message: 'auth_invalid: authentication is not verified' } }, 400)))
    try { await createApiClient(() => 'token')('/runs/run/assignments') }
    catch (error) {
      expect(error).toBeInstanceOf(HttpError)
      expect((error as HttpError).requestId).toBe('integration-request')
      expect((error as Error).message).toContain('auth_invalid')
      return
    }
    throw new Error('Expected a rejected request')
  })
  it('does not count a planning task as an actual delivery', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url.includes('/projects')) return json({ items: [{ id: 'project', name: 'Project', grantStatus: 'active', currentBaselineId: 'base' }] })
      if (url.includes('/agents/summary')) return json({ products: [] })
      return json({ items: [{ id: 'run', goal: 'Planning only', status: 'awaiting_confirmation', statusLabel: '等待确认' }] })
    }))
    const overview = await createLiveRepository(() => 'token').loadOverview('project')
    expect(overview.recentDeliveries).toHaveLength(0)
    expect(overview.attentionItems[0]?.to).toBe('/tasks/run')
  })
})
