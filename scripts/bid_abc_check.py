"""
scripts/bid_abc_check.py

═══════════════════════════════════════════════════════════════════
  Tier-2 Bid Submission Evaluator — Bidder-Capacity-Compliance
═══════════════════════════════════════════════════════════════════
Validates the bidder's Available Bid Capacity (ABC) declaration in
Statement X against AP-GO-062.

AP-GO-062 NL verbatim:
  "Available Bid Capacity for AP Works = (A × N × 2) − B"
  where A = max civil-engineering works value executed in any one
  year of last 5 years (price-updated), N = number of years from
  project completion to date, B = existing commitments.

The coefficient 2 is BAKED IN to the formula per AP-GO-062 — not
parameterised. M ≠ 2 = formula violation, regardless of arithmetic.

Three independent failure modes (any one triggers INELIGIBLE):
  1. M_violation         — bidder.M_multiplier != 2
  2. arithmetic_error    — |declared_ABC - recompute(A, N, M, B)| > 0.01
  3. capacity_insufficient — declared_ABC < ECV
Multiple modes may fire on a single bid; decision_reason composes
them joined with "+".

rule_shape: "formula_verification_bidder_side"
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

TYPOLOGY = "Bidder-Capacity-Compliance"
TIER = 2
RULE_ID = "AP-GO-062"

# AP-GO-062 baked-in coefficient (NOT parameterised)
AP_GO_062_M_REQUIRED = 2
ARITHMETIC_TOLERANCE_CR = 0.01


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
                 "Prefer": "return=representation"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def rest_delete(path, params=None):
    r = requests.delete(f"{REST}/rest/v1/{path}", params=params or {},
                        headers=H, timeout=30)
    r.raise_for_status()


def _delete_prior_tier2_bid_abc(bid_id):
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


def load_statement_x_fact(bid_id):
    rows = rest_get("fact_sheets", {
        "select": "id,doc_id,fact_group,extracted_facts,section_heading,"
                  "source_file,line_start,line_end,extracted_by",
        "doc_id": f"eq.{bid_id}",
        "fact_group": "eq.Statement-X-BidCapacity",
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


def compute_verdict(M, A, N, B, declared_ABC, ECV):
    """Three independent failure-mode checks. Returns (verdict, calc_dict)."""
    inputs_complete = all(x is not None for x in (M, A, N, B, declared_ABC, ECV))
    if not inputs_complete:
        return "GAP_INSUFFICIENT_DATA", {
            "M_violation": None, "arithmetic_error": None,
            "capacity_insufficient": None,
            "recomputed_abc_cr": None, "abc_minus_ecv_cr": None,
            "missing_inputs": [k for k, v in
                               [("M_multiplier", M), ("A", A), ("N", N), ("B", B),
                                ("declared_ABC", declared_ABC), ("ECV", ECV)]
                               if v is None],
        }
    # Recompute using bidder's declared M (NOT the rule's M=2) — this lets
    # us detect arithmetic errors independent of the M_violation check.
    recomputed = M * A * N - B
    arithmetic_delta = abs(declared_ABC - recomputed)
    M_violation = (M != AP_GO_062_M_REQUIRED)
    arithmetic_error = (arithmetic_delta > ARITHMETIC_TOLERANCE_CR)
    capacity_insufficient = (declared_ABC < ECV)
    failures = (M_violation or arithmetic_error or capacity_insufficient)
    return ("INELIGIBLE" if failures else "QUALIFIED"), {
        "M_required":                AP_GO_062_M_REQUIRED,
        "M_declared":                M,
        "M_violation":               M_violation,
        "recomputed_abc_cr":         round(recomputed, 4),
        "arithmetic_delta_cr":       round(arithmetic_delta, 4),
        "arithmetic_error":          arithmetic_error,
        "declared_abc_cr":           declared_ABC,
        "ecv_cr":                    ECV,
        "abc_minus_ecv_cr":          round(declared_ABC - ECV, 4),
        "capacity_insufficient":     capacity_insufficient,
        "capacity_ratio":            (round(declared_ABC / ECV, 4)
                                       if ECV and ECV > 0 else None),
    }


def evaluation_consequence_for(verdict):
    return {"INELIGIBLE": "HARD_BLOCK", "QUALIFIED": "ADVISORY",
            "GAP_INSUFFICIENT_DATA": "WARNING",
            "SKIP_NOT_APPLICABLE": "ADVISORY"}.get(verdict, "ADVISORY")


def compose_decision_reason(verdict, calc, M, A, N, B, declared_ABC, ECV):
    if verdict == "QUALIFIED":
        return (f"qualified_M_{M}_arithmetic_ok_abc_{declared_ABC}cr"
                f"_above_ecv_{ECV}cr")
    if verdict == "GAP_INSUFFICIENT_DATA":
        return f"gap_missing_inputs_{','.join(calc.get('missing_inputs') or [])}"
    modes = []
    if calc.get("M_violation"):
        modes.append(f"M_{M}_not_2_per_ap_go_062")
    if calc.get("arithmetic_error"):
        modes.append(f"arithmetic_error_declared_{declared_ABC}cr"
                     f"_vs_recomputed_{calc.get('recomputed_abc_cr')}cr")
    if calc.get("capacity_insufficient"):
        modes.append(f"abc_{declared_ABC}cr_below_ecv_{ECV}cr")
    return "ineligible_" + "+".join(modes)


def main():
    t_start = time.perf_counter()
    print("=" * 76)
    print(f"  Tier-2 Bid Submission Evaluator — {TYPOLOGY}")
    print(f"  bid_id : {BID_ID}    rule: {RULE_ID} (M_required={AP_GO_062_M_REQUIRED})")
    print("=" * 76)

    n_f, n_e = _delete_prior_tier2_bid_abc(BID_ID)
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
    print(f"\n── Bidder ── {bidder_name}  class={bidder_props.get('contractor_class')}")

    fact_row = load_statement_x_fact(BID_ID)
    if fact_row is None:
        finding = _emit_gap_finding(BID_ID, bid_node, bidder_props, tender_id,
                                    bidder_profile_id, None,
                                    reason="missing_statement_x_fact_sheet")
        print(f"  → GAP_INSUFFICIENT_DATA {finding['node_id']}")
        return 0

    ef = fact_row.get("extracted_facts") or {}
    M             = ef.get("M_multiplier")
    A             = ef.get("A_max_one_year_works_cr")
    N             = ef.get("N_completion_years")
    B             = ef.get("B_existing_commitments_cr")
    declared_ABC  = ef.get("computed_abc_cr")
    ECV           = ef.get("ecv_cr")
    formula_used  = ef.get("formula_used")
    qualifies_seed = ef.get("qualifies")
    designed_to_trip = ef.get("_designed_to_trip")
    print(f"\n── Statement-X ── fact_sheet_id={fact_row['id']}")
    print(f"  M={M}  A={A}cr  N={N}yr  B={B}cr  declared_ABC={declared_ABC}cr  ECV={ECV}cr")
    print(f"  formula_used: {formula_used!r}")

    print(f"\n── Rule selection ──")
    rule = select_rule(tender_state="AndhraPradesh", tender_type="Works")
    if rule is None:
        finding = _emit_skip_finding(BID_ID, bid_node, bidder_props, tender_id,
                                     bidder_profile_id, bidder_name)
        print(f"  → SKIP_NOT_APPLICABLE {finding['node_id']}")
        return 0

    verdict, calc = compute_verdict(M, A, N, B, declared_ABC, ECV)
    consequence = evaluation_consequence_for(verdict)
    print(f"\n── Decision ──")
    print(f"  M_violation           : {calc.get('M_violation')}")
    print(f"  arithmetic_error      : {calc.get('arithmetic_error')}  (Δ={calc.get('arithmetic_delta_cr')}cr)")
    print(f"  capacity_insufficient : {calc.get('capacity_insufficient')}  (Δ={calc.get('abc_minus_ecv_cr')}cr, ratio={calc.get('capacity_ratio')})")
    print(f"  verdict               : {verdict}    consequence={consequence}")

    # Ground truth: seed's qualifies bool
    predicted_meets = (verdict == "QUALIFIED")
    matches_ground_truth = (predicted_meets == bool(qualifies_seed)
                            if qualifies_seed is not None else None)
    print(f"  ground_truth  : {qualifies_seed}    predicted_matches: {matches_ground_truth}")
    if designed_to_trip:
        print(f"  designed_to_trip: {designed_to_trip}")

    rule_node_id = get_or_create_rule_node(BID_ID, RULE_ID)
    decision_reason = compose_decision_reason(verdict, calc, M, A, N, B,
                                              declared_ABC, ECV)
    label = (f"{TYPOLOGY}: {bidder_name} ABC ₹{declared_ABC}cr "
             f"{'≥' if verdict == 'QUALIFIED' else '<'} ECV ₹{ECV}cr "
             f"(M={M}) → {verdict}")

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

        # fact sheet citation
        "fact_sheet_id": fact_row["id"],
        "fact_sheet_fact_group": fact_row["fact_group"],
        "fact_sheet_source_file": fact_row.get("source_file"),
        "fact_sheet_extracted_by": fact_row.get("extracted_by"),
        "M_declared": M, "A_max_one_year_cr": A,
        "N_completion_years": N, "B_existing_commitments_cr": B,
        "declared_abc_cr": declared_ABC,
        "formula_used_string": formula_used,

        # tender criterion citation
        "tender_id": tender_id, "tender_nit_no": bid_props.get("tender_nit_no"),
        "ecv_cr": ECV, "ecv_source": "fact_sheet.extracted_facts.ecv_cr",

        # rule citation
        "rule_natural_language": rule.get("natural_language"),
        "rule_condition_when": rule.get("condition_when"),
        "rule_layer": rule.get("layer"),
        "rule_typology_code": rule.get("typology_code"),
        "rule_facts_evaluated": rule.get("_facts_evaluated"),
        "verdict_origin": rule.get("verdict_origin"),
        "severity_origin": rule.get("severity_origin"),
        "rule_M_required": AP_GO_062_M_REQUIRED,
        "arithmetic_tolerance_cr": ARITHMETIC_TOLERANCE_CR,

        # computation (three failure modes)
        "M_violation": calc.get("M_violation"),
        "arithmetic_error": calc.get("arithmetic_error"),
        "arithmetic_delta_cr": calc.get("arithmetic_delta_cr"),
        "capacity_insufficient": calc.get("capacity_insufficient"),
        "recomputed_abc_cr": calc.get("recomputed_abc_cr"),
        "abc_minus_ecv_cr": calc.get("abc_minus_ecv_cr"),
        "capacity_ratio": calc.get("capacity_ratio"),
        "meets_threshold": (verdict == "QUALIFIED"),

        # outcome
        "verdict": verdict, "decision_reason": decision_reason,

        # ground-truth cross-check
        "ground_truth_meets": bool(qualifies_seed) if qualifies_seed is not None else None,
        "ground_truth_label": designed_to_trip,
        "predicted_matches_ground_truth": matches_ground_truth,

        # extraction metadata
        "extracted_by": "tier2:bid_abc_check_v1",
        "rule_shape": "formula_verification_bidder_side",
        "extraction_path": "structured_fact_sheets_formula_recompute",
        "input_contract": "fact_sheets.Statement-X-BidCapacity",
        "defeated": False,
    }

    finding = rest_post("kg_nodes", [{
        "doc_id": BID_ID, "node_type": "BidEvaluationFinding",
        "label": label, "properties": finding_props,
        "source_ref": f"tier2:bid_abc_check:{RULE_ID}",
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
                "M_violation": calc.get("M_violation"),
                "arithmetic_error": calc.get("arithmetic_error"),
                "capacity_insufficient": calc.get("capacity_insufficient"),
                "declared_abc_cr": declared_ABC, "ecv_cr": ECV,
                "decision_reason": decision_reason,
                "finding_node_id": finding["node_id"], "defeated": False,
            },
        }])[0]
        print(f"  → BIDDER_VIOLATES_RULE {edge['edge_id']}")
    else:
        print(f"  → no edge ({verdict} is silent)")

    wall = time.perf_counter() - t_start
    print(f"\n  TIMING wall={wall*1000:.0f}ms verdict={verdict} gt_match={matches_ground_truth}")
    if matches_ground_truth is False:
        return 2
    return 0


def _emit_skip_finding(bid_id, bid_node, bidder_props, tender_id,
                       bidder_profile_id, bidder_name):
    return rest_post("kg_nodes", [{
        "doc_id": bid_id, "node_type": "BidEvaluationFinding",
        "label": f"{TYPOLOGY}: SKIP — AP-GO-062 does not fire",
        "properties": {
            "tier": TIER, "typology_code": TYPOLOGY, "rule_id": RULE_ID,
            "severity": "ADVISORY", "evaluation_consequence": "ADVISORY",
            "verdict": "SKIP_NOT_APPLICABLE",
            "decision_reason": "ap_go_062_condition_when_did_not_fire",
            "bid_submission_id": bid_id, "bid_submission_node_id": bid_node["node_id"],
            "bidder_profile_id": bidder_profile_id, "bidder_name": bidder_name,
            "tender_id": tender_id,
            "extracted_by": "tier2:bid_abc_check_v1",
            "extraction_path": "skip_rule_inapplicable",
            "input_contract": "fact_sheets.Statement-X-BidCapacity",
            "defeated": False,
        },
        "source_ref": f"tier2:bid_abc_check:{RULE_ID}",
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
            "extracted_by": "tier2:bid_abc_check_v1",
            "extraction_path": "gap_missing_input_facts",
            "input_contract": "fact_sheets.Statement-X-BidCapacity",
            "defeated": False,
        },
        "source_ref": f"tier2:bid_abc_check:{RULE_ID}",
    }])[0]


if __name__ == "__main__":
    from modules.validation.verdict_emitter import main_with_crash_resilience
    raise SystemExit(main_with_crash_resilience(
        main, doc_id=BID_ID, typology=TYPOLOGY))
