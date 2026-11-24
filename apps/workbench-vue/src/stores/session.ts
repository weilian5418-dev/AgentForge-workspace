import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { HttpError } from '@/data/http'

export const useSessionStore = defineStore('session', () => {
  const token = ref<string | null>(null)
  const connected = ref(false)
  const endpointLabel = ref('127.0.0.1:8765')
  const apiBase = ref('/api/v1')
  const connecting = ref(false)
  const error = ref('')
  const projectId = ref<string | null>(null)
  const streamStatus = ref<'idle' | 'connecting' | 'connected' | 'reconnecting'>('idle')
  const lastSync = ref<string | null>(null)
  const streamLabel = computed(() => ({ idle: '尚未订阅任务', connecting: '正在连接', connected: '已连接', reconnecting: '正在重连' })[streamStatus.value])
  let connection: Promise<boolean> | null = null

  function setToken(next: string | null) {
    token.value = next
    connected.value = Boolean(next)
  }

  function connect(options: { endpoint?: string; token?: string } = {}): Promise<boolean> {
    if (connection) return connection
    connection = (async () => {
      connecting.value = true
      error.value = ''
      try {
        const endpoint = options.endpoint?.trim().replace(/\/$/, '')
        const localProxy = !endpoint || endpoint === 'http://127.0.0.1:8765'
        if (endpoint) {
          const url = new URL(endpoint)
          if (!['http:', 'https:'].includes(url.protocol) || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) || url.username || url.password || url.pathname !== '/' || url.search || url.hash) {
            throw new Error('请输入本机服务地址，例如 http://127.0.0.1:8765')
          }
        }
        const base = localProxy ? '/api/v1' : `${endpoint}/api/v1`
        let nextToken = options.token?.trim()
        if (!nextToken) {
          const claim = await fetch(`${base}/local/ui-handoff/claim`, { method: 'POST', signal: AbortSignal.timeout(8000) })
          if (!claim.ok) throw new HttpError(claim.status, claim.headers.get('X-Request-ID'), await claim.text())
          nextToken = (await claim.json()).apiToken
        }
        if (!nextToken) throw new Error('本机服务未提供有效凭据')
        const response = await fetch(`${base}/projects?limit=1`, { headers: { Authorization: `Bearer ${nextToken}` }, signal: AbortSignal.timeout(8000) })
        if (!response.ok) throw new HttpError(response.status, response.headers.get('X-Request-ID'), await response.text())
        apiBase.value = base
        endpointLabel.value = endpoint?.replace(/^https?:\/\//, '') ?? '127.0.0.1:8765'
        setToken(nextToken)
        return true
      } catch (cause) {
        setToken(null)
        error.value = cause instanceof Error ? cause.message : '无法连接本机服务'
        return false
      } finally {
        connecting.value = false
        connection = null
      }
    })()
    return connection
  }

  return { token, connected, endpointLabel, apiBase, connecting, error, projectId, streamStatus, streamLabel, lastSync, setToken, connect }
})
