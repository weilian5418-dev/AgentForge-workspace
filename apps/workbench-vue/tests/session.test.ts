import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useSessionStore } from '../src/stores/session'

beforeEach(() => setActivePinia(createPinia()))
afterEach(() => vi.unstubAllGlobals())
describe('local service connection', () => {
  it('validates a handoff credential before showing connected', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ apiToken: 'local-memory-token' }))).mockResolvedValueOnce(new Response(JSON.stringify({ items: [] })))
    vi.stubGlobal('fetch', fetch)
    const session = useSessionStore()
    expect(await session.connect()).toBe(true)
    expect(session.connected).toBe(true)
    expect(session.token).toBe('local-memory-token')
    expect(fetch.mock.calls[1]?.[1].headers.Authorization).toBe('Bearer local-memory-token')
  })
  it('does not send credentials to a nonlocal endpoint', async () => {
    const fetch = vi.fn()
    vi.stubGlobal('fetch', fetch)
    const session = useSessionStore()
    expect(await session.connect({ endpoint: 'https://outside.example', token: 'secret' })).toBe(false)
    expect(fetch).not.toHaveBeenCalled()
    expect(session.connected).toBe(false)
  })
  it('clears a rejected token and preserves the backend failure reason', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ error: { message: 'Authentication required' } }), { status: 401 })))
    const session = useSessionStore()
    session.setToken('previous-token')
    expect(await session.connect({ token: 'wrong-token' })).toBe(false)
    expect(session.token).toBeNull()
    expect(session.error).toBe('Authentication required')
  })
})
