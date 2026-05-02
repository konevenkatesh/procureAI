"""scripts/test_vector_checker.py — sanity-run for VectorChecker."""
from __future__ import annotations

import json
import time
from pathlib import Path

from modules.validator.vector_checker import VectorChecker, CONCEPTS


REPO = Path(__file__).resolve().parent.parent
DOC  = REPO / "source_documents" / "e_procurement" / "processed_md" / "High court  bid document.md"


def main() -> int:
    text = DOC.read_text()
    print(f"=== Input: {DOC.name} ({len(text):,} chars) ===\n")

    print("Loading VectorChecker (downloads BGE-M3 first time, ~2 GB)…")
    t0 = time.perf_counter()
    vc = VectorChecker()
    print(f"  init: {(time.perf_counter()-t0)*1000:.0f} ms (model dim={vc.dim})\n")

    t0 = time.perf_counter()
    out = vc.check_document(
        document_text=text,
        source_file=DOC.name,
        is_ap_tender=True,
        estimated_value=350_00_00_000,
        duration_months=24,
    )
    wall_ms = (time.perf_counter() - t0) * 1000
    timings = out["timing_ms"]

    print(f"=== Run summary ===")
    print(f"  WALL: {wall_ms:.0f} ms")
    print(f"  chunk={timings['chunk_ms']} ms  embed_sections={timings['embed_sections_ms']} ms  "
          f"upsert={timings['upsert_ms']} ms  query={timings['query_ms']} ms")
    print(f"  sections produced: {len(out['sections'])}")
    print()

    print("=== First 3 sections + SAC summaries ===")
    for i, (sec, sac) in enumerate(zip(out["section_objects"][:3], out["sacs"][:3]), 1):
        print(f"--- Section {i}: '{sec.heading[:70]}…'  ({sec.word_count} words, char {sec.char_start}) ---")
        print(f"SAC: {sac}")
        print()

    print("=== integrity-pact concept — top 3 sections ===")
    ip_result = next(r for r in out["concept_results"] if r.concept_id == "integrity-pact")
    print(f"max_similarity={ip_result.max_similarity}  threshold={ip_result.threshold}  "
          f"present={ip_result.present}  mandatory={ip_result.mandatory}")
    for i, m in enumerate(ip_result.top_matches, 1):
        print(f"  #{i} score={m['score']:.4f}  heading: {m['heading'][:70]}")
        print(f"      snippet: {m['snippet'][:150]}…")
    print()

    print("=== ALL concept results (present / absent) ===")
    print(f"{'concept_id':<26}{'sev':<11}{'mandatory':<10}{'max_sim':>8}{'thresh':>8}  PRESENT?")
    for r in out["concept_results"]:
        marker = "✓ PRESENT" if r.present else ("✗ ABSENT" if r.mandatory else "  absent (not mandatory)")
        print(f"{r.concept_id:<26}{r.severity:<11}{str(r.mandatory):<10}"
              f"{r.max_similarity:>8.4f}{r.threshold:>8.2f}  {marker}")
    print()

    print(f"=== FINDINGS ({len(out['findings'])}) — mandatory concepts NOT present ===")
    for f in out["findings"]:
        print(f"  • {f.concept_id} ({f.severity})  max_sim={f.max_similarity:.4f}  threshold={f.threshold}")
        for m in f.top_matches:
            print(f"    closest:  score={m['score']:.4f}  '{m['heading'][:60]}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
