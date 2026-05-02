"""Run High Court doc twice through VectorChecker to verify caching."""
from __future__ import annotations

import time
from pathlib import Path

from modules.validator.vector_checker import VectorChecker


REPO = Path(__file__).resolve().parent.parent
DOC  = REPO / "source_documents" / "e_procurement" / "processed_md" / "High court  bid document.md"


def _show(label: str, out: dict, wall_ms: float):
    t = out["timing_ms"]
    print(f"\n--- {label} ---")
    print(f"doc_id:    {out['doc_id']}")
    print(f"cache_hit: {t['cache_hit']}")
    print(f"sections:  {len(out['sections'])}")
    print(f"timings:")
    print(f"  chunk_ms          = {t['chunk_ms']}")
    print(f"  embed_sections_ms = {t['embed_sections_ms']}")
    print(f"  upsert_ms         = {t['upsert_ms']}")
    print(f"  query_ms          = {t['query_ms']}")
    print(f"WALL: {wall_ms:.0f} ms")
    print(f"findings: {len(out['findings'])}  ({', '.join(f.concept_id for f in out['findings']) or 'none'})")


def main() -> int:
    text = DOC.read_text()
    print(f"document: {DOC.name}  ({len(text):,} chars)")
    print(f"shared collection: tender_sections")
    print()

    print("Initialising VectorChecker (loads BGE-M3)…")
    t0 = time.perf_counter()
    vc = VectorChecker()
    print(f"  init: {(time.perf_counter()-t0):.1f}s")

    # Wipe any leftover points for this doc so we test from a clean state
    from qdrant_client.http import models as qm
    doc_id = vc._doc_id(text)
    vc.client.delete(
        collection_name=vc.SHARED_COLLECTION,
        points_selector=qm.FilterSelector(filter=qm.Filter(must=[
            qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id)),
        ])),
    )
    print(f"  wiped any pre-existing points for doc_id={doc_id}\n")

    # ── First run: cache MISS ──
    t0 = time.perf_counter()
    out1 = vc.check_document(
        document_text=text, source_file=DOC.name,
        is_ap_tender=True, estimated_value=350_00_00_000, duration_months=24,
    )
    wall1 = (time.perf_counter() - t0) * 1000
    _show("RUN 1 (cache MISS, full embedding)", out1, wall1)

    # ── Second run: cache HIT ──
    t0 = time.perf_counter()
    out2 = vc.check_document(
        document_text=text, source_file=DOC.name,
        is_ap_tender=True, estimated_value=350_00_00_000, duration_months=24,
    )
    wall2 = (time.perf_counter() - t0) * 1000
    _show("RUN 2 (cache HIT, query only)", out2, wall2)

    print(f"\n=== Speedup ===")
    print(f"  RUN 1 (full):    {wall1/1000:.1f} s")
    print(f"  RUN 2 (cached):  {wall2/1000:.2f} s")
    print(f"  speedup:         {wall1/max(wall2,1):.0f}×")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
