# The Lenny Growth Assistant

A conversational assistant that answers product and growth questions **only** from Lenny's Podcast transcripts, cites the episode and timestamp behind every claim, turns those answers into Ship 30 for 30–style essays, and renders Markdown and HTML artifacts safely beside the chat.

Runs entirely on a local model. No API key required.

```
┌──────────────┬────────────────────────────────┬──────────────────┐
│  Sessions    │  Chat                          │  Artifact viewer │
│              │                                │                  │
│  Pricing…    │  Madhavan Ramanujam argues     │  # How to price  │
│  Retention…  │  that pricing is a product     │  a B2B SaaS…     │
│  + New chat  │  decision, not a finance one   │                  │
│              │  [S1]                          │  Ship 30 spec:   │
│  ollama      │                                │  9/9 checks      │
│  llama3.2    │  🛡 Grounded in 4 passages      │                  │
│  40 eps      │  [S1] Madhavan Ramanujam 12:04 │  [Rendered|Source│
└──────────────┴────────────────────────────────┴──────────────────┘
```

---

## Contents

- [What it does](#what-it-does)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Switching models](#switching-models)
- [Building the knowledge base](#building-the-knowledge-base)
- [Tests](#tests)
- [Troubleshooting](#troubleshooting)
- [Known limitations](#known-limitations)
- [Documentation](#documentation)
- [Attribution](#attribution)

---

## What it does

**Grounded answers.** Ask a product or growth question and the assistant retrieves passages from ingested transcripts, answers from those passages only, and shows each one as a citation chip. Clicking a chip reveals the transcript excerpt and links to the exact second of the YouTube episode.

**Honest refusal.** Ask something the archive does not cover and it says so instead of answering from the model's own knowledge. This is enforced by a similarity floor in retrieval, not by asking the model nicely — see [`retrieval.py`](backend/app/rag/retrieval.py).

**Follow-ups that work.** "What about for PLG?" is condensed into a standalone query before retrieval, so pronouns and references to earlier turns resolve correctly.

**A real skill, not a prompt.** The Ship 30 essay style is encoded in [`.claude/skills/ship30/SKILL.md`](.claude/skills/ship30/SKILL.md) and enforced by a [programmatic validator](backend/app/skills/ship30_validator.py) that checks word count, section count, hook length, citation coverage, bullet usage and banned phrases — and reports the result in the UI.

**Artifacts you can trust.** Generated Markdown and HTML render beside the chat. HTML is sanitized server-side against an allowlist and then rendered in an iframe that grants nothing — no scripts, no same-origin, no network. The UI states what was stripped.

---

## Architecture

```
frontend/  Vite + React + TypeScript + Tailwind
   │       split-pane chat + artifact viewer, SSE streaming
   ▼
backend/app/
   api/         FastAPI routers, request/response contracts, health
   agent/       AgentRuntime ─┬─ LocalToolLoopRuntime   (Ollama | Azure)
                              └─ ClaudeAgentSDKRuntime  (claude-agent-sdk)
                tools.py  ← ONE registry, consumed by BOTH runtimes
   skills/      loader + ship30 generation + validator
   providers/   LLMProvider ─┬─ OpenAICompatProvider (Ollama, NIM, OpenAI…)
                             └─ AzureOpenAIProvider
   rag/         retrieval with score floor + lexical fallback
   ingest/      fetch → parse → chunk → embed → store (resumable)
   db/          asyncpg pool + numbered SQL migrations
   ▼
PostgreSQL + pgvector
```

The load-bearing idea: **tools and skills are declared once and executed by two different runtimes.** `.claude/skills/ship30/SKILL.md` is read by the local runtime as a style reference and by the Claude Agent SDK through its own skill loader. `search_transcripts`, `write_ship30_essay` and `create_artifact` live in one registry that the SDK wraps as an in-process MCP server. That is what makes "swap the model without changing application code" true rather than aspirational.

Full detail in [`docs/architecture.md`](docs/architecture.md).

---

## Prerequisites

| | Version | Notes |
|---|---|---|
| Python | 3.11+ | 3.12 recommended |
| [uv](https://docs.astral.sh/uv/) | latest | dependency manager |
| Node.js | 20+ | 22/24 fine |
| [Ollama](https://ollama.com) | latest | for the local model |
| PostgreSQL | 14+ with `pgvector` | [Supabase](https://supabase.com) free tier works and has pgvector preinstalled |

Docker is optional — see [Running with Docker](#running-with-docker).

---

## Quick start

### 1. Clone and configure

```bash
git clone https://github.com/rogerdemello/lenny-growth-assistant
cd lenny-growth-assistant
cp .env.example .env
```

Set one value in `.env`:

```dotenv
DATABASE_URL=postgresql://postgres:<password>@<host>:6543/postgres
```

A free Supabase project gives you this in **Project Settings → Database → Connection string → URI**. Everything else has a working default.

### 2. Pull the local models

```bash
ollama pull llama3.2          # ~2.0 GB, chat
ollama pull nomic-embed-text  # ~274 MB, embeddings
```

### 3. Start

```powershell
# Windows
./scripts/start.ps1 -Ingest
```

```bash
# macOS / Linux
chmod +x scripts/start.sh
./scripts/start.sh --ingest
```

The script checks prerequisites, installs dependencies, applies migrations, builds the knowledge base, and starts both servers. `--ingest` is needed on the first run only; without it the corpus is empty and every question is refused.

| | |
|---|---|
| Web | http://localhost:5173 |
| API docs | http://127.0.0.1:8000/docs |
| Health | http://127.0.0.1:8000/health |

Ingestion takes **10–20 minutes** on CPU for the default 40 episodes. It is resumable — interrupt it and re-run.

### Running with Docker

```bash
docker compose up
```

> ⚠️ **`docker-compose.yml` has not been executed.** It was written for evaluators who have Docker, but the development machine was a non-administrator Windows account where Docker Desktop cannot be installed. The verified path is `scripts/start.ps1`. This is called out in the compose file itself rather than left for you to discover.

---

## Configuration

Every setting lives in `.env`. [`.env.example`](.env.example) documents all of them; these are the ones that matter.

| Variable | Default | What it does |
|---|---|---|
| `DATABASE_URL` | — | **Required.** PostgreSQL with pgvector. |
| `LLM_PROVIDER` | `ollama` | `ollama` · `azure` · `openai_compat` |
| `LLM_MODEL` | `llama3.2` | Chat model. |
| `LLM_FALLBACK_PROVIDER` | *(empty)* | Retried on timeout or error. Blank disables. |
| `ESSAY_PROVIDER` | *(inherits)* | Route long-form generation elsewhere. |
| `AGENT_RUNTIME` | `local` | `local` · `claude_sdk` |
| `EMBED_PROVIDER` / `EMBED_MODEL` | `ollama` / `nomic-embed-text` | Changing these requires a re-index. |
| `RETRIEVAL_TOP_K` | `8` | Passages retrieved and shown as citations. |
| `PROMPT_TOP_K` | `4` | Passages actually placed in the prompt. |
| `RETRIEVAL_SCORE_FLOOR` | `0.35` | Below this, the assistant refuses. Raise to make it more cautious. |
| `INGEST_MAX_EPISODES` | `40` | Corpus size. |

**Why `PROMPT_TOP_K` is lower than `RETRIEVAL_TOP_K`:** prefill dominates latency on CPU. Measured on a Ryzen 7 7730U with no GPU, 8 passages cost ~22 s to first token versus ~11 s for 4. Showing the user 8 citations is free; feeding the model 8 is not.

---

## Switching models

No code changes — edit `.env` and restart. The active provider is shown in the UI header and sidebar, and reported by `GET /api/config`.

**Local (default).** Nothing to configure beyond having Ollama running.

**Azure OpenAI.**

```dotenv
LLM_PROVIDER=azure
AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com
AZURE_OPENAI_API_KEY=<key>
AZURE_OPENAI_CHAT_DEPLOYMENT=gpt-4o-mini
AZURE_OPENAI_API_VERSION=2024-10-21
```

**Any OpenAI-compatible endpoint** (NVIDIA NIM, OpenAI, Groq, vLLM):

```dotenv
LLM_PROVIDER=openai_compat
OPENAI_COMPAT_BASE_URL=https://integrate.api.nvidia.com/v1
OPENAI_COMPAT_API_KEY=<key>
OPENAI_COMPAT_MODEL=meta/llama-3.3-70b-instruct
```

**Fallback behaviour.** Set `LLM_FALLBACK_PROVIDER` and a failed primary is retried on the fallback before the first token. The switch is logged, and the response records which provider actually answered — visible under each message. Failover deliberately does not apply mid-stream: restarting after output has begun would either duplicate text or discard what the user already read, so a mid-stream failure surfaces as an error instead.

**Claude Agent SDK.**

```bash
uv pip install -e ".[agent-sdk]"
```
```dotenv
AGENT_RUNTIME=claude_sdk
ANTHROPIC_API_KEY=sk-ant-...
```

This runtime is fully implemented — same tool registry, same `SKILL.md`, sessions resumed by id — but **was not run against the live API**, because the development machine had Azure credentials and no Anthropic key. It is covered by tests against a mocked transport. It also accepts any Anthropic-Messages-format gateway via `ANTHROPIC_BASE_URL`. See [`docs/architecture.md`](docs/architecture.md#agent-runtimes) for the full reasoning and the gateway constraints.

---

## Building the knowledge base

```bash
cd backend
python -m app.ingest.pipeline              # the configured subset (default 40)
python -m app.ingest.pipeline --limit 5    # quick smoke run
python -m app.ingest.pipeline --all        # every qualifying episode (~1-2h on CPU)
python -m app.ingest.pipeline --dry-run    # parse and chunk only, write nothing
python -m app.ingest.pipeline --force      # re-embed even if unchanged
```

**Selection** is defined in [`corpus.yml`](corpus.yml) and runs in three passes: drop excluded slugs and anything under `INGEST_MIN_DURATION_SECONDS` (the repo mixes 4-minute YouTube shorts in with 90-minute interviews); ingest the pinned growth/pricing/PM canon; fill the remaining budget by recency.

**Refresh** is cheap — each episode carries a content hash, and re-running skips anything unchanged.

**Traceability** — every run records the upstream commit SHA and regenerates `INGESTED.md`, which lists exactly which episodes the assistant knows, with chunk counts and hashes. If a question is not covered there, the assistant should refuse it.

A deliberately bounded corpus also makes the refusal behaviour demonstrable: there are guaranteed out-of-corpus questions to try.

---

## Tests

```bash
cd backend
uv run pytest              # 123 tests, no network required
uv run pytest -v           # per-test detail
uv run ruff check app      # lint
```

| Suite | Covers |
|---|---|
| `test_ingest.py` | frontmatter edge cases (wrapped titles, doubled apostrophes, malformed YAML), speaker-turn regex, timestamp conversion, `[inaudible]` stripping, chunker never splitting a turn, overlap without duplication, corpus selection rules |
| `test_agent.py` | intent routing, **grounding refusal below the score floor**, lexical fallback when embeddings are down, Ship 30 validator, Azure URL layout, secrets never appearing in `/api/config` |
| `test_security.py` | 22 XSS payloads, `data:` allowed for images but not links, CSP and sandbox policy assertions |

The manual UI plan is in [`docs/manual-test-plan.md`](docs/manual-test-plan.md).

---

## Troubleshooting

**`/health` says `degraded`.** It reports each component separately — read `components` to see which one. It deliberately never returns 500, so you always get a diagnosis rather than a stack trace.

**"Ollama is not reachable."** Start it with `ollama serve` and confirm `curl http://localhost:11434/api/tags`. Or set `LLM_PROVIDER=azure` to use a cloud model instead.

**Migrations fail with `type "vector" does not exist`.** The pgvector extension is not enabled. On Supabase: **Database → Extensions → vector**. Elsewhere: `CREATE EXTENSION vector;`.

**`DuplicatePreparedStatementError`.** Supabase's pooler on port 6543 uses transaction mode, which breaks asyncpg's statement cache. The app detects port 6543 and disables the cache automatically — if you see this, you are likely on a different pooler; set the port to 6543 or use the direct connection on 5432.

**Database connects, then stops.** Supabase free projects auto-pause after inactivity. Open the dashboard to wake it.

**Every question is refused.** The corpus is empty. Run ingestion, then check `GET /health` shows `embedded_chunks > 0`.

**Answers are very slow.** Expected on CPU: roughly 7–9 tokens/sec on a 3B model, ~11 s to first token for a typical prompt. Lower `PROMPT_TOP_K`, or set `LLM_PROVIDER=azure`.

**Essay generation takes minutes.** Also expected — ~1,250 words at 7–9 tok/s. Sections stream as they complete so you can watch progress. Set `ESSAY_PROVIDER=azure` for seconds instead.

**Out of memory / thrashing.** `llama3.2` needs ~3 GB resident and `nomic-embed-text` another ~0.4 GB. Close other applications, and set `OLLAMA_KEEP_ALIVE=30m` so the model is not unloaded and reloaded between calls.

---

## Known limitations

Stated plainly rather than left to be discovered:

- **`docker-compose.yml` is unverified.** No Docker on the authoring machine. `scripts/start.ps1` is the tested path.
- **The Claude Agent SDK runtime has not run against the live API.** No Anthropic key was available; it is exercised through mocked-transport tests only.
- **Retrieval is vector-only.** The `tsvector` column and GIN index ship, and lexical search is used as a fallback when embeddings are unavailable, but RRF hybrid fusion is deliberately deferred — tuning it needs an evaluation set we did not build, and an untuned hybrid can rank worse than plain vector search. Reasoning in [`docs/design.md`](docs/design.md).
- **Intent routing is a keyword dispatcher, not a classifier.** A deliberate choice for a 3B local model; the trade-off is documented in [`docs/design.md`](docs/design.md).
- **No authentication.** Sessions carry a `user_id` and client metadata but there is no auth system. Out of scope, and noted in the PRD.
- **Small-model quality.** `llama3.2` at 3B follows the Ship 30 spec imperfectly. The validator surfaces exactly where it fell short rather than hiding it — which is the point of having a validator.

---

## Documentation

| Document | Contents |
|---|---|
| [`docs/PRD.md`](docs/PRD.md) | Discovery brief, user and problem, success metrics, assumptions, scope, flows, acceptance criteria, risks |
| [`docs/architecture.md`](docs/architecture.md) | Schema, endpoints, component boundaries, ingestion and retrieval flow, agent routing, model toggle, security, deployment |
| [`docs/design.md`](docs/design.md) | UI/UX principles, information architecture, interaction states, responsive behaviour, accessibility, sanitizer allow/block table |
| [`docs/manual-test-plan.md`](docs/manual-test-plan.md) | Step-by-step UI verification |
| [`agent-transcripts/`](agent-transcripts/) | Coding-agent transcripts, including failed attempts and corrections |

---

## Attribution

Transcripts are sourced from [ChatPRD/lennys-podcast-transcripts](https://github.com/ChatPRD/lennys-podcast-transcripts) and are **not** redistributed in this repository — they are fetched at ingestion time. All podcast content belongs to [Lenny's Podcast](https://www.lennyspodcast.com/).

Writing principles for the Ship 30 skill are derived from the publicly published [Ship 30 for 30 guides](https://www.ship30for30.com/).
