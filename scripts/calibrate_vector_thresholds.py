"""
scripts/calibrate_vector_thresholds.py

Empirically calibrate the VectorChecker similarity thresholds against
documents with hand-annotated ground truth for which concepts ARE / ARE NOT
present.

Ground truth (provided):
  • High court bid document   — HAS: pbg, emd, perf-sec    | MISSING: integrity-pact
  • Vizag UGSS Volume III     — HAS: pbg@2.5%, perf-sec    | MISSING: integrity-pact
  • Judicial Academy bid      — HAS: emd, perf-sec, pvc    | MISSING: integrity-pact

For each concept × document we record the max_similarity from VectorChecker,
bucket scores into PRESENT vs ABSENT, then compute a per-concept threshold:

  threshold = midpoint( min_present, max_absent )    if no overlap
  threshold = min_present - 0.02 (lower bias)        if ranges overlap
                                                     ↑ per user instruction:
                                                       "missing real violations
                                                       is worse than false
                                                       positives" — bias toward
                                                       lower thresholds so
                                                       present concepts always
                                                       register as PRESENT.

Concepts with no PRESENT or no ABSENT data inherit a fall-back threshold
derived from the average of calibrated peer concepts.

Outputs:
  • console table (concept | old | new | present scores | absent scores)
  • data/vector_concepts_calibrated.json (used by VectorChecker on next load)
"""
from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from pathlib import Path

from modules.validator.vector_checker import VectorChecker, CONCEPTS, Concept


REPO = Path(__file__).resolve().parent.parent
MD = REPO / "source_documents" / "e_procurement" / "processed_md"
OUT = REPO / "data" / "vector_concepts_calibrated.json"


# ─── Ground truth ────────────────────────────────────────────────────────────

# Concept-IDs are those defined in modules/validator/vector_checker.py CONCEPTS.
# 'pbg' from spec maps to 'performance-security' in our concept catalogue.
GROUND_TRUTH: dict[str, dict[str, list[str]]] = {
    "High court  bid document.md": {
        "present": ["performance-security", "earnest-money"],
        "absent":  ["integrity-pact"],
    },
    "3 Volume_III _GCC,_SCC.md": {
        "present": ["performance-security"],
        "absent":  ["integrity-pact"],
    },
    "Bid Document of Judicial Academy.md": {
        "present": ["performance-security", "earnest-money",
                    "price-variation-clause"],
        "absent":  ["integrity-pact"],
    },
}

# Estimated values used purely so the mandatory-when checks fire correctly;
# they don't affect the similarity scores we're calibrating against.
DOC_VALUES: dict[str, float] = {
    "High court  bid document.md":          350_00_00_000,
    "3 Volume_III _GCC,_SCC.md":            350_00_00_000,
    "Bid Document of Judicial Academy.md":  150_00_00_000,
}


# ─── Calibration ────────────────────────────────────────────────────────────

def _compute_threshold(present: list[float], absent: list[float],
                       fallback: float = 0.55) -> tuple[float, str]:
    """Return (threshold, derivation_note)."""
    if present and absent:
        min_p, max_a = min(present), max(absent)
        if max_a < min_p:
            return round((min_p + max_a) / 2, 4), f"midpoint({min_p:.4f}, {max_a:.4f}) — clear gap"
        # Overlap: lower-bias threshold so all present examples pass
        thr = max(0.50, round(min_p - 0.02, 4))
        return thr, f"min_present−0.02 ({min_p:.4f}) — ranges overlap, lower-bias"
    if present and not absent:
        thr = max(0.50, round(min(present) - 0.02, 4))
        return thr, f"min_present−0.02 ({min(present):.4f}) — no absent examples"
    if absent and not present:
        thr = round(max(absent) + 0.02, 4)
        return thr, f"max_absent+0.02 ({max(absent):.4f}) — no present examples"
    return fallback, "fallback (no calibration data)"


# ─── Run ────────────────────────────────────────────────────────────────────

