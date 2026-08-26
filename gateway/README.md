# Anthropic-Messages gateway

**What this solves.** The assignment specifies the Anthropic Claude Agent SDK as the agent layer. The SDK authenticates with an Anthropic API key, and none was available for this build — the machine had Azure OpenAI credentials.

Rather than ship the SDK runtime as code nobody had ever executed, this gateway makes it run: LiteLLM presents an Anthropic Messages endpoint, and translates to Azure OpenAI behind it.

**Status: verified working.** The result is reproduced below.

---

## Running it

```powershell
./scripts/start-gateway.ps1     # Windows
```
```bash
./scripts/start-gateway.sh      # macOS / Linux
```

Then in `.env`:

```dotenv
AGENT_RUNTIME=claude_sdk
ANTHROPIC_BASE_URL=http://127.0.0.1:4000
ANTHROPIC_AUTH_TOKEN=sk-gateway-local-only
ANTHROPIC_MODEL=claude-azure-gpt4o
```

Restart the API. `GET /api/config` will report `agent_runtime: claude_sdk`.

Requires `litellm[proxy]` and `claude-agent-sdk`:

```bash
uv pip install -e ".[agent-sdk,gateway]"
```

---

## The three things that had to be solved

Anthropic documents a gateway protocol, but "speaks the Messages format" is necessary and not sufficient. Three concrete incompatibilities surfaced, each producing a hard failure:

### 1. Model discovery filters on the name

Claude Code keeps only model ids containing `claude` or `anthropic`, case-insensitive. A deployment exposed as `azure-gpt-4o` is discovered and then silently discarded.

**Fix:** alias it. The model is exposed as **`claude-azure-gpt4o`**.

The SDK still logs `[claude-code:unrecognized_model]` — that is cosmetic and does not stop the request.

### 2. `max_tokens: 32000` exceeds what gpt-4o accepts

The SDK sends a Claude-sized output budget. gpt-4o caps completions at 16384 and returns:

```
AzureException BadRequestError - max_tokens is too large: 32000.
This model supports at most 16384 completion tokens, whereas you provided 32000.
```

Setting `max_tokens` in `litellm_params` does **not** fix this. Those are defaults, and an explicit client-supplied value wins the merge — verified by trying it first.

**Fix:** a pre-call hook (`clamp.py`), which runs after parameter merging and is therefore the only place the value can be rewritten.

### 3. Anthropic-only fields

`thinking`, `context_management` and `output_config` have no meaning to Azure. `drop_params: true` handles most of it; the hook removes the rest explicitly.

`CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING=1` and `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1` are set by the start script so the SDK stops sending them at source.

---

## Verified result

```
connecting to gateway at http://127.0.0.1:4000
  -> tool_use: mcp__lenny__search_transcripts {'query': 'pricing a B2B SaaS product'}

--- answer (18.1s) ---
Madhavan Ramanujam emphasizes that pricing a B2B SaaS product is fundamentally
a product decision, not just a financial one. It's crucial to engage in a
willingness-to-pay conversation with potential customers before you even build
the product. [S1]

tool called      : 1 time(s)
tool args        : {'query': 'pricing a B2B SaaS product'}
session id       : 71fe427b-e3fa-4ba2-84af-ae9439eeb1ea
citation present : True

RESULT: PASS — tool round-trip works
```

That is the whole chain working: **Claude Agent SDK → in-process MCP tool → LiteLLM → Azure OpenAI → a grounded, cited answer.**

---

## What this does *not* solve

**It cannot drive the local Ollama demo.** The SDK's bundled agent binary sends a system prompt on the order of 10–15k tokens. At the ~11 s per 1k tokens of prefill measured on this hardware, that is minutes per turn before any useful work starts, and a 3B model will not hold the tool-use protocol across a prompt that size.

So the architecture keeps two runtimes, and this gateway changes *which claim* we can make about the second one:

| Runtime | Model | Status |
|---|---|---|
| `LocalToolLoopRuntime` | Ollama `llama3.2` | The local demo. Fully offline, no key. |
| `LocalToolLoopRuntime` | Azure `gpt-4o` | Same code, cloud provider. |
| `ClaudeAgentSDKRuntime` | Azure `gpt-4o` via this gateway | **Verified working.** |
| `ClaudeAgentSDKRuntime` | Anthropic Claude | Should work unchanged with `ANTHROPIC_API_KEY`; not exercised — no key available. |

---

## Security notes

- The master key is a local-only placeholder. **Do not expose this gateway beyond localhost** without replacing it and putting real auth in front.
- `AZURE_OPENAI_API_KEY` is read from the environment. No credential appears in `litellm_config.yaml`.
- Pin LiteLLM to **≥ 1.83.0**. Versions **1.82.7 and 1.82.8 shipped credential-stealing malware**; that is why the dependency carries a floor rather than a loose range.
