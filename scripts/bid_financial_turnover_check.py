"""
scripts/bid_financial_turnover_check.py

═══════════════════════════════════════════════════════════════════
  Tier-2 Bid Submission Evaluator — Bidder-Financial-Turnover-Eligibility
  (Module 3 Extension 3 — Dual Turnover Criterion, pilot extension)
═══════════════════════════════════════════════════════════════════
Validates the bidder's 3-year financial turnover (audited balance
sheet operational income) against the tender's financial PQ floor,
distinct from the existing 5-year construction-turnover check
(bid_turnover_check, anchored on CVC-028).

This validator is the FINANCIAL half of the Ext-3 Dual Turnover
split. The CONSTRUCTION half stays on bid_turnover_check anchored
on CVC-028 (operationally treated as 5-year construction floor).

Rule anchor:
  MPG-255 (HARD_BLOCK) — "Pre-Qualification Criterion 3 (Financial
  Standing): the average annual financial turnover of the bidder
  over the last three years ending 'The Relevant Date' must be at
  the specified Rs. millions threshold (or equivalent foreign
  currency at exchange rate prevalent on Relevant Date), as per the
  audited annual report duly authenticated by a Chartered
  Accountant/Cost Accountant. The net worth of the bidder shall NOT
  be negative."

L27 downgrade EXPECTED: MPG-255 condition_when has
PrequalificationApplied unknown subterm → severity HARD_BLOCK →
ADVISORY in audit fields. evaluation_consequence on INELIGIBLE
remains HARD_BLOCK per Tier-2 semantic-blocking convention
(mirrors bid_similar_works pattern).

Verdict (Tier-2 four-state, per L61):
  QUALIFIED              — bidder.financial_3yr ≥ tender.financial_pq_floor
  INELIGIBLE             — bidder.financial_3yr < tender.financial_pq_floor
  GAP_INSUFFICIENT_DATA  — missing bidder financial or tender floor
  SKIP_NOT_APPLICABLE    — MPG-255 condition_when does NOT fire

Reads:
  - kg_nodes.BidderProfile.properties.financial_turnover_3yr_avg_cr
  - fact_sheets.Statement-I-AnnualTurnover.extracted_facts.financial_3yr_cr
    (defensive cross-check vs BidderProfile)
  - fact_sheets.Statement-I-AnnualTurnover.extracted_facts.financial_pq_floor_cr
    (tender PQ floor; mirrors existing pq_floor_cr pattern)

rule_shape: "threshold_bidder_side"
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

TYPOLOGY = "Bidder-Financial-Turnover-Eligibility"
TIER = 2
RULE_ID = "MPG-255"
SOURCE_REF = "ext-3:bid_financial_turnover_check_v1"


# ── Synthetic tender catalog (Ext-3 financial floors per CVC-028 30% × ECV) ──
# Mirrors bid_turnover_check.SYNTHETIC_TENDER_CATALOG pattern (Path γ from
# Ext-3 diagnose: BOTH fact_sheets and in-script catalog, defensive
# cross-check pattern).

SYNTHETIC_TENDER_CATALOG: dict[str, dict] = {
    "tender_synth_kurnool": dict(
        tender_type="Works",
        work_type="Civil",
        is_ap_tender=True,
        estimated_value_cr=85.0,
        tenure_years=3,
        nit_no="100/PROC/APIIC/1/2026",
        title="District Hospital, Kurnool",
        financial_pq_floor_cr=25.5,      # 30% × 85
    ),
    "tender_synth_ja": dict(
        tender_type="Works",
        work_type="Civil",
        is_ap_tender=True,
        estimated_value_cr=125.5,
        tenure_years=3,
        nit_no="JA/2026/CW/001",
        title="Andhra Pradesh Judicial Academy",
        financial_pq_floor_cr=37.65,     # 30% × 125.5
    ),
    "tender_synth_hc": dict(
        tender_type="Works",
        work_type="Civil",
        is_ap_tender=True,
        estimated_value_cr=365.16,
        tenure_years=3,
        nit_no="HC/2026/CW/001",
        title="Andhra Pradesh High Court",
        financial_pq_floor_cr=109.55,    # 30% × 365.16
    ),
}


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


# ── Idempotent cleanup ────────────────────────────────────────────────

def _delete_prior_tier2_bid_financial_turnover(bid_id: str) -> tuple[int, int]:
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


# ── RuleNode get-or-create (mirror bid_turnover_check pattern) ────────

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
        raise RuntimeError(f"No BidSubmission for doc_id={bid_id!r}")
    return rows[0]


def load_bidder_profile(bidder_profile_id: str) -> dict:
    rows = rest_get("kg_nodes", {
        "select":    "node_id,doc_id,label,properties",
        "doc_id":    f"eq.{bidder_profile_id}",
        "node_type": "eq.BidderProfile",
    })
    if not rows:
        raise RuntimeError(f"No BidderProfile for doc_id={bidder_profile_id!r}")
    return rows[0]


def load_statement_i_fact(bid_id: str) -> dict | None:
    rows = rest_get("fact_sheets", {
        "select":     "id,doc_id,fact_group,extracted_facts,section_heading,"
                      "source_file,line_start,line_end,extracted_by",
        "doc_id":     f"eq.{bid_id}",
        "fact_group": "eq.Statement-I-AnnualTurnover",
    })
    return rows[0] if rows else None


# ── Rule selection ────────────────────────────────────────────────────

def select_rule(tender: dict) -> dict | None:
    rows = rest_get("rules", {
        "select":  "rule_id,condition_when,defeats,severity,layer,natural_language,typology_code",
        "rule_id": f"eq.{RULE_ID}",
    })
    if not rows:
        return None
    rule = rows[0]
    cw = rule.get("condition_when") or ""
    # MPG-255: TenderType=ANY AND PrequalificationApplied=true
    # PrequalificationApplied is not in our synthetic facts → UNKNOWN
    # → L27 downgrade to ADVISORY (accepted; mirrors bid_similar_works pattern)
    facts = {
        "TenderType":              tender.get("tender_type"),
        "tender_type":             tender.get("tender_type"),
        "TenderState":             ("AndhraPradesh" if tender.get("is_ap_tender") else "Other"),
        "is_ap_tender":            bool(tender.get("is_ap_tender")),
    }
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
        print(f"  ⚠ L27 downgrade: {severity_origin} → ADVISORY (PrequalificationApplied unknown)")
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

def compute_verdict(financial_3yr_cr: float | None,
                    financial_pq_floor_cr: float | None) -> tuple[str, dict]:
    if financial_3yr_cr is None or financial_pq_floor_cr is None:
        return "GAP_INSUFFICIENT_DATA", {
            "delta_cr": None, "ratio": None, "meets_threshold": None,
        }
    delta = financial_3yr_cr - financial_pq_floor_cr
    ratio = (financial_3yr_cr / financial_pq_floor_cr
             if financial_pq_floor_cr > 0 else None)
    meets = financial_3yr_cr >= financial_pq_floor_cr
    return ("QUALIFIED" if meets else "INELIGIBLE"), {
        "delta_cr":        round(delta, 4),
        "ratio":           round(ratio, 4) if ratio is not None else None,
        "meets_threshold": meets,
    }


def evaluation_consequence_for(verdict: str) -> str:
    return {"INELIGIBLE": "HARD_BLOCK", "QUALIFIED": "ADVISORY",
            "GAP_INSUFFICIENT_DATA": "WARNING",
            "SKIP_NOT_APPLICABLE": "ADVISORY"}.get(verdict, "ADVISORY")


# ── Main ──────────────────────────────────────────────────────────────

def main() -> int:
    t_start = time.perf_counter()
    print("=" * 76)
    print(f"  Tier-2 Bid Evaluator — {TYPOLOGY} (Ext-3 pilot extension)")
    print(f"  bid_id : {BID_ID}    rule: {RULE_ID}")
    print("=" * 76)

    n_f, n_e = _delete_prior_tier2_bid_financial_turnover(BID_ID)
    if n_f or n_e:
        print(f"  cleanup: removed {n_f} prior finding(s) + {n_e} edge(s)")

    bid_node = load_bid_submission(BID_ID)
    bid_props = bid_node["properties"] or {}
    bidder_profile_id = (bid_props.get("bidder_profile_id")
                         or bid_props.get("bidder_id"))
    tender_id = bid_props.get("tender_id")
    print(f"\n── Bid submission ──")
    print(f"  bid_node_id       : {bid_node['node_id']}")
    print(f"  bidder_profile_id : {bidder_profile_id}")
    print(f"  tender_id         : {tender_id}")

    bidder_node = load_bidder_profile(bidder_profile_id)
    bidder_props = bidder_node["properties"] or {}
    bidder_name = (bidder_props.get("company_name")
                   or bidder_props.get("bidder_name") or "(unnamed)")
    print(f"\n── Bidder profile ──")
    print(f"  name              : {bidder_name}")
    print(f"  financial_3yr     : ₹{bidder_props.get('financial_turnover_3yr_avg_cr')}cr")
    print(f"  construction_5yr  : ₹{bidder_props.get('construction_turnover_5yr_avg_cr')}cr (Ext-3 alias)")

    tender = SYNTHETIC_TENDER_CATALOG.get(tender_id)
    if tender is None:
        raise RuntimeError(
            f"Tender {tender_id!r} not in SYNTHETIC_TENDER_CATALOG — "
            f"add an entry (Ext-3 catalog mirror)"
        )
    print(f"\n── Tender criterion (Ext-3 financial) ──")
    print(f"  title              : {tender.get('title')}")
    print(f"  estimated_value    : ₹{tender.get('estimated_value_cr')}cr")
    print(f"  financial_pq_floor : ₹{tender.get('financial_pq_floor_cr')}cr (30% × ECV per CVC-028 minimum)")

    # Rule pick
    print(f"\n── Rule selection ──")
    rule = select_rule(tender)
    if rule is None:
        verdict = "SKIP_NOT_APPLICABLE"
        print(f"  → {verdict} (MPG-255 condition_when does not fire)")
        finding = _emit_skip_finding(BID_ID, bid_node, bidder_props,
                                     tender, tender_id, bidder_profile_id)
        print(f"  → BidEvaluationFinding {finding['node_id']}")
        return 0

    # Load Statement-I (defensive cross-check on financial fact)
    fact_row = load_statement_i_fact(BID_ID)
    if fact_row is None:
        finding = _emit_gap_finding(BID_ID, bid_node, bidder_props, tender,
                                    tender_id, bidder_profile_id, rule,
                                    reason="missing_statement_i_fact_sheet")
        print(f"  → BidEvaluationFinding {finding['node_id']}  (GAP)")
        return 0

    ef = fact_row.get("extracted_facts") or {}
    financial_3yr_from_fact = ef.get("financial_3yr_cr")
    financial_3yr_from_profile = bidder_props.get("financial_turnover_3yr_avg_cr")
    financial_floor_from_fact = ef.get("financial_pq_floor_cr")
    financial_floor_from_catalog = tender.get("financial_pq_floor_cr")
    designed_to_trip = ef.get("_designed_to_trip")

    print(f"\n── Statement-I (Ext-3 financial sub-fields) ──")
    print(f"  fact_sheet_id           : {fact_row['id']}")
    print(f"  financial_3yr (fact)    : ₹{financial_3yr_from_fact}cr")
    print(f"  financial_3yr (profile) : ₹{financial_3yr_from_profile}cr")
    print(f"  financial_floor (fact)  : ₹{financial_floor_from_fact}cr")
    print(f"  financial_floor (catalog): ₹{financial_floor_from_catalog}cr")

    financial_3yr_consistent = (financial_3yr_from_fact == financial_3yr_from_profile)
    financial_floor_consistent = (financial_floor_from_fact == financial_floor_from_catalog)
    if not financial_3yr_consistent:
        print(f"  ⚠ financial_3yr mismatch (fact={financial_3yr_from_fact}, profile={financial_3yr_from_profile})")
    if not financial_floor_consistent:
        print(f"  ⚠ financial_floor mismatch (fact={financial_floor_from_fact}, catalog={financial_floor_from_catalog})")

    financial_3yr_used = financial_3yr_from_fact if financial_3yr_from_fact is not None else financial_3yr_from_profile
    financial_floor_used = financial_floor_from_fact if financial_floor_from_fact is not None else financial_floor_from_catalog

    verdict, calc = compute_verdict(financial_3yr_used, financial_floor_used)
    consequence = evaluation_consequence_for(verdict)
    print(f"\n── Decision ──")
    print(f"  bidder_financial_3yr : ₹{financial_3yr_used}cr")
    print(f"  tender_financial_pq  : ₹{financial_floor_used}cr")
    print(f"  delta                : {calc['delta_cr']}")
    print(f"  ratio                : {calc['ratio']}")
    print(f"  verdict              : {verdict}")
    print(f"  consequence          : {consequence}")
    print(f"  rule.severity        : {rule['severity']}")

    # Ground-truth cross-check
    seed_gt_meets = ef.get("meets_financial_pq_threshold")
    predicted_meets = (verdict == "QUALIFIED")
    matches_ground_truth = (predicted_meets == seed_gt_meets
                            if seed_gt_meets is not None else None)
    print(f"  ground_truth_meets   : {seed_gt_meets}")
    print(f"  predicted_matches    : {matches_ground_truth}")

    # Build finding
    rule_node_id = get_or_create_rule_node(BID_ID, RULE_ID)

    decision_reason = (
        f"qualified_financial_{financial_3yr_used}cr_at_or_above_floor_{financial_floor_used}cr"
        if verdict == "QUALIFIED" else
        f"ineligible_financial_{financial_3yr_used}cr_below_floor_{financial_floor_used}cr_per_mpg_255"
    )

    label = (
        f"{TYPOLOGY}: {bidder_name} financial-3yr ₹{financial_3yr_used}cr "
        f"{'≥' if verdict == 'QUALIFIED' else '<'} floor ₹{financial_floor_used}cr "
        f"→ {verdict}"
    )

    finding_props = {
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
        "bidder_financial_3yr_cr":    financial_3yr_used,
        "bidder_construction_5yr_cr": bidder_props.get("construction_turnover_5yr_avg_cr"),  # cross-reference

        # — tender criterion citation —
        "tender_id":                  tender_id,
        "tender_nit_no":              bid_props.get("tender_nit_no"),
        "tender_title":               tender.get("title"),
        "tender_estimated_value_cr":  tender.get("estimated_value_cr"),
        "financial_pq_floor_cr":      financial_floor_used,
        "financial_pq_floor_source": (
            "fact_sheet.extracted_facts.financial_pq_floor_cr"
            if financial_floor_consistent
            else "fact_sheet.extracted_facts.financial_pq_floor_cr (catalog mismatch flagged)"
        ),
        "financial_pq_floor_catalog_cr":   financial_floor_from_catalog,
        "financial_pq_floor_consistent":   financial_floor_consistent,
        "financial_3yr_consistent":        financial_3yr_consistent,
        "financial_floor_methodology":     "30%_of_ecv_per_CVC-028_minimum",

        # — regulatory rule citation —
        "rule_natural_language":      rule.get("natural_language"),
        "rule_condition_when":        rule.get("condition_when"),
        "rule_layer":                 rule.get("layer"),
        "rule_typology_code":         rule.get("typology_code"),
        "rule_facts_evaluated":       rule.get("_facts_evaluated"),
        "verdict_origin":             rule.get("verdict_origin"),
        "severity_origin":            rule.get("severity_origin"),

        # — computation —
        "delta_cr":                   calc["delta_cr"],
        "ratio":                      calc["ratio"],
        "meets_threshold":            calc["meets_threshold"],

        # — outcome —
        "verdict":                    verdict,
        "decision_reason":            decision_reason,

        # — ground-truth cross-check —
        "ground_truth_meets":              seed_gt_meets,
        "ground_truth_label":              designed_to_trip,
        "predicted_matches_ground_truth":  matches_ground_truth,

        # — Ext-3 metadata —
        "extension_id":               "Ext-3",
        "extension_name":             "Dual Turnover Criterion (Financial half)",
        "companion_validator":        "bid_turnover_check (Construction 5yr half)",
        "turnover_methodology_note":  bidder_props.get("turnover_methodology_note"),

        # — extraction metadata —
        "extracted_by":               "ext-3:bid_financial_turnover_check_v1",
        "rule_shape":                 "threshold_bidder_side",
        "extraction_path":            "structured_fact_sheets_direct_compare",
        "input_contract":             "fact_sheets.Statement-I-AnnualTurnover (Ext-3 financial sub-fields)",
        "defeated":                   False,
    }

    finding = rest_post("kg_nodes", [{
        "doc_id":     BID_ID,
        "node_type":  "BidEvaluationFinding",
        "label":      label,
        "properties": finding_props,
        "source_ref": SOURCE_REF,
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
                "tier":                       TIER,
                "rule_id":                    RULE_ID,
                "typology":                   TYPOLOGY,
                "severity":                   rule.get("severity"),
                "evaluation_consequence":     consequence,
                "verdict":                    verdict,
                "bid_submission_id":          BID_ID,
                "bidder_profile_id":          bidder_profile_id,
                "bidder_financial_3yr_cr":    financial_3yr_used,
                "financial_pq_floor_cr":      financial_floor_used,
                "delta_cr":                   calc["delta_cr"],
                "ratio":                      calc["ratio"],
                "decision_reason":            decision_reason,
                "finding_node_id":            finding["node_id"],
                "defeated":                   False,
            },
        }])[0]
        print(f"  → BIDDER_VIOLATES_RULE {edge['edge_id']}")
    else:
        print(f"  → no edge ({verdict} is silent)")

    wall = time.perf_counter() - t_start
    print()
    print("=" * 76)
    print(f"  TIMING: wall={wall*1000:.0f}ms  verdict={verdict}  "
          f"consequence={consequence}  gt_match={matches_ground_truth}")
    print("=" * 76)
    if matches_ground_truth is False:
        return 2
    return 0


def _emit_skip_finding(bid_id, bid_node, bidder_props, tender,
                       tender_id, bidder_profile_id) -> dict:
    bidder_name = (bidder_props.get("company_name")
                   or bidder_props.get("bidder_name") or "(unnamed)")
    return rest_post("kg_nodes", [{
        "doc_id":     bid_id,
        "node_type":  "BidEvaluationFinding",
        "label":      f"{TYPOLOGY}: SKIP — MPG-255 does not fire",
        "properties": {
            "tier": TIER, "typology_code": TYPOLOGY, "rule_id": RULE_ID,
            "severity": "ADVISORY", "evaluation_consequence": "ADVISORY",
            "verdict": "SKIP_NOT_APPLICABLE",
            "decision_reason": "mpg_255_condition_when_did_not_fire",
            "bid_submission_id": bid_id, "bid_submission_node_id": bid_node["node_id"],
            "bidder_profile_id": bidder_profile_id, "bidder_name": bidder_name,
            "tender_id": tender_id, "tender_title": tender.get("title"),
            "extension_id": "Ext-3",
            "extracted_by": "ext-3:bid_financial_turnover_check_v1",
            "extraction_path": "skip_rule_inapplicable",
            "input_contract": "fact_sheets.Statement-I-AnnualTurnover",
            "defeated": False,
        },
        "source_ref": SOURCE_REF,
    }])[0]


def _emit_gap_finding(bid_id, bid_node, bidder_props, tender, tender_id,
                      bidder_profile_id, rule, reason: str) -> dict:
    bidder_name = (bidder_props.get("company_name")
                   or bidder_props.get("bidder_name") or "(unnamed)")
    return rest_post("kg_nodes", [{
        "doc_id":     bid_id,
        "node_type":  "BidEvaluationFinding",
        "label":      f"{TYPOLOGY}: GAP — {reason}",
        "properties": {
            "tier": TIER, "typology_code": TYPOLOGY, "rule_id": RULE_ID,
            "severity": (rule.get("severity") if rule else "ADVISORY"),
            "evaluation_consequence": "WARNING",
            "verdict": "GAP_INSUFFICIENT_DATA", "decision_reason": reason,
            "bid_submission_id": bid_id, "bid_submission_node_id": bid_node["node_id"],
            "bidder_profile_id": bidder_profile_id, "bidder_name": bidder_name,
            "tender_id": tender_id,
            "extension_id": "Ext-3",
            "extracted_by": "ext-3:bid_financial_turnover_check_v1",
            "extraction_path": "gap_missing_input_facts",
            "input_contract": "fact_sheets.Statement-I-AnnualTurnover",
            "defeated": False,
        },
        "source_ref": SOURCE_REF,
    }])[0]


if __name__ == "__main__":
    from modules.validation.verdict_emitter import main_with_crash_resilience
    raise SystemExit(main_with_crash_resilience(
        main, doc_id=BID_ID, typology=TYPOLOGY))
