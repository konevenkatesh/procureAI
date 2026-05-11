"""
scripts/bid_class_check.py

═══════════════════════════════════════════════════════════════════
  Tier-2 Bid Submission Evaluator — Bidder-Class-Eligibility
═══════════════════════════════════════════════════════════════════
Validates the bidder's AP contractor registration class against the
tender's required class (per AP-GO-092 monetary tendering limits).

Tier-1 counterpart `tier1_class_mismatch_check.py` asks:
  "Does the TENDER'S required-class declaration match the ECV-band
  per AP-GO-092?"  (doc-side: is the tender restricting bidders
  beyond what AP-GO-092 allows?)

This Tier-2 evaluator asks:
  "Does THIS BIDDER'S registered class meet the tender's required
  class?"  (bid-side: is the bidder licensed to bid for this ECV?)

Verdict vocabulary (Tier-2 four-state, per L61):
  QUALIFIED              — bidder.class ≥ tender.required_class
  INELIGIBLE             — bidder.class < tender.required_class
  GAP_INSUFFICIENT_DATA  — missing bidder fact or tender requirement
  SKIP_NOT_APPLICABLE    — AP-GO-092 condition_when does NOT fire

Class ordinal (per AP-GO-092 monetary limits, ascending):
  Class-V < Class-IV < Class-III < Class-II < Class-I < Special

rule_shape: "ordinal_bidder_side"
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

TYPOLOGY = "Bidder-Class-Eligibility"
TIER = 2
RULE_ID = "AP-GO-092"

# Ordinal: higher integer = higher class privilege per AP-GO-092 GO 8/2003.
# Bidder QUALIFIED iff CLASS_RANK[bidder] >= CLASS_RANK[required].
CLASS_RANK: dict[str, int] = {
    "Class-V":   1,
    "Class-IV":  2,
    "Class-III": 3,
    "Class-II":  4,
    "Class-I":   5,
    "Special":   6,
}


# ── Supabase REST helpers ─────────────────────────────────────────────

REST = settings.supabase_rest_url
H = {"apikey": settings.supabase_anon_key,
     "Authorization": f"Bearer {settings.supabase_anon_key}"}


def rest_get(path: str, params: dict | None = None) -> list[dict]:
    r = requests.get(f"{REST}/rest/v1/{path}", params=params or {},
                     headers=H, timeout=30)
    r.raise_for_status()
    return r.json()


def rest_post(path: str, body: list[dict]) -> list[dict]:
    r = requests.post(
        f"{REST}/rest/v1/{path}",
        json=body,
        headers={**H, "Content-Type": "application/json",
                 "Prefer": "return=representation"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def rest_delete(path: str, params: dict | None = None) -> None:
    r = requests.delete(f"{REST}/rest/v1/{path}", params=params or {},
                        headers=H, timeout=30)
    r.raise_for_status()


# ── Idempotent cleanup ────────────────────────────────────────────────

def _delete_prior_tier2_bid_class(bid_id: str) -> tuple[int, int]:
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


def load_statement_iv_fact(bid_id: str) -> dict | None:
    rows = rest_get("fact_sheets", {
        "select":     "id,doc_id,fact_group,extracted_facts,section_heading,"
                      "source_file,line_start,line_end,extracted_by",
        "doc_id":     f"eq.{bid_id}",
        "fact_group": "eq.Statement-IV-BidderDetails",
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

def compute_verdict(bidder_class: str | None,
                    required_class: str | None) -> tuple[str, dict]:
    if not bidder_class or not required_class:
        return "GAP_INSUFFICIENT_DATA", {
            "bidder_class_rank":   None,
            "required_class_rank": None,
            "rank_delta":          None,
        }
    b_rank = CLASS_RANK.get(bidder_class)
    r_rank = CLASS_RANK.get(required_class)
    if b_rank is None or r_rank is None:
        return "GAP_INSUFFICIENT_DATA", {
            "bidder_class_rank":   b_rank,
            "required_class_rank": r_rank,
            "rank_delta":          None,
            "unknown_class_label": (bidder_class if b_rank is None
                                    else required_class),
        }
    return ("QUALIFIED" if b_rank >= r_rank else "INELIGIBLE"), {
        "bidder_class_rank":   b_rank,
        "required_class_rank": r_rank,
        "rank_delta":          b_rank - r_rank,
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
    print(f"  rule   : {RULE_ID}")
    print("=" * 76)

    n_f, n_e = _delete_prior_tier2_bid_class(BID_ID)
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
    print(f"  contractor_class  : {bidder_props.get('contractor_class')}")
    print(f"  pan               : {bidder_props.get('pan')}")

    fact_row = load_statement_iv_fact(BID_ID)
    if fact_row is None:
        finding = _emit_gap_finding(BID_ID, bid_node, bidder_props,
                                    tender_id, bidder_profile_id, None,
                                    reason="missing_statement_iv_fact_sheet")
        print(f"  → BidEvaluationFinding {finding['node_id']}  (GAP)")
        return 0

    ef = fact_row.get("extracted_facts") or {}
    bidder_class_from_fact = ef.get("contractor_class")
    required_class = ef.get("required_class")
    class_eligible_seed = ef.get("class_eligible_for_tender")
    designed_to_trip = ef.get("_designed_to_trip")

    # Defensive cross-check: BidderProfile.contractor_class should match
    # Statement-IV.contractor_class. Drift = seed inconsistency.
    bidder_class_from_profile = bidder_props.get("contractor_class")
    class_consistent = (bidder_class_from_fact == bidder_class_from_profile)
    bidder_class_used = bidder_class_from_fact or bidder_class_from_profile

    print(f"\n── Statement-IV fact ──")
    print(f"  fact_sheet_id     : {fact_row['id']}")
    print(f"  bidder_class (fact)   : {bidder_class_from_fact!r}")
    print(f"  bidder_class (profile): {bidder_class_from_profile!r}")
    print(f"  required_class        : {required_class!r}")
    print(f"  class_eligible (seed) : {class_eligible_seed}")
    if not class_consistent:
        print(f"  ⚠ class mismatch (fact vs profile) — using fact value")

    # Tender facts for rule selection (synthetic catalog has AP+Works for all 3)
    print(f"\n── Rule selection ──")
    rule = select_rule(tender_state="AndhraPradesh", tender_type="Works")
    if rule is None:
        verdict = "SKIP_NOT_APPLICABLE"
        finding = _emit_skip_finding(BID_ID, bid_node, bidder_props, tender_id,
                                     bidder_profile_id, bidder_name)
        print(f"  → {verdict}  ({finding['node_id']})")
        return 0

    verdict, calc = compute_verdict(bidder_class_used, required_class)
    consequence = evaluation_consequence_for(verdict)
    print(f"\n── Decision ──")
    print(f"  bidder_class_used : {bidder_class_used!r} (rank {calc.get('bidder_class_rank')})")
    print(f"  required_class    : {required_class!r} (rank {calc.get('required_class_rank')})")
    print(f"  rank_delta        : {calc.get('rank_delta')}")
    print(f"  verdict           : {verdict}")
    print(f"  consequence       : {consequence}")

    # Ground truth: seed marks class_eligible_for_tender boolean
    predicted_meets = (verdict == "QUALIFIED")
    matches_ground_truth = (predicted_meets == bool(class_eligible_seed)
                            if class_eligible_seed is not None else None)
    print(f"  ground_truth      : {class_eligible_seed}")
    print(f"  predicted_matches : {matches_ground_truth}")
    if designed_to_trip:
        print(f"  designed_to_trip  : {designed_to_trip}")

    rule_node_id = get_or_create_rule_node(BID_ID, RULE_ID)

    decision_reason = (
        f"qualified_class_{bidder_class_used}_at_or_above_required_{required_class}"
        if verdict == "QUALIFIED" else
        f"ineligible_class_{bidder_class_used}_below_required_{required_class}_per_ap_go_092"
    )
    label = (
        f"{TYPOLOGY}: {bidder_name} class {bidder_class_used} "
        f"{'≥' if verdict == 'QUALIFIED' else '<'} required {required_class} "
        f"→ {verdict}"
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
        "bidder_contractor_class":    bidder_class_used,
        "bidder_pan":                 bidder_props.get("pan"),
        "fact_sheet_id":              fact_row["id"],
        "fact_sheet_fact_group":      fact_row["fact_group"],
        "fact_sheet_source_file":     fact_row.get("source_file"),
        "fact_sheet_extracted_by":    fact_row.get("extracted_by"),
        "bidder_class_from_fact":     bidder_class_from_fact,
        "bidder_class_from_profile":  bidder_class_from_profile,
        "bidder_class_consistent":    class_consistent,

        # — tender criterion citation —
        "tender_id":                  tender_id,
        "tender_nit_no":              bid_props.get("tender_nit_no"),
        "required_class":             required_class,
        "required_class_source":      "fact_sheet.extracted_facts.required_class",

        # — regulatory rule citation —
        "rule_natural_language":      rule.get("natural_language"),
        "rule_condition_when":        rule.get("condition_when"),
        "rule_layer":                 rule.get("layer"),
        "rule_typology_code":         rule.get("typology_code"),
        "rule_facts_evaluated":       rule.get("_facts_evaluated"),
        # L27 audit
        "verdict_origin":             rule.get("verdict_origin"),
        "severity_origin":            rule.get("severity_origin"),

        # — computation —
        "bidder_class_rank":          calc.get("bidder_class_rank"),
        "required_class_rank":        calc.get("required_class_rank"),
        "rank_delta":                 calc.get("rank_delta"),
        "class_rank_table":           dict(CLASS_RANK),
        "meets_threshold":            (verdict == "QUALIFIED"),

        # — outcome —
        "verdict":                    verdict,
        "decision_reason":            decision_reason,

        # — ground-truth cross-check —
        "ground_truth_meets":              bool(class_eligible_seed) if class_eligible_seed is not None else None,
        "ground_truth_label":              designed_to_trip,
        "predicted_matches_ground_truth":  matches_ground_truth,

        # — extraction metadata —
        "extracted_by":               "tier2:bid_class_check_v1",
        "rule_shape":                 "ordinal_bidder_side",
        "extraction_path":            "structured_fact_sheets_ordinal_compare",
        "input_contract":             "fact_sheets.Statement-IV-BidderDetails",
        "defeated":                   False,
    }

    finding = rest_post("kg_nodes", [{
        "doc_id":     BID_ID,
        "node_type":  "BidEvaluationFinding",
        "label":      label,
        "properties": finding_props,
        "source_ref": f"tier2:bid_class_check:{RULE_ID}",
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
                "bidder_contractor_class": bidder_class_used,
                "required_class":          required_class,
                "rank_delta":              calc.get("rank_delta"),
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
        "label":     f"{TYPOLOGY}: SKIP — AP-GO-092 does not fire",
        "properties": {
            "tier": TIER, "typology_code": TYPOLOGY, "rule_id": RULE_ID,
            "severity": "ADVISORY", "evaluation_consequence": "ADVISORY",
            "verdict": "SKIP_NOT_APPLICABLE",
            "decision_reason": "ap_go_092_condition_when_did_not_fire",
            "bid_submission_id": bid_id, "bid_submission_node_id": bid_node["node_id"],
            "bidder_profile_id": bidder_profile_id, "bidder_name": bidder_name,
            "tender_id": tender_id,
            "extracted_by": "tier2:bid_class_check_v1",
            "extraction_path": "skip_rule_inapplicable",
            "input_contract": "fact_sheets.Statement-IV-BidderDetails",
            "defeated": False,
        },
        "source_ref": f"tier2:bid_class_check:{RULE_ID}",
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
            "extracted_by": "tier2:bid_class_check_v1",
            "extraction_path": "gap_missing_input_facts",
            "input_contract": "fact_sheets.Statement-IV-BidderDetails",
            "defeated": False,
        },
        "source_ref": f"tier2:bid_class_check:{RULE_ID}",
    }])[0]


if __name__ == "__main__":
    from modules.validation.verdict_emitter import main_with_crash_resilience
    raise SystemExit(main_with_crash_resilience(
        main, doc_id=BID_ID, typology=TYPOLOGY))
