"""Routing, grounding behaviour, the essay validator, and provider selection.

The grounding tests are the important ones. "The assistant refuses when the
corpus does not cover the question" is a product guarantee, and it is enforced
in exactly one place — the score floor in `app.rag.retrieval.search`. If that
regresses, the assistant starts answering from the model's own knowledge while
still looking grounded, which is the worst possible failure for this product.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.agent.local_loop import _is_echo, _looks_like_followup
from app.agent.router import Intent, classify, needs_retrieval
from app.core.config import Settings
from app.providers.registry import build_provider, describe_configuration
from app.rag.retrieval import Citation, RetrievalResult, format_sources_block, search
from app.skills.ship30_validator import validate

# --------------------------------------------------------------------------
# Router
# --------------------------------------------------------------------------


class TestRouter:
    @pytest.mark.parametrize(
        "message",
        [
            "Write me an essay about growth loops",
            "Turn that into an essay",
            "write this up as a ship 30 post",
            "Can you write an article on pricing?",
            "draft a newsletter piece about retention",
        ],
    )
    def test_essay_requests(self, message: str):
        assert classify(message) is Intent.ESSAY

    @pytest.mark.parametrize(
        "message",
        [
            "Make me a one-pager on positioning",
            "Create a checklist for launch",
            "give me an HTML summary",
            "build a table comparing those",
        ],
    )
    def test_artifact_requests(self, message: str):
        assert classify(message) is Intent.ARTIFACT

    @pytest.mark.parametrize("message", ["hi", "hey", "thanks!", "ok", "  Hello  "])
    def test_smalltalk(self, message: str):
        assert classify(message) is Intent.SMALLTALK

    @pytest.mark.parametrize("message", ["what can you do?", "which episodes do you know about?"])
    def test_meta_questions_are_not_retrieved(self, message: str):
        # Answering "what can you do" from transcripts would be nonsense.
        assert classify(message) is Intent.SMALLTALK

    @pytest.mark.parametrize(
        "message",
        [
            "How should I price a B2B SaaS product?",
            "What drives retention early on?",
            "what about for PLG?",
            "Tell me about growth loops",
        ],
    )
    def test_questions_default_to_grounded_chat(self, message: str):
        assert classify(message) is Intent.CHAT

    def test_empty_message_is_smalltalk(self):
        assert classify("") is Intent.SMALLTALK
        assert classify("   ") is Intent.SMALLTALK

    def test_essay_wins_over_artifact_when_both_appear(self):
        """'write this up as a one-pager essay' is an essay that mentions a format."""
        assert classify("write an essay as a one-pager") is Intent.ESSAY

    def test_retrieval_is_mandatory_for_grounded_intents(self):
        assert needs_retrieval(Intent.CHAT)
        assert needs_retrieval(Intent.ESSAY)
        assert needs_retrieval(Intent.ARTIFACT)
        assert not needs_retrieval(Intent.SMALLTALK)


class TestFollowUpCondensation:
    """A 3B model often 'condenses' by echoing the input without punctuation.

    Observed against the live model: "What about for PLG?" came back as
    "what about for PLG" — which resolves nothing, so retrieval had no subject
    to search for. These guards detect that and fall back to a deterministic
    rewrite that concatenates the previous user turn.
    """

    @pytest.mark.parametrize(
        "condensed,original",
        [
            ("what about for PLG", "What about for PLG?"),
            ("What about for PLG", "what about for plg?"),
            ("  what about for PLG.  ", "What about for PLG?"),
        ],
    )
    def test_echo_is_detected(self, condensed: str, original: str):
        assert _is_echo(condensed, original)

    @pytest.mark.parametrize(
        "condensed,original",
        [
            ("product-led growth pricing strategy", "What about for PLG?"),
            ("Elena Verna retention growth loops", "what did she say about that?"),
        ],
    )
    def test_a_real_rewrite_is_not_an_echo(self, condensed: str, original: str):
        assert not _is_echo(condensed, original)

    @pytest.mark.parametrize(
        "message",
        [
            "What about for PLG?",
            "what did she say about that?",
            "And how about retention?",
            "why is that",
            "ok but what if the market is smaller",
        ],
    )
    def test_followups_are_recognised(self, message: str):
        assert _looks_like_followup(message)

    def test_a_standalone_question_is_not_a_followup(self):
        assert not _looks_like_followup(
            "How should a founder approach pricing when entering a crowded enterprise market"
        )


# --------------------------------------------------------------------------
# Grounding
# --------------------------------------------------------------------------


def _citation(score: float, index: int = 1) -> Citation:
    return Citation(
        chunk_id=f"chunk-{index}",
        episode_slug="brian-balfour",
        guest="Brian Balfour",
        episode_title="Why ChatGPT will be the next big growth channel",
        speaker="Brian Balfour",
        start_seconds=155,
        text="Retention is the clearest signal of product-market fit.",
        score=score,
        youtube_url="https://www.youtube.com/watch?v=cX4cL6B-_aU&t=155s",
    )


class TestGrounding:
    @pytest.fixture
    def settings(self):
        return Settings(retrieval_score_floor=0.35, retrieval_top_k=8, prompt_top_k=4)

    async def test_high_scoring_results_are_grounded(self, settings):
        with patch(
            "app.rag.retrieval.vector_search",
            AsyncMock(return_value=[_citation(0.82), _citation(0.61, 2)]),
        ):
            result = await search("retention", settings=settings)
        assert result.grounded is True
        assert len(result.citations) == 2
        assert result.best_score == 0.82

    async def test_results_below_the_floor_are_withheld(self, settings):
        """The refusal path: better to say nothing than to answer from noise."""
        with patch(
            "app.rag.retrieval.vector_search",
            AsyncMock(return_value=[_citation(0.20), _citation(0.11, 2)]),
        ):
            result = await search("best kubernetes ingress controller", settings=settings)
        assert result.grounded is False
        assert result.citations == []
        assert "below" in (result.reason or "")

    async def test_partial_results_keep_only_what_clears_the_floor(self, settings):
        with patch(
            "app.rag.retrieval.vector_search",
            AsyncMock(return_value=[_citation(0.75), _citation(0.12, 2)]),
        ):
            result = await search("retention", settings=settings)
        assert result.grounded is True
        assert len(result.citations) == 1

    async def test_empty_corpus_is_not_grounded(self, settings):
        with patch("app.rag.retrieval.vector_search", AsyncMock(return_value=[])):
            result = await search("anything", settings=settings)
        assert result.grounded is False
        assert result.reason == "no chunks in corpus"

    async def test_empty_query_short_circuits(self, settings):
        result = await search("   ", settings=settings)
        assert result.grounded is False
        assert result.strategy == "none"

    async def test_embedding_outage_degrades_to_lexical_search(self, settings):
        """A dead embedding model should not take retrieval down with it."""
        from app.core.errors import ProviderUnavailableError

        with (
            patch("app.rag.retrieval.vector_search", AsyncMock(side_effect=ProviderUnavailableError("ollama down"))),
            patch("app.rag.retrieval.lexical_search", AsyncMock(return_value=[_citation(0.4)])),
        ):
            result = await search("retention", settings=settings)
        assert result.grounded is True
        assert result.strategy == "lexical_fallback"

    async def test_total_outage_reports_honestly(self, settings):
        from app.core.errors import ProviderUnavailableError

        with (
            patch("app.rag.retrieval.vector_search", AsyncMock(side_effect=ProviderUnavailableError("down"))),
            patch("app.rag.retrieval.lexical_search", AsyncMock(return_value=[])),
        ):
            result = await search("retention", settings=settings)
        assert result.grounded is False


class TestSourceFormatting:
    def test_labels_are_stable_and_one_based(self):
        block = format_sources_block([_citation(0.9, 1), _citation(0.8, 2)])
        assert "[S1]" in block and "[S2]" in block
        assert "[S0]" not in block

    def test_label_carries_attribution_and_timestamp(self):
        block = format_sources_block([_citation(0.9)])
        assert "Brian Balfour" in block
        assert "2:35" in block

    def test_empty_input_produces_empty_block(self):
        assert format_sources_block([]) == ""

    def test_citation_timestamp_formatting(self):
        assert _citation(0.9).timestamp == "2:35"
        c = _citation(0.9)
        c.start_seconds = 5352
        assert c.timestamp == "1:29:12"


class TestRetrievalResult:
    def test_serialises_for_the_api(self):
        result = RetrievalResult([_citation(0.9)], grounded=True, strategy="vector", best_score=0.9)
        payload = result.to_dict()
        assert payload["grounded"] is True
        assert payload["citations"][0]["guest"] == "Brian Balfour"
        assert "timestamp" in payload["citations"][0]


# --------------------------------------------------------------------------
# Ship 30 validator
# --------------------------------------------------------------------------


def _essay(words_per_section: int = 230, sections: int = 5, citations: int = 3) -> str:
    parts = [
        "# How to price a B2B SaaS product without guessing",
        "",
        "Most pricing decisions are made in a spreadsheet nobody revisits.",
        "",
    ]
    for i in range(sections):
        heading = "Your takeaway: what to do tomorrow" if i == sections - 1 else f"Section heading {i}"
        body = " ".join(f"word{j}" for j in range(words_per_section - 20))
        label = f"[S{(i % citations) + 1}]"
        parts += [
            f"## {heading}",
            "",
            "One sentence opens this section.",
            "",
            f"{body} {label}.",
            "",
            "- A bulleted item",
            "- Another item",
            "",
            "One sentence closes it.",
            "",
        ]
    return "\n".join(parts)


class TestShip30Validator:
    def test_a_conforming_essay_passes(self):
        report = validate(_essay())
        failed = [c.name for c in report.checks if not c.passed]
        assert failed == [], f"unexpected failures: {failed}"

    def test_word_count_is_enforced(self):
        report = validate(_essay(words_per_section=40))
        assert not report.passed
        assert any(c.name == "word count" and not c.passed for c in report.checks)

    def test_section_count_is_enforced(self):
        report = validate(_essay(sections=2, words_per_section=600))
        assert any(c.name == "section count" and not c.passed for c in report.checks)

    def test_missing_citations_are_caught(self):
        essay = _essay().replace("[S1]", "").replace("[S2]", "").replace("[S3]", "")
        report = validate(essay)
        assert any(c.name == "citations" and not c.passed for c in report.checks)

    def test_multi_sentence_hook_is_caught(self):
        essay = _essay().replace(
            "Most pricing decisions are made in a spreadsheet nobody revisits.",
            "Pricing is hard. Everyone gets it wrong. Here is why.",
        )
        report = validate(essay)
        assert any(c.name == "single-sentence hook" and not c.passed for c in report.checks)

    def test_banned_phrases_are_caught(self):
        report = validate(_essay() + "\n\nLet us delve into the tapestry of growth.\n")
        assert any(c.name == "voice" and not c.passed for c in report.checks)

    def test_sources_section_does_not_pad_the_word_count(self):
        """Otherwise a long citation list could fake a compliant length."""
        short = _essay(words_per_section=40)
        padded = short + "\n\n## Sources\n\n" + "\n".join(f"- **[S{i}]** " + "x " * 200 for i in range(6))
        assert validate(short).word_count == validate(padded).word_count

    def test_report_serialises(self):
        payload = validate(_essay()).to_dict()
        assert set(payload) >= {"passed", "score", "word_count", "section_count", "checks"}
        assert isinstance(payload["checks"], list)

    def test_summary_line_is_human_readable(self):
        line = validate(_essay()).summary_line()
        assert "words" in line and "citations" in line


# --------------------------------------------------------------------------
# Provider configuration
# --------------------------------------------------------------------------


class TestProviderRegistry:
    def test_ollama_needs_no_key(self):
        provider = build_provider("ollama", Settings())
        assert provider.info.requires_key is False
        assert provider.info.configured is True

    def test_azure_is_unconfigured_without_credentials(self):
        provider = build_provider("azure", Settings(azure_openai_endpoint="", azure_openai_api_key=""))
        assert provider.info.configured is False

    def test_azure_url_layout_uses_deployment_and_api_version(self):
        provider = build_provider(
            "azure",
            Settings(
                azure_openai_endpoint="https://example.openai.azure.com",
                azure_openai_api_key="secret",
                azure_openai_chat_deployment="gpt-4o-mini",
                azure_openai_api_version="2024-10-21",
            ),
        )
        url = provider._chat_url()
        assert "/openai/deployments/gpt-4o-mini/chat/completions" in url
        assert "api-version=2024-10-21" in url

    def test_azure_sends_key_in_its_own_header(self):
        provider = build_provider(
            "azure",
            Settings(azure_openai_endpoint="https://x.openai.azure.com", azure_openai_api_key="secret"),
        )
        headers = provider._headers()
        assert headers["api-key"] == "secret"
        assert "Authorization" not in headers

    def test_config_never_leaks_secrets(self):
        payload = describe_configuration(
            Settings(azure_openai_api_key="super-secret-value", openai_compat_api_key="another-secret")
        )
        assert "super-secret-value" not in str(payload)
        assert "another-secret" not in str(payload)

    def test_blank_fallback_is_treated_as_unset(self):
        """`LLM_FALLBACK_PROVIDER=` in .env is how people disable fallback."""
        assert Settings(llm_fallback_provider="").llm_fallback_provider is None

    def test_essay_provider_defaults_to_the_chat_provider(self):
        assert Settings(llm_provider="ollama").effective_essay_provider == "ollama"
        assert Settings(llm_provider="ollama", essay_provider="azure").effective_essay_provider == "azure"