def main() -> int:
    print("Loading VectorChecker (downloads BGE-M3 if needed)…")
    t0 = time.perf_counter()
    vc = VectorChecker()
    print(f"  init: {(time.perf_counter()-t0):.1f}s   model dim={vc.dim}\n")

    # concept_scores[concept_id][doc_name] = max_similarity
    raw_scores: dict[str, dict[str, float]] = {c.concept_id: {} for c in CONCEPTS}

    for doc_name in GROUND_TRUTH:
        path = MD / doc_name
        text = path.read_text()
        print(f">>> Running on: {doc_name}  ({len(text):,} chars)")
        t0 = time.perf_counter()
        out = vc.check_document(
            document_text=text,
            source_file=doc_name,
            is_ap_tender=True,
            estimated_value=DOC_VALUES[doc_name],
            duration_months=24,
        )
        print(f"    {len(out['sections'])} sections, {(time.perf_counter()-t0):.1f}s")
        for r in out["concept_results"]:
            raw_scores[r.concept_id][doc_name] = r.max_similarity

    # Bucket scores by present/absent according to GT
    by_concept: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: {"present": [], "absent": []}
    )
    for doc_name, gt in GROUND_TRUTH.items():
        for cid in gt.get("present", []):
            by_concept[cid]["present"].append(raw_scores[cid][doc_name])
        for cid in gt.get("absent", []):
            by_concept[cid]["absent"].append(raw_scores[cid][doc_name])

    # Compute calibrated thresholds
    calibrated: list[dict] = []
    fallback_default = 0.55
    for c in CONCEPTS:
        present_scores = by_concept[c.concept_id]["present"]
        absent_scores  = by_concept[c.concept_id]["absent"]
        new_thr, note = _compute_threshold(present_scores, absent_scores, fallback_default)
        calibrated.append({
            "concept_id":    c.concept_id,
            "old_threshold": c.threshold,
            "new_threshold": new_thr,
            "derivation":    note,
            "present_scores": [round(s, 4) for s in present_scores],
            "absent_scores":  [round(s, 4) for s in absent_scores],
            "all_scores":    {d: round(raw_scores[c.concept_id].get(d, -1), 4)
                              for d in GROUND_TRUTH},
        })

    # Use the AVERAGE of calibrated peer thresholds as the fallback for
    # concepts that have no GT data (anti-collusion, judicial-preview, reverse-
    # tendering, mobilisation-advance).
    has_data = [r["new_threshold"] for r in calibrated
                if r["present_scores"] or r["absent_scores"]]
    if has_data:
        peer_avg = round(sum(has_data) / len(has_data), 4)
        for r in calibrated:
            if not r["present_scores"] and not r["absent_scores"]:
                r["new_threshold"] = peer_avg
                r["derivation"]    = f"peer-average ({peer_avg}) — no GT for this concept"

    # ── Save calibrated catalogue ──
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "ground_truth":    GROUND_TRUTH,
        "raw_max_scores":  raw_scores,
        "calibration":     calibrated,
    }, indent=2, ensure_ascii=False))
    print(f"\nCalibrated catalogue saved to: {OUT.relative_to(REPO)}\n")

    # ── Print table ──
    print("=" * 130)
    print(f"{'concept_id':<25}{'old':>7}{'new':>7}  {'present_scores':<26}{'absent_scores':<22}{'derivation':<40}")
    print("-" * 130)
    for r in calibrated:
        ps = ",".join(f"{s:.3f}" for s in r["present_scores"]) or "—"
        as_ = ",".join(f"{s:.3f}" for s in r["absent_scores"]) or "—"
        print(f"{r['concept_id']:<25}"
              f"{r['old_threshold']:>7.2f}"
              f"{r['new_threshold']:>7.4f}  "
              f"{ps:<26}{as_:<22}{r['derivation']:<40}")
    print("=" * 130)

    return 0


if __name__ == "__main__":
    sys.exit(main())
