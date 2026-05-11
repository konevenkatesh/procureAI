"""
scripts/bid_personnel_check.py

═══════════════════════════════════════════════════════════════════
  Tier-2 Bid Submission Evaluator — Bidder-Personnel-Coverage
═══════════════════════════════════════════════════════════════════
Validates the bidder's Statement VI Key Personnel against MPW-041.

MPW-041 NL verbatim:
  "PQ Criterion (Personnel Capabilities): the applicant's KEY
  personnel must meet the qualification and experience
  requirements specified — limited to a small number of key roles
  (e.g., project/contract manager, superintendents responsible
  for major components like dredging/piling/earthworks).
  Acceptability criteria must be based on minimum qualification
  AND a minimum number of years of relevant experience."

Rule-strict reading: every required role must be filled at bid
time with a named person carrying valid qualification + years of
experience. Vacant roles ("to be hired post-award") do NOT
satisfy MPW-041 — the rule is strict about bid-time presence.

Recompute discipline (L61 addendum-3): the validator recomputes
roles_filled from the personnel[] array (counting entries with
name != null and status not starting with "vacant"). Disagreement
with seed's roles_filled returns RC=2 (L64 seed defect).

L27 downgrade EXPECTED: MPW-041 condition_when='TenderType=Works
AND PQB=true'; PQB unknown → severity WARNING → ADVISORY.
evaluation_consequence is HARD_BLOCK on INELIGIBLE per Tier-2
semantic-blocking convention.

rule_shape: "role_coverage_bidder_side"
═══════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import os
import sys
import time
import requests
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from builder.config import settings
from modules.validator.condition_evaluator import evaluate as evaluate_when, Verdict


BID_ID = sys.argv[1] if len(sys.argv) > 1 else "bid_synth_b1_kurnool"

TYPOLOGY = "Bidder-Personnel-Coverage"
TIER = 2
RULE_ID = "MPW-041"


REST = settings.supabase_rest_url
H = {"apikey": settings.supabase_anon_key,
     "Authorization": f"Bearer {settings.supabase_anon_key}"}


def rest_get(path, params=None):
    r = requests.get(f"{REST}/rest/v1/{path}", params=params or {},
                     headers=H, timeout=30)
    r.raise_for_status()
    return r.json()


def rest_post(path, body):
    r = requests.post(
        f"{REST}/rest/v1/{path}", json=body,
        headers={**H, "Content-Type": "application/json",
                 "Prefer": "return=representation"}, timeout=30,
    )
    r.raise_for_status()
    return r.json()


def rest_delete(path, params=None):
    r = requests.delete(f"{REST}/rest/v1/{path}", params=params or {},
                        headers=H, timeout=30)
    r.raise_for_status()


def _delete_prior_tier2_bid_personnel(bid_id):
    edges = rest_get("kg_edges", {
        "select": "edge_id", "doc_id": f"eq.{bid_id}",
        "edge_type": "eq.BIDDER_VIOLATES_RULE",
        "properties->>typology": f"eq.{TYPOLOGY}",
        "properties->>tier": f"eq.{TIER}",
    })
    n_e = 0
    for e in edges:
        rest_delete("kg_edges", {"edge_id": f"eq.{e['edge_id']}"}); n_e += 1
    findings = rest_get("kg_nodes", {
        "select": "node_id", "doc_id": f"eq.{bid_id}",
        "node_type": "eq.BidEvaluationFinding",
        "properties->>typology_code": f"eq.{TYPOLOGY}",
        "properties->>tier": f"eq.{TIER}",
    })
    n_f = 0
    for f in findings:
        rest_delete("kg_nodes", {"node_id": f"eq.{f['node_id']}"}); n_f += 1
    return n_f, n_e


def get_or_create_rule_node(parent_doc_id, rule_id):
    existing = rest_get("kg_nodes", {
        "select": "node_id", "doc_id": f"eq.{parent_doc_id}",
        "node_type": "eq.RuleNode",
        "properties->>rule_id": f"eq.{rule_id}",
    })
    if existing:
        return existing[0]["node_id"]
    rule_rows = rest_get("rules", {
        "select": "rule_id,natural_language,layer,severity,rule_type,pattern_type,typology_code,defeats",
        "rule_id": f"eq.{rule_id}",
    })
    r = rule_rows[0] if rule_rows else {}
    inserted = rest_post("kg_nodes", [{
        "doc_id": parent_doc_id, "node_type": "RuleNode",
        "label": f"{rule_id}: {(r.get('natural_language') or '')[:90]}",
        "properties": {
            "rule_id": rule_id, "layer": r.get("layer"),
            "severity": r.get("severity"), "rule_type": r.get("rule_type"),
            "pattern_type": r.get("pattern_type"),
            "typology_code": r.get("typology_code"),
            "defeats": r.get("defeats") or [],
        },
        "source_ref": f"rules:{rule_id}",
    }])
    return inserted[0]["node_id"]


def load_bid_submission(bid_id):
    rows = rest_get("kg_nodes", {
        "select": "node_id,doc_id,label,properties",
        "doc_id": f"eq.{bid_id}", "node_type": "eq.BidSubmission",
    })
    if not rows:
        raise RuntimeError(f"No BidSubmission for doc_id={bid_id!r}")
    return rows[0]


def load_bidder_profile(bidder_profile_id):
    rows = rest_get("kg_nodes", {
        "select": "node_id,doc_id,label,properties",
        "doc_id": f"eq.{bidder_profile_id}", "node_type": "eq.BidderProfile",
    })
    if not rows:
        raise RuntimeError(f"No BidderProfile for doc_id={bidder_profile_id!r}")
    return rows[0]


def load_statement_vi_fact(bid_id):
    rows = rest_get("fact_sheets", {
        "select": "id,doc_id,fact_group,extracted_facts,section_heading,"
                  "source_file,line_start,line_end,extracted_by",
        "doc_id": f"eq.{bid_id}",
        "fact_group": "eq.Statement-VI-KeyPersonnel",
    })
    return rows[0] if rows else None


def select_rule(tender_state, tender_type):
    rows = rest_get("rules", {
        "select": "rule_id,condition_when,defeats,severity,layer,natural_language,typology_code",
        "rule_id": f"eq.{RULE_ID}",
    })
    if not rows:
        return None
    rule = rows[0]
    cw = rule.get("condition_when") or ""
    facts = {"TenderState": tender_state, "TenderType": tender_type,
             "tender_type": tender_type}
    verdict = evaluate_when(cw, facts).verdict
    print(f"  [{RULE_ID}] condition={cw!r}  → verdict={verdict.value}")
    if verdict == Verdict.SKIP:
        return None
    severity_origin = rule.get("severity")
    severity_effective = severity_origin
    verdict_origin = "FIRE"
    if verdict == Verdict.UNKNOWN:
        severity_effective = "ADVISORY"
        verdict_origin = "UNKNOWN"
        print(f"  ⚠ L27 downgrade: {severity_origin} → ADVISORY (PQB=true unknown)")
    return {
        "rule_id": rule["rule_id"], "severity": severity_effective,
        "severity_origin": severity_origin, "verdict_origin": verdict_origin,
        "layer": rule.get("layer"), "typology_code": rule.get("typology_code"),
        "natural_language": rule.get("natural_language"),
        "condition_when": cw, "defeats": rule.get("defeats") or [],
        "_facts_evaluated": facts,
    }


def _is_role_filled(entry: dict) -> bool:
    """Return True if entry has a named person with qualification + years."""
    if not isinstance(entry, dict):
        return False
    name = entry.get("name")
    if not name or not isinstance(name, str) or not name.strip():
        return False
    status = (entry.get("status") or "").lower()
    if status.startswith("vacant"):
        return False
    qual = entry.get("qualification")
    years = entry.get("years_experience")
    return bool(qual) and (years is not None)


def compute_verdict(personnel, roles_total, seed_roles_filled):
    """Recompute roles_filled from personnel[] per L61 addendum-3."""
    if not isinstance(personnel, list) or roles_total is None:
        return "GAP_INSUFFICIENT_DATA", {
            "personnel_count":    None,
            "recomputed_filled":  None,
            "seed_roles_filled":  seed_roles_filled,
            "recompute_seed_agree": None,
            "vacant_roles":       None,
            "filled_roles":       None,
        }
    filled_entries = [p for p in personnel if _is_role_filled(p)]
    vacant_entries = [p for p in personnel if not _is_role_filled(p)]
    recomputed = len(filled_entries)
    recompute_seed_agree = (recomputed == seed_roles_filled
                            if seed_roles_filled is not None else None)
    if recomputed == roles_total and recomputed > 0:
        verdict = "QUALIFIED"
    else:
        verdict = "INELIGIBLE"
    return verdict, {
        "personnel_count":      len(personnel),
        "recomputed_filled":    recomputed,
        "seed_roles_filled":    seed_roles_filled,
        "recompute_seed_agree": recompute_seed_agree,
        "vacant_roles":         [
            {"role": p.get("role"), "status": p.get("status")}
            for p in vacant_entries
        ],
        "filled_roles":         [
            {"role": p.get("role"), "name": p.get("name"),
             "qualification": p.get("qualification"),
             "years_experience": p.get("years_experience"),
             "membership_ref": p.get("membership_ref")}
            for p in filled_entries
        ],
    }


def evaluation_consequence_for(verdict):
    return {"INELIGIBLE": "HARD_BLOCK", "QUALIFIED": "ADVISORY",
            "GAP_INSUFFICIENT_DATA": "WARNING",
            "SKIP_NOT_APPLICABLE": "ADVISORY"}.get(verdict, "ADVISORY")


def compose_decision_reason(verdict, calc, roles_total):
    if verdict == "QUALIFIED":
        return (f"qualified_all_{roles_total}_roles_filled_with_qualified_personnel"
                f"_per_mpw_041")
    if verdict == "GAP_INSUFFICIENT_DATA":
        return f"gap_missing_personnel_array_or_roles_total"
    vacant_roles = ",".join((v.get("role") or "?")[:30]
                            for v in (calc.get("vacant_roles") or []))
    return (f"ineligible_{calc['recomputed_filled']}_of_{roles_total}_roles_filled"
            f"_vacant_{vacant_roles}_per_mpw_041_rule_strict")


def main():
    t_start = time.perf_counter()
    print("=" * 76)
    print(f"  Tier-2 Bid Submission Evaluator — {TYPOLOGY}")
    print(f"  bid_id : {BID_ID}    rule: {RULE_ID}")
    print("=" * 76)

    n_f, n_e = _delete_prior_tier2_bid_personnel(BID_ID)
    if n_f or n_e:
        print(f"  cleanup: removed {n_f} prior finding(s) + {n_e} edge(s)")

    bid_node = load_bid_submission(BID_ID)
    bid_props = bid_node["properties"] or {}
    bidder_profile_id = (bid_props.get("bidder_profile_id")
                         or bid_props.get("bidder_id"))
    tender_id = bid_props.get("tender_id")
    bidder_node = load_bidder_profile(bidder_profile_id)
    bidder_props = bidder_node["properties"] or {}
    bidder_name = (bidder_props.get("company_name")
                   or bidder_props.get("bidder_name") or "(unnamed)")
    print(f"\n── Bidder ── {bidder_name}")

    fact_row = load_statement_vi_fact(BID_ID)
    if fact_row is None:
        finding = _emit_gap_finding(BID_ID, bid_node, bidder_props, tender_id,
                                    bidder_profile_id, None,
                                    reason="missing_statement_vi_fact_sheet")
        print(f"  → GAP {finding['node_id']}")
        return 0

    ef = fact_row.get("extracted_facts") or {}
    personnel = ef.get("personnel") or []
    roles_total = ef.get("roles_total")
    seed_roles_filled = ef.get("roles_filled")
    designed_to_trip = ef.get("_designed_to_trip")

    print(f"\n── Statement-VI ── fact_sheet_id={fact_row['id']}")
    print(f"  personnel entries: {len(personnel)}  roles_total={roles_total}  seed_roles_filled={seed_roles_filled}")
    for p in personnel:
        is_filled = _is_role_filled(p)
        mark = "✓" if is_filled else "✗"
        print(f"    {mark} role={(p.get('role','?'))[:40]:40s}  name={(p.get('name') or '(vacant)')[:25]:25s}  "
              f"qual={p.get('qualification')!r}  yrs={p.get('years_experience')}")

    print(f"\n── Rule selection ──")
    rule = select_rule(tender_state="AndhraPradesh", tender_type="Works")
    if rule is None:
        finding = _emit_skip_finding(BID_ID, bid_node, bidder_props, tender_id,
                                     bidder_profile_id, bidder_name)
        print(f"  → SKIP {finding['node_id']}")
        return 0

    verdict, calc = compute_verdict(personnel, roles_total, seed_roles_filled)
    consequence = evaluation_consequence_for(verdict)
    print(f"\n── Decision (rule-strict per MPW-041) ──")
    print(f"  recomputed_filled : {calc.get('recomputed_filled')}")
    print(f"  seed_roles_filled : {calc.get('seed_roles_filled')}")
    print(f"  recompute_seed_agree: {calc.get('recompute_seed_agree')}")
    print(f"  vacant_roles      : {len(calc.get('vacant_roles') or [])}")
    print(f"  verdict   : {verdict}    consequence: {consequence}")

    # Ground truth: derived from seed's roles_filled == roles_total
    seed_gt_qualified = (seed_roles_filled == roles_total
                         if seed_roles_filled is not None and roles_total is not None
                         else None)
    predicted_meets = (verdict == "QUALIFIED")
    matches_ground_truth = (predicted_meets == seed_gt_qualified
                            if seed_gt_qualified is not None else None)
    print(f"  seed_gt_qualified : {seed_gt_qualified}")
    print(f"  predicted_matches : {matches_ground_truth}")
    if designed_to_trip:
        print(f"  designed_to_trip  : {designed_to_trip}")

    # L64 seed-defect surfacing
    if calc.get("recompute_seed_agree") is False:
        print(f"\n  ✗✗✗ L64 SEED DEFECT — recompute disagrees with seed.roles_filled ✗✗✗")
        print(f"    recomputed={calc['recomputed_filled']}  seed={seed_roles_filled}")

    rule_node_id = get_or_create_rule_node(BID_ID, RULE_ID)
    decision_reason = compose_decision_reason(verdict, calc, roles_total)
    label = (f"{TYPOLOGY}: {bidder_name} {calc.get('recomputed_filled')}/"
             f"{roles_total} roles filled → {verdict}")

    finding_props = {
        "tier": TIER, "typology_code": TYPOLOGY, "rule_id": RULE_ID,
        "severity": rule.get("severity"),
        "evaluation_consequence": consequence,

        # bidder citation
        "bid_submission_id": BID_ID,
        "bid_submission_node_id": bid_node["node_id"],
        "bidder_profile_id": bidder_profile_id,
        "bidder_profile_node_id": bidder_node["node_id"],
        "bidder_name": bidder_name,
        "bidder_contractor_class": bidder_props.get("contractor_class"),
        "bidder_pan": bidder_props.get("pan"),

        # fact citation
        "fact_sheet_id": fact_row["id"],
        "fact_sheet_fact_group": fact_row["fact_group"],
        "fact_sheet_source_file": fact_row.get("source_file"),
        "fact_sheet_extracted_by": fact_row.get("extracted_by"),
        "personnel": personnel,
        "seed_roles_total": roles_total,
        "seed_roles_filled": seed_roles_filled,

        # tender criterion citation
        "tender_id": tender_id,
        "tender_nit_no": bid_props.get("tender_nit_no"),
        "criterion_source": "seed_roles_total_proxy_for_tender_specified_roles",

        # rule citation
        "rule_natural_language": rule.get("natural_language"),
        "rule_condition_when": rule.get("condition_when"),
        "rule_layer": rule.get("layer"),
        "rule_typology_code": rule.get("typology_code"),
        "rule_facts_evaluated": rule.get("_facts_evaluated"),
        "verdict_origin": rule.get("verdict_origin"),
        "severity_origin": rule.get("severity_origin"),

        # computation
        "recomputed_filled": calc.get("recomputed_filled"),
        "vacant_roles":      calc.get("vacant_roles"),
        "filled_roles":      calc.get("filled_roles"),
        "meets_threshold":   (verdict == "QUALIFIED"),

        # outcome
        "verdict": verdict, "decision_reason": decision_reason,

        # ground-truth + L64 recompute-vs-seed
        "ground_truth_meets": seed_gt_qualified,
        "ground_truth_label": designed_to_trip,
        "predicted_matches_ground_truth": matches_ground_truth,
        "recompute_seed_agree": calc.get("recompute_seed_agree"),
        "l64_seed_defect_surfaced": (calc.get("recompute_seed_agree") is False),

        # extraction metadata
        "extracted_by": "tier2:bid_personnel_check_v1",
        "rule_shape": "role_coverage_bidder_side",
        "extraction_path": "structured_fact_sheets_role_recompute",
        "input_contract": "fact_sheets.Statement-VI-KeyPersonnel",
        "defeated": False,
    }

    finding = rest_post("kg_nodes", [{
        "doc_id": BID_ID, "node_type": "BidEvaluationFinding",
        "label": label, "properties": finding_props,
        "source_ref": f"tier2:bid_personnel_check:{RULE_ID}",
    }])[0]
    print(f"\n  → BidEvaluationFinding {finding['node_id']}")

    edge = None
    if verdict == "INELIGIBLE":
        edge = rest_post("kg_edges", [{
            "doc_id": BID_ID, "from_node_id": bid_node["node_id"],
            "to_node_id": rule_node_id,
            "edge_type": "BIDDER_VIOLATES_RULE", "weight": 1.0,
            "properties": {
                "tier": TIER, "rule_id": RULE_ID, "typology": TYPOLOGY,
                "severity": rule.get("severity"),
                "evaluation_consequence": consequence, "verdict": verdict,
                "bid_submission_id": BID_ID,
                "bidder_profile_id": bidder_profile_id,
                "roles_total": roles_total,
                "recomputed_filled": calc.get("recomputed_filled"),
                "vacant_count": len(calc.get("vacant_roles") or []),
                "decision_reason": decision_reason,
                "finding_node_id": finding["node_id"], "defeated": False,
            },
        }])[0]
        print(f"  → BIDDER_VIOLATES_RULE {edge['edge_id']}")
    else:
        print(f"  → no edge ({verdict} is silent)")

    wall = time.perf_counter() - t_start
    print(f"\n  TIMING wall={wall*1000:.0f}ms verdict={verdict} gt_match={matches_ground_truth}")
    if calc.get("recompute_seed_agree") is False:
        return 2
    if matches_ground_truth is False:
        return 2
    return 0


def _emit_skip_finding(bid_id, bid_node, bidder_props, tender_id,
                       bidder_profile_id, bidder_name):
    return rest_post("kg_nodes", [{
        "doc_id": bid_id, "node_type": "BidEvaluationFinding",
        "label": f"{TYPOLOGY}: SKIP — MPW-041 does not fire",
        "properties": {
            "tier": TIER, "typology_code": TYPOLOGY, "rule_id": RULE_ID,
            "severity": "ADVISORY", "evaluation_consequence": "ADVISORY",
            "verdict": "SKIP_NOT_APPLICABLE",
            "decision_reason": "mpw_041_condition_when_did_not_fire",
            "bid_submission_id": bid_id, "bid_submission_node_id": bid_node["node_id"],
            "bidder_profile_id": bidder_profile_id, "bidder_name": bidder_name,
            "tender_id": tender_id,
            "extracted_by": "tier2:bid_personnel_check_v1",
            "extraction_path": "skip_rule_inapplicable",
            "input_contract": "fact_sheets.Statement-VI-KeyPersonnel",
            "defeated": False,
        },
        "source_ref": f"tier2:bid_personnel_check:{RULE_ID}",
    }])[0]


def _emit_gap_finding(bid_id, bid_node, bidder_props, tender_id,
                      bidder_profile_id, rule, reason):
    bidder_name = (bidder_props.get("company_name")
                   or bidder_props.get("bidder_name") or "(unnamed)")
    return rest_post("kg_nodes", [{
        "doc_id": bid_id, "node_type": "BidEvaluationFinding",
        "label": f"{TYPOLOGY}: GAP — {reason}",
        "properties": {
            "tier": TIER, "typology_code": TYPOLOGY, "rule_id": RULE_ID,
            "severity": (rule.get("severity") if rule else "ADVISORY"),
            "evaluation_consequence": "WARNING",
            "verdict": "GAP_INSUFFICIENT_DATA", "decision_reason": reason,
            "bid_submission_id": bid_id, "bid_submission_node_id": bid_node["node_id"],
            "bidder_profile_id": bidder_profile_id, "bidder_name": bidder_name,
            "tender_id": tender_id,
            "extracted_by": "tier2:bid_personnel_check_v1",
            "extraction_path": "gap_missing_input_facts",
            "input_contract": "fact_sheets.Statement-VI-KeyPersonnel",
            "defeated": False,
        },
        "source_ref": f"tier2:bid_personnel_check:{RULE_ID}",
    }])[0]


if __name__ == "__main__":
    from modules.validation.verdict_emitter import main_with_crash_resilience
    raise SystemExit(main_with_crash_resilience(
        main, doc_id=BID_ID, typology=TYPOLOGY))
