import { useState } from 'react'
import type { Citation } from '../lib/api'

/**
 * Citations are the product's trust surface.
 *
 * A chip shows the guest and timestamp; expanding it reveals the transcript
 * passage the answer was built from, and the link opens YouTube at that exact
 * second. The point is that a user can verify a claim in two clicks without
 * leaving the flow — an assistant that cites but cannot be checked is just a
 * more confident hallucination.
 */
export function Citations({ citations }: { citations: Citation[] }) {
  const [expanded, setExpanded] = useState<string | null>(null)

  if (citations.length === 0) return null

  return (
    <div className="mt-3">
      <div className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-grounded">
        <svg width="13" height="13" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path
            d="M6.5 8.5l1.5 1.5 3-3.5M8 1.5l5.5 2.5v4c0 3-2.3 5.6-5.5 6.5-3.2-.9-5.5-3.5-5.5-6.5v-4z"
            stroke="currentColor"
            strokeWidth="1.3"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
        Grounded in {citations.length} passage{citations.length === 1 ? '' : 's'}
      </div>

      <ul className="flex flex-wrap gap-1.5">
        {citations.map((citation, index) => {
          const key = citation.chunk_id
          const isOpen = expanded === key
          return (
            <li key={key} className="w-full">
              <button
                onClick={() => setExpanded(isOpen ? null : key)}
                aria-expanded={isOpen}
                className="flex w-full items-center gap-2 rounded-md border border-ink-300 bg-white px-2.5 py-1.5 text-left text-xs hover:border-accent hover:bg-accent-soft/40"
              >
                <span className="shrink-0 rounded bg-ink-900 px-1.5 py-0.5 font-mono text-[10px] font-semibold text-white">
                  S{index + 1}
                </span>
                <span className="truncate font-medium text-ink-900">{citation.guest}</span>
                <span className="truncate text-ink-500">{citation.episode_title}</span>
                <span className="ml-auto shrink-0 font-mono text-[10px] text-ink-500">
                  {citation.timestamp}
                </span>
              </button>

              {isOpen && (
                <div className="mt-1 rounded-md border border-ink-300 bg-ink-50 p-3 text-xs">
                  <p className="mb-2 max-h-52 overflow-y-auto whitespace-pre-wrap leading-relaxed text-ink-800">
                    {citation.text}
                  </p>
                  <div className="flex items-center justify-between gap-2 border-t border-ink-300 pt-2">
                    <span className="text-[11px] text-ink-500">
                      {citation.speaker} · similarity {citation.score.toFixed(3)}
                    </span>
                    {citation.youtube_url && (
                      <a
                        href={citation.youtube_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="font-medium text-accent hover:underline"
                      >
                        Watch at {citation.timestamp} →
                      </a>
                    )}
                  </div>
                </div>
              )}
            </li>
          )
        })}
      </ul>
    </div>
  )
}
