#!/usr/bin/env bash
#
# One-command startup for The Lenny Growth Assistant (macOS / Linux).
#
#   ./scripts/start.sh            # start
#   ./scripts/start.sh --ingest   # build the corpus first (required once)
#
# Every step is idempotent, so re-running is safe.
#
# NOTE: this is the POSIX twin of scripts/start.ps1, which is the script that
# was verified on the development machine (Windows). The logic is identical.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
PYTHON="$BACKEND/.venv/bin/python"

INGEST=0
SKIP_INSTALL=0
for arg in "$@"; do
  case "$arg" in
    --ingest) INGEST=1 ;;
    --skip-install) SKIP_INSTALL=1 ;;
    *) echo "Unknown option: $arg" >&2; exit 1 ;;
  esac
done

step() { printf '\n\033[36m=> %s\033[0m\n' "$1"; }
ok()   { printf '   \033[32mOK\033[0m  %s\n' "$1"; }
warn() { printf '   \033[33m!\033[0m   %s\n' "$1"; }
err()  { printf '   \033[31mX\033[0m   %s\n' "$1"; }

step "Checking prerequisites"
for tool in node npm uv; do
  command -v "$tool" >/dev/null 2>&1 || { err "$tool not found."; exit 1; }
done
ok "node, npm, uv"

if [ ! -f "$ROOT/.env" ]; then
  cp "$ROOT/.env.example" "$ROOT/.env"
  warn "Created .env from .env.example — set DATABASE_URL before continuing."
  exit 1
fi
if grep -q 'DATABASE_URL=postgresql://postgres:password@localhost' "$ROOT/.env"; then
  err "DATABASE_URL is still the placeholder. Set a real connection string in .env."
  err "A free Supabase project works: https://supabase.com"
  exit 1
fi
ok ".env present with a DATABASE_URL"

step "Checking Ollama"
OLLAMA_UP=0
if curl -sf --max-time 5 http://localhost:11434/api/tags >/dev/null 2>&1; then
  OLLAMA_UP=1
  MODELS="$(curl -s http://localhost:11434/api/tags | tr ',' '\n' | grep -o '"name":"[^"]*"' | cut -d'"' -f4 | tr '\n' ' ')"
  ok "running — models: $MODELS"
  for needed in llama3.2 nomic-embed-text; do
    case "$MODELS" in
      *"$needed"*) ;;
      *) warn "$needed not pulled. Fetching..."; ollama pull "$needed" ;;
    esac
  done
else
  warn "Ollama is not reachable at http://localhost:11434"
  warn "Start it with 'ollama serve', or set LLM_PROVIDER=azure in .env."
  warn "Continuing — /health will report the provider as unavailable."
fi

if [ "$SKIP_INSTALL" -eq 0 ]; then
  step "Installing backend dependencies"
  (cd "$BACKEND" && { [ -x "$PYTHON" ] || uv venv --python 3.12; } && uv pip install -e '.[dev]' --quiet)
  ok "backend"

  step "Installing frontend dependencies"
  (cd "$FRONTEND" && npm install --no-audit --no-fund --silent)
  ok "frontend"
fi

step "Applying database migrations"
(cd "$BACKEND" && "$PYTHON" -m app.db.migrate)
ok "schema up to date"

if [ "$INGEST" -eq 1 ]; then
  if [ "$OLLAMA_UP" -eq 0 ]; then
    err "Ingestion needs an embedding model. Start Ollama, or set EMBED_PROVIDER=azure."
    exit 1
  fi
  step "Ingesting transcripts (10-20 minutes on CPU)"
  (cd "$BACKEND" && "$PYTHON" -m app.ingest.pipeline)
  ok "corpus built — see INGESTED.md"
fi

step "Starting servers"
(cd "$BACKEND" && "$PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port 8000) &
API_PID=$!
sleep 4

if curl -sf --max-time 20 http://127.0.0.1:8000/health >/dev/null 2>&1; then
  STATUS="$(curl -s http://127.0.0.1:8000/health | grep -o '"status":"[^"]*"' | cut -d'"' -f4)"
  [ "$STATUS" = "ok" ] && ok "API healthy on http://127.0.0.1:8000" || warn "API degraded — see http://127.0.0.1:8000/health"
else
  err "API did not come up."
fi

(cd "$FRONTEND" && npm run dev) &
WEB_PID=$!

cat <<'BANNER'

  The Lenny Growth Assistant is running.
    Web    http://localhost:5173
    API    http://127.0.0.1:8000
    Docs   http://127.0.0.1:8000/docs
    Health http://127.0.0.1:8000/health

  Press Ctrl+C to stop.
BANNER

trap 'kill $API_PID $WEB_PID 2>/dev/null || true' EXIT INT TERM
wait $API_PID
