"""
experiments/tender_graph/test_kg_overrides.py

Proves the OVERRIDES_VIOLATION + defeated:true edge path fires when the
validator surfaces a rule that an AP-GO defeater takes out.

We monkey-patch _run_validator to additionally include rule_ids that we
know are defeated (e.g. MPW-133, defeated by AP-GO-019 in AP context).
Then we run build_kg under a synthetic doc_id and check that:
    • OVERRIDES_VIOLATION edges appear
    • At least one VIOLATES_RULE edge has defeated:true

This is end-to-end coverage of the defeasibility contract; no production
data is mutated because we use a separate synthetic doc_id.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

import kg_builder
from kg_builder import build_kg
from _common import SOURCE_FILES, rest_select, rest_delete_doc


SYNTH_DOC_ID = "_synth_kg_override_test"


def main() -> int:
    print("=" * 70)
    print("Synthetic test — force defeated-rule violation, observe override path")
    print("=" * 70)

    # The 27 defeaters — pick one and inject one of its victims as a "violation".
    # We must pick a victim that IS referenced by some matched DRAFTING_CLAUSE
    # template in this document, otherwise neither the victim nor the defeater
    # gets a RuleNode and the override path can't fire. From the production
    # build, AP-GO-056 → MPW-103 is realised as a DEFEATS edge, so we know
    # MPW-103 is in the referenced set.
    DEFEATER_RID = "AP-GO-056"
    VICTIM_RID   = "MPW-103"   # AP-GO-056 defeats [MPW-103, MPW25-074, MPG-142, CVC-067]
    print(f"  defeater: {DEFEATER_RID}")
    print(f"  forcing violation of: {VICTIM_RID}")

    # Monkey-patch the validator to add the victim rule_id to the result.
    # We retain the real validator's findings so the rest of the graph
    # remains realistic.
    real_run_validator = kg_builder._run_validator
    real_findings_for_kg = kg_builder._validation_findings_for_kg

    def fake_run_validator(text, document_name):
        rids = real_run_validator(text, document_name)
        rids.add(VICTIM_RID)
        return rids

    def fake_findings_for_kg(text, document_name):
        out = real_findings_for_kg(text, document_name)
        out.append({
            "rule_id":       VICTIM_RID,
            "typology_code": "Synthetic-Test",
            "severity":      "WARNING",
            "evidence":      "Synthetic injected violation for override-path test.",
            "source_clause": "test:overrides",
            "defeated_by":   [DEFEATER_RID],
        })
        return out

    kg_builder._run_validator = fake_run_validator
    kg_builder._validation_findings_for_kg = fake_findings_for_kg

    try:
        # The test depends on the matched DRAFTING_CLAUSE templates referencing
        # MPW-133 somewhere; Vizag UGSS does (via PVC clauses). If not, we'd
        # need to adjust the SOURCE_FILES list — but for this experiment
        # Vizag UGSS is sufficient.
        summary = build_kg(
            doc_id=SYNTH_DOC_ID,
            document=SOURCE_FILES,
            document_name=f"SYNTHETIC TEST [{SYNTH_DOC_ID}]",
            clear_existing=True,
        )
    finally:
        # Always restore the real functions, even if build_kg raised
        kg_builder._run_validator = real_run_validator
        kg_builder._validation_findings_for_kg = real_findings_for_kg

    print()
    print("─" * 70)
    print("Assertions:")
    print("─" * 70)

    # 1. RuleNode for the defeater must exist
    defeater_nodes = rest_select("kg_nodes", params={
        "select": "node_id,properties",
        "doc_id": f"eq.{SYNTH_DOC_ID}",
        "node_type": "eq.RuleNode",
    })
    has_defeater  = any(n["properties"].get("rule_id") == DEFEATER_RID
                        for n in defeater_nodes)
    has_victim    = any(n["properties"].get("rule_id") == VICTIM_RID
                        for n in defeater_nodes)
    print(f"  defeater {DEFEATER_RID} RuleNode present: {has_defeater}")
    print(f"  victim   {VICTIM_RID} RuleNode present: {has_victim}")
    assert has_defeater and has_victim, "expected both rules materialised as RuleNodes"

    # 2. DEFEATS edge between them
    defeats_edges = rest_select("kg_edges", params={
        "select": "from_node_id,to_node_id",
        "doc_id": f"eq.{SYNTH_DOC_ID}",
        "edge_type": "eq.DEFEATS",
    })
    print(f"  DEFEATS edges (any): {len(defeats_edges)}")
    assert len(defeats_edges) > 0, "expected DEFEATS edges"

    # 3. At least one VIOLATES_RULE with defeated:true
    vio = rest_select("kg_edges", params={
        "select": "properties",
        "doc_id": f"eq.{SYNTH_DOC_ID}",
        "edge_type": "eq.VIOLATES_RULE",
    })
    n_defeated = sum(1 for e in vio if e["properties"].get("defeated") is True)
    print(f"  VIOLATES_RULE total: {len(vio)}")
    print(f"  VIOLATES_RULE with defeated=true: {n_defeated}")
    assert n_defeated > 0, "expected at least one VIOLATES_RULE flagged defeated:true"

    # 4. OVERRIDES_VIOLATION edges
    overrides = rest_select("kg_edges", params={
        "select": "properties",
        "doc_id": f"eq.{SYNTH_DOC_ID}",
        "edge_type": "eq.OVERRIDES_VIOLATION",
    })
    print(f"  OVERRIDES_VIOLATION edges: {len(overrides)}")
    assert len(overrides) > 0, "expected at least one OVERRIDES_VIOLATION edge"

    print()
    print("  ✓ All defeasibility assertions passed")

    # Cleanup synthetic doc — never leave test data in production tables
    print()
    print(f"  Cleaning up synthetic doc_id={SYNTH_DOC_ID}...")
    rest_delete_doc("kg_edges", SYNTH_DOC_ID)
    rest_delete_doc("kg_nodes", SYNTH_DOC_ID)
    print("  Done.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
