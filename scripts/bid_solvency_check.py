"""
scripts/bid_solvency_check.py

═══════════════════════════════════════════════════════════════════
  Tier-2 Bid Submission Evaluator — Bidder-Solvency-Compliance
═══════════════════════════════════════════════════════════════════
Validates the bidder's submitted Solvency Certificate against the
AP-GO-089 framework: cert must be issued within 12 months AND the
declared amount must be ≥ the required threshold (10% of class
monetary minimum per AP-GO-089).

Tier-1 counterpart `tier1_solvency_check.py` asks:
  "Does the TENDER DOCUMENT carry the AP-GO-089 solvency clause?"
  (doc-side: framework presence)

This Tier-2 evaluator asks:
  "Does THIS BIDDER'S submitted Solvency Certificate satisfy the
  AP-GO-089 framework (12mo + 10% threshold)?"  (bid-side)

Verdict vocabulary (Tier-2 four-state, per L61):
  QUALIFIED              — cert ≤12mo AND declared ≥ required
  INELIGIBLE             — cert >12mo (stale) OR declared < required
                           (decision_reason distinguishes the two)
  GAP_INSUFFICIENT_DATA  — missing inputs
  SKIP_NOT_APPLICABLE    — AP-GO-089 condition_when does NOT fire

Universal rule — no tender-side parameter (AP-GO-089 cap is universal).
rule_shape: "date_threshold_bidder_side"
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


# ── Constants ─────────────────────────────────────────────────────────

BID_ID = sys.argv[1] if len(sys.argv) > 1 else "bid_synth_b1_kurnool"

TYPOLOGY = "Bidder-Solvency-Compliance"
TIER = 2
RULE_ID = "AP-GO-089"

# AP-GO-089: solvency cert validity cap (months)
SOLVENCY_VALIDITY_CAP_MONTHS = 12


# ── Supabase REST helpers ─────────────────────────────────────────────

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


# ── Idempotent cleanup ────────────────────────────────────────────────

def _delete_prior_tier2_bid_solvency(bid_id: str) -> tuple[int, int]:
    edges = rest_get("kg_edges", {
        "select":                "edge_id",
        "doc_id":                f"eq.{bid_id}",
        "edge_type":             "eq.BIDDER_VIOLATES_RULE",
        "properties->>typology": f"eq.{TYPOLOGY}",
        "properties->>tier":     f"eq.{TIER}",
    })
    n_e = 0
    for e in edges:
        rest_delete("kg_edges", {"edge_id": f"eq.{e['edge_id']}"}); n_e += 1
    findings = rest_get("kg_nodes", {
        "select":                     "node_id",
        "doc_id":                     f"eq.{bid_id}",
        "node_type":                  "eq.BidEvaluationFinding",
        "properties->>typology_code": f"eq.{TYPOLOGY}",
        "properties->>tier":          f"eq.{TIER}",
    })
    n_f = 0
    for f in findings:
        rest_delete("kg_nodes", {"node_id": f"eq.{f['node_id']}"}); n_f += 1
    return n_f, n_e


# ── RuleNode get-or-create ────────────────────────────────────────────

def get_or_create_rule_node(parent_doc_id: str, rule_id: str) -> str:
    existing = rest_get("kg_nodes", {
        "select":               "node_id",
        "doc_id":               f"eq.{parent_doc_id}",
        "node_type":            "eq.RuleNode",
        "properties->>rule_id": f"eq.{rule_id}",
    })
    if existing:
        return existing[0]["node_id"]
    rule_rows = rest_get("rules", {
        "select":  "rule_id,natural_language,layer,severity,rule_type,pattern_type,typology_code,defeats",
        "rule_id": f"eq.{rule_id}",
    })
    r = rule_rows[0] if rule_rows else {}
    inserted = rest_post("kg_nodes", [{
        "doc_id":    parent_doc_id,
        "node_type": "RuleNode",
        "label":     f"{rule_id}: {(r.get('natural_language') or '')[:90]}",
        "properties": {
            "rule_id":       rule_id,
            "layer":         r.get("layer"),
            "severity":      r.get("severity"),
            "rule_type":     r.get("rule_type"),
            "pattern_type":  r.get("pattern_type"),
            "typology_code": r.get("typology_code"),
            "defeats":       r.get("defeats") or [],
        },
        "source_ref": f"rules:{rule_id}",
    }])
    return inserted[0]["node_id"]


# ── Fact loaders ──────────────────────────────────────────────────────

def load_bid_submission(bid_id: str) -> dict:
    rows = rest_get("kg_nodes", {
        "select":    "node_id,doc_id,label,properties",
        "doc_id":    f"eq.{bid_id}",
        "node_type": "eq.BidSubmission",
    })
    if not rows:
        raise RuntimeError(f"No BidSubmission node found for doc_id={bid_id!r}")
    return rows[0]


def load_bidder_profile(bidder_profile_id: str) -> dict:
    rows = rest_get("kg_nodes", {
        "select":    "node_id,doc_id,label,properties",
        "doc_id":    f"eq.{bidder_profile_id}",
        "node_type": "eq.BidderProfile",
    })
    if not rows:
        raise RuntimeError(
            f"No BidderProfile found for doc_id={bidder_profile_id!r}")
    return rows[0]


def load_statement_viii_fact(bid_id: str) -> dict | None:
    rows = rest_get("fact_sheets", {
        "select":     "id,doc_id,fact_group,extracted_facts,section_heading,"
                      "source_file,line_start,line_end,extracted_by",
        "doc_id":     f"eq.{bid_id}",
        "fact_group": "eq.Statement-VIII-FinancialSolvency",
    })
    return rows[0] if rows else None


# ── Rule selection ────────────────────────────────────────────────────

def select_rule(tender_state: str, tender_type: str | None) -> dict | None:
    rows = rest_get("rules", {
        "select":  "rule_id,condition_when,defeats,severity,layer,"
                   "natural_language,typology_code",
        "rule_id": f"eq.{RULE_ID}",
    })
    if not rows:
        return None
    rule = rows[0]
    cw = rule.get("condition_when") or ""
    facts = {
        "TenderState":  tender_state,
        "TenderType":   tender_type,
        "tender_type":  tender_type,
    }
    verdict = evaluate_when(cw, facts).verdict
    print(f"  [{RULE_ID}] condition={cw!r}")
    print(f"  facts: TenderState={tender_state!r}, TenderType={tender_type!r}")
    print(f"  → verdict={verdict.value}")
    if verdict == Verdict.SKIP:
        return None
    severity_origin = rule.get("severity")
    severity_effective = severity_origin
    verdict_origin = "FIRE"
    if verdict == Verdict.UNKNOWN:
        severity_effective = "ADVISORY"
        verdict_origin = "UNKNOWN"
        print(f"  ⚠ L27 downgrade: severity {severity_origin} → ADVISORY")
    return {
        "rule_id":          rule["rule_id"],
        "severity":         severity_effective,
        "severity_origin":  severity_origin,
        "verdict_origin":   verdict_origin,
        "layer":            rule.get("layer"),
        "typology_code":    rule.get("typology_code"),
        "natural_language": rule.get("natural_language"),
        "condition_when":   cw,
        "defeats":          rule.get("defeats") or [],
        "_facts_evaluated": facts,
    }


# ── Verdict logic ─────────────────────────────────────────────────────

def compute_verdict(validity_months_ago, declared_solvency_cr,
                    required_solvency_cr, validity_window_months=None) -> tuple[str, dict]:
    """Ext-5: validity_window_months is now a parameter (was module-level constant).
    Defaults to SOLVENCY_VALIDITY_CAP_MONTHS (12) when not supplied — preserves
    backward compat for existing callers."""
    if validity_window_months is None:
        validity_window_months = SOLVENCY_VALIDITY_CAP_MONTHS
    if (validity_months_ago is None or declared_solvency_cr is None
            or required_solvency_cr is None):
        return "GAP_INSUFFICIENT_DATA", {
            "validity_months_ago":  validity_months_ago,
            "validity_window_months": validity_window_months,
            "declared_solvency_cr": declared_solvency_cr,
            "required_solvency_cr": required_solvency_cr,
            "stale":                None,
            "insufficient":         None,
            "amount_delta_cr":      None,
        }
    stale = validity_months_ago > validity_window_months
    insufficient = declared_solvency_cr < required_solvency_cr
    amount_delta = declared_solvency_cr - required_solvency_cr
    if stale or insufficient:
        return "INELIGIBLE", {
            "validity_months_ago":     validity_months_ago,
            "validity_window_months":  validity_window_months,
            "declared_solvency_cr":    declared_solvency_cr,
            "required_solvency_cr":    required_solvency_cr,
            "stale":                   stale,
            "insufficient":            insufficient,
            "amount_delta_cr":         round(amount_delta, 4),
        }
    return "QUALIFIED", {
        "validity_months_ago":     validity_months_ago,
        "validity_window_months":  validity_window_months,
        "declared_solvency_cr":    declared_solvency_cr,
        "required_solvency_cr":    required_solvency_cr,
        "stale":                   False,
        "insufficient":            False,
        "amount_delta_cr":         round(amount_delta, 4),
    }


def evaluation_consequence_for(verdict: str) -> str:
    return {
        "INELIGIBLE":            "HARD_BLOCK",
        "QUALIFIED":             "ADVISORY",
        "GAP_INSUFFICIENT_DATA": "WARNING",
        "SKIP_NOT_APPLICABLE":   "ADVISORY",
    }.get(verdict, "ADVISORY")


# ── Main ──────────────────────────────────────────────────────────────

def main() -> int:
    t_start = time.perf_counter()
    print("=" * 76)
    print(f"  Tier-2 Bid Submission Evaluator — {TYPOLOGY}")
    print(f"  bid_id : {BID_ID}")
    print(f"  rule   : {RULE_ID}  (validity cap = {SOLVENCY_VALIDITY_CAP_MONTHS}mo)")
    print("=" * 76)

    n_f, n_e = _delete_prior_tier2_bid_solvency(BID_ID)
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
    print(f"\n── Bidder ──")
    print(f"  name              : {bidder_name}")
    print(f"  contractor_class  : {bidder_props.get('contractor_class')}")

    fact_row = load_statement_viii_fact(BID_ID)
    if fact_row is None:
        finding = _emit_gap_finding(BID_ID, bid_node, bidder_props, tender_id,
                                    bidder_profile_id, None,
                                    reason="missing_statement_viii_fact_sheet")
        print(f"  → BidEvaluationFinding {finding['node_id']}  (GAP)")
        return 0

    ef = fact_row.get("extracted_facts") or {}
    cert_source           = ef.get("certificate_source")
    cert_ref              = ef.get("certificate_ref")
    issue_date            = ef.get("issue_date")
    validity_months_ago   = ef.get("validity_months_ago")
    is_within_one_year    = ef.get("is_within_one_year")
    declared_solvency_cr  = ef.get("declared_solvency_cr")
    required_solvency_cr  = ef.get("required_solvency_cr")
    class_minimum_cr      = ef.get("class_minimum_cr")
    designed_to_trip      = ef.get("_designed_to_trip")
    meets_threshold_seed  = ef.get("meets_threshold")

    print(f"\n── Statement-VIII fact ──")
    print(f"  fact_sheet_id     : {fact_row['id']}")
    print(f"  cert_source       : {cert_source}  ref={cert_ref}")
    print(f"  issue_date        : {issue_date}")
    print(f"  validity_months_ago: {validity_months_ago}mo (cap {SOLVENCY_VALIDITY_CAP_MONTHS}mo)")
    print(f"  is_within_one_year: {is_within_one_year}")
    print(f"  declared_solvency : ₹{declared_solvency_cr}cr")
    print(f"  required_solvency : ₹{required_solvency_cr}cr")
    print(f"  class_minimum     : ₹{class_minimum_cr}cr")

    # Defensive cross-check: BidderProfile.solvency_cert_validity_months_ago
    # should match the Statement-VIII value
    profile_age = bidder_props.get("solvency_cert_validity_months_ago")
    age_consistent = (profile_age == validity_months_ago)
    profile_source = bidder_props.get("solvency_cert_source")
    source_consistent = (profile_source == cert_source)
    if not age_consistent:
        print(f"  ⚠ validity age mismatch (fact={validity_months_ago}, profile={profile_age})")
    if not source_consistent:
        print(f"  ⚠ cert source mismatch (fact={cert_source!r}, profile={profile_source!r})")

    print(f"\n── Rule selection ──")
    rule = select_rule(tender_state="AndhraPradesh", tender_type="Works")
    if rule is None:
        verdict = "SKIP_NOT_APPLICABLE"
        finding = _emit_skip_finding(BID_ID, bid_node, bidder_props, tender_id,
                                     bidder_profile_id, bidder_name)
        print(f"  → {verdict}  ({finding['node_id']})")
        return 0

    # Ext-5: read configurable validity window from BidderProfile.
    # Default 12mo preserves existing B1-B8 behavior.
    validity_window = bidder_props.get("solvency_cert_validity_window_months",
                                        SOLVENCY_VALIDITY_CAP_MONTHS)
    source_rule = bidder_props.get("solvency_cert_source_rule", "AP_GO_089_12MO")
    print(f"  Ext-5: validity_window={validity_window}mo (source_rule={source_rule!r})")

    verdict, calc = compute_verdict(validity_months_ago, declared_solvency_cr,
                                    required_solvency_cr,
                                    validity_window_months=validity_window)
    # Surface Ext-5 metadata on calc for citation chain
    calc["solvency_cert_source_rule"] = source_rule
    calc["solvency_cert_validity_window_months"] = validity_window
    consequence = evaluation_consequence_for(verdict)
    print(f"\n── Decision ──")
    print(f"  stale             : {calc['stale']}")
    print(f"  insufficient      : {calc['insufficient']}")
    print(f"  amount_delta_cr   : {calc['amount_delta_cr']}")
    print(f"  verdict           : {verdict}")
    print(f"  consequence       : {consequence}")

    # Ground truth: seed marks meets_threshold AND is_within_one_year;
    # ground-truth "compliant" = both true
    gt_compliant = (bool(meets_threshold_seed) and bool(is_within_one_year)
                    if meets_threshold_seed is not None and is_within_one_year is not None
                    else None)
    predicted_meets = (verdict == "QUALIFIED")
    matches_ground_truth = (predicted_meets == gt_compliant
                            if gt_compliant is not None else None)
    print(f"  ground_truth      : {gt_compliant}  (meets_threshold={meets_threshold_seed}, within_1y={is_within_one_year})")
    print(f"  predicted_matches : {matches_ground_truth}")
    if designed_to_trip:
        print(f"  designed_to_trip  : {designed_to_trip}")

    rule_node_id = get_or_create_rule_node(BID_ID, RULE_ID)

    # decision_reason distinguishes stale vs insufficient (composite reasons
    # when both fire)
    if verdict == "QUALIFIED":
        decision_reason = (
            f"qualified_cert_{cert_source}_age_{validity_months_ago}mo"
            f"_declared_{declared_solvency_cr}cr_above_required_{required_solvency_cr}cr"
        )
    elif calc.get("stale") and calc.get("insufficient"):
        decision_reason = (
            f"ineligible_cert_age_{validity_months_ago}mo_exceeds_"
            f"{SOLVENCY_VALIDITY_CAP_MONTHS}mo_cap_AND_declared_"
            f"{declared_solvency_cr}cr_below_required_{required_solvency_cr}cr"
        )
    elif calc.get("stale"):
        decision_reason = (
            f"ineligible_cert_age_{validity_months_ago}mo_exceeds_"
            f"{SOLVENCY_VALIDITY_CAP_MONTHS}mo_cap_per_ap_go_089"
        )
    elif calc.get("insufficient"):
        decision_reason = (
            f"ineligible_declared_solvency_{declared_solvency_cr}cr_"
            f"below_required_{required_solvency_cr}cr"
        )
    else:
        decision_reason = "gap_insufficient_data"

    label = (
        f"{TYPOLOGY}: {bidder_name} cert {cert_source} {validity_months_ago}mo "
        f"₹{declared_solvency_cr}cr/₹{required_solvency_cr}cr → {verdict}"
    )

    finding_props = {
        # — identity —
        "tier":                       TIER,
        "typology_code":              TYPOLOGY,
        "rule_id":                    RULE_ID,
        "severity":                   rule.get("severity"),
        "evaluation_consequence":     consequence,

        # — bidder citation —
        "bid_submission_id":          BID_ID,
        "bid_submission_node_id":     bid_node["node_id"],
        "bidder_profile_id":          bidder_profile_id,
        "bidder_profile_node_id":     bidder_node["node_id"],
        "bidder_name":                bidder_name,
        "bidder_contractor_class":    bidder_props.get("contractor_class"),
        "bidder_pan":                 bidder_props.get("pan"),
        "fact_sheet_id":              fact_row["id"],
        "fact_sheet_fact_group":      fact_row["fact_group"],
        "fact_sheet_source_file":     fact_row.get("source_file"),
        "fact_sheet_extracted_by":    fact_row.get("extracted_by"),
        "cert_source":                cert_source,
        "cert_ref":                   cert_ref,
        "cert_issue_date":            issue_date,
        "validity_months_ago":        validity_months_ago,
        "is_within_one_year":         is_within_one_year,
        "declared_solvency_cr":       declared_solvency_cr,
        "required_solvency_cr":       required_solvency_cr,
        "class_minimum_cr":           class_minimum_cr,
        "profile_validity_consistent": age_consistent,
        "profile_source_consistent":   source_consistent,

        # — tender criterion citation (universal rule, no tender param) —
        "tender_id":                  tender_id,
        "tender_nit_no":              bid_props.get("tender_nit_no"),
        "criterion_source":           "universal_rule_no_tender_parameter",

        # — regulatory rule citation —
        "rule_natural_language":      rule.get("natural_language"),
        "rule_condition_when":        rule.get("condition_when"),
        "rule_layer":                 rule.get("layer"),
        "rule_typology_code":         rule.get("typology_code"),
        "rule_facts_evaluated":       rule.get("_facts_evaluated"),
        "verdict_origin":             rule.get("verdict_origin"),
        "severity_origin":            rule.get("severity_origin"),
        "validity_cap_months":        SOLVENCY_VALIDITY_CAP_MONTHS,

        # — computation —
        "stale":                      calc.get("stale"),
        "insufficient":               calc.get("insufficient"),
        "amount_delta_cr":            calc.get("amount_delta_cr"),
        "meets_threshold":            (verdict == "QUALIFIED"),

        # — outcome —
        "verdict":                    verdict,
        "decision_reason":            decision_reason,

        # — ground-truth cross-check —
        "ground_truth_meets":              gt_compliant,
        "ground_truth_label":              designed_to_trip,
        "predicted_matches_ground_truth":  matches_ground_truth,

        # — extraction metadata —
        "extracted_by":               "tier2:bid_solvency_check_v1",
        "rule_shape":                 "date_threshold_bidder_side",
        "extraction_path":            "structured_fact_sheets_date_amount_compare",
        "input_contract":             "fact_sheets.Statement-VIII-FinancialSolvency",
        "defeated":                   False,
    }

    finding = rest_post("kg_nodes", [{
        "doc_id":     BID_ID,
        "node_type":  "BidEvaluationFinding",
        "label":      label,
        "properties": finding_props,
        "source_ref": f"tier2:bid_solvency_check:{RULE_ID}",
    }])[0]
    print(f"\n  → BidEvaluationFinding {finding['node_id']}")

    edge = None
    if verdict == "INELIGIBLE":
        edge = rest_post("kg_edges", [{
            "doc_id":       BID_ID,
            "from_node_id": bid_node["node_id"],
            "to_node_id":   rule_node_id,
            "edge_type":    "BIDDER_VIOLATES_RULE",
            "weight":       1.0,
            "properties": {
                "tier":                    TIER,
                "rule_id":                 RULE_ID,
                "typology":                TYPOLOGY,
                "severity":                rule.get("severity"),
                "evaluation_consequence":  consequence,
                "verdict":                 verdict,
                "bid_submission_id":       BID_ID,
                "bidder_profile_id":       bidder_profile_id,
                "validity_months_ago":     validity_months_ago,
                "declared_solvency_cr":    declared_solvency_cr,
                "required_solvency_cr":    required_solvency_cr,
                "stale":                   calc.get("stale"),
                "insufficient":            calc.get("insufficient"),
                "decision_reason":         decision_reason,
                "finding_node_id":         finding["node_id"],
                "defeated":                False,
            },
        }])[0]
        print(f"  → BIDDER_VIOLATES_RULE {edge['edge_id']}")
    else:
        print(f"  → no edge ({verdict} is silent)")

    wall = time.perf_counter() - t_start
    print()
    print("=" * 76)
    print(f"  TIMING: wall={wall*1000:.0f}ms  verdict={verdict}  "
          f"gt_match={matches_ground_truth}")
    print("=" * 76)
    if matches_ground_truth is False:
        return 2
    return 0


def _emit_skip_finding(bid_id, bid_node, bidder_props, tender_id,
                       bidder_profile_id, bidder_name) -> dict:
    return rest_post("kg_nodes", [{
        "doc_id":    bid_id,
        "node_type": "BidEvaluationFinding",
        "label":     f"{TYPOLOGY}: SKIP — AP-GO-089 does not fire",
        "properties": {
            "tier": TIER, "typology_code": TYPOLOGY, "rule_id": RULE_ID,
            "severity": "ADVISORY", "evaluation_consequence": "ADVISORY",
            "verdict": "SKIP_NOT_APPLICABLE",
            "decision_reason": "ap_go_089_condition_when_did_not_fire",
            "bid_submission_id": bid_id, "bid_submission_node_id": bid_node["node_id"],
            "bidder_profile_id": bidder_profile_id, "bidder_name": bidder_name,
            "tender_id": tender_id,
            "extracted_by": "tier2:bid_solvency_check_v1",
            "extraction_path": "skip_rule_inapplicable",
            "input_contract": "fact_sheets.Statement-VIII-FinancialSolvency",
            "defeated": False,
        },
        "source_ref": f"tier2:bid_solvency_check:{RULE_ID}",
    }])[0]


def _emit_gap_finding(bid_id, bid_node, bidder_props, tender_id,
                      bidder_profile_id, rule, reason: str) -> dict:
    bidder_name = (bidder_props.get("company_name")
                   or bidder_props.get("bidder_name") or "(unnamed)")
    return rest_post("kg_nodes", [{
        "doc_id":    bid_id,
        "node_type": "BidEvaluationFinding",
        "label":     f"{TYPOLOGY}: GAP — {reason}",
        "properties": {
            "tier": TIER, "typology_code": TYPOLOGY, "rule_id": RULE_ID,
            "severity": (rule.get("severity") if rule else "ADVISORY"),
            "evaluation_consequence": "WARNING",
            "verdict": "GAP_INSUFFICIENT_DATA", "decision_reason": reason,
            "bid_submission_id": bid_id, "bid_submission_node_id": bid_node["node_id"],
            "bidder_profile_id": bidder_profile_id, "bidder_name": bidder_name,
            "tender_id": tender_id,
            "extracted_by": "tier2:bid_solvency_check_v1",
            "extraction_path": "gap_missing_input_facts",
            "input_contract": "fact_sheets.Statement-VIII-FinancialSolvency",
            "defeated": False,
        },
        "source_ref": f"tier2:bid_solvency_check:{RULE_ID}",
    }])[0]


if __name__ == "__main__":
    from modules.validation.verdict_emitter import main_with_crash_resilience
    raise SystemExit(main_with_crash_resilience(
        main, doc_id=BID_ID, typology=TYPOLOGY))
