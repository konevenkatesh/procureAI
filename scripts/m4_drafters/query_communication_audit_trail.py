"""M4.3 Reverse-drilldown query helper.

Given a finding node_id, find all Communications that cite it.

Usage:
  python3 scripts/m4_drafters/query_communication_audit_trail.py <finding_node_id>

The reverse query is the RTI-friendly direction: a citizen or vigilance
officer asks "what communications were generated from this evidence?"
This script answers by querying Communication kg_nodes whose
source_finding_node_ids[] JSONB array contains the supplied UUID.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from scripts.m4_drafters._common import REST, H, rest_get_range  # noqa: E402
import requests


def query_communications_citing_finding(finding_node_id: str) -> list[dict]:
    """All Communications whose source_finding_node_ids[] contains the given UUID.

    Uses PostgREST `cs.` (contains) array operator on JSONB.
    """
    return rest_get_range("kg_nodes", {
        "select": "node_id,label,properties",
        "node_type": "eq.Communication",
        "properties->source_finding_node_ids": f"cs.[\"{finding_node_id}\"]",
    })


def query_finding_by_id(node_id: str) -> dict | None:
    rows = rest_get_range("kg_nodes", {
        "select": "node_id,node_type,doc_id,label,properties",
    })
    for r in rows:
        if r["node_id"] == node_id:
            return r
    # Fallback: direct lookup if not in the paginated batch
    r = requests.get(f"{REST}/rest/v1/kg_nodes", params={
        "select": "node_id,node_type,doc_id,label,properties",
        "node_id": f"eq.{node_id}",
    }, headers=H, timeout=30).json()
    return r[0] if r else None


def print_audit_trail(finding_node_id: str) -> int:
    print("=" * 76)
    print(f"  Reverse drilldown — Communications citing finding {finding_node_id}")
    print("=" * 76)

    # Fetch the finding itself first
    finding = query_finding_by_id(finding_node_id)
    if finding is None:
        print(f"  ✗ Finding {finding_node_id} not found in kg_nodes.")
        return 1

    nt = finding["node_type"]
    fp = finding.get("properties") or {}
    print(f"\n── Source finding ──")
    print(f"  node_type:  {nt}")
    print(f"  doc_id:     {finding['doc_id']}")
    print(f"  label:      {finding.get('label', '?')}")
    if nt == "BidEvaluationFinding":
        print(f"  typology:   {fp.get('typology_code')}")
        print(f"  verdict:    {fp.get('verdict')}")
        print(f"  rule_id:    {fp.get('rule_id')}")
        print(f"  consequence: {fp.get('evaluation_consequence')}")
    elif nt == "EligibilityMatrix":
        print(f"  aggregate_verdict: {fp.get('aggregate_verdict')}")
        print(f"  bidder × tender:    {fp.get('bidder_profile_id')} × {fp.get('tender_id')}")
    elif nt == "TenderRanking":
        print(f"  tender_id: {fp.get('tender_id')}")
        print(f"  effective_l1: (see ComparativeStatement)")
    elif nt == "BidAnomalyFinding":
        print(f"  anomaly_class: {fp.get('anomaly_class')}")
        print(f"  severity:      {fp.get('aggregate_severity')}")
    elif nt == "ComparativeStatement":
        print(f"  effective_l1: {fp.get('effective_l1_bidder_name')} @ ₹{fp.get('effective_l1_amount_cr')}cr")

    # Reverse lookup
    comms = query_communications_citing_finding(finding_node_id)
    print(f"\n── {len(comms)} Communication(s) citing this finding ──")
    if not comms:
        print("  (no communications cite this finding)")
        return 0

    for c in comms:
        cp = c.get("properties") or {}
        print(f"\n  Communication node_id: {c['node_id']}")
        print(f"    type:        {cp.get('communication_type')}")
        print(f"    recipient:   {cp.get('recipient_bidder_profile_id')} "
              f"({cp.get('bidder_name', '?')})")
        print(f"    tender:      {cp.get('tender_id')} ({cp.get('tender_name', '?')})")
        print(f"    audit_id:    {cp.get('audit_id')}")
        print(f"    status:      {cp.get('status')}")
        print(f"    artifact:    {cp.get('artifact_path_md')}")
        n_sources = len(cp.get("source_finding_node_ids") or [])
        print(f"    other sources cited (besides this finding): {n_sources - 1}")
        print(f"    label: {c.get('label', '?')[:100]}")

    print("\n" + "=" * 76)
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: query_communication_audit_trail.py <finding_node_id>")
        print()
        print("Example: query_communication_audit_trail.py <UUID-of-EligibilityMatrix-row>")
        return 1
    return print_audit_trail(sys.argv[1])


if __name__ == "__main__":
    raise SystemExit(main())
