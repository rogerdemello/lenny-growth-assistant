import { useMemo, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Artifact } from '../lib/api'
import { api } from '../lib/api'
import { CSP_TEXT } from '../lib/constants'

/**
 * The artifact viewer — the client half of the two-layer isolation strategy.
 *
 * HTML artifacts render inside an iframe whose `sandbox` attribute is the empty
 * string. That is the maximally restrictive value: no scripts, no same-origin,
 * no forms, no popups, no top-level navigation. Combined with a
 * `default-src 'none'` CSP injected into the document, an artifact cannot run
 * code, reach the network, read cookies, or touch this page — even if the
 * server-side sanitizer missed something.
 *
 * Markdown renders through react-markdown *without* `rehype-raw`, so embedded
 * HTML is inert by construction rather than by filtering.
 */
export function ArtifactViewer({
  artifact,
  onClose,
}: {
  artifact: Artifact
  onClose: () => void
}) {
  const [tab, setTab] = useState<'rendered' | 'source'>('rendered')
  const [copied, setCopied] = useState(false)

  const removed = artifact.sanitizer_report?.removed ?? []

  // The CSP is injected into the artifact document itself, so the restriction
  // travels with the content instead of depending on the parent page.
  const srcDoc = useMemo(() => {
    if (artifact.kind !== 'html') return ''
    return `<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="${CSP_TEXT}">
<style>body{margin:0;padding:24px;font-family:ui-sans-serif,system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;line-height:1.6;color:#1a1a1a;background:#fff}</style>
</head><body>${artifact.sanitized_content}</body></html>`
  }, [artifact.kind, artifact.sanitized_content])

  async function copy() {
    await navigator.clipboard.writeText(artifact.raw_content)
    setCopied(true)
    setTimeout(() => setCopied(false), 1600)
  }

  return (
    <section
      className="flex h-full flex-col border-l border-ink-300 bg-white"
      aria-label="Artifact viewer"
    >
      <header className="flex shrink-0 items-center gap-2 border-b border-ink-300 px-4 py-2.5">
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-sm font-semibold text-ink-900" title={artifact.title}>
            {artifact.title}
          </h2>
          <p className="text-xs text-ink-500">
            {artifact.kind === 'html' ? 'HTML / CSS' : 'Markdown'}
            {artifact.version && artifact.version > 1 ? ` · v${artifact.version}` : ''}
          </p>
        </div>

        <div
          className="flex rounded-md border border-ink-300 p-0.5"
          role="tablist"
          aria-label="Artifact view mode"
        >
          {(['rendered', 'source'] as const).map((mode) => (
            <button
              key={mode}
              role="tab"
              aria-selected={tab === mode}
              onClick={() => setTab(mode)}
              className={`rounded px-2.5 py-1 text-xs font-medium capitalize transition-colors ${
                tab === mode ? 'bg-ink-900 text-white' : 'text-ink-700 hover:bg-ink-100'
              }`}
            >
              {mode}
            </button>
          ))}
        </div>

        <button
          onClick={copy}
          className="rounded-md border border-ink-300 px-2.5 py-1.5 text-xs font-medium text-ink-700 hover:bg-ink-100"
          aria-label="Copy artifact source to clipboard"
        >
          {copied ? 'Copied' : 'Copy'}
        </button>

        {artifact.id && (
          <a
            href={api.artifactDownloadUrl(artifact.id)}
            download
            className="rounded-md border border-ink-300 px-2.5 py-1.5 text-xs font-medium text-ink-700 hover:bg-ink-100"
          >
            Download
          </a>
        )}

        <button
          onClick={onClose}
          className="rounded-md p-1.5 text-ink-500 hover:bg-ink-100 hover:text-ink-900"
          aria-label="Close artifact viewer"
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
          </svg>
        </button>
      </header>

      {artifact.validation && <ValidationBar validation={artifact.validation} />}

      {removed.length > 0 && (
        <div
          className="flex shrink-0 items-start gap-2 border-b border-warn/30 bg-warn/10 px-4 py-2 text-xs text-ink-800"
          role="status"
        >
          <svg width="14" height="14" viewBox="0 0 16 16" className="mt-0.5 shrink-0" aria-hidden="true">
            <path
              d="M8 1.5l6.5 12h-13z M8 6.5v3.5 M8 11.8v.7"
              stroke="currentColor"
              strokeWidth="1.3"
              fill="none"
              strokeLinecap="round"
            />
          </svg>
          <span>
            <strong className="font-semibold">Sanitized before rendering.</strong> Removed:{' '}
            {removed.join(', ')}. The source tab shows the model's original output.
          </span>
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-auto">
        {tab === 'source' ? (
          <pre className="m-0 h-full overflow-auto bg-ink-950 p-4 text-xs leading-relaxed text-ink-100">
            <code>{artifact.raw_content}</code>
          </pre>
        ) : artifact.kind === 'html' ? (
          <iframe
            // Empty sandbox = deny everything. Scripts are already stripped
            // server-side; this makes a sanitizer bypass unexploitable too.
            sandbox=""
            srcDoc={srcDoc}
            title={`Rendered artifact: ${artifact.title}`}
            className="h-full w-full border-0 bg-white"
          />
        ) : (
          <div className="prose-chat px-6 py-5 text-[0.9rem] text-ink-900">
            <Markdown remarkPlugins={[remarkGfm]}>{artifact.sanitized_content}</Markdown>
          </div>
        )}
      </div>
    </section>
  )
}

function ValidationBar({ validation }: { validation: NonNullable<Artifact['validation']> }) {
  const [open, setOpen] = useState(false)

  return (
    <div className="shrink-0 border-b border-ink-300 bg-ink-50">
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-4 py-2 text-left text-xs hover:bg-ink-100"
      >
        <span
          className={`inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[10px] font-bold text-white ${
            validation.passed ? 'bg-grounded' : 'bg-warn'
          }`}
          aria-hidden="true"
        >
          {validation.passed ? '✓' : '!'}
        </span>
        <span className="font-medium text-ink-900">Ship 30 spec: {validation.score} checks</span>
        <span className="text-ink-500">
          {validation.word_count} words · {validation.section_count} sections ·{' '}
          {validation.citation_count} citations
        </span>
        <span className="ml-auto text-ink-500">{open ? 'Hide' : 'Details'}</span>
      </button>

      {open && (
        <ul className="space-y-1 border-t border-ink-300 px-4 py-2.5 text-xs">
          {validation.checks.map((check) => (
            <li key={check.name} className="flex gap-2">
              <span
                className={check.passed ? 'text-grounded' : 'text-warn'}
                aria-label={check.passed ? 'passed' : 'failed'}
              >
                {check.passed ? '✓' : '✗'}
              </span>
              <span className="font-medium text-ink-800">{check.name}</span>
              <span className="text-ink-500">— {check.detail}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
