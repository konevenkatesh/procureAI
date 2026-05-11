"""
scripts/bid_litigation_check.py

═══════════════════════════════════════════════════════════════════
  Tier-2 Bid Submission Evaluator — Bidder-Litigation-Disqualifier
═══════════════════════════════════════════════════════════════════
Validates the bidder's Statement VII Litigation disclosure against
AP-GO-066 (committee-discretion litigation disqualifier — distinct
from AP-GO-096 administrative blacklist).

AP-GO-066 NL verbatim:
  "AP works tender bidder may be DISQUALIFIED if found to have:
  misled with false representations, record of poor performance
  (abandoning works, inordinate delays), litigation history,
  financial failures, OR participation in previous tendering for
  same work with unreasonably high bid prices."

Distinct from bid_blacklist_check:
  bid_blacklist (AP-GO-096 HARD_BLOCK) — formal administrative
    debarment process (Chief Engineer + Govt approval); criminal
    turpitude grounds; bidder is on a public blacklist register.
  bid_litigation (AP-GO-066 WARNING) — evaluation-committee
    discretion at bid opening; any disclosed pending litigation
    triggers committee review; not auto-disqualification.

Verdict per AP-GO-066 "may be DISQUALIFIED ... if litigation history":
  QUALIFIED  if litigation_count == 0
  INELIGIBLE if litigation_count > 0 (any disclosed case triggers
              committee review)
  GAP_INSUFFICIENT_DATA if litigation_count or cases array missing

evaluation_consequence = WARNING on INELIGIBLE (committee-discretion
review, not auto-disqualification per AP-GO-066 severity=WARNING).
This differs from other Batch-3 validators' HARD_BLOCK consequence
because AP-GO-066 is rule-WARNING, not rule-HARD_BLOCK.

L27: AP-GO-066 condition_when='TenderState=AndhraPradesh AND
TenderType IN [Works, EPC]' — fires cleanly (no downgrade).

rule_shape: "boolean_disclosure_bidder_side"
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

TYPOLOGY = "Bidder-Litigation-Disqualifier"
TIER = 2
RULE_ID = "AP-GO-066"


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


def _delete_prior_tier2_bid_litigation(bid_id):
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


def load_statement_vii_fact(bid_id):
    rows = rest_get("fact_sheets", {
        "select": "id,doc_id,fact_group,extracted_facts,section_heading,"
                  "source_file,line_start,line_end,extracted_by",
        "doc_id": f"eq.{bid_id}",
        "fact_group": "eq.Statement-VII-Litigation",
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
        print(f"  ⚠ L27 downgrade: {severity_origin} → ADVISORY")
    return {
        "rule_id": rule["rule_id"], "severity": severity_effective,
        "severity_origin": severity_origin, "verdict_origin": verdict_origin,
        "layer": rule.get("layer"), "typology_code": rule.get("typology_code"),
        "natural_language": rule.get("natural_language"),
        "condition_when": cw, "defeats": rule.get("defeats") or [],
        "_facts_evaluated": facts,
    }


def compute_verdict(litigation_count, cases):
    if litigation_count is None or cases is None:
        return "GAP_INSUFFICIENT_DATA", {
            "litigation_count_used": litigation_count,
            "cases_count": None,
            "recompute_seed_agree": None,
            "opposing_parties": None,
            "case_subjects": None,
        }
    # Recompute from array per L61 addendum-3
    cases_recompute = len(cases) if isinstance(cases, list) else 0
    recompute_seed_agree = (cases_recompute == litigation_count)
    if cases_recompute == 0:
        verdict = "QUALIFIED"
    else:
        verdict = "INELIGIBLE"
    return verdict, {
        "litigation_count_used":  litigation_count,
        "cases_count":            cases_recompute,
        "recompute_seed_agree":   recompute_seed_agree,
        "opposing_parties":       [c.get("opposing_party") for c in cases]
                                   if isinstance(cases, list) else None,
        "case_subjects":          [c.get("subject") for c in cases]
                                   if isinstance(cases, list) else None,
        "case_summary":           [
            {"case_no": c.get("case_no"),
             "opposing_party": c.get("opposing_party"),
             "subject": c.get("subject"),
             "status": c.get("status"),
             "year_filed": c.get("year_filed"),
             "disputed_amount_cr": c.get("disputed_amount_cr")}
            for c in (cases if isinstance(cases, list) else [])
        ],
    }


def evaluation_consequence_for(verdict):
    # AP-GO-066 is severity=WARNING (committee-discretion review).
    # On INELIGIBLE we mark evaluation_consequence=WARNING (not HARD_BLOCK)
    # so the EligibilityMatrix surfaces it as a committee-review trigger
    # rather than an automatic disqualification.
    return {"INELIGIBLE": "WARNING", "QUALIFIED": "ADVISORY",
            "GAP_INSUFFICIENT_DATA": "WARNING",
            "SKIP_NOT_APPLICABLE": "ADVISORY"}.get(verdict, "ADVISORY")


def compose_decision_reason(verdict, calc):
    if verdict == "QUALIFIED":
        return "qualified_no_litigation_history_disclosed_per_ap_go_066"
    if verdict == "GAP_INSUFFICIENT_DATA":
        return "gap_litigation_count_or_cases_missing"
    parties = ",".join((p or "?")[:25] for p in (calc.get("opposing_parties") or []))
    return (f"ineligible_committee_review_trigger_{calc['cases_count']}_"
            f"active_disclosed_cases_parties_{parties}_per_ap_go_066")


def main():
    t_start = time.perf_counter()
    print("=" * 76)
    print(f"  Tier-2 Bid Submission Evaluator — {TYPOLOGY}")
    print(f"  bid_id : {BID_ID}    rule: {RULE_ID}")
    print("=" * 76)

    n_f, n_e = _delete_prior_tier2_bid_litigation(BID_ID)
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

    fact_row = load_statement_vii_fact(BID_ID)
    if fact_row is None:
        finding = _emit_gap_finding(BID_ID, bid_node, bidder_props, tender_id,
                                    bidder_profile_id, None,
                                    reason="missing_statement_vii_fact_sheet")
        print(f"  → GAP {finding['node_id']}")
        return 0

    ef = fact_row.get("extracted_facts") or {}
    litigation_count = ef.get("litigation_count")
    cases = ef.get("cases") or []
    designed_to_trip = ef.get("_designed_to_trip")

    print(f"\n── Statement-VII ── fact_sheet_id={fact_row['id']}")
    print(f"  litigation_count: {litigation_count}  cases array: {len(cases)} entries")
    for c in cases[:5]:
        print(f"    • {(c.get('case_no','?'))[:40]:40s}  vs {c.get('opposing_party','?')}")
        print(f"      subject={(c.get('subject') or '?')[:80]}")
        print(f"      status={(c.get('status') or '?')[:60]}")

    print(f"\n── Rule selection ──")
    rule = select_rule(tender_state="AndhraPradesh", tender_type="Works")
    if rule is None:
        finding = _emit_skip_finding(BID_ID, bid_node, bidder_props, tender_id,
                                     bidder_profile_id, bidder_name)
        print(f"  → SKIP {finding['node_id']}")
        return 0

    verdict, calc = compute_verdict(litigation_count, cases)
    consequence = evaluation_consequence_for(verdict)
    print(f"\n── Decision ──")
    print(f"  litigation_count    : {calc.get('litigation_count_used')}")
    print(f"  cases_count         : {calc.get('cases_count')}")
    print(f"  recompute_seed_agree: {calc.get('recompute_seed_agree')}")
    print(f"  verdict   : {verdict}    consequence: {consequence}")

    # Ground truth proxy: litigation_count == 0 → QUALIFIED expected
    seed_gt_qualified = (litigation_count == 0
                         if litigation_count is not None else None)
    predicted_meets = (verdict == "QUALIFIED")
    matches_ground_truth = (predicted_meets == seed_gt_qualified
                            if seed_gt_qualified is not None else None)
    print(f"  ground_truth      : {seed_gt_qualified}    predicted_matches: {matches_ground_truth}")
    if designed_to_trip:
        print(f"  designed_to_trip  : {designed_to_trip}")

    # L64 surfacing
    if calc.get("recompute_seed_agree") is False:
        print(f"\n  ✗✗✗ L64 SEED DEFECT — len(cases) != litigation_count ✗✗✗")
        print(f"    cases_count={calc['cases_count']}  litigation_count={litigation_count}")

    rule_node_id = get_or_create_rule_node(BID_ID, RULE_ID)
    decision_reason = compose_decision_reason(verdict, calc)
    label = (f"{TYPOLOGY}: {bidder_name} {calc.get('cases_count')} active "
             f"disclosed case(s) → {verdict}")

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
        "litigation_count": litigation_count,
        "cases": cases,

        # tender criterion citation
        "tender_id": tender_id,
        "tender_nit_no": bid_props.get("tender_nit_no"),
        "criterion_source": "universal_rule_no_tender_parameter",

        # rule citation
        "rule_natural_language": rule.get("natural_language"),
        "rule_condition_when": rule.get("condition_when"),
        "rule_layer": rule.get("layer"),
        "rule_typology_code": rule.get("typology_code"),
        "rule_facts_evaluated": rule.get("_facts_evaluated"),
        "verdict_origin": rule.get("verdict_origin"),
        "severity_origin": rule.get("severity_origin"),

        # computation
        "cases_count": calc.get("cases_count"),
        "opposing_parties": calc.get("opposing_parties"),
        "case_subjects": calc.get("case_subjects"),
        "case_summary": calc.get("case_summary"),
        "meets_threshold": (verdict == "QUALIFIED"),

        # outcome
        "verdict": verdict, "decision_reason": decision_reason,

        # ground-truth + L64 recompute
        "ground_truth_meets": seed_gt_qualified,
        "ground_truth_label": designed_to_trip,
        "predicted_matches_ground_truth": matches_ground_truth,
        "recompute_seed_agree": calc.get("recompute_seed_agree"),
        "l64_seed_defect_surfaced": (calc.get("recompute_seed_agree") is False),

        # Cross-reference with bid_blacklist_check (both read Statement-VII;
        # blacklist's typology=Bidder-Blacklist-Disclosure focuses on
        # AP-GO-096 administrative-debarment process; this typology focuses
        # on AP-GO-066 committee-discretion review — distinct process layers)
        "distinct_from_typology": "Bidder-Blacklist-Disclosure",
        "process_layer": "evaluation_committee_review_not_administrative_blacklist",

        # extraction metadata
        "extracted_by": "tier2:bid_litigation_check_v1",
        "rule_shape": "boolean_disclosure_bidder_side",
        "extraction_path": "structured_fact_sheets_boolean_disclosure",
        "input_contract": "fact_sheets.Statement-VII-Litigation",
        "defeated": False,
    }

    finding = rest_post("kg_nodes", [{
        "doc_id": BID_ID, "node_type": "BidEvaluationFinding",
        "label": label, "properties": finding_props,
        "source_ref": f"tier2:bid_litigation_check:{RULE_ID}",
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
                "cases_count": calc.get("cases_count"),
                "opposing_parties": calc.get("opposing_parties"),
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
        "label": f"{TYPOLOGY}: SKIP — AP-GO-066 does not fire",
        "properties": {
            "tier": TIER, "typology_code": TYPOLOGY, "rule_id": RULE_ID,
            "severity": "ADVISORY", "evaluation_consequence": "ADVISORY",
            "verdict": "SKIP_NOT_APPLICABLE",
            "decision_reason": "ap_go_066_condition_when_did_not_fire",
            "bid_submission_id": bid_id, "bid_submission_node_id": bid_node["node_id"],
            "bidder_profile_id": bidder_profile_id, "bidder_name": bidder_name,
            "tender_id": tender_id,
            "extracted_by": "tier2:bid_litigation_check_v1",
            "extraction_path": "skip_rule_inapplicable",
            "input_contract": "fact_sheets.Statement-VII-Litigation",
            "defeated": False,
        },
        "source_ref": f"tier2:bid_litigation_check:{RULE_ID}",
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
            "extracted_by": "tier2:bid_litigation_check_v1",
            "extraction_path": "gap_missing_input_facts",
            "input_contract": "fact_sheets.Statement-VII-Litigation",
            "defeated": False,
        },
        "source_ref": f"tier2:bid_litigation_check:{RULE_ID}",
    }])[0]


if __name__ == "__main__":
    from modules.validation.verdict_emitter import main_with_crash_resilience
    raise SystemExit(main_with_crash_resilience(
        main, doc_id=BID_ID, typology=TYPOLOGY))
