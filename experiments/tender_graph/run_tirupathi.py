"""
experiments/tender_graph/run_tirupathi.py

Generalisation test — run kg_builder on RFP_Tirupathi (most-different
document from Vizag UGSS) and compare side by side.

Why this matters:
    The Vizag state machine hardcodes filename patterns ("Volume_I_",
    "Volume_III") and Roman-numeral parent markers tuned to a 5-volume
    EPC tender. RFP_Tirupathi is a single-file PPP/DBFOT RFP from a
    different department for a 22-year concession. Same AP-State
    context, everything else different. If kg_builder's section
    classifier and DRAFTING_CLAUSE matcher generalise, defeasibility
    behaviour should still fire and the section_type distribution
    should still span the taxonomy. If it doesn't, we'll see it as a
    flood of "Other"-typed sections and a Cartesian-explosion regrowth.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from kg_builder import build_kg
from _common import rest_select


TIRUPATHI_DOC_ID = "tirupathi_wte_exp_001"
TIRUPATHI_SOURCE = (
    REPO / "source_documents" / "e_procurement" / "processed_md"
        / "RFP_Tirupathi_NITI_01042026.md"
)

VIZAG_DOC_ID = "vizag_ugss_exp_001"


def _section_dist(doc_id: str) -> dict[str, int]:
    rows = rest_select("kg_nodes", params={
        "select":    "properties",
        "doc_id":    f"eq.{doc_id}",
        "node_type": "eq.Section",
    })
    from collections import Counter
    return dict(Counter(r["properties"].get("section_type", "?") for r in rows))


def _violations(doc_id: str) -> dict:
    rows = rest_select("kg_edges", params={
        "select":    "properties",
        "doc_id":    f"eq.{doc_id}",
        "edge_type": "eq.VIOLATES_RULE",
    })
    from collections import Counter
    by_rule = Counter(r["properties"].get("rule_id") for r in rows)
    n_defeated = sum(1 for r in rows if r["properties"].get("defeated") is True)
    return {"total": len(rows), "by_rule": dict(by_rule), "defeated": n_defeated}


def main() -> int:
    print("=" * 72)
    print("Generalisation test — kg_builder on RFP_Tirupathi WtE PPP RFP")
    print("=" * 72)
    print(f"  source: {TIRUPATHI_SOURCE.name}")
    print(f"  doc_id: {TIRUPATHI_DOC_ID}")
    print()

    t0 = time.perf_counter()
    summary = build_kg(
        doc_id=TIRUPATHI_DOC_ID,
        document=TIRUPATHI_SOURCE,
        document_name="Tirupati WtE 12 MW PPP RFP (NREDCAP)",
        clear_existing=True,
    )
    elapsed_s = time.perf_counter() - t0

    print(summary)
    print(f"\nWall: {elapsed_s:.1f}s")

    print()
    print("=" * 72)
    print("SIDE-BY-SIDE COMPARISON")
    print("=" * 72)

    # Pull comparable counts for Vizag
    def _node_counts(doc_id):
        from collections import Counter
        rows = rest_select("kg_nodes", params={
            "select": "node_type", "doc_id": f"eq.{doc_id}",
        })
        return Counter(r["node_type"] for r in rows)

    def _edge_counts(doc_id):
        from collections import Counter
        rows = rest_select("kg_edges", params={
            "select": "edge_type", "doc_id": f"eq.{doc_id}",
        })
        return Counter(r["edge_type"] for r in rows)

    viz_n = _node_counts(VIZAG_DOC_ID)
    tir_n = _node_counts(TIRUPATHI_DOC_ID)
    viz_e = _edge_counts(VIZAG_DOC_ID)
    tir_e = _edge_counts(TIRUPATHI_DOC_ID)

    print(f"\n  {'metric':28s} {'Vizag':>8s}    {'Tirupathi':>10s}")
    print("  " + "─" * 60)
    for nt in ("TenderDocument", "Section", "ClauseInstance", "RuleNode", "ValidationFinding"):
        print(f"  {nt:28s} {viz_n.get(nt, 0):8d}    {tir_n.get(nt, 0):10d}")
    print()
    for et in ("HAS_SECTION", "HAS_CLAUSE", "CROSS_REFERENCES",
                "SATISFIES_RULE", "VIOLATES_RULE", "DEFEATS", "OVERRIDES_VIOLATION"):
        print(f"  edge: {et:22s} {viz_e.get(et, 0):8d}    {tir_e.get(et, 0):10d}")

    # Section type distribution
    print(f"\n  Section-type distribution:")
    viz_sd = _section_dist(VIZAG_DOC_ID)
    tir_sd = _section_dist(TIRUPATHI_DOC_ID)
    all_types = sorted(set(viz_sd) | set(tir_sd))
    for st in all_types:
        print(f"  {st:28s} {viz_sd.get(st, 0):8d}    {tir_sd.get(st, 0):10d}")

    # Violations
    print(f"\n  Validator violations (post-defeasibility):")
    viz_v = _violations(VIZAG_DOC_ID)
    tir_v = _violations(TIRUPATHI_DOC_ID)
    print(f"    Vizag:     {viz_v['total']} edges, {viz_v['defeated']} defeated, "
          f"rule_ids: {sorted(viz_v['by_rule'])}")
    print(f"    Tirupathi: {tir_v['total']} edges, {tir_v['defeated']} defeated, "
          f"rule_ids: {sorted(tir_v['by_rule'])}")

    # TenderDocument property comparison — did the classifier produce sensible context?
    viz_doc = rest_select("kg_nodes", params={
        "select": "properties", "doc_id": f"eq.{VIZAG_DOC_ID}",
        "node_type": "eq.TenderDocument",
    })[0]["properties"]
    tir_doc = rest_select("kg_nodes", params={
        "select": "properties", "doc_id": f"eq.{TIRUPATHI_DOC_ID}",
        "node_type": "eq.TenderDocument",
    })[0]["properties"]
    print(f"\n  Classifier output:")
    for k in ("tender_type", "is_ap_tender", "estimated_value_cr", "duration_months", "funding_source"):
        print(f"    {k:22s} Vizag={viz_doc.get(k)}    Tirupathi={tir_doc.get(k)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
