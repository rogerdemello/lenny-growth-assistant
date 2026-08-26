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
from app.rag.relevance import gate as relevance_gate
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
    # What the full two-stage pipeline decided, and which stage decided it.
    grounded: bool = False
    decided_by: str = ""


async def probe_all(top_k: int = 8, *, with_gate: bool = True) -> list[Probe]:
    settings = get_settings()
    results: list[Probe] = []

    for query, in_domain in [(q, True) for q in IN_DOMAIN] + [(q, False) for q in OUT_OF_DOMAIN]:
        citations = await vector_search(query, top_k=top_k, settings=settings)
        best = citations[0].score if citations else 0.0
        probe = Probe(query=query, in_domain=in_domain, best_score=best)

        kept = [c for c in citations if c.score >= settings.retrieval_score_floor]
        if not kept:
            probe.grounded, probe.decided_by = False, "floor"
        elif best >= settings.retrieval_confident_score:
            probe.grounded, probe.decided_by = True, "confident"
        elif not with_gate:
            probe.grounded, probe.decided_by = True, "floor (gate skipped)"
        else:
            keep, _reason = await relevance_gate(query, kept, best, settings=settings)
            probe.grounded, probe.decided_by = keep, "gate" if keep else "gate (rejected)"

        results.append(probe)

    return results


def report(probes: list[Probe], settings) -> int:  # noqa: ANN001
    in_scores = sorted(p.best_score for p in probes if p.in_domain)
    out_scores = sorted((p.best_score for p in probes if not p.in_domain), reverse=True)

    print("\nIN-DOMAIN (should be answered)")
    for p in sorted((p for p in probes if p.in_domain), key=lambda p: p.best_score):
        mark = "ok " if p.grounded else "MISS"
        print(f"  [{mark}] {p.best_score:.4f}  ({p.decided_by:<16}) {p.query}")

    print("\nOUT-OF-DOMAIN (should be refused)")
    for p in sorted((p for p in probes if not p.in_domain), key=lambda p: -p.best_score):
        mark = "LEAK" if p.grounded else "ok "
        print(f"  [{mark}] {p.best_score:.4f}  ({p.decided_by:<16}) {p.query}")

    worst_in, best_out = in_scores[0], out_scores[0]
    gap = worst_in - best_out

    print("\n" + "=" * 66)
    print("  STAGE 1 — cosine score alone")
    print(f"    worst in-domain    {worst_in:.4f}")
    print(f"    best out-of-domain {best_out:.4f}")
    print(f"    separation gap     {gap:+.4f}  " + ("(separable)" if gap > 0 else "(NOT separable)"))

    if gap <= 0:
        print("    -> A single threshold cannot enforce grounding on this corpus.")
        print("       This is why the relevance gate exists (app/rag/relevance.py).")
    else:
        print(f"    -> A floor alone would work here; anywhere in {best_out:.3f}-{worst_in:.3f}.")

    missed = [p for p in probes if p.in_domain and not p.grounded]
    leaked = [p for p in probes if not p.in_domain and p.grounded]
    total_in = sum(1 for p in probes if p.in_domain)
    total_out = sum(1 for p in probes if not p.in_domain)

    print("\n  STAGE 2 — full pipeline (floor + relevance gate)")
    print(f"    in-domain answered   {total_in - len(missed)}/{total_in}")
    print(f"    out-of-domain refused {total_out - len(leaked)}/{total_out}")
    print("=" * 66)

    if leaked:
        print("\n  FAILED: out-of-domain questions were treated as grounded:")
        for p in leaked:
            print(f"    {p.best_score:.4f} ({p.decided_by})  {p.query}")
        print("\n  This is the grounding guarantee failing. Do not ship.")
        return 1

    if missed:
        print("\n  WARNING: in-domain questions were refused:")
        for p in missed:
            print(f"    {p.best_score:.4f} ({p.decided_by})  {p.query}")
        print(f"\n  Lower RETRIEVAL_SCORE_FLOOR (currently {settings.retrieval_score_floor})")
        print("  or soften the relevance gate prompt.")
        return 1

    print("\n  PASS — every in-domain question answered, every out-of-domain question refused.")
    return 0


async def main_async(top_k: int, with_gate: bool) -> int:
    settings = get_settings()
    await init_pool(settings.database_url)
    try:
        print(f"embed model      : {settings.embed_model}")
        print(f"query prefix     : {settings.query_prefix!r}")
        print(f"doc prefix       : {settings.document_prefix!r}")
        print(f"score floor      : {settings.retrieval_score_floor}")
        print(f"confident score  : {settings.retrieval_confident_score}")
        print(f"relevance gate   : {'on' if with_gate else 'OFF'}")
        probes = await probe_all(top_k=top_k, with_gate=with_gate)
        return report(probes, settings)
    finally:
        await close_pool()


def main() -> None:
    parser = argparse.ArgumentParser(description="Calibrate retrieval grounding thresholds.")
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument(
        "--no-gate",
        action="store_true",
        help="Measure the score floor in isolation, without the relevance gate.",
    )
    args = parser.parse_args()

    settings = get_settings()
    configure_logging("WARNING", settings.log_format)
    raise SystemExit(asyncio.run(main_async(args.top_k, with_gate=not args.no_gate)))


if __name__ == "__main__":
    main()
