"""
scripts/bid_emd_validity_check.py

═══════════════════════════════════════════════════════════════════
  Tier-2 Bid Submission Evaluator — Bidder-EMD-Validity
═══════════════════════════════════════════════════════════════════
Validates the bidder's submitted EMD Bank Guarantee against bid
validity requirements.

Rule anchors:
  Primary  : MPW25-050 (Bid Validity for Works, default 90 days)
  Secondary: MPW-079   (Bid Security/EMD — bids without requisite
             EMD shall be rejected as non-responsive)

Derived check (no explicit rule for BG-vs-bid-validity span):
  BG must remain valid AT LEAST until bid_validity_end_date.
  bid_validity_end_date = signature_date + bid_validity_days.
  ("+28 day extension" framing in earlier directives has NO rule
  basis in this corpus — dropped per approved Step-1 diagnose
  stop point #2.)

Composite input contract (NEW L61 addendum-2 variant):
  Source 1 (per-bid supplementary): kg_nodes.EMD_BG
    fields: bg_issue_date, bg_expiry_date, bg_amount_cr, bg_unconditional
  Source 2 (per-bid supplementary): kg_nodes.LetterOfBid
    fields: bid_validity_days, signature_date
  input_contract_pattern: "composite_multi_supplementary_per_bid"

Verdict:
  bid_validity_end_date = signature_date + bid_validity_days
  bg_outlasts_bid           = bg_expiry_date >= bid_validity_end_date
  bg_expired_at_signature   = bg_expiry_date < signature_date
  QUALIFIED if bg_outlasts_bid AND not bg_expired_at_signature
                AND bg_unconditional == True
  INELIGIBLE otherwise; decision_reason composes active failure modes.

rule_shape: "date_arithmetic_bidder_side"
═══════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import os
import sys
import time
from datetime import date, datetime, timedelta
import requests
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from builder.config import settings
from modules.validator.condition_evaluator import evaluate as evaluate_when, Verdict


BID_ID = sys.argv[1] if len(sys.argv) > 1 else "bid_synth_b1_kurnool"

TYPOLOGY = "Bidder-EMD-Validity"
TIER = 2
PRIMARY_RULE_ID   = "MPW25-050"   # bid validity definition
SECONDARY_RULE_ID = "MPW-079"     # EMD must back the bid


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


def _delete_prior_tier2_bid_emd_validity(bid_id):
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


def load_emd_bg(bid_id):
    rows = rest_get("kg_nodes", {
        "select": "node_id,doc_id,label,properties",
        "doc_id": f"eq.{bid_id}", "node_type": "eq.EMD_BG",
    })
    return rows[0] if rows else None


def load_letter_of_bid(bid_id):
    rows = rest_get("kg_nodes", {
        "select": "node_id,doc_id,label,properties",
        "doc_id": f"eq.{bid_id}", "node_type": "eq.LetterOfBid",
    })
    return rows[0] if rows else None


def select_rules(tender_state, tender_type):
    """Select primary (MPW25-050) and secondary (MPW-079) rules."""
    out = []
    for rid in [PRIMARY_RULE_ID, SECONDARY_RULE_ID]:
        rows = rest_get("rules", {
            "select": "rule_id,condition_when,defeats,severity,layer,natural_language,typology_code",
            "rule_id": f"eq.{rid}",
        })
        if not rows:
            print(f"  [{rid}] not in rules table")
            out.append(None)
            continue
        rule = rows[0]
        cw = rule.get("condition_when") or ""
        facts = {"TenderState": tender_state, "TenderType": tender_type,
                 "tender_type": tender_type}
        verdict = evaluate_when(cw, facts).verdict
        print(f"  [{rid}] condition={cw!r}  → verdict={verdict.value}")
        if verdict == Verdict.SKIP:
            out.append(None)
            continue
        severity_origin = rule.get("severity")
        severity_effective = severity_origin
        verdict_origin = "FIRE"
        if verdict == Verdict.UNKNOWN:
            severity_effective = "ADVISORY"
            verdict_origin = "UNKNOWN"
            print(f"  ⚠ L27 downgrade [{rid}]: {severity_origin} → ADVISORY")
        out.append({
            "rule_id": rule["rule_id"], "severity": severity_effective,
            "severity_origin": severity_origin, "verdict_origin": verdict_origin,
            "layer": rule.get("layer"), "typology_code": rule.get("typology_code"),
            "natural_language": rule.get("natural_language"),
            "condition_when": cw, "defeats": rule.get("defeats") or [],
            "_facts_evaluated": facts,
        })
    return out[0], out[1]


def parse_date(s):
    if not s:
        return None
    if isinstance(s, date):
        return s
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def compute_verdict(bg_issue_date, bg_expiry_date, bg_unconditional,
                    signature_date, bid_validity_days):
    """Date arithmetic + boolean check. Returns (verdict, calc)."""
    inputs = (bg_expiry_date, bg_unconditional, signature_date, bid_validity_days)
    if any(x is None for x in inputs):
        return "GAP_INSUFFICIENT_DATA", {
            "bid_validity_end_date": None, "bg_outlasts_bid": None,
            "bg_expired_at_signature": None, "missing_inputs": [
                k for k, v in [("bg_expiry_date", bg_expiry_date),
                               ("bg_unconditional", bg_unconditional),
                               ("signature_date", signature_date),
                               ("bid_validity_days", bid_validity_days)]
                if v is None],
        }
    bid_validity_end_date = signature_date + timedelta(days=int(bid_validity_days))
    bg_outlasts_bid = (bg_expiry_date >= bid_validity_end_date)
    bg_expired_at_signature = (bg_expiry_date < signature_date)
    bg_unconditional_bool = bool(bg_unconditional)
    failures = (not bg_outlasts_bid) or bg_expired_at_signature or (not bg_unconditional_bool)
    return ("INELIGIBLE" if failures else "QUALIFIED"), {
        "bg_issue_date":             bg_issue_date.isoformat() if bg_issue_date else None,
        "bg_expiry_date":            bg_expiry_date.isoformat(),
        "signature_date":            signature_date.isoformat(),
        "bid_validity_days":         int(bid_validity_days),
        "bid_validity_end_date":     bid_validity_end_date.isoformat(),
        "bg_outlasts_bid":           bg_outlasts_bid,
        "bg_expired_at_signature":   bg_expired_at_signature,
        "bg_unconditional":          bg_unconditional_bool,
        "bg_days_remaining_at_bid_end":
            (bg_expiry_date - bid_validity_end_date).days,
    }


def evaluation_consequence_for(verdict):
    return {"INELIGIBLE": "HARD_BLOCK", "QUALIFIED": "ADVISORY",
            "GAP_INSUFFICIENT_DATA": "WARNING",
            "SKIP_NOT_APPLICABLE": "ADVISORY"}.get(verdict, "ADVISORY")


def compose_decision_reason(verdict, calc):
    if verdict == "GAP_INSUFFICIENT_DATA":
        return f"gap_missing_inputs_{','.join(calc.get('missing_inputs') or [])}"
    if verdict == "QUALIFIED":
        return (f"qualified_bg_expires_{calc['bg_expiry_date']}"
                f"_outlasts_bid_validity_end_{calc['bid_validity_end_date']}"
                f"_margin_{calc['bg_days_remaining_at_bid_end']}d_unconditional")
    modes = []
    if calc.get("bg_expired_at_signature"):
        modes.append(f"bg_already_expired_at_signature_expiry_"
                     f"{calc['bg_expiry_date']}_before_signature_"
                     f"{calc['signature_date']}")
    if not calc.get("bg_outlasts_bid"):
        modes.append(f"bg_expiry_{calc['bg_expiry_date']}_before_required_"
                     f"{calc['bid_validity_end_date']}_per_mpw25_050")
    if not calc.get("bg_unconditional"):
        modes.append("bg_not_unconditional_per_mpw_079")
    return "ineligible_" + "+".join(modes)


def main():
    t_start = time.perf_counter()
    print("=" * 76)
    print(f"  Tier-2 Bid Submission Evaluator — {TYPOLOGY}")
    print(f"  bid_id : {BID_ID}    rules: {PRIMARY_RULE_ID} + {SECONDARY_RULE_ID}")
    print("=" * 76)

    n_f, n_e = _delete_prior_tier2_bid_emd_validity(BID_ID)
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

    # Source 1: EMD_BG (per-bid supplementary)
    emd_node = load_emd_bg(BID_ID)
    if emd_node is None:
        finding = _emit_gap_finding(BID_ID, bid_node, bidder_props, tender_id,
                                    bidder_profile_id, None,
                                    reason="missing_emd_bg_node")
        print(f"  → GAP {finding['node_id']}")
        return 0
    emd_props = emd_node["properties"] or {}
    bg_issue_date_str = emd_props.get("bg_issue_date")
    bg_expiry_date_str = emd_props.get("bg_expiry_date")
    bg_amount_cr = emd_props.get("bg_amount_cr")
    bg_unconditional = emd_props.get("bg_unconditional")
    bg_reference = emd_props.get("bg_reference")
    bg_issuing_bank = emd_props.get("bg_issuing_bank")
    designed_to_trip_emd = emd_props.get("_designed_to_trip")

    # Source 2: LetterOfBid (per-bid supplementary)
    lob_node = load_letter_of_bid(BID_ID)
    if lob_node is None:
        finding = _emit_gap_finding(BID_ID, bid_node, bidder_props, tender_id,
                                    bidder_profile_id, None,
                                    reason="missing_letter_of_bid_node")
        print(f"  → GAP {finding['node_id']}")
        return 0
    lob_props = lob_node["properties"] or {}
    signature_date_str = lob_props.get("signature_date")
    bid_validity_days = lob_props.get("bid_validity_days")

    bg_issue_date = parse_date(bg_issue_date_str)
    bg_expiry_date = parse_date(bg_expiry_date_str)
    signature_date = parse_date(signature_date_str)

    print(f"\n── Source 1: EMD_BG ── node_id={emd_node['node_id']}")
    print(f"  bg_reference     : {bg_reference}")
    print(f"  bg_issuing_bank  : {bg_issuing_bank}")
    print(f"  bg_issue_date    : {bg_issue_date_str}")
    print(f"  bg_expiry_date   : {bg_expiry_date_str}")
    print(f"  bg_amount_cr     : ₹{bg_amount_cr}cr")
    print(f"  bg_unconditional : {bg_unconditional}")
    if designed_to_trip_emd:
        print(f"  designed_to_trip : {designed_to_trip_emd[:100]}")

    print(f"\n── Source 2: LetterOfBid ── node_id={lob_node['node_id']}")
    print(f"  signature_date    : {signature_date_str}")
    print(f"  bid_validity_days : {bid_validity_days}")

    print(f"\n── Rule selection ──")
    primary_rule, secondary_rule = select_rules(
        tender_state="AndhraPradesh", tender_type="Works")
    if primary_rule is None:
        finding = _emit_skip_finding(BID_ID, bid_node, bidder_props, tender_id,
                                     bidder_profile_id, bidder_name)
        print(f"  → SKIP {finding['node_id']}")
        return 0

    verdict, calc = compute_verdict(bg_issue_date, bg_expiry_date,
                                    bg_unconditional, signature_date,
                                    bid_validity_days)
    consequence = evaluation_consequence_for(verdict)
    print(f"\n── Decision ──")
    if verdict != "GAP_INSUFFICIENT_DATA":
        print(f"  bid_validity_end_date    : {calc.get('bid_validity_end_date')}")
        print(f"  bg_outlasts_bid          : {calc.get('bg_outlasts_bid')}  "
              f"(margin {calc.get('bg_days_remaining_at_bid_end')}d)")
        print(f"  bg_expired_at_signature  : {calc.get('bg_expired_at_signature')}")
        print(f"  bg_unconditional         : {calc.get('bg_unconditional')}")
    print(f"  verdict                  : {verdict}    consequence: {consequence}")

    # Ground-truth proxy: synthetic seed's bg_validity_180_days bool
    # (180d > 90+0d bid validity end → True maps to QUALIFIED expectation;
    # B3's expired BG → False maps to INELIGIBLE expectation)
    bg_validity_180_days = emd_props.get("bg_validity_180_days")
    seed_gt_meets = bool(bg_validity_180_days) if bg_validity_180_days is not None else None
    predicted_meets = (verdict == "QUALIFIED")
    matches_ground_truth = (predicted_meets == seed_gt_meets
                            if seed_gt_meets is not None else None)
    print(f"  ground_truth (180d valid): {seed_gt_meets}")
    print(f"  predicted_matches        : {matches_ground_truth}")

    primary_rule_node_id = get_or_create_rule_node(BID_ID, PRIMARY_RULE_ID)
    decision_reason = compose_decision_reason(verdict, calc)
    label = (f"{TYPOLOGY}: {bidder_name} BG expires {bg_expiry_date_str} "
             f"vs bid_validity_end {calc.get('bid_validity_end_date','?')} "
             f"→ {verdict}")

    finding_props = {
        "tier": TIER, "typology_code": TYPOLOGY,
        "rule_id": PRIMARY_RULE_ID,
        "secondary_rule_id": SECONDARY_RULE_ID,
        "severity": primary_rule.get("severity"),
        "evaluation_consequence": consequence,

        # bidder citation
        "bid_submission_id": BID_ID,
        "bid_submission_node_id": bid_node["node_id"],
        "bidder_profile_id": bidder_profile_id,
        "bidder_profile_node_id": bidder_node["node_id"],
        "bidder_name": bidder_name,
        "bidder_contractor_class": bidder_props.get("contractor_class"),
        "bidder_pan": bidder_props.get("pan"),

        # composite source 1: EMD_BG
        "emd_bg_node_id": emd_node["node_id"],
        "bg_reference": bg_reference,
        "bg_issuing_bank": bg_issuing_bank,
        "bg_issue_date": bg_issue_date_str,
        "bg_expiry_date": bg_expiry_date_str,
        "bg_amount_cr": bg_amount_cr,
        "bg_unconditional": bg_unconditional,
        "emd_bg_source": "kg_nodes.EMD_BG.properties",

        # composite source 2: LetterOfBid
        "letter_of_bid_node_id": lob_node["node_id"],
        "signature_date": signature_date_str,
        "bid_validity_days": bid_validity_days,
        "letter_of_bid_source": "kg_nodes.LetterOfBid.properties",

        # composite input contract marker (L61 addendum-2)
        "input_contract": "composite:EMD_BG+LetterOfBid",
        "input_contract_pattern": "composite_multi_supplementary_per_bid",

        # tender criterion citation (derived from primary rule; no tender param)
        "tender_id": tender_id, "tender_nit_no": bid_props.get("tender_nit_no"),
        "criterion_source": "derived_from_MPW25-050_default_90d_bid_validity",

        # primary rule citation
        "rule_natural_language": primary_rule.get("natural_language"),
        "rule_condition_when": primary_rule.get("condition_when"),
        "rule_layer": primary_rule.get("layer"),
        "rule_typology_code": primary_rule.get("typology_code"),
        "rule_facts_evaluated": primary_rule.get("_facts_evaluated"),
        "verdict_origin": primary_rule.get("verdict_origin"),
        "severity_origin": primary_rule.get("severity_origin"),

        # secondary rule citation
        "secondary_rule_natural_language": (secondary_rule.get("natural_language")
                                            if secondary_rule else None),
        "secondary_rule_condition_when": (secondary_rule.get("condition_when")
                                          if secondary_rule else None),
        "secondary_rule_layer": (secondary_rule.get("layer")
                                 if secondary_rule else None),
        "secondary_rule_verdict_origin": (secondary_rule.get("verdict_origin")
                                          if secondary_rule else None),

        # computation (date arithmetic)
        "bid_validity_end_date": calc.get("bid_validity_end_date"),
        "bg_outlasts_bid": calc.get("bg_outlasts_bid"),
        "bg_expired_at_signature": calc.get("bg_expired_at_signature"),
        "bg_days_remaining_at_bid_end": calc.get("bg_days_remaining_at_bid_end"),
        "meets_threshold": (verdict == "QUALIFIED"),

        # outcome
        "verdict": verdict, "decision_reason": decision_reason,

        # ground-truth cross-check
        "ground_truth_meets": seed_gt_meets,
        "ground_truth_label": designed_to_trip_emd,
        "predicted_matches_ground_truth": matches_ground_truth,

        # extraction metadata
        "extracted_by": "tier2:bid_emd_validity_check_v1",
        "rule_shape": "date_arithmetic_bidder_side",
        "extraction_path": "composite_supplementary_date_arithmetic",
        "defeated": False,
    }

    finding = rest_post("kg_nodes", [{
        "doc_id": BID_ID, "node_type": "BidEvaluationFinding",
        "label": label, "properties": finding_props,
        "source_ref": f"tier2:bid_emd_validity_check:{PRIMARY_RULE_ID}+{SECONDARY_RULE_ID}",
    }])[0]
    print(f"\n  → BidEvaluationFinding {finding['node_id']}")

    edge = None
    if verdict == "INELIGIBLE":
        edge = rest_post("kg_edges", [{
            "doc_id": BID_ID, "from_node_id": bid_node["node_id"],
            "to_node_id": primary_rule_node_id,
            "edge_type": "BIDDER_VIOLATES_RULE", "weight": 1.0,
            "properties": {
                "tier": TIER, "rule_id": PRIMARY_RULE_ID,
                "secondary_rule_id": SECONDARY_RULE_ID,
                "typology": TYPOLOGY,
                "severity": primary_rule.get("severity"),
                "evaluation_consequence": consequence, "verdict": verdict,
                "bid_submission_id": BID_ID,
                "bidder_profile_id": bidder_profile_id,
                "bg_expiry_date": bg_expiry_date_str,
                "bid_validity_end_date": calc.get("bid_validity_end_date"),
                "bg_outlasts_bid": calc.get("bg_outlasts_bid"),
                "bg_expired_at_signature": calc.get("bg_expired_at_signature"),
                "bg_unconditional": calc.get("bg_unconditional"),
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
        "label": f"{TYPOLOGY}: SKIP — MPW25-050 does not fire",
        "properties": {
            "tier": TIER, "typology_code": TYPOLOGY,
            "rule_id": PRIMARY_RULE_ID, "secondary_rule_id": SECONDARY_RULE_ID,
            "severity": "ADVISORY", "evaluation_consequence": "ADVISORY",
            "verdict": "SKIP_NOT_APPLICABLE",
            "decision_reason": "mpw25_050_condition_when_did_not_fire",
            "bid_submission_id": bid_id, "bid_submission_node_id": bid_node["node_id"],
            "bidder_profile_id": bidder_profile_id, "bidder_name": bidder_name,
            "tender_id": tender_id,
            "extracted_by": "tier2:bid_emd_validity_check_v1",
            "extraction_path": "skip_rule_inapplicable",
            "input_contract": "composite:EMD_BG+LetterOfBid",
            "input_contract_pattern": "composite_multi_supplementary_per_bid",
            "defeated": False,
        },
        "source_ref": f"tier2:bid_emd_validity_check:{PRIMARY_RULE_ID}",
    }])[0]


def _emit_gap_finding(bid_id, bid_node, bidder_props, tender_id,
                      bidder_profile_id, primary_rule, reason):
    bidder_name = (bidder_props.get("company_name")
                   or bidder_props.get("bidder_name") or "(unnamed)")
    return rest_post("kg_nodes", [{
        "doc_id": bid_id, "node_type": "BidEvaluationFinding",
        "label": f"{TYPOLOGY}: GAP — {reason}",
        "properties": {
            "tier": TIER, "typology_code": TYPOLOGY,
            "rule_id": PRIMARY_RULE_ID, "secondary_rule_id": SECONDARY_RULE_ID,
            "severity": (primary_rule.get("severity") if primary_rule else "ADVISORY"),
            "evaluation_consequence": "WARNING",
            "verdict": "GAP_INSUFFICIENT_DATA", "decision_reason": reason,
            "bid_submission_id": bid_id, "bid_submission_node_id": bid_node["node_id"],
            "bidder_profile_id": bidder_profile_id, "bidder_name": bidder_name,
            "tender_id": tender_id,
            "extracted_by": "tier2:bid_emd_validity_check_v1",
            "extraction_path": "gap_missing_input_facts",
            "input_contract": "composite:EMD_BG+LetterOfBid",
            "input_contract_pattern": "composite_multi_supplementary_per_bid",
            "defeated": False,
        },
        "source_ref": f"tier2:bid_emd_validity_check:{PRIMARY_RULE_ID}",
    }])[0]


if __name__ == "__main__":
    from modules.validation.verdict_emitter import main_with_crash_resilience
    raise SystemExit(main_with_crash_resilience(
        main, doc_id=BID_ID, typology=TYPOLOGY))
