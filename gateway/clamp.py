"""LiteLLM pre-call hook: adapt Claude's parameter envelope to Azure OpenAI.

The Claude Agent SDK assumes it is talking to a Claude model, and sends a
Claude-shaped request. Most of that translates cleanly. Two things do not, and
both produce a hard 400 rather than a graceful degradation:

  * **`max_tokens: 32000`** — a Claude-sized output budget. gpt-4o caps
    completions at 16384 and rejects anything larger outright.

  * **`thinking` / `context_management` / `output_config`** — Anthropic-only
    fields that a non-Anthropic upstream does not recognise.

Setting `max_tokens` in `litellm_params` does *not* fix the first one: those
are defaults, and an explicit client-supplied value wins the merge. A pre-call
hook runs after merging, which makes it the only place the value can be
rewritten.

This is exactly the class of incompatibility Anthropic's gateway protocol
documentation warns about — a gateway has to *translate* the parameter
envelope, not merely forward it.
"""

from litellm.integrations.custom_logger import CustomLogger

# gpt-4o's documented completion ceiling.
MAX_OUTPUT_TOKENS = 16384

# Fields with no meaning outside Anthropic's API.
ANTHROPIC_ONLY_FIELDS = ("thinking", "context_management", "output_config")


class ParameterEnvelopeAdapter(CustomLogger):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data: dict, call_type):  # noqa: ANN001
        requested = data.get("max_tokens")
        if isinstance(requested, int) and requested > MAX_OUTPUT_TOKENS:
            print(f"[gateway] clamping max_tokens {requested} -> {MAX_OUTPUT_TOKENS}")
            data["max_tokens"] = MAX_OUTPUT_TOKENS

        for field in ANTHROPIC_ONLY_FIELDS:
            if field in data:
                print(f"[gateway] dropping Anthropic-only field: {field}")
                data.pop(field, None)

        return data


proxy_handler_instance = ParameterEnvelopeAdapter()
