import type { AppConfig, Health, Session } from '../lib/api'

export function Sidebar({
  sessions,
  activeId,
  config,
  health,
  onNew,
  onSelect,
  onDelete,
}: {
  sessions: Session[]
  activeId: string | null
  config: AppConfig | null
  health: Health | null
  onNew: () => void
  onSelect: (id: string) => void
  onDelete: (id: string) => void
}) {
  return (
    <nav
      className="flex h-full w-60 shrink-0 flex-col border-r border-ink-300 bg-ink-50"
      aria-label="Chat sessions"
    >
      <div className="p-3">
        <button
          onClick={onNew}
          className="w-full rounded-lg bg-ink-900 px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-ink-800"
        >
          + New chat
        </button>
      </div>

      <ul className="min-h-0 flex-1 space-y-0.5 overflow-y-auto px-2">
        {sessions.map((session) => (
          <li key={session.id} className="group relative">
            <button
              onClick={() => onSelect(session.id)}
              aria-current={session.id === activeId ? 'true' : undefined}
              className={`w-full truncate rounded-md py-2 pl-2.5 pr-8 text-left text-sm transition-colors ${
                session.id === activeId
                  ? 'bg-white font-medium text-ink-900 shadow-sm'
                  : 'text-ink-700 hover:bg-white/70'
              }`}
              title={session.title}
            >
              {session.title}
            </button>
            <button
              onClick={() => onDelete(session.id)}
              aria-label={`Delete chat: ${session.title}`}
              className="absolute right-1 top-1.5 rounded p-1 text-ink-500 opacity-0 transition-opacity hover:bg-ink-100 hover:text-danger focus-visible:opacity-100 group-hover:opacity-100"
            >
              <svg width="13" height="13" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                <path
                  d="M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.5 8.5h6l.5-8.5"
                  stroke="currentColor"
                  strokeWidth="1.3"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </button>
          </li>
        ))}
        {sessions.length === 0 && (
          <li className="px-2.5 py-3 text-xs text-ink-500">No chats yet.</li>
        )}
      </ul>

      <SystemPanel config={config} health={health} />
    </nav>
  )
}

/**
 * The provider badge and system status.
 *
 * The brief asks that the selected provider be visible in the UI. This goes
 * further and shows the whole picture — runtime, chat model, embedding model,
 * corpus size, and which components are healthy — because the first question
 * anyone asks about a local-model demo is "what is it actually running?"
 */
function SystemPanel({ config, health }: { config: AppConfig | null; health: Health | null }) {
  const degraded = health?.status === 'degraded'
  const failing = Object.entries(health?.components ?? {})
    .filter(([, component]) => !component.ok)
    .map(([name]) => name)

  return (
    <div className="shrink-0 space-y-2 border-t border-ink-300 p-3 text-xs">
      <div className="flex items-center gap-1.5">
        <span
          className={`h-2 w-2 shrink-0 rounded-full ${degraded ? 'bg-warn' : 'bg-grounded'}`}
          aria-hidden="true"
        />
        <span className="font-medium text-ink-900">
          {degraded ? 'Degraded' : 'All systems ok'}
        </span>
      </div>

      {degraded && failing.length > 0 && (
        <p className="text-[11px] leading-snug text-ink-700">Unavailable: {failing.join(', ')}</p>
      )}

      {config && (
        <dl className="space-y-1 text-[11px] text-ink-700">
          <Row label="Provider" value={config.active_provider} strong />
          <Row label="Model" value={config.active_model ?? '—'} strong />
          <Row label="Runtime" value={config.agent_runtime} />
          <Row label="Embeddings" value={config.embed_model} />
          {config.fallback_provider && <Row label="Fallback" value={config.fallback_provider} />}
          {config.essay_provider !== config.active_provider && (
            <Row label="Essays" value={config.essay_provider} />
          )}
          <Row
            label="Corpus"
            value={
              config.corpus?.error
                ? 'unavailable'
                : `${config.corpus?.episodes ?? 0} eps · ${config.corpus?.embedded_chunks ?? 0} chunks`
            }
          />
        </dl>
      )}
    </div>
  )
}

function Row({ label, value, strong }: { label: string; value: string; strong?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <dt className="shrink-0 text-ink-500">{label}</dt>
      <dd
        className={`truncate text-right font-mono ${strong ? 'font-semibold text-ink-900' : ''}`}
        title={value}
      >
        {value}
      </dd>
    </div>
  )
}
