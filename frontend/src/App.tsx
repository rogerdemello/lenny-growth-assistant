import { useCallback, useEffect, useRef, useState } from 'react'
import { ArtifactViewer } from './components/ArtifactViewer'
import { ChatPane, type ChatTurn, type Stage } from './components/ChatPane'
import { Sidebar } from './components/Sidebar'
import { api, type AppConfig, type Artifact, type Health, type Session, type StreamEvent } from './lib/api'

const STAGE_LABELS: Record<string, string> = {
  routing: 'Reading your question',
  condensed: 'Resolving the follow-up',
  retrieving: 'Searching transcripts',
  no_grounding: 'No matching passages',
  generating: 'Writing the answer',
  essay: 'Starting the essay',
  searching: 'Searching transcripts',
  outlining: 'Planning the structure',
  writing: 'Writing',
}

export default function App() {
  const [sessions, setSessions] = useState<Session[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [turns, setTurns] = useState<ChatTurn[]>([])
  const [artifacts, setArtifacts] = useState<Artifact[]>([])
  const [openArtifact, setOpenArtifact] = useState<Artifact | null>(null)
  const [config, setConfig] = useState<AppConfig | null>(null)
  const [health, setHealth] = useState<Health | null>(null)
  const [stage, setStage] = useState<Stage | null>(null)
  const [streaming, setStreaming] = useState(false)
  const [input, setInput] = useState('')
  const [sidebarOpen, setSidebarOpen] = useState(false)

  const abortRef = useRef<(() => void) | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // -- bootstrap ---------------------------------------------------------

  const refreshStatus = useCallback(async () => {
    const [cfg, hp] = await Promise.allSettled([api.config(), api.health()])
    if (cfg.status === 'fulfilled') setConfig(cfg.value)
    if (hp.status === 'fulfilled') setHealth(hp.value)
  }, [])

  useEffect(() => {
    refreshStatus()
    // Polled so a provider going down mid-session shows up without a reload —
    // which is exactly the failure the demo deliberately triggers.
    const timer = setInterval(refreshStatus, 20000)
    return () => clearInterval(timer)
  }, [refreshStatus])

  useEffect(() => {
    api
      .listSessions()
      .then((list) => {
        setSessions(list)
        if (list.length > 0) void selectSession(list[0].id)
      })
      .catch(() => setSessions([]))
    // Intentionally once, on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // -- session handling --------------------------------------------------

  async function selectSession(id: string) {
    setActiveId(id)
    setOpenArtifact(null)
    try {
      const detail = await api.getSession(id)
      setTurns(
        detail.messages
          .filter((m) => m.role === 'user' || m.role === 'assistant')
          .map((m) => ({
            id: m.id,
            role: m.role as 'user' | 'assistant',
            content: m.content,
            citations: m.citations ?? [],
            provider: m.provider,
            model: m.model,
            latencyMs: m.latency_ms,
          })),
      )
      setArtifacts(detail.artifacts ?? [])
    } catch {
      setTurns([])
      setArtifacts([])
    }
  }

  async function newSession() {
    const session = await api.createSession()
    setSessions((prev) => [session, ...prev])
    setActiveId(session.id)
    setTurns([])
    setArtifacts([])
    setOpenArtifact(null)
    textareaRef.current?.focus()
    return session
  }

  async function removeSession(id: string) {
    await api.deleteSession(id)
    setSessions((prev) => prev.filter((s) => s.id !== id))
    if (activeId === id) {
      setActiveId(null)
      setTurns([])
      setArtifacts([])
      setOpenArtifact(null)
    }
  }

  // -- sending -----------------------------------------------------------

  async function send(text: string) {
    const message = text.trim()
    if (!message || streaming) return

    let sessionId = activeId
    if (!sessionId) sessionId = (await newSession()).id

    setInput('')
    setStreaming(true)
    setStage({ label: 'Thinking' })

    const userTurn: ChatTurn = {
      id: `u-${Date.now()}`,
      role: 'user',
      content: message,
      citations: [],
    }
    const assistantId = `a-${Date.now()}`
    setTurns((prev) => [
      ...prev,
      userTurn,
      { id: assistantId, role: 'assistant', content: '', citations: [] },
    ])

    const patch = (fn: (turn: ChatTurn) => ChatTurn) =>
      setTurns((prev) => prev.map((t) => (t.id === assistantId ? fn(t) : t)))

    const handle = (event: StreamEvent) => {
      switch (event.type) {
        case 'stage':
          setStage({
            label: STAGE_LABELS[event.stage] ?? event.detail ?? event.stage,
            progress: event.progress,
          })
          if (event.stage === 'writing' && event.detail) setStage({ label: event.detail, progress: event.progress })
          break

        case 'token':
          patch((t) => ({ ...t, content: t.content + event.text }))
          break

        case 'citations':
          patch((t) => ({ ...t, citations: event.citations }))
          break

        case 'outline':
          // The essay plan arrives before five sections start streaming.
          // Showing it turns a multi-minute wait into visible structure —
          // the user can see what is coming rather than watching a counter.
          setStage({ label: `Planned "${event.title}" — ${event.sections.length} sections` })
          break

        case 'artifact': {
          const artifact = event as unknown as Artifact
          setArtifacts((prev) => [artifact, ...prev])
          setOpenArtifact(artifact)
          break
        }

        case 'artifact_saved': {
          // The persisted record carries an id, which the download link needs.
          const saved = event as unknown as Artifact
          setArtifacts((prev) => [saved, ...prev.filter((a) => a.title !== saved.title || a.id)])
          setOpenArtifact(saved)
          break
        }

        case 'done':
          patch((t) => ({
            ...t,
            grounded: (event as { grounded?: boolean }).grounded,
            provider: (event as { provider?: string }).provider ?? null,
            model: config?.active_model ?? null,
          }))
          break

        case 'saved':
          patch((t) => ({ ...t, latencyMs: event.latency_ms }))
          break

        case 'error':
          patch((t) => ({ ...t, error: { message: event.message, hint: event.hint } }))
          break
      }
    }

    const { done, abort } = api.streamMessage(sessionId, message, handle)
    abortRef.current = abort

    try {
      await done
    } catch (error) {
      if ((error as Error).name !== 'AbortError') {
        patch((t) => ({ ...t, error: { message: (error as Error).message } }))
      }
    } finally {
      setStreaming(false)
      setStage(null)
      abortRef.current = null
      void refreshStatus()
      api.listSessions().then(setSessions).catch(() => undefined)
    }
  }

  function stop() {
    abortRef.current?.()
    setStreaming(false)
    setStage(null)
  }

  // -- render ------------------------------------------------------------

  return (
    <div className="flex h-full bg-white text-ink-900">
      <div className="hidden md:flex">
        <Sidebar
          sessions={sessions}
          activeId={activeId}
          config={config}
          health={health}
          onNew={newSession}
          onSelect={selectSession}
          onDelete={removeSession}
        />
      </div>

      {/* Below md the sidebar becomes a drawer rather than disappearing. */}
      {sidebarOpen && (
        <div className="fixed inset-0 z-30 flex md:hidden">
          <div className="bg-white shadow-xl">
            <Sidebar
              sessions={sessions}
              activeId={activeId}
              config={config}
              health={health}
              onNew={async () => {
                await newSession()
                setSidebarOpen(false)
              }}
              onSelect={(id) => {
                void selectSession(id)
                setSidebarOpen(false)
              }}
              onDelete={removeSession}
            />
          </div>
          <button
            className="flex-1 bg-ink-950/40"
            onClick={() => setSidebarOpen(false)}
            aria-label="Close menu"
          />
        </div>
      )}

      <main className="flex min-w-0 flex-1 flex-col">
        <header className="flex shrink-0 items-center gap-3 border-b border-ink-300 px-4 py-2.5">
          <button
            onClick={() => setSidebarOpen(true)}
            className="rounded-md p-1.5 text-ink-700 hover:bg-ink-100 md:hidden"
            aria-label="Open menu"
          >
            <svg width="18" height="18" viewBox="0 0 16 16" aria-hidden="true">
              <path d="M2 4h12M2 8h12M2 12h12" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          </button>

          <h1 className="text-sm font-semibold">The Lenny Growth Assistant</h1>

          {config && (
            <span
              className="ml-auto hidden items-center gap-1.5 rounded-full border border-ink-300 px-2.5 py-1 font-mono text-[11px] text-ink-700 sm:inline-flex"
              title={`Agent runtime: ${config.agent_runtime} · embeddings: ${config.embed_model}`}
            >
              <span
                className={`h-1.5 w-1.5 rounded-full ${health?.status === 'ok' ? 'bg-grounded' : 'bg-warn'}`}
                aria-hidden="true"
              />
              {config.active_provider} · {config.active_model}
            </span>
          )}

          {artifacts.length > 0 && !openArtifact && (
            <button
              onClick={() => setOpenArtifact(artifacts[0])}
              className="rounded-md border border-ink-300 px-2.5 py-1.5 text-xs font-medium hover:bg-ink-100"
            >
              Artifacts ({artifacts.length})
            </button>
          )}
        </header>

        <div className="flex min-h-0 flex-1">
          <div className={`flex min-w-0 flex-col ${openArtifact ? 'hidden lg:flex lg:flex-1' : 'flex-1'}`}>
            <div className="min-h-0 flex-1">
              <ChatPane turns={turns} stage={stage} streaming={streaming} onSuggestion={send} />
            </div>

            <div className="shrink-0 border-t border-ink-300 bg-white px-4 py-3 sm:px-6">
              <form
                className="mx-auto flex max-w-3xl items-end gap-2"
                onSubmit={(event) => {
                  event.preventDefault()
                  void send(input)
                }}
              >
                <label htmlFor="composer" className="sr-only">
                  Ask a product or growth question
                </label>
                <textarea
                  id="composer"
                  ref={textareaRef}
                  rows={1}
                  value={input}
                  disabled={streaming}
                  placeholder="Ask about product, growth, pricing, positioning…"
                  onChange={(event) => {
                    setInput(event.target.value)
                    const el = event.target
                    el.style.height = 'auto'
                    el.style.height = `${Math.min(el.scrollHeight, 180)}px`
                  }}
                  onKeyDown={(event) => {
                    // Enter sends, Shift+Enter newlines — the convention users
                    // already have from every other chat product.
                    if (event.key === 'Enter' && !event.shiftKey) {
                      event.preventDefault()
                      void send(input)
                    }
                  }}
                  className="max-h-44 flex-1 resize-none rounded-xl border border-ink-300 px-3.5 py-2.5 text-sm outline-none placeholder:text-ink-500 focus:border-accent disabled:bg-ink-50"
                />
                {streaming ? (
                  <button
                    type="button"
                    onClick={stop}
                    className="shrink-0 rounded-xl border border-ink-300 px-4 py-2.5 text-sm font-medium hover:bg-ink-100"
                  >
                    Stop
                  </button>
                ) : (
                  <button
                    type="submit"
                    disabled={!input.trim()}
                    className="shrink-0 rounded-xl bg-ink-900 px-4 py-2.5 text-sm font-medium text-white disabled:opacity-40"
                  >
                    Send
                  </button>
                )}
              </form>
              <p className="mx-auto mt-1.5 max-w-3xl text-[11px] text-ink-500">
                Answers come only from ingested Lenny's Podcast transcripts. Local models are slow —
                a long essay can take several minutes.
              </p>
            </div>
          </div>

          {openArtifact && (
            <div className="min-w-0 flex-1 lg:max-w-[46%]">
              <ArtifactViewer artifact={openArtifact} onClose={() => setOpenArtifact(null)} />
            </div>
          )}
        </div>
      </main>
    </div>
  )
}
