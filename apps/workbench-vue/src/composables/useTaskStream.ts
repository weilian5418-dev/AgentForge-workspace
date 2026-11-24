import { watch, type Ref } from 'vue'
import { useQueryClient } from '@tanstack/vue-query'
import { shouldUseReferenceFromUrl } from '@/app/runtime'
import { useSessionStore } from '@/stores/session'
import { readRunStream } from '@/data/realtime/sse'
import { HttpError } from '@/data/http'
import type { TaskViewModel } from '@/domain/view-models'

export function useTaskStream(task: Ref<TaskViewModel | undefined>) {
  const session = useSessionStore()
  const client = useQueryClient()
  if (shouldUseReferenceFromUrl(window.location.search)) return
  watch(() => [task.value?.id, session.token] as const, ([runId, token], _, onCleanup) => {
    if (!runId || !token || !task.value?.live) return
    const controller = new AbortController()
    let cursor = task.value.live.latestSequence
    let refreshTimer: ReturnType<typeof setTimeout> | undefined
    let retryTimer: ReturnType<typeof setTimeout> | undefined
    let failures = 0
    function refresh() {
      if (refreshTimer) return
      refreshTimer = setTimeout(() => {
        refreshTimer = undefined
        void client.invalidateQueries({ predicate: query => ['task', 'delivery', 'plan', 'tasks', 'task-sidebar', 'overview', 'inbox'].includes(String(query.queryKey[0])) })
      }, 150)
    }
    async function connect() {
      if (controller.signal.aborted) return
      session.streamStatus = failures ? 'reconnecting' : 'connecting'
      try {
        await readRunStream({ runId: runId!, token: token!, base: session.apiBase, afterSequence: cursor, signal: controller.signal,
          onConnected: () => { session.streamStatus = 'connected'; failures = 0 },
          onFrame: frame => {
            session.lastSync = new Date().toLocaleTimeString('zh-CN')
            if (frame.event === 'auth_revoked') { session.setToken(null); controller.abort(); return }
            if (frame.event === 'resync_required') { cursor = task.value?.live?.latestSequence ?? cursor; refresh(); return }
            const data = frame.data as { sequence?: number }
            if (typeof data?.sequence === 'number') cursor = Math.max(cursor, data.sequence)
            refresh()
          },
        })
      } catch (error) {
        if (controller.signal.aborted) return
        if (error instanceof HttpError && error.status === 401) { session.setToken(null); return }
        if (error instanceof HttpError && error.status === 409) {
          await client.invalidateQueries({ queryKey: ['task', runId] })
          await client.invalidateQueries({ queryKey: ['delivery', runId] })
          cursor = task.value?.live?.latestSequence ?? cursor
        }
      }
      if (!controller.signal.aborted && session.connected) {
        session.streamStatus = 'reconnecting'
        retryTimer = setTimeout(() => { void connect() }, Math.min(1000 * 2 ** failures++, 15000))
      }
    }
    void connect()
    onCleanup(() => { controller.abort(); clearTimeout(refreshTimer); clearTimeout(retryTimer); session.streamStatus = 'idle' })
  }, { immediate: true })
}
