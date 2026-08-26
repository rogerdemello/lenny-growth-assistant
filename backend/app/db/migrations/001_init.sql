-- 001_init.sql — schema for The Lenny Growth Assistant
--
-- Applied by `python -m app.db.migrate`, which runs every file in this
-- directory in filename order and records what it has applied. Re-running is
-- safe; each file is applied at most once.
--
-- Two halves:
--   * conversation state  — sessions, messages, artifacts
--   * knowledge base      — episodes, chunks, ingest_runs

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- Conversation state
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title           TEXT        NOT NULL DEFAULT 'New chat',
    -- Anonymous-but-stable client identifier. The brief asks for user metadata;
    -- there is no auth in scope, so a client-generated id plus request context
    -- is what we can honestly persist.
    user_id         TEXT        NOT NULL DEFAULT 'anonymous',
    client_metadata JSONB       NOT NULL DEFAULT '{}'::jsonb,
    -- Provider/model captured at session creation, so a conversation records
    -- what actually answered it even after the env config changes.
    provider        TEXT,
    model           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS sessions_user_created_idx
    ON sessions (user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id   UUID        NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    role         TEXT        NOT NULL CHECK (role IN ('user', 'assistant', 'system', 'tool')),
    content      TEXT        NOT NULL DEFAULT '',
    -- Tool invocations and their results, for transparency in the UI and for
    -- debugging routing decisions after the fact.
    tool_calls   JSONB       NOT NULL DEFAULT '[]'::jsonb,
    -- Resolved citations: episode, guest, timestamp, YouTube deep link.
    citations    JSONB       NOT NULL DEFAULT '[]'::jsonb,
    -- Which provider actually answered. Differs from the session's provider
    -- when the fallback chain fired.
    provider     TEXT,
    model        TEXT,
    latency_ms   INTEGER,
    token_usage  JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS messages_session_created_idx
    ON messages (session_id, created_at);

CREATE TABLE IF NOT EXISTS artifacts (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id        UUID        NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    message_id        UUID        REFERENCES messages (id) ON DELETE SET NULL,
    kind              TEXT        NOT NULL CHECK (kind IN ('markdown', 'html')),
    title             TEXT        NOT NULL DEFAULT 'Untitled artifact',
    -- Both forms are kept: `raw_content` so the source toggle can show what the
    -- model actually produced, `sanitized_content` because that is the only
    -- thing ever rendered. Keeping both is what makes the sanitizer auditable.
    raw_content       TEXT        NOT NULL,
    sanitized_content TEXT        NOT NULL,
    -- What the sanitizer removed, so the UI can say so out loud.
    sanitizer_report  JSONB       NOT NULL DEFAULT '{}'::jsonb,
    version           INTEGER     NOT NULL DEFAULT 1,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS artifacts_session_created_idx
    ON artifacts (session_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- Knowledge base
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS episodes (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug             TEXT        NOT NULL UNIQUE,
    guest            TEXT        NOT NULL DEFAULT '',
    title            TEXT        NOT NULL DEFAULT '',
    youtube_url      TEXT,
    video_id         TEXT,
    publish_date     DATE,
    duration_seconds DOUBLE PRECISION,
    view_count       BIGINT,
    keywords         TEXT[]      NOT NULL DEFAULT '{}',
    -- Hash of the transcript body. Re-ingest skips episodes whose hash is
    -- unchanged, which is what makes refresh cheap.
    content_hash     TEXT        NOT NULL,
    -- Commit SHA of the transcripts repo this came from — the traceability
    -- link back to the exact source revision.
    source_sha       TEXT,
    ingested_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chunks (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    episode_id    UUID    NOT NULL REFERENCES episodes (id) ON DELETE CASCADE,
    ord           INTEGER NOT NULL,
    -- Speaker and timestamps survive chunking so a citation can deep-link to
    -- the exact moment in the YouTube video.
    speaker       TEXT,
    start_seconds INTEGER NOT NULL DEFAULT 0,
    end_seconds   INTEGER NOT NULL DEFAULT 0,
    text          TEXT    NOT NULL,
    token_count   INTEGER NOT NULL DEFAULT 0,
    -- Dimension is substituted from EMBED_DIM at migration time, because it is
    -- a property of the embedding model, not of the schema. Changing the embed
    -- model means a re-index, and the README says so.
    embedding     VECTOR(${EMBED_DIM}),
    -- Lexical index. Vector search is primary; this column exists so hybrid
    -- retrieval is a query change rather than a migration. See docs/design.md.
    tsv           TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
    UNIQUE (episode_id, ord)
);

CREATE INDEX IF NOT EXISTS chunks_tsv_idx ON chunks USING GIN (tsv);
CREATE INDEX IF NOT EXISTS chunks_episode_idx ON chunks (episode_id);

-- IVFFlat rather than HNSW: it builds in seconds on a corpus this size and
-- Supabase's free tier has limited memory for HNSW graph construction.
-- `lists = 100` suits the low-thousands of rows we ingest.
CREATE INDEX IF NOT EXISTS chunks_embedding_idx
    ON chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

CREATE TABLE IF NOT EXISTS ingest_runs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_sha      TEXT,
    embed_model     TEXT,
    status          TEXT        NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running', 'completed', 'failed')),
    episodes_count  INTEGER     NOT NULL DEFAULT 0,
    chunks_count    INTEGER     NOT NULL DEFAULT 0,
    error           TEXT,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ
);
