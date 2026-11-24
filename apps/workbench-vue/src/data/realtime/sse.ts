import { createParser } from 'eventsource-parser'
import { HttpError } from '@/data/http'

export interface SseFrame {
  event: string
  id?: string
  data: unknown
}

export async function readRunStream(input: {
  runId: string
  afterSequence: number
  token: string
  signal: AbortSignal
  onFrame: (frame: SseFrame) => void
  base?: string
  onConnected?: () => void
}) {
  const response = await fetch(
    `${input.base ?? '/api/v1'}/runs/${encodeURIComponent(input.runId)}/events?afterSequence=${input.afterSequence}`,
    {
      headers: {
        Authorization: `Bearer ${input.token}`,
        Accept: 'text/event-stream',
      },
      signal: input.signal,
    },
  )
  if (!response.ok) {
    throw new HttpError(response.status, response.headers.get('X-Request-ID'), await response.text())
  }
  if (!response.body || !response.headers.get('Content-Type')?.includes('text/event-stream')) {
    throw new Error('无效的事件流响应')
  }
  input.onConnected?.()

  let parseFailure: Error | undefined
  const parser = createParser({
    onEvent(message) {
      if (parseFailure) return
      try {
        input.onFrame({
          event: message.event ?? 'message',
          id: message.id,
          data: JSON.parse(message.data),
        })
      } catch (error) {
        parseFailure = error instanceof Error ? error : new Error('事件格式错误')
      }
    },
  })

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      parser.feed(decoder.decode(value, { stream: true }))
      if (parseFailure) throw parseFailure
    }
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock() }
}
