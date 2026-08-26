# PRD — The Lenny Growth Assistant

**Status:** v1 shipped · **Owner:** Forward Deployed Engineer · **Date:** 26 Aug 2026

---

## 1. Forward deployment brief

### The engagement

A product and growth team asked for an internal assistant built on Lenny's Podcast transcripts. The brief was one paragraph. Everything below the first line is a decision I made, and I have tried to be explicit about which is which.

### Who this is for

**Primary user: a product manager or growth lead at a Series A–C company**, two to eight years in, who already listens to Lenny's Podcast and treats it as a professional reference rather than entertainment.

They are not the person who wants a summary. They are the person who remembers that *someone* said something useful about pricing tiers eighteen months ago, cannot remember who, and needs it in the next forty minutes because they are writing a strategy doc.

**Secondary user: the content or founder-marketing person** on the same team who needs to turn that internal knowledge into something publishable.

### The job to be done

> "When I'm about to make a product or growth decision, I want to know what operators who have already made it actually did — so I can borrow their reasoning instead of guessing, and defend the decision to my team with a source."

The emphasis is on **defend**. A PM who cannot say where an idea came from is making an assertion. A PM who can say "Madhavan Ramanujam's argument on Lenny's Podcast, and here's the ninety seconds where he says it" is making a case.

### The pain we remove

Today the alternative is one of three bad options:

1. **Search YouTube and scrub.** The information exists, but finding one exchange inside a ninety-minute episode costs twenty minutes.
2. **Ask a general LLM.** It answers instantly, confidently, and with no way to tell which parts came from a real practitioner and which it composed. For a decision you have to defend, that is worse than nothing.
3. **Give up and go with instinct.** Which is what actually happens most of the time.

The assistant collapses this to one question and one answer, with the receipts attached.

**The insight the product is built around:** for this user, *citation is not a compliance feature — it is the product.* An uncited answer has failed even if it is correct, because it cannot be used for the thing they need it for.

### Success metrics

**Primary — Grounded Answer Rate.**

> Share of substantive answers that cite at least two distinct transcript passages **and** are not followed within two turns by a rephrase of the same question.

The second clause matters. Citation count alone is trivially gamed by retrieving more. A rephrase within two turns is the strongest available signal that the user did not get what they asked for.

*Target: ≥ 70% in week one.*

**Secondary — Citation Inspection Rate.**

> Share of assistant answers where the user expands at least one citation or follows a YouTube deep link.

This measures whether the trust mechanism is actually load-bearing or merely decorative. If nobody ever opens a citation, we have built a chatbot with footnotes and the whole grounding architecture is unjustified.

*Target: ≥ 25% of answers.*

**Operational — Honest Refusal Rate.**

> Share of out-of-corpus questions that produce an explicit refusal rather than an answer.

Measured against a fixed held-out set of twenty questions the corpus provably does not cover. This is the metric that protects against the failure mode that would destroy trust fastest.

*Target: 100%. Anything less is a defect, not a miss.*

**Counter-metric — Time to First Token.**

Watched, not optimised. If grounding quality is bought with a ninety-second wait, the product loses to a general LLM regardless of correctness.

*Threshold: p50 under 15 s locally, under 3 s on a cloud provider.*

### Assumptions

Recorded because the brief was incomplete and these shaped the build:

| # | Assumption | If wrong |
|---|---|---|
| A1 | Users are internal and trusted. No auth, no per-user isolation beyond session scoping. | Add auth; sessions already carry `user_id` and metadata, so the schema does not change. |
| A2 | The corpus is stable reference material, not breaking news. Refresh weekly at most. | Ingestion is already incremental and hash-based; run it on a schedule. |
| A3 | Users want the operator's reasoning, not a neutral summary. Answers name people and quote specifics. | Soften the prompt in `agent/prompts.py`. |
| A4 | A bounded, high-quality corpus beats an exhaustive one. 40 episodes covering the growth/pricing/PM canon. | `--all` ingests all 303; costs 1–2 h on CPU. |
| A5 | Artifacts are documents, not applications. No interactive JavaScript. | Would require a fundamentally different, and much weaker, security posture. |
| A6 | The evaluator runs this on a laptop without a GPU. | Every latency decision is tuned for that; a cloud provider only makes it faster. |
| A7 | "Citations" means "verifiable in one click", so timestamp-level deep links are required, not episode-level. | Simplifies chunking considerably, but guts the core value. |

