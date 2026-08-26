# Architecture

## Contents

- [System shape](#system-shape)
- [Component boundaries](#component-boundaries)
- [Database schema](#database-schema)
- [API endpoints](#api-endpoints)
- [Ingestion flow](#ingestion-flow)
- [Retrieval flow](#retrieval-flow)
- [Agent runtimes](#agent-runtimes)
- [The model toggle](#the-model-toggle)
- [Security](#security)
- [Observability](#observability)
- [Resilience](#resilience)
- [Deployment topology](#deployment-topology)

---

## System shape

```
                       ┌─────────────────────────────────┐
   Browser  ────────►  │  React SPA (Vite + TS + Tailwind)│
                       │  chat · citations · artifacts    │
                       └───────────────┬─────────────────┘
                                       │ SSE + JSON
                       ┌───────────────▼─────────────────┐
                       │  FastAPI                         │
                       │  ┌────────────────────────────┐  │
                       │  │ api/     routers, contracts│  │
                       │  ├────────────────────────────┤  │
                       │  │ agent/   runtimes + tools  │  │
                       │  │ skills/  SKILL.md + valid. │  │
                       │  ├────────────────────────────┤  │
                       │  │ rag/     retrieval         │  │
                       │  │ providers/ model adapters  │  │
                       │  └────────────────────────────┘  │
                       └───┬──────────────────┬───────────┘
                           │                  │
              ┌────────────▼──────┐   ┌───────▼──────────────┐
              │ PostgreSQL        │   │ Model provider       │
              │ + pgvector        │   │ Ollama · Azure · …   │
              └───────────────────┘   └──────────────────────┘
```

Three processes in the verified local setup: the API, the Vite dev server, and Ollama. PostgreSQL is remote (Supabase).

---

## Component boundaries

Each layer depends only on the one below it. The rule that keeps this honest: **nothing above `providers/` knows which model is running, and nothing above `rag/` knows how retrieval works.**

| Layer | Owns | May import | Must not |
|---|---|---|---|
| `api/` | HTTP contracts, validation, SSE framing, persistence orchestration | everything below | contain prompt text or SQL |
| `agent/` | Turn orchestration, routing, the tool registry | `skills`, `rag`, `providers`, `core` | know about HTTP or FastAPI |
| `skills/` | Skill definitions and their validators | `rag`, `providers`, `core` | know about HTTP |
| `rag/` | Retrieval, scoring, citation shaping | `db`, `providers`, `core` | know about prompts or agents |
| `providers/` | Model adapters, fallback chain | `core` | know about retrieval or agents |
| `db/` | Pool, schema, all SQL | `core` | know about models |
| `core/` | Config, logging, errors | — | import anything else |

Two consequences worth naming:

- **All SQL lives in `db/repository.py` and `db/migrations/`.** Session isolation is therefore auditable: every query that touches conversation content takes a `session_id` and filters on it. There is no path that can read across sessions by accident.
- **All prompt text lives in `agent/prompts.py` and `.claude/skills/`.** Changing how the assistant behaves does not mean hunting through request handlers.

---

## Database schema

PostgreSQL 14+ with `pgvector`. Migrations are numbered `.sql` files in `backend/app/db/migrations/`, applied forward-only by `python -m app.db.migrate`, which records what it has applied in `schema_migrations`.

No ORM. The only non-trivial thing this layer does is talk to pgvector, and hand-written SQL does that more clearly than a mapper would — while keeping the schema readable by a client engineer who has not learned our tooling.

### Conversation state

**`sessions`**

| Column | Type | Notes |
|---|---|---|
| `id` | uuid pk | |
| `title` | text | Auto-set from the first user message |
| `user_id` | text | Anonymous but stable; no auth in scope |
| `client_metadata` | jsonb | User agent, locale, timezone, viewport |
| `provider`, `model` | text | Captured at creation, so a conversation records what answered it even after config changes |
| `created_at`, `updated_at` | timestamptz | |

**`messages`**

| Column | Type | Notes |
|---|---|---|
| `id` | uuid pk | |
| `session_id` | uuid fk → sessions, cascade | |
| `role` | text | check: user / assistant / system / tool |
| `content` | text | |
| `tool_calls` | jsonb | Which tools ran, with arguments |
| `citations` | jsonb | Resolved passages: guest, episode, timestamp, deep link |
| `provider`, `model` | text | **Differs from the session's when fallback fired** |
| `latency_ms`, `token_usage` | int / jsonb | |

**`artifacts`**

| Column | Type | Notes |
|---|---|---|
| `kind` | text | check: markdown / html |
| `raw_content` | text | Exactly what the model produced |
| `sanitized_content` | text | The only thing ever rendered |
| `sanitizer_report` | jsonb | What was removed — surfaced in the UI |
| `version` | int | Increments per `(session_id, title)` |

Storing both forms is what makes the sanitizer auditable: a reviewer can diff what the model wrote against what we serve, via `GET /api/artifacts/{id}/download?raw=true`.

### Knowledge base

**`episodes`** — one row per ingested transcript: `slug` (unique), `guest`, `title`, `youtube_url`, `video_id`, `publish_date`, `duration_seconds`, `view_count`, `keywords[]`, `content_hash`, `source_sha`, `ingested_at`.

`content_hash` drives incremental refresh. `source_sha` is the upstream commit, so every citation traces to an exact source revision.

**`chunks`**

| Column | Type | Notes |
|---|---|---|
| `episode_id` | uuid fk, cascade | A changed transcript invalidates every chunk derived from it |
| `ord` | int | Unique with `episode_id` |
| `speaker` | text | Who the citation names |
| `start_seconds`, `end_seconds` | int | Drives the YouTube deep link |
| `text`, `token_count` | text / int | |
| `embedding` | `vector(EMBED_DIM)` | Dimension substituted at migration time — it is a property of the model, not the schema |
| `tsv` | tsvector, generated | Lexical index |

Indexes: `ivfflat (embedding vector_cosine_ops) lists=100`, `GIN (tsv)`.

IVFFlat rather than HNSW: it builds in seconds at this corpus size, and Supabase's free tier has limited memory for HNSW graph construction. At six figures of chunks, HNSW would be the right call.

**`ingest_runs`** — one row per run: status, source SHA, embed model, counts, error, timings. This is what `GET /api/ingest/status` reports.

---

## API endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Per-component health. Never 500s — see [Resilience](#resilience) |
| `GET` | `/api/config` | Active provider, model, runtime, fallback, corpus stats, retrieval settings |
| `POST` | `/api/sessions` | Create a session; captures client metadata |
| `GET` | `/api/sessions` | List, newest first |
| `GET` | `/api/sessions/{id}` | Session with messages and artifacts |
| `DELETE` | `/api/sessions/{id}` | Cascades to messages and artifacts |
| `GET` | `/api/sessions/{id}/messages` | Messages only |
| `POST` | `/api/sessions/{id}/messages` | **The chat turn.** SSE by default, JSON with `stream: false` |
| `GET` | `/api/artifacts/{id}` | Artifact JSON |
| `GET` | `/api/artifacts/{id}/render` | Standalone HTML with restrictive headers |
| `GET` | `/api/artifacts/{id}/download` | File export; `?raw=true` for pre-sanitization |
| `POST` | `/api/ingest` | Trigger re-index; token-guarded, disabled by default |
| `GET` | `/api/ingest/status` | Last run plus corpus stats |

### Error contract

Everything that fails leaves through one shape:

```json
{
  "error": {
    "code": "provider_unavailable",
    "message": "Could not reach ollama at http://localhost:11434/v1",
    "request_id": "a3f9c21b8e04",
    "hint": "Ollama is not reachable. Start it with `ollama serve`, or set LLM_PROVIDER to a cloud provider."
  }
}
```

`hint` is deliberately actionable. The client engineer who inherits this should get a fix, not a diagnosis.

### SSE event stream

`POST /api/sessions/{id}/messages` emits `data: {json}` frames, terminated by `data: [DONE]`.

| Event | Payload | Meaning |
|---|---|---|
| `stage` | `stage`, `detail`, `progress?` | Pipeline position — drives the progress label |
| `token` | `text` | An increment of the answer |
| `tool_call` | `name`, `arguments` | A tool fired |
| `citations` | `citations[]`, `final?` | Retrieved passages; `final` = filtered to those actually cited |
| `outline` | `title`, `hook`, `sections[]` | Essay plan, before sections generate |
| `artifact` | full artifact | Generated, before persistence |
| `artifact_saved` | artifact with `id` | Persisted — the id the download link needs |
| `validation` | `report` | Ship 30 scorecard |
| `done` | `grounded`, `citations`, `provider`, `timings` | Turn complete |
| `saved` | `message_id`, `latency_ms` | Persisted |
| `error` | `code`, `message`, `hint` | Failed |

The streaming and non-streaming paths drain the **same generator**, so they cannot diverge. Persistence happens inside that generator, which means a client disconnecting mid-stream still gets its turn saved — a dropped connection does not silently lose an expensive generation.

---

## Ingestion flow

```
codeload tarball ──► parse ──► select ──► chunk ──► embed ──► store ──► INGESTED.md
   (cached)          YAML +     3 passes   turn-      batched   batched     manifest
                     speakers              aware      32        200
```

**Fetch.** A tarball rather than `git clone`: no git dependency in the container, one HTTP request instead of hundreds, and the commit SHA is recorded for traceability. Cached on disk so development re-runs do not re-download.

**Parse.** PyYAML frontmatter plus a speaker-turn regex. Properties of the real data that a naive parser gets wrong, each covered by a test:

- Titles wrap across lines (PyYAML block folding) — a raw newline would reach the UI.
- Apostrophes are doubled inside single-quoted scalars.
- `[inaudible 00:00:42]` markers appear inline and waste tokens in both the embedding and the prompt.

**Four turn-header formats exist in the corpus**, and getting this wrong is expensive because the symptom is silent:

| Shape | Example | Supported |
|---|---|---|
| `Speaker (HH:MM:SS):` | `Brian Balfour (00:12:34):` | yes |
| `Speaker (MM:SS):` | `Casey Winters (00:12):` | yes |
| `(MM:SS):` continuation | `(00:13):` | yes — attributed to the previous speaker |
| `Speaker:` with no timestamp | `Adriel Frederick:` | **no, deliberately** |

An initial implementation accepted only `HH:MM:SS`. That produced **zero turns for 30 of 303 episodes**, which then dropped out of corpus selection with no error at all — the failure looked like "the assistant has never heard of Casey Winters" rather than a crash. Supporting `MM:SS` and bare continuation markers recovered 29 episodes and correctly attributed 17,420 continuation turns that would otherwise have had no speaker.

The timestamp-less format is rejected on purpose: every chunk would carry `start_seconds=0`, so every citation would deep-link to 0:00 — a link that looks authoritative and points at the wrong place. One episode is affected, and the parser logs `parser.unsupported_format` rather than dropping it silently.

A related subtlety worth recording: the separator between the speaker and the timestamp must be `[ \t]*`, not `\s*`. `\s` matches newlines, which let the speaker group begin on the *previous* line, capture a whole paragraph as a speaker name, and swallow the following header.

**Select** — three passes, defined in `corpus.yml`:

1. Drop excluded slugs and anything under `INGEST_MIN_DURATION_SECONDS` (default 1800s). The repo mixes 4-minute YouTube shorts in with 90-minute interviews; the shorts are citation-poor and measurably hurt retrieval precision.
2. Ingest pinned slugs — the growth, pricing, positioning and PM canon. Pinned episodes bypass the duration filter and are never dropped by the cap, because silently dropping a pin would make `corpus.yml` lie.
3. Fill the remaining budget by most recent `publish_date`.

**Chunk.** Whole speaker turns accumulated to ~700 tokens with ~100 overlap. **A speaker turn is never split.** A half-turn cannot be honestly attributed, and attribution is the product. A turn larger than the target becomes its own chunk rather than being broken.

Token counting is `len(text) / 4`, not a real tokenizer. Bringing in tiktoken for a chunk-size heuristic would add a dependency and a model-specific vocabulary to a decision that only needs to be roughly right. Being 15% off costs a slightly larger prompt, not a wrong answer.

**Embed and store.** Batches of 32 through the embedding provider, inserted 200 rows at a time — per-row round-trips to a remote database would roughly double wall-clock. Written per batch rather than at the end, so an interrupted run leaves a partially-but-correctly ingested corpus and resumes cleanly.

**Refresh.** `content_hash` per episode; unchanged episodes are skipped. `--force` overrides.

**Traceability.** Every run regenerates `INGESTED.md` — episodes, chunk counts, hashes, source revision. That file, not `corpus.yml`, is the record of what the assistant actually knows.

---

## Retrieval flow

```
question ──► condense ──► embed ──► pgvector cosine top-k ──► score floor ──► citations
              (if any        │                                     │
               history)      └── on embedding failure ──► tsvector lexical fallback
                                                                   │
                                                       below floor ─┴──► refusal path
```

**Condensation** runs only when history exists — the first message is already standalone, and skipping saves ~10 s of prefill on CPU. It resolves pronouns and references so "what about for PLG?" becomes a query that retrieves correctly. If it fails or returns something degenerate, it falls back to the raw message, which is always safe.

**The score floor is a product decision, not a tuning knob.** Below `RETRIEVAL_SCORE_FLOOR`, `search()` returns *nothing* rather than the least-bad match. That is what lets the layer above say "the transcripts don't cover this" — the refusal prompt runs with no sources available, so there is nothing to answer from even if the model wanted to.

**Retrieve broadly, ground narrowly.** `RETRIEVAL_TOP_K` (8) passages become citation chips; `PROMPT_TOP_K` (4) enter the prompt. Measured on a Ryzen 7 7730U with no GPU: 8 passages cost ~22 s to first token, 4 cost ~11 s. Showing the user 8 sources is free; feeding the model 8 is not.

**Citations are filtered to those the answer actually referenced.** The `[S#]` markers are parsed back out of the generated text, so the list under an answer matches the answer rather than the retrieval.

### Why hybrid retrieval is deferred, not missing

The `tsv` column and its GIN index ship, and `lexical_search()` is used as a fallback when embeddings are unavailable. What is *not* there is RRF fusion of the two rankings.

That is deliberate. Tuning fusion weights requires an evaluation set, which we did not have time to build, and an **untuned hybrid can rank worse than plain vector search** — it is not a free improvement. Shipping an uncalibrated knob and calling it hybrid retrieval would be worse than shipping without it and saying so. The infrastructure is in place, so adding fusion is a query change rather than a migration.

---

## Agent runtimes

Two implementations of one contract, over **one shared tool registry and one shared skill definition**.

```
                          app/agent/tools.py            .claude/skills/ship30/SKILL.md
                          search_transcripts                    (one file)
                          write_ship30_essay                        │
                          create_artifact                           │
                                  │                                 │
                 ┌────────────────┴─────────────────┐               │
                 ▼                                  ▼               │
    LocalToolLoopRuntime                  ClaudeAgentSDKRuntime ◄────┘
    Ollama · Azure · any                  in-process MCP server
    OpenAI-compatible endpoint            via create_sdk_mcp_server
```

This sharing is the point. `search_transcripts` behaves identically whether a 3B model on a laptop or Claude called it. Adding a tool means adding it once. Editing the Ship 30 rubric takes effect in both runtimes, and there is exactly one answer to "where is the skill defined?"

### LocalToolLoopRuntime — the demo path

An **orchestrated pipeline, not a free-running tool loop**:

```
classify ──► condense ──► retrieve ──► (refuse | generate) ──► post-process
```

A frontier model can be handed a tool list and trusted to sequence its own work. `llama3.2` at 3B cannot — it skips retrieval when it believes it already knows the answer, which is precisely the failure the grounding requirement exists to prevent. So the model's judgement is used where it is good (writing prose from sources) and not where it is unreliable (deciding whether to look things up).

Tool *calling* is still offered on top for artifact creation, where a wrong call is cheap and recoverable, bounded at three rounds.

**Routing** is a keyword dispatcher, not a classifier. A regex that is right ~90% of the time and costs 0 ms beats a model call that is right ~92% and costs ten seconds of prefill — especially when the failure mode is benign: `chat` is the default, `chat` always retrieves, and a misrouted essay request produces a grounded answer instead of an essay. On a frontier model the right call would be to let the model choose; this exists because of what is running the demo.

### ClaudeAgentSDKRuntime

A real implementation: the same three tools wrapped by `create_sdk_mcp_server` as `mcp__lenny__*`, the same `SKILL.md` loaded through `ClaudeAgentOptions(setting_sources=["project"], skills="all")`, and sessions resumed by id so context carries across turns.

**It has not been run against the live Anthropic API.** The development machine had Azure OpenAI credentials and no Anthropic key. Two consequences, stated rather than obscured:

- It is exercised by tests against a mocked transport, not the real API.
- The recorded demo runs on `LocalToolLoopRuntime` — which is also what the mandatory local-Ollama requirement needs, since the SDK's bundled agent binary sends a system prompt on the order of 10–15k tokens. At ~11 s of prefill per 1k tokens on this hardware, that is minutes per turn before any work happens, and a 3B model would not hold the tool protocol across it.

To run it: `uv pip install -e ".[agent-sdk]"`, set `ANTHROPIC_API_KEY`, set `AGENT_RUNTIME=claude_sdk`.

It also accepts any gateway speaking the Anthropic Messages format via `ANTHROPIC_BASE_URL` — for example a LiteLLM proxy in front of Azure OpenAI. Three constraints apply, from Anthropic's gateway protocol:

- Responses **must** stream SSE; a gateway that buffers stalls the client.
- `anthropic-version` and `anthropic-beta` must pass through unchanged.
- Model discovery keeps only ids containing `claude` or `anthropic`, so an Azure deployment must be aliased (e.g. `claude-azure-gpt4o`).

`CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING=1` and `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1` are the escape hatches when a non-Anthropic upstream rejects the `thinking` or `context_management` fields.

---

## The model toggle

```
LLM_PROVIDER=ollama ──┐
LLM_MODEL=llama3.2    ├──► registry.py ──► LLMProvider ──► chat_stream / embed
LLM_BASE_URL=…        ┘         │
                                └──► fallback chain on failure
```

`providers/registry.py` is the **only** module that reads provider configuration and the only one that decides which model answers. Adding a provider means a branch there and some variables in `.env.example`; nothing in `api/`, `agent/` or `rag/` changes.

One adapter covers Ollama, NVIDIA NIM, OpenAI, Groq and vLLM, because they all speak `/chat/completions` and `/embeddings` identically. Azure subclasses it for three differences: the deployment in the path, `api-version` as a query parameter, and the key in an `api-key` header rather than `Authorization`.

**Fallback.** `chat_stream_with_fallback` retries the fallback provider on `ProviderUnavailableError`, `ProviderTimeoutError` or `MissingCredentialsError`. Validation errors are not retried — they would fail identically and just double the latency. Failover applies **only before the first token**: restarting mid-stream would either duplicate text or discard what the user already read, so a mid-stream failure surfaces as an error. Every response carries the provider that actually answered, shown under the message and recorded in `messages.provider`.

---

## Security

### Artifact rendering

Generated HTML is untrusted input. It is written by a model that has just read attacker-influenceable text — transcripts, user instructions, or an injection riding along in either. Treating it as trusted because "we generated it" is the mistake worth avoiding.

**Layer 1 — sanitize (server).** `nh3` allowlist over tags and attributes. `<script>`, `on*` handlers, `javascript:` and `data:text/html` URLs, `<form>`, `<object>`, `<embed>`, nested `<iframe>`, `<base>`, `<link>` and `<meta http-equiv>` are removed. `clean_content_tags` removes script *bodies*, not just tags, so nothing is left behind as visible text.

CSS gets its own pass, because nh3 sanitizes markup and not stylesheet contents: `@import`, `expression()`, `behavior:`, `-moz-binding` and any non-`data:`/`https:` `url()` are stripped from `<style>` blocks.

URL policy is narrowed **per attribute**, because the safe answer differs by position: `data:image/png` in an `<img src>` is fine, `data:text/html` in an `<a href>` is a navigation XSS vector.

**Layer 2 — isolate (client).** The iframe's `sandbox` attribute is the empty string — the maximally restrictive value, granting no scripts, no same-origin, no forms, no popups, no navigation. Plus a CSP of `default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'`.

`img-src data:` and nothing else network-facing means **an artifact cannot make a single outbound request** — which closes the exfiltration channel where generated content encodes data into a remote image URL. Worth more than the ability to hotlink an image.

Because scripts are stripped in layer 1, layer 2 never needs `allow-scripts`. That is what makes the policy explainable in one sentence: **artifacts are documents, not programs.** The trade-off — no interactive HTML artifacts — is accepted deliberately for an assistant that produces documents.

The full allow/block table is in [`design.md`](design.md#artifact-security-policy). 22 payloads are regression-tested in `tests/test_security.py`.

### Prompt injection

Transcripts enter the prompt as data in a delimited block, never as instructions. Tools are a fixed registry, so a compromised generation cannot invent one. Artifacts are sanitized and sandboxed regardless of origin. The grounding floor is enforced in code rather than by prompt compliance, so an injection cannot talk the assistant out of refusing.

### Secrets

No secrets in the repository; `.env` is gitignored and `.env.example` carries safe defaults. `/api/config` reports whether each provider is *configured*, never key values — and that is asserted by a test. Startup logs the provider and model, never credentials.

### Endpoint exposure

`POST /api/ingest` is guarded by `INGEST_ADMIN_TOKEN` and **disabled entirely when that token is unset**. An unauthenticated endpoint that can saturate the machine embedding 300 episodes is not something to leave open by default.

---

## Observability

`structlog`, JSON in production (`LOG_FORMAT=json`), human-readable in development.

Every request gets a request id — from the inbound `X-Request-Id` if present, otherwise generated — attached to every log line in that request and returned in the response header. Error bodies carry the same id, so a user-reported failure maps to a log line.

Per-stage timings are collected into one structured event rather than scattered across three:

```json
{
  "event": "chat.turn_complete",
  "request_id": "a3f9c21b8e04",
  "session_id": "…",
  "latency_ms": 14820,
  "chars": 1180,
  "citations": 4,
  "artifacts": 0,
  "provider": "ollama"
}
```

The stage timers (`condense_ms`, `retrieve_ms`, `generate_ms`) answer the question an on-call engineer actually has when told "it's slow": *which part?*

Notable events: `retrieval.below_floor` (with the score and the query — the signal for tuning the floor), `provider.failed` / `provider.fallback_succeeded`, `sanitize.removed`, `agent.condensed`, `ingest.episode_done` with observed chunks/sec.

`/health` is polled and therefore logged at debug, so it does not drown the signal.

---

## Resilience

Each failure has a defined behaviour, and each is covered by a test.

| Failure | Behaviour |
|---|---|
| **Missing API key** | `MissingCredentialsError` with a hint naming the variable. Detected before the request is attempted. |
| **Ollama not running** | Chat returns `provider_unavailable` with the start command. Retrieval degrades to lexical search rather than failing. `/health` names the component. |
| **Model timeout** | `ProviderTimeoutError` with the configured limit and how to raise it. |
| **Empty or weak retrieval** | Not an error — a product behaviour. The refusal path runs with no sources. |
| **Database unreachable** | **The app still starts.** `/health` reports the database as down. An API that refuses to boot gives an evaluator a stack trace and no diagnosis. |
| **Supabase pooler** | Port 6543 is transaction-mode, which breaks asyncpg's prepared-statement cache. Detected automatically and the cache disabled. |
| **Schema not applied** | `/health` says so, with the migration command. |
| **Corpus empty** | `/health` reports `corpus: not ok` with the ingest command — distinct from a database failure, and a different fix. |
| **One episode fails to ingest** | Logged and skipped; the run continues and reports errors at the end. One bad episode must not end the run. |
| **Bad tool arguments** | Returned to the model as `{"error": …}` so it can recover, rather than raising and aborting the turn. |
| **Client disconnects mid-stream** | The turn completes and persists. |
| **Provider fails mid-stream** | Surfaced as an error — see [The model toggle](#the-model-toggle) for why it is not retried. |

`/health` **never returns 500.** A dead embedding model does not mean the API is down, and a 500 would hide which component is at fault. It reports `degraded` and names the failing components.

---

## Deployment topology

### Verified — local

```
scripts/start.ps1  (Windows)  ·  scripts/start.sh  (macOS/Linux)

  ┌────────────┐   ┌────────────┐   ┌────────────┐
  │ Vite :5173 │──►│ API  :8000 │──►│ Supabase   │
  └────────────┘   └─────┬──────┘   │ + pgvector │
                         │          └────────────┘
                         ▼
                  ┌────────────┐
                  │ Ollama     │
                  │ :11434     │
                  └────────────┘
```

Vite proxies `/api` and `/health` to the API in development, so the browser sees one origin and SSE behaves the same locally as it would behind a reverse proxy.

### Provided but unverified — Docker

`docker-compose.yml` brings up pgvector, Ollama, a model-pull job, the API and an nginx-served frontend. It was written for evaluators who have Docker but **could not be executed here** — the development machine is a non-administrator Windows account where Docker Desktop cannot be installed. This is labelled in the compose file itself and in the README, because an untested compose file presented as the happy path fails in front of a reviewer and discredits everything around it.

### Production considerations, not implemented

- Terminate SSE at a proxy that does not buffer (`proxy_buffering off` for nginx). The API already sets `X-Accel-Buffering: no`.
- Run the API under multiple workers; it is stateless apart from the SDK session map, which would need externalising.
- Move ingestion to a scheduled job rather than an HTTP trigger.
- Add auth before this leaves an internal network.
