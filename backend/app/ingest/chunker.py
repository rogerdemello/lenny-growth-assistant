"""Turn transcripts into retrievable chunks.

The governing constraint is citation quality, not retrieval scores. A chunk
must be attributable to a speaker at a timestamp, because the product promise
is "here is where Lenny's guest actually said this" with a link that lands on
the right second of the video.

That leads to one firm rule: **never split a speaker turn across chunks.** A
half-turn cannot be honestly attributed, and podcast turns are conversational
enough that the token budget is a soft target rather than a hard one.

Token counting is a `len(text) / 4` approximation rather than a real tokenizer.
Bringing in tiktoken for a chunk-size heuristic would add a dependency and a
model-specific vocabulary to a decision that only needs to be roughly right.
The cost of being 15% off is a slightly larger prompt, not a wrong answer.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.ingest.parser import Episode, Turn

CHARS_PER_TOKEN = 4


@dataclass(slots=True)
class Chunk:
    ord: int
    speaker: str
    start_seconds: int
    end_seconds: int
    text: str
    token_count: int


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


def _render(turn: Turn) -> str:
    """Keep the speaker inline.

    The embedding then carries who is talking, which matters when a question
    names a guest — and it means the retrieved text is readable on its own in
    the citation panel.
    """
    return f"{turn.speaker}: {turn.text}"


def chunk_turns(
    turns: list[Turn],
    *,
    target_tokens: int = 700,
    overlap_tokens: int = 100,
) -> list[Chunk]:
    if not turns:
        return []

    chunks: list[Chunk] = []
    window: list[Turn] = []
    window_tokens = 0

    def flush() -> None:
        nonlocal window, window_tokens
        if not window:
            return
        text = "\n\n".join(_render(t) for t in window)
        chunks.append(
            Chunk(
                ord=len(chunks),
                # The first speaker in the window is the one the citation names.
                speaker=window[0].speaker,
                start_seconds=window[0].start_seconds,
                # The last turn's start is the best end estimate available;
                # transcripts carry no turn durations.
                end_seconds=window[-1].start_seconds,
                text=text,
                token_count=estimate_tokens(text),
            )
        )
        # Carry the tail of this window into the next one so a thought that
        # straddles the boundary is retrievable from either side.
        carry: list[Turn] = []
        carried = 0
        for turn in reversed(window):
            cost = estimate_tokens(_render(turn))
            if carried + cost > overlap_tokens:
                break
            carry.insert(0, turn)
            carried += cost
        # A single turn larger than the overlap budget would otherwise repeat
        # in full and stall progress.
        if len(carry) == len(window):
            carry = carry[1:] if len(carry) > 1 else []
        window = list(carry)
        window_tokens = sum(estimate_tokens(_render(t)) for t in window)

    for turn in turns:
        cost = estimate_tokens(_render(turn))

        # An oversized single turn becomes its own chunk. Splitting it would
        # break the attribution rule; monologues on this podcast run long.
        if cost >= target_tokens:
            flush()
            text = _render(turn)
            chunks.append(
                Chunk(
                    ord=len(chunks),
                    speaker=turn.speaker,
                    start_seconds=turn.start_seconds,
                    end_seconds=turn.start_seconds,
                    text=text,
                    token_count=estimate_tokens(text),
                )
            )
            window, window_tokens = [], 0
            continue

        if window_tokens + cost > target_tokens:
            flush()

        window.append(turn)
        window_tokens += cost

    flush()

    # `flush` seeds the next window from the overlap, so a trailing flush with
    # nothing new can duplicate the previous chunk verbatim.
    if len(chunks) >= 2 and chunks[-1].text == chunks[-2].text:
        chunks.pop()

    for i, chunk in enumerate(chunks):
        chunk.ord = i
    return chunks


def chunk_episode(episode: Episode, *, target_tokens: int = 700, overlap_tokens: int = 100) -> list[Chunk]:
    return chunk_turns(episode.turns, target_tokens=target_tokens, overlap_tokens=overlap_tokens)