### Scope

**In scope, and built:**

- Grounded conversational Q&A with per-passage citations and YouTube deep links
- Explicit refusal when the corpus does not support an answer
- Follow-up handling via history-aware query condensation
- Multi-session chat with independent context, persisted in PostgreSQL
- Ship 30 for 30 essay skill with a programmatic validator
- Markdown and HTML artifact generation with an in-app sandboxed viewer
- Provider switching by environment variable, with an implemented fallback chain
- Two agent runtimes over one shared tool registry and one shared skill definition
- Structured logging, health reporting, and graceful degradation on every external dependency
- Incremental, resumable, traceable ingestion

**Deliberately excluded:**

| Excluded | Why |
|---|---|
| Authentication and multi-tenancy | Internal tool (A1). Building auth would have cost the artifact viewer. |
| RRF hybrid retrieval | Tuning fusion needs an evaluation set. An untuned hybrid can rank *worse* than plain vector search, and shipping a knob nobody calibrated is worse than shipping without it. The column and index are in place. |
| Reranking | Same reason, plus a cross-encoder on CPU would roughly double latency. |
| Streaming the Claude Agent SDK path in the demo | No Anthropic key was available. Building it and saying so beats faking it. |
| Interactive HTML artifacts | Directly opposed to A5 and to the security posture. |
| Conversation summarisation for long sessions | Last four turns plus query condensation covers the observed need. Revisit past ~20 turns. |
| Episode-level filters in the UI | No evidence users want them yet. `INGESTED.md` covers "what do you know?" |

### Risks and trade-offs

| Risk | Severity | What we did |
|---|---|---|
| **Hallucination** — answering from model knowledge while appearing grounded | Critical | Similarity floor withholds sources entirely rather than passing weak ones; the refusal prompt has no sources to work from. Enforced in code, tested, not left to prompt compliance. |
| **Prompt injection via transcript text** | High | Transcripts enter as data in a delimited block, never as instructions. Artifacts are sanitized and sandboxed regardless of origin. Tools are a fixed registry — a compromised generation cannot invent a tool. |
| **Unsafe artifact rendering** | High | Two independent layers: nh3 allowlist server-side, and an iframe with an empty `sandbox` plus a no-network CSP. Neither is load-bearing alone. 22 payloads regression-tested. |
| **Local model quality** | High | Accepted, and made visible. The router does not trust the model to choose tools; retrieval is mandatory; the essay validator reports where the output missed the spec instead of hiding it. |
| **Latency on CPU** | High | Measured, not guessed: ~7–9 tok/s, ~11 s to first token at 4 passages, ~22 s at 8. Drove `PROMPT_TOP_K < RETRIEVAL_TOP_K`, section-by-section essay streaming, and skipping condensation on the first turn. |
| **Cost, if run on cloud** | Medium | `PROMPT_TOP_K` bounds prompt size. Essays are the expensive path and are separately configurable. |
| **Data leakage** | Medium | No secrets in the repo; `.env` gitignored; `/api/config` returns configured-or-not, never key values, and that is a test. Artifacts cannot make outbound requests, so generated content cannot beacon out. |
| **Corpus staleness** | Low | Hash-based incremental refresh; `INGESTED.md` records the source commit. |
| **Third-party content** | Low | Transcripts fetched at ingestion, never vendored; attribution in the README. |

---

## 2. Key flows

### Flow 1 — Grounded question

