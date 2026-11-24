export class HttpError extends Error {
  status: number
  requestId: string | null
  responseBody: string
  constructor(
    status: number,
    requestId: string | null,
    responseBody: string,
  ) {
    let message = `请求失败（HTTP ${status}）`
    try {
      const body = JSON.parse(responseBody)
      message = body.error?.message ?? body.message ?? body.detail ?? message
    } catch { /* A proxy can return a non-JSON error. */ }
    super(message)
    this.status = status
    this.requestId = requestId
    this.responseBody = responseBody
  }
}

export function createApiClient(getToken: () => string | null, base: string | (() => string) = '/api/v1') {
  return async function request<Result>(path: string, init: RequestInit = {}): Promise<Result> {
    if (!path.startsWith('/') || path.startsWith('//')) {
      throw new Error('API path must be relative to configured base')
    }
    const token = getToken()
    if (!token) throw new Error('请先连接后端服务')
    const headers = new Headers(init.headers)
    headers.set('Authorization', `Bearer ${token}`)
    headers.set('Accept', 'application/json')
    if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json')
    }
    const response = await fetch(`${typeof base === 'function' ? base() : base}${path}`, { ...init, headers })
    if (!response.ok) {
      throw new HttpError(response.status, response.headers.get('X-Request-ID'), await response.text())
    }
    if (response.status === 204) return undefined as Result
    return response.json() as Promise<Result>
  }
}
