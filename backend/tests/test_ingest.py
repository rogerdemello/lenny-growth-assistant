"""Parser and chunker.

These are the tests that matter most for grounding quality: if the parser
mangles a timestamp, every citation built on it points at the wrong moment in
the video, and the product's central promise quietly breaks. The parser is also
the piece most exposed to upstream change, since the transcripts are a
third-party repository we do not control.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.ingest.chunker import chunk_turns, estimate_tokens
from app.ingest.parser import Turn, parse_episode, parse_frontmatter, timestamp_to_seconds
from app.ingest.pipeline import select_episodes
from app.ingest.source import CorpusPolicy

FIXTURE = Path(__file__).parent / "fixtures" / "sample_transcript.md"


@pytest.fixture(scope="module")
def episode():
    return parse_episode("brian-balfour", FIXTURE.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# Frontmatter
# --------------------------------------------------------------------------


class TestFrontmatter:
    def test_extracts_core_metadata(self, episode):
        assert episode.guest == "Brian Balfour"
        assert episode.video_id == "cX4cL6B-_aU"
        assert episode.publish_date == date(2025, 8, 17)
        assert episode.duration_seconds == 5352.0
        assert episode.view_count == 38284
        assert "growth" in episode.keywords

    def test_wrapped_title_is_rejoined(self, episode):
        """PyYAML wraps long titles across lines; a raw newline would reach the UI."""
        assert "\n" not in episode.title
        assert episode.title.endswith("| Brian Balfour")
        assert "capitalize on it" in episode.title

    def test_multiline_description_does_not_break_parsing(self, episode):
        # The description uses a quoted block with a blank line inside it. If
        # that broke the YAML parse we would lose every other field too.
        assert episode.guest and episode.video_id

    def test_missing_frontmatter_returns_body_unchanged(self):
        meta, body = parse_frontmatter("# Just a heading\n\nSome text.")
        assert meta == {}
        assert body.startswith("# Just a heading")

    def test_malformed_yaml_does_not_raise(self):
        meta, body = parse_frontmatter("---\nguest: [unclosed\n---\nbody text\n")
        assert isinstance(meta, dict)
        assert "body text" in body


# --------------------------------------------------------------------------
# Turns and timestamps
# --------------------------------------------------------------------------


class TestTurns:
    def test_parses_every_speaker_turn(self, episode):
        assert len(episode.turns) == 6
        assert {t.speaker for t in episode.turns} == {"Lenny Rachitsky", "Brian Balfour"}

    def test_timestamps_convert_to_seconds(self):
        assert timestamp_to_seconds("00:00:00") == 0
        assert timestamp_to_seconds("00:02:35") == 155
        assert timestamp_to_seconds("01:29:12") == 5352

    def test_turn_start_times_are_chronological(self, episode):
        starts = [t.start_seconds for t in episode.turns]
        assert starts == sorted(starts)
        assert starts[0] == 0
        assert starts[-1] == 155

    def test_inaudible_markers_are_stripped(self, episode):
        combined = " ".join(t.text for t in episode.turns)
        assert "[inaudible" not in combined
        # The surrounding sentence must survive intact.
        assert "distribution platform are essentially happening" in combined

    def test_preamble_before_first_speaker_is_dropped(self, episode):
        assert not any("## Transcript" in t.text for t in episode.turns)
        assert not any(t.text.startswith("# Why ChatGPT") for t in episode.turns)


class TestEpisodeHelpers:
    def test_youtube_deep_link_targets_the_second(self, episode):
        assert episode.youtube_link_at(155) == "https://www.youtube.com/watch?v=cX4cL6B-_aU&t=155s"

    def test_negative_timestamps_clamp_to_zero(self, episode):
        assert episode.youtube_link_at(-5).endswith("t=0s")

    def test_full_episode_is_not_a_short(self, episode):
        assert episode.is_short is False

    def test_short_clip_is_detected(self):
        raw = FIXTURE.read_text(encoding="utf-8").replace("duration_seconds: 5352.0", "duration_seconds: 230.0")
        assert parse_episode("clip", raw).is_short is True

    def test_content_hash_changes_with_content(self, episode):
        other = parse_episode("brian-balfour", FIXTURE.read_text(encoding="utf-8") + "\nextra\n")
        assert episode.content_hash != other.content_hash


# --------------------------------------------------------------------------
# Chunking
# --------------------------------------------------------------------------


def _turns(count: int, words: int = 40) -> list[Turn]:
    """Synthetic turns.

    Each turn's text is unique. Identical filler text would make legitimately
    distinct chunks compare equal and produce false duplicate-detection
    failures.
    """
    return [
        Turn(
            speaker=f"Speaker {i % 2}",
            start_seconds=i * 30,
            text=f"turn{i} " + " ".join(f"w{i}x{j}" for j in range(words)),
        )
        for i in range(count)
    ]


class TestChunker:
    def test_empty_input_yields_nothing(self):
        assert chunk_turns([]) == []

    def test_never_splits_a_speaker_turn(self):
        """The attribution rule: half a turn cannot be honestly cited."""
        turns = _turns(30, words=60)
        chunks = chunk_turns(turns, target_tokens=700, overlap_tokens=100)
        for chunk in chunks:
            for line in chunk.text.split("\n\n"):
                assert line.startswith("Speaker "), "a turn was split mid-way"

    def test_respects_the_token_budget(self):
        chunks = chunk_turns(_turns(40, words=50), target_tokens=700, overlap_tokens=100)
        # Only oversized single turns may exceed the target.
        assert all(c.token_count <= 700 * 1.5 for c in chunks)

    def test_oversized_turn_becomes_its_own_chunk(self):
        long_turn = Turn(speaker="Monologue", start_seconds=10, text=" ".join(["word"] * 900))
        chunks = chunk_turns([long_turn], target_tokens=300, overlap_tokens=50)
        assert len(chunks) == 1
        assert chunks[0].token_count > 300

    def test_timestamps_survive_chunking(self):
        turns = _turns(20)
        chunks = chunk_turns(turns, target_tokens=400, overlap_tokens=50)
        assert all(c.start_seconds <= c.end_seconds for c in chunks)
        starts = [c.start_seconds for c in chunks]
        assert starts == sorted(starts)
        assert chunks[0].start_seconds == 0

    def test_speaker_is_carried_into_the_chunk(self):
        chunks = chunk_turns(_turns(10), target_tokens=400, overlap_tokens=50)
        assert all(c.speaker for c in chunks)
        assert all(c.speaker in c.text for c in chunks)

    def test_ordinals_are_contiguous(self):
        chunks = chunk_turns(_turns(25), target_tokens=400, overlap_tokens=60)
        assert [c.ord for c in chunks] == list(range(len(chunks)))

    def test_no_duplicate_chunks(self):
        """Overlap seeds the next window, which can emit the same chunk twice."""
        chunks = chunk_turns(_turns(12, words=100), target_tokens=500, overlap_tokens=200)
        texts = [c.text for c in chunks]
        assert len(texts) == len(set(texts))

    def test_overlap_repeats_content_between_neighbours(self):
        chunks = chunk_turns(_turns(24, words=45), target_tokens=400, overlap_tokens=120)
        assert len(chunks) >= 2
        # Consecutive chunks should share at least one turn.
        first = set(chunks[0].text.split("\n\n"))
        second = set(chunks[1].text.split("\n\n"))
        assert first & second

    def test_real_episode_chunks_cleanly(self, episode):
        chunks = chunk_turns(episode.turns, target_tokens=200, overlap_tokens=40)
        assert len(chunks) >= 2
        assert all(c.text.strip() for c in chunks)

    def test_token_estimate_scales_with_length(self):
        assert estimate_tokens("a" * 400) > estimate_tokens("a" * 40)
        assert estimate_tokens("") >= 1


# --------------------------------------------------------------------------
# Corpus selection
# --------------------------------------------------------------------------


class TestSelection:
    def _episode(self, slug: str, duration: float, published: date | None = None):
        ep = parse_episode(slug, FIXTURE.read_text(encoding="utf-8"))
        ep.duration_seconds = duration
        ep.publish_date = published or date(2024, 1, 1)
        return ep

    def test_shorts_are_excluded(self):
        episodes = [self._episode("short", 200.0), self._episode("full", 4000.0)]
        selected = select_episodes(
            episodes, CorpusPolicy([], set()), min_duration=1800, max_episodes=10
        )
        assert [e.slug for e in selected] == ["full"]

    def test_excluded_slugs_are_dropped(self):
        episodes = [self._episode("eoy-review", 4000.0), self._episode("real", 4000.0)]
        selected = select_episodes(
            episodes, CorpusPolicy([], {"eoy-review"}), min_duration=1800, max_episodes=10
        )
        assert [e.slug for e in selected] == ["real"]

    def test_pinned_episodes_survive_the_cap(self):
        episodes = [self._episode(f"ep{i}", 4000.0, date(2025, 1, i + 1)) for i in range(10)]
        episodes.append(self._episode("pinned-guest", 4000.0, date(2019, 1, 1)))
        selected = select_episodes(
            episodes, CorpusPolicy(["pinned-guest"], set()), min_duration=1800, max_episodes=3
        )
        assert "pinned-guest" in [e.slug for e in selected]
        assert len(selected) == 3

    def test_remainder_fills_by_recency(self):
        episodes = [
            self._episode("old", 4000.0, date(2020, 1, 1)),
            self._episode("new", 4000.0, date(2025, 6, 1)),
        ]
        selected = select_episodes(
            episodes, CorpusPolicy([], set()), min_duration=1800, max_episodes=1
        )
        assert [e.slug for e in selected] == ["new"]

    def test_missing_pin_is_skipped_not_fatal(self):
        episodes = [self._episode("real", 4000.0)]
        selected = select_episodes(
            episodes, CorpusPolicy(["does-not-exist"], set()), min_duration=1800, max_episodes=5
        )
        assert [e.slug for e in selected] == ["real"]

    def test_no_cap_returns_everything_eligible(self):
        episodes = [self._episode(f"ep{i}", 4000.0) for i in range(7)]
        selected = select_episodes(episodes, CorpusPolicy([], set()), min_duration=1800, max_episodes=None)
        assert len(selected) == 7
