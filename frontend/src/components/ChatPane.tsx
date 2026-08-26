import { useEffect, useRef } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Citation } from '../lib/api'
import { Citations } from './Citations'

export type ChatTurn = {
  id: string
  role: 'user' | 'assistant'
  content: string
  citations: Citation[]
  provider?: string | null
  model?: string | null
  latencyMs?: number | null
  grounded?: boolean
  error?: { message: string; hint?: string }
}

export type Stage = {
  label: string
  progress?: { current: number; total: number }
}

const SUGGESTIONS = [
  'How should I think about pricing a B2B SaaS product?',
  'What actually drives retention in the early days?',
  'How do I know if I have product-market fit?',
  'Write a Ship 30 essay about growth loops',
]

export function ChatPane({
  turns,
  stage,
  streaming,
  onSuggestion,
}: {
  turns: ChatTurn[]
  stage: Stage | null
  streaming: boolean
  onSuggestion: (text: string) => void
}) {
  const endRef = useRef<HTMLDivElement>(null)
  const pinnedRef = useRef(true)
  const scrollRef = useRef<HTMLDivElement>(null)

  // Follow the stream, but stop fighting the user the moment they scroll up to
  // read something. Yanking the viewport back down mid-read is the most
  // annoying thing a streaming chat UI can do.
  useEffect(() => {
    if (pinnedRef.current) endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [turns, stage])

  function handleScroll() {
    const el = scrollRef.current
    if (!el) return
    pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120
  }

  if (turns.length === 0 && !streaming) {
    return (
      <div className="flex h-full items-center justify-center overflow-y-auto p-6">
        <div className="max-w-lg text-center">
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-xl bg-ink-900 text-lg font-bold text-white">
            L
          </div>
          <h2 className="mb-2 text-xl font-semibold text-ink-900">The Lenny Growth Assistant</h2>
          <p className="mb-6 text-sm leading-relaxed text-ink-700">
            Ask a product or growth question. Every answer is built from Lenny's Podcast
            transcripts and cites the episode and timestamp it came from — so you can check it.
          </p>
          <ul className="space-y-2 text-left">
            {SUGGESTIONS.map((suggestion) => (
              <li key={suggestion}>
                <button
                  onClick={() => onSuggestion(suggestion)}
                  className="w-full rounded-lg border border-ink-300 bg-white px-3.5 py-2.5 text-left text-sm text-ink-800 transition-colors hover:border-accent hover:bg-accent-soft/40"
                >
                  {suggestion}
                </button>
              </li>
            ))}
          </ul>
        </div>
      </div>
    )
  }

  return (
    <div ref={scrollRef} onScroll={handleScroll} className="h-full overflow-y-auto px-4 py-6 sm:px-6">
      <div className="mx-auto flex max-w-3xl flex-col gap-6">
        {turns.map((turn) =>
          turn.role === 'user' ? (
            <div key={turn.id} className="flex justify-end">
              <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-ink-900 px-4 py-2.5 text-sm text-white">
                {turn.content}
              </div>
            </div>
          ) : (
            <article key={turn.id} className="flex flex-col">
              {turn.error ? (
                <div className="rounded-lg border border-danger/40 bg-danger/5 p-3.5 text-sm">
                  <p className="font-medium text-danger">{turn.error.message}</p>
                  {turn.error.hint && <p className="mt-1 text-xs text-ink-700">{turn.error.hint}</p>}
                </div>
              ) : (
                <>
                  {turn.grounded === false && (
                    <p className="mb-2 inline-flex w-fit items-center gap-1.5 rounded-md bg-warn/12 px-2 py-1 text-xs font-medium text-ink-800">
                      <span aria-hidden="true">⚠</span> Not covered by the transcript archive
                    </p>
                  )}
                  <div className="prose-chat text-sm text-ink-900">
                    <Markdown remarkPlugins={[remarkGfm]}>{turn.content}</Markdown>
                  </div>
                  <Citations citations={turn.citations} />
                  {(turn.provider || turn.latencyMs) && (
                    <p className="mt-2 font-mono text-[10px] text-ink-500">
                      {turn.provider}
                      {turn.model ? ` · ${turn.model}` : ''}
                      {turn.latencyMs ? ` · ${(turn.latencyMs / 1000).toFixed(1)}s` : ''}
                    </p>
                  )}
                </>
              )}
            </article>
          ),
        )}

        {stage && (
          <div className="flex items-center gap-2.5 text-sm text-ink-700" role="status" aria-live="polite">
            <span className="flex gap-1" aria-hidden="true">
              <span className="dot-pulse h-1.5 w-1.5 rounded-full bg-accent" />
              <span className="dot-pulse h-1.5 w-1.5 rounded-full bg-accent [animation-delay:0.2s]" />
              <span className="dot-pulse h-1.5 w-1.5 rounded-full bg-accent [animation-delay:0.4s]" />
            </span>
            <span>{stage.label}</span>
            {stage.progress && (
              <span className="font-mono text-xs text-ink-500">
                {stage.progress.current}/{stage.progress.total}
              </span>
            )}
          </div>
        )}

        <div ref={endRef} />
      </div>
    </div>
  )
}
