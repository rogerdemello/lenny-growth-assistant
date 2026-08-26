"""Calibrate RETRIEVAL_SCORE_FLOOR against the ingested corpus.

    python -m app.rag.calibrate

The score floor is the single control that enforces the product's grounding
guarantee: below it, the assistant is handed no sources and must refuse. Set it
too low and out-of-domain questions get answered from the model's own knowledge
while still looking grounded. Set it too high and legitimate questions get
refused.

**The correct value is model-specific and cannot be guessed.** A plausible
initial value of 0.35 let "best kubernetes ingress controller" through at 0.44 —
scoring *higher* than a legitimate in-domain question about retention. That is
not a tuning nit; it is the grounding guarantee silently failing.

So this measures instead. It runs two fixed query sets against the live corpus
and reports the gap between the worst in-domain score and the best
out-of-domain one. A floor has to sit inside that gap. If the gap is negative,
no floor works and the problem is upstream — wrong embedding model, missing
task prefixes, or a corpus that genuinely covers the "out-of-domain" topic.

Re-run this whenever EMBED_MODEL, the prefixes, or the corpus changes.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.pool import close_pool, init_pool
from app.rag.retrieval import vector_search

# Questions the corpus SHOULD answer. Drawn from the pinned episodes in
# corpus.yml, so they stay valid as long as those pins do.
IN_DOMAIN = [
    "how should I think about pricing a B2B SaaS product",
    "what actually drives retention in the early days",
    "how do I know if I have product-market fit",
    "what is product-led growth and when does it work",
    "how do I position a product against an incumbent",
    "what makes a good growth model",
    "how should a PM prioritise a roadmap",
    "how do I run continuous product discovery",
    "when should I hire a growth team",
    "how do I think about activation and onboarding",
]

# Questions the corpus should REFUSE. Deliberately varied: adjacent-technical,
# general knowledge, everyday, and plausible-but-absent business topics.
OUT_OF_DOMAIN = [
    "best kubernetes ingress controller",
    "what is the capital of France",
    "how do I fix a leaking kitchen tap",
    "explain the python asyncio event loop",
    "what is the offside rule in football",
    "how do I bake sourdough bread",
    "what are the tax implications of an ISA",
    "how does photosynthesis work",
    "write me a bash script to rotate logs",
    "what is the weather forecast for tomorrow",
]


@dataclass
class Probe:
    query: str
    in_domain: bool
    best_score: float


async def probe_all(top_k: int = 8) -> list[Probe]:
    settings = get_settings()
    results: list[Probe] = []

    for query, in_domain in [(q, True) for q in IN_DOMAIN] + [(q, False) for q in OUT_OF_DOMAIN]:
        citations = await vector_search(query, top_k=top_k, settings=settings)
        best = citations[0].score if citations else 0.0
        results.append(Probe(query=query, in_domain=in_domain, best_score=best))

    return results


def report(probes: list[Probe], current_floor: float) -> int:
    in_scores = sorted(p.best_score for p in probes if p.in_domain)
    out_scores = sorted((p.best_score for p in probes if not p.in_domain), reverse=True)

    print("\nIN-DOMAIN (should be answered)")
    for p in sorted((p for p in probes if p.in_domain), key=lambda p: p.best_score):
        print(f"  {p.best_score:.4f}  {p.query}")

    print("\nOUT-OF-DOMAIN (should be refused)")
    for p in sorted((p for p in probes if not p.in_domain), key=lambda p: -p.best_score):
        print(f"  {p.best_score:.4f}  {p.query}")

    worst_in, best_out = in_scores[0], out_scores[0]
    gap = worst_in - best_out

    print("\n" + "=" * 62)
    print(f"  worst in-domain    {worst_in:.4f}")
    print(f"  best out-of-domain {best_out:.4f}")
    print(f"  separation gap     {gap:+.4f}")
    print("=" * 62)

    if gap <= 0:
        print("\n  NOT SEPARABLE — no score floor can enforce the grounding guarantee.")
        print("  Likely causes, in order of likelihood:")
        print("    * the embedding model needs task prefixes and is not getting them")
        print("      (nomic/e5/bge are asymmetric — check EMBED_QUERY_PREFIX)")
        print("    * EMBED_MODEL is a poor fit for this corpus")
        print("    * an 'out-of-domain' question is genuinely covered by the corpus")
        return 1

    # Sit slightly below the midpoint. Refusing a real question is more visible
    # and more annoying to a user than occasionally retrieving weak-but-related
    # passages, and the prompt still instructs the model to say when sources do
    # not answer the question.
    recommended = round(best_out + gap * 0.4, 2)

    print(f"\n  RECOMMENDED  RETRIEVAL_SCORE_FLOOR={recommended}")
    print(f"  (any value in {best_out:.3f}–{worst_in:.3f} separates the two sets)")

    if current_floor <= best_out:
        print(f"\n  WARNING: the configured floor ({current_floor}) is at or below the best")
        print("  out-of-domain score. Out-of-domain questions will be answered as if grounded.")
        return 1
    if current_floor >= worst_in:
        print(f"\n  WARNING: the configured floor ({current_floor}) is at or above the worst")
        print("  in-domain score. Legitimate questions will be refused.")
        return 1

    print(f"\n  The configured floor ({current_floor}) is inside the gap. OK.")
    return 0


async def main_async(top_k: int) -> int:
    settings = get_settings()
    await init_pool(settings.database_url)
    try:
        print(f"embed model   : {settings.embed_model}")
        print(f"query prefix  : {settings.query_prefix!r}")
        print(f"doc prefix    : {settings.document_prefix!r}")
        print(f"current floor : {settings.retrieval_score_floor}")
        probes = await probe_all(top_k=top_k)
        return report(probes, settings.retrieval_score_floor)
    finally:
        await close_pool()


def main() -> None:
    parser = argparse.ArgumentParser(description="Calibrate the retrieval score floor.")
    parser.add_argument("--top-k", type=int, default=8)
    args = parser.parse_args()

    settings = get_settings()
    configure_logging("WARNING", settings.log_format)
    raise SystemExit(asyncio.run(main_async(args.top_k)))


if __name__ == "__main__":
    main()