1. User asks: *"How should I think about pricing for a B2B SaaS product?"*
2. Router classifies `chat`. No history, so condensation is skipped.
3. Retrieval embeds the query, searches pgvector, applies the score floor.
4. Top 8 passages become citation chips; top 4 enter the prompt.
5. Answer streams with `[S1]`-style markers.
6. Citations are filtered to those the answer actually referenced, then persisted.

**Acceptance:** ≥2 citations; every chip expands to a passage and a working timestamped link; the answer contains no claim absent from the passages.

### Flow 2 — Follow-up

1. User asks: *"What about for PLG?"*
2. Condensation folds the last turns into *"product-led growth pricing strategy"*.
3. Retrieval runs on the rewritten query; the UI shows what it searched for.

**Acceptance:** retrieval returns PLG-relevant passages, not a repeat of the previous answer's.

### Flow 3 — Out-of-corpus question

1. User asks: *"What's the best Kubernetes ingress controller?"*
2. Best similarity falls below the floor; retrieval returns nothing.
3. The refusal prompt runs with no sources available.

**Acceptance:** the assistant states the archive does not cover this, names what it does cover, and offers no answer. Zero citations shown.

### Flow 4 — Ship 30 essay

1. User asks: *"Turn that into a Ship 30 essay."*
2. Router classifies `essay`; topic resolves from the previous turn.
3. Broader retrieval (10 passages), then a JSON outline, then five sections generated one at a time — each sent the outline and sources, never the accumulated draft, so cost per section stays flat.
4. The validator scores the result; the essay opens as a Markdown artifact with its scorecard.

**Acceptance:** 1,150–1,350 words, 5–6 sections, ≥3 citations, a takeaway section, and a validator report visible whether it passed or failed.

### Flow 5 — HTML artifact

1. User asks: *"Make me an HTML one-pager of that."*
2. `create_artifact` fires; content is sanitized server-side; what was removed is recorded.
3. It renders in an iframe with an empty sandbox and a no-network CSP.

**Acceptance:** renders styled; the source tab shows the model's original output; injected `<script>` is visibly absent and named in the sanitizer banner; download works.

### Flow 6 — Provider degradation

1. Ollama is stopped mid-session.
2. `/health` reports `degraded` and names the component; the sidebar turns amber within 20 s.
3. A new question either fails over (if configured) or returns a clear provider-unavailable error with a fix hint.

**Acceptance:** no stack trace reaches the user; the failure names the component and what to do about it.

---

## 3. Acceptance criteria

- [x] FastAPI backend; sessions with independent context; PostgreSQL persistence of conversations, ids, timestamps and user metadata
- [x] Provider switchable by config alone, visible in the UI, with documented fallback
- [x] Local Ollama is the default and needs no key
- [x] Transcripts ingested with documented loading, chunking, selection, indexing, refresh and traceability
- [x] Answers cite their source; uncovered questions are refused
- [x] Ship 30 skill encoded as a skill file plus validator, not an ad-hoc prompt
- [x] Markdown and HTML artifacts render in an in-app viewer with a documented isolation strategy
- [x] One-command startup, `.env.example`, no committed secrets
- [x] Structured logs with request ids and per-stage timings; health endpoint that degrades rather than failing
- [x] Graceful handling of missing keys, Ollama down, timeouts, empty retrieval, DB failure
- [x] Automated tests for API, retrieval, routing and persistence behaviour, plus a manual UI plan
- [x] Agent layer built on the Claude Agent SDK — implemented, with its unrun status documented rather than obscured

---

## 4. What I would do next

In priority order, with reasoning:

1. **Build a 50-question evaluation set.** Everything else is guesswork without it. It unblocks hybrid retrieval, reranking, and any claim that a change improved quality rather than merely changed it.
2. **Instrument the success metrics.** Grounded Answer Rate and Citation Inspection Rate are defined but not yet measured; the events exist in the logs.
3. **Then** RRF hybrid retrieval, tuned against (1).
4. **Ingest the full 303 episodes** on a machine with a GPU.
5. **Answer-level caching.** Repeated questions are common in a team tool and the second answer is free.
6. **Auth**, if this leaves the internal context.
