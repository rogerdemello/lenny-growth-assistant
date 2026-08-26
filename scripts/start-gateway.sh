#!/usr/bin/env bash
#
# Start the Anthropic-Messages gateway in front of Azure OpenAI.
# Lets the Claude Agent SDK runtime run without an Anthropic API key.
# See gateway/README.md.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GATEWAY="$ROOT/gateway"
LITELLM="$ROOT/backend/.venv/bin/litellm"
PORT="${1:-4000}"

if [ ! -x "$LITELLM" ]; then
  echo "litellm not installed. Run:" >&2
  echo '  cd backend && uv pip install -e ".[agent-sdk,gateway]"' >&2
  exit 1
fi

[ -f "$ROOT/.env" ] || { echo ".env not found." >&2; exit 1; }

# The config file holds no secrets; credentials come from the environment.
set -a
# shellcheck disable=SC2046
export $(grep -E '^AZURE_OPENAI_[A-Z_]+=' "$ROOT/.env" | xargs -d '\n') 2>/dev/null || true
set +a

for required in AZURE_OPENAI_ENDPOINT AZURE_OPENAI_API_KEY AZURE_OPENAI_API_VERSION; do
  if [ -z "${!required:-}" ]; then
    echo "$required is not set in .env" >&2
    exit 1
  fi
done

# clamp.py is imported by name from the config.
export PYTHONPATH="$GATEWAY"

cat <<BANNER

  Anthropic-Messages gateway -> Azure OpenAI
    listening on  http://127.0.0.1:$PORT
    model alias   claude-azure-gpt4o

  Then set in .env and restart the API:
    AGENT_RUNTIME=claude_sdk
    ANTHROPIC_BASE_URL=http://127.0.0.1:$PORT
    ANTHROPIC_AUTH_TOKEN=sk-gateway-local-only
    ANTHROPIC_MODEL=claude-azure-gpt4o

BANNER

exec "$LITELLM" --config "$GATEWAY/litellm_config.yaml" --port "$PORT"
