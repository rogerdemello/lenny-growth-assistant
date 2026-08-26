/**
 * API client.
 *
 * Streaming uses `fetch` with a manual SSE reader rather than `EventSource`,
 * for two reasons: EventSource cannot issue a POST, and it reconnects
 * automatically — which for a chat turn means silently re-running an expensive
 * generation after a network blip. Here a dropped stream surfaces as an error
 * the user can see and retry deliberately.
 */

const BASE = import.meta.env.VITE_API_BASE_URL ?? ''

export type Citation = {
  chunk_id: string
  episode_slug: string
  guest: string
  episode_title: string
  speaker: string
  start_seconds: number
  timestamp: string
  text: string
  score: number
  youtube_url: string | null
}

export type ValidationCheck = { name: string; passed: boolean; detail: string }

export type Validation = {
  passed: boolean
  score: string
  word_count: number
  section_count: number
  citation_count: number
  checks: ValidationCheck[]
}

export type Artifact = {
  id?: string
  kind: 'markdown' | 'html'
  title: string
  raw_content: string
  sanitized_content: string
  sanitizer_report: { removed?: string[]; modified?: boolean; policy?: string }
  validation?: Validation
  version?: number
  created_at?: string
}

export type Session = {
  id: string
  title: string
  user_id: string
  provider: string | null
  model: string | null
  created_at: string
  updated_at: string
  message_count: number
}

export type Message = {
  id: string
  role: 'user' | 'assistant' | 'system' | 'tool'
  content: string
  citations: Citation[]
  provider: string | null
  model: string | null
  latency_ms: number | null
  created_at: string
}

export type AppConfig = {
  active_provider: string
  active_model: string | null
  fallback_provider: string | null
  essay_provider: string
  agent_runtime: string
  embed_provider: string
  embed_model: string
  available: Array<{ name: string; model?: string; configured: boolean }>
  runtime: Record<string, unknown>
  corpus: { episodes?: number; chunks?: number; embedded_chunks?: number; error?: string }
  retrieval: { top_k: number; prompt_top_k: number; score_floor: number }
}

export type Health = {
  status: 'ok' | 'degraded'
  version: string
  components: Record<string, { ok: boolean; detail: Record<string, unknown> }>
}

/** Every SSE frame the backend can emit. */
export type StreamEvent =
  | { type: 'stage'; stage: string; detail?: string; intent?: string; progress?: { current: number; total: number } }
  | { type: 'token'; text: string }
  | { type: 'tool_call'; name: string; arguments: Record<string, unknown> }
  | { type: 'citations'; citations: Citation[]; final?: boolean }
  | { type: 'outline'; title: string; hook: string; sections: string[] }
  | { type: 'validation'; report: Validation }
  | { type: 'artifact'; [k: string]: unknown }
  | { type: 'artifact_saved'; [k: string]: unknown }
  | { type: 'done'; [k: string]: unknown }
  | { type: 'saved'; message_id: string; latency_ms: number }
  | { type: 'error'; code: string; message: string; hint?: string }

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  })
  if (!res.ok) {
    let message = `Request failed (${res.status})`
    let hint: string | undefined
    try {
      const body = await res.json()
      message = body?.error?.message ?? message
      hint = body?.error?.hint
    } catch {
      /* the body was not JSON; the status-based message stands */
    }
    throw new ApiError(message, res.status, hint)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public hint?: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

export const api = {
  health: () => request<Health>('/health'),
  config: () => request<AppConfig>('/api/config'),

  listSessions: () => request<Session[]>('/api/sessions'),
  createSession: (title = 'New chat') =>
    request<Session>('/api/sessions', {
      method: 'POST',
      body: JSON.stringify({
        title,
        client_metadata: {
          timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
          viewport: `${window.innerWidth}x${window.innerHeight}`,
        },
      }),
    }),
  getSession: (id: string) =>
    request<Session & { messages: Message[]; artifacts: Artifact[] }>(`/api/sessions/${id}`),
  deleteSession: (id: string) => request<void>(`/api/sessions/${id}`, { method: 'DELETE' }),

  artifactDownloadUrl: (id: string, raw = false) =>
    `${BASE}/api/artifacts/${id}/download${raw ? '?raw=true' : ''}`,

  /**
   * Send a message and stream the reply.
   *
   * Returns an abort function so the UI can cancel a generation that is taking
   * minutes — which, on a 3B model over CPU, is a normal thing to want.
   */
  streamMessage(
    sessionId: string,
    message: string,
    onEvent: (event: StreamEvent) => void,
  ): { done: Promise<void>; abort: () => void } {
    const controller = new AbortController()

    const done = (async () => {
      const res = await fetch(`${BASE}/api/sessions/${sessionId}/messages`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message, stream: true }),
        signal: controller.signal,
      })

      if (!res.ok || !res.body) {
        let msg = `Chat request failed (${res.status})`
        try {
          const body = await res.json()
          msg = body?.error?.message ?? msg
        } catch {
          /* non-JSON error body */
        }
        onEvent({ type: 'error', code: 'request_failed', message: msg })
        return
      }

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done: finished, value } = await reader.read()
        if (finished) break
        buffer += decoder.decode(value, { stream: true })

        // Frames are separated by a blank line. Keep the trailing partial
        // frame in the buffer until the rest of it arrives.
        const frames = buffer.split('\n\n')
        buffer = frames.pop() ?? ''

        for (const frame of frames) {
          const line = frame.trim()
          if (!line.startsWith('data:')) continue
          const payload = line.slice(5).trim()
          if (payload === '[DONE]') return
          try {
            onEvent(JSON.parse(payload) as StreamEvent)
          } catch {
            console.warn('Unparseable SSE frame', payload.slice(0, 200))
          }
        }
      }
    })()

    return { done, abort: () => controller.abort() }
  },
}
