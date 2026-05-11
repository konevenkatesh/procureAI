"""
scripts/bid_similar_works_check.py

═══════════════════════════════════════════════════════════════════
  Tier-2 Bid Submission Evaluator — Bidder-Similar-Works-Qualification
═══════════════════════════════════════════════════════════════════
Validates the bidder's Statement II Similar Works submission against
MPW-040 3/2/1 rule.

MPW-040 NL verbatim:
  "PQ Criterion 2 (Particular Construction Experience): applicant
  must have successfully or substantially completed similar works
  during last 7 years ending last day of the month previous to the
  application — meeting EITHER (i) THREE similar works costing
  >=40% of estimated cost, OR (ii) TWO similar works costing >=50%
  of estimated cost, OR (iii) ONE similar work costing >=80% of
  estimated cost. The definition of 'similar works' must be
  unambiguously specified in the PQ document."

The (n_required, fraction_of_ECV) tuples are prose-embedded in the
NL — lifted verbatim as SIMILAR_WORKS_THRESHOLDS constant below.

RECOMPUTE-FROM-ARRAY DISCIPLINE: this validator does NOT trust the
synthetic seed's `meets_3_2_1_rule` boolean. It recomputes from
`similar_works[]` and compares to the seed boolean as a cross-check.
Any disagreement returns RC=2 and surfaces as L64 seed defect (per
approved diagnose-propose stop point #3).

Verdict:
  for (n_required, pct) in THRESHOLDS:
      threshold_value = ecv_cr × pct
      count_meeting = sum(1 for w in works if w.ecv_cr ≥ threshold_value)
      if count_meeting ≥ n_required: QUALIFIED (record branch)
  else: INELIGIBLE (record all 3 branch counts in decision_reason)

L27 downgrade EXPECTED: MPW-040 condition_when='TenderType=Works AND
PQB=true'; PQB is not extracted from synthetic data → UNKNOWN →
severity HARD_BLOCK→ADVISORY (acceptable per pilot pattern).

rule_shape: "count_or_value_branching_bidder_side"
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

TYPOLOGY = "Bidder-Similar-Works-Qualification"
TIER = 2
RULE_ID = "MPW-040"

# MPW-040 NL verbatim (PQ Criterion 2):
# "Applicant must have successfully or substantially completed similar works
#  during last 7 years meeting EITHER (i) THREE similar works costing >=40% of
#  estimated cost, OR (ii) TWO similar works costing >=50%, OR (iii) ONE similar
#  work costing >=80%."
# Tuples are (min_count_required, fraction_of_ECV). Order matters for branch
# reporting — the validator picks the FIRST satisfying branch and reports it.
SIMILAR_WORKS_THRESHOLDS = [(3, 0.40), (2, 0.50), (1, 0.80)]


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


def _delete_prior_tier2_bid_similar_works(bid_id):
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


def load_statement_ii_fact(bid_id):
    rows = rest_get("fact_sheets", {
        "select": "id,doc_id,fact_group,extracted_facts,section_heading,"
                  "source_file,line_start,line_end,extracted_by",
        "doc_id": f"eq.{bid_id}",
        "fact_group": "eq.Statement-II-SimilarWorks",
    })
    return rows[0] if rows else None


def select_rule(tender_state, tender_type):
    """MPW-040 condition_when has PQB=true unknown → L27 downgrade expected."""
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


def _ext6_compliance(work: dict) -> tuple[bool, str]:
    """Ext-6: Check counter-signature/TDS compliance per work entry.

    Returns (is_compliant, reason).
    Rules (from B9 spec Ext-6 contract):
      GOVT/PSU client → requires counter_signature_status IN (EE_SIGNED, SE_SIGNED)
      PRIVATE client → requires tds_certificate_node_id IS NOT NULL
      MISSING/NOT_REQUIRED on GOVT+PSU → NON_COMPLIANT (excluded from 3/2/1)

    Backward-compat default: if Ext-6 fields absent, assume legacy entry
    is COMPLIANT (preserves B1-B8 behavior on un-backfilled rows).
    """
    if not isinstance(work, dict):
        return False, "non_dict_entry"
    client_type = work.get("client_type")
    cs_status   = work.get("counter_signature_status")
    tds_id      = work.get("tds_certificate_node_id")
    if client_type is None and cs_status is None:
        # Legacy entry without Ext-6 fields → compliant by default
        return True, "legacy_no_ext6_fields_assumed_compliant"
    if client_type in ("GOVT", "PSU"):
        if cs_status in ("EE_SIGNED", "SE_SIGNED"):
            return True, f"{client_type}_with_{cs_status}"
        return False, f"{client_type}_missing_counter_signature_{cs_status!r}"
    if client_type == "PRIVATE":
        if tds_id is not None:
            return True, "PRIVATE_with_tds_cert"
        return False, "PRIVATE_missing_tds_certificate"
    return False, f"unknown_client_type_{client_type!r}"


def compute_verdict(similar_works, ecv_cr):
    """Recompute 3/2/1 branches from similar_works[]. Returns (verdict, calc).

    Ext-6: filter out non-compliant works (missing counter-signature for
    GOVT/PSU, missing TDS for PRIVATE) BEFORE counting toward 3/2/1
    branches. ext6_compliance_summary surfaces the breakdown for audit.
    """
    if not isinstance(similar_works, list) or ecv_cr is None:
        return "GAP_INSUFFICIENT_DATA", {
            "branches": None, "satisfying_branch": None, "works_count": None,
            "ext6_compliance_summary": None,
        }
    # Ext-6: per-entry compliance evaluation (informational; works that
    # fail compliance still appear in works_count but are excluded from
    # branch eligibility)
    ext6_summary = []
    compliant_works: list[dict] = []
    for i, w in enumerate(similar_works):
        is_compliant, reason = _ext6_compliance(w)
        ext6_summary.append({
            "index":         i,
            "work_name":     w.get("name") if isinstance(w, dict) else None,
            "client":        w.get("client") if isinstance(w, dict) else None,
            "client_type":   w.get("client_type") if isinstance(w, dict) else None,
            "counter_signature_status": w.get("counter_signature_status") if isinstance(w, dict) else None,
            "compliant":     is_compliant,
            "reason":        reason,
        })
        if is_compliant:
            compliant_works.append(w)

    branch_results = []
    satisfying_branch = None
    for n_required, pct in SIMILAR_WORKS_THRESHOLDS:
        threshold_cr = ecv_cr * pct
        # Ext-6: count only compliant works toward branch eligibility
        meeting = [w for w in compliant_works
                   if isinstance(w, dict) and (w.get("ecv_cr") or 0) >= threshold_cr]
        passed = len(meeting) >= n_required
        branch_results.append({
            "n_required": n_required,
            "fraction_of_ecv": pct,
            "threshold_cr": round(threshold_cr, 4),
            "count_meeting": len(meeting),
            "passed": passed,
        })
        if passed and satisfying_branch is None:
            satisfying_branch = {
                "n_required": n_required, "fraction_of_ecv": pct,
                "threshold_cr": round(threshold_cr, 4),
                "count_meeting": len(meeting),
            }
    calc = {
        "branches": branch_results,
        "satisfying_branch": satisfying_branch,
        "works_count": len(similar_works),
        "compliant_works_count": len(compliant_works),
        "ext6_compliance_summary": ext6_summary,
    }
    return ("QUALIFIED" if satisfying_branch else "INELIGIBLE"), calc


def evaluation_consequence_for(verdict):
    return {"INELIGIBLE": "HARD_BLOCK", "QUALIFIED": "ADVISORY",
            "GAP_INSUFFICIENT_DATA": "WARNING",
            "SKIP_NOT_APPLICABLE": "ADVISORY"}.get(verdict, "ADVISORY")


def compose_decision_reason(verdict, calc):
    if verdict == "GAP_INSUFFICIENT_DATA":
        return "gap_missing_similar_works_array_or_ecv"
    sb = calc.get("satisfying_branch")
    if verdict == "QUALIFIED":
        return (f"qualified_branch_{sb['n_required']}at{int(sb['fraction_of_ecv']*100)}pct"
                f"_count_{sb['count_meeting']}_threshold_{sb['threshold_cr']}cr")
    # INELIGIBLE — list every branch's count vs requirement
    parts = []
    for b in calc.get("branches") or []:
        parts.append(f"{b['n_required']}@{int(b['fraction_of_ecv']*100)}pct_"
                     f"count{b['count_meeting']}of{b['n_required']}required"
                     f"_threshold{b['threshold_cr']}cr")
    return "ineligible_no_branch_satisfied_" + "+".join(parts)


def main():
    t_start = time.perf_counter()
    print("=" * 76)
    print(f"  Tier-2 Bid Submission Evaluator — {TYPOLOGY}")
    print(f"  bid_id : {BID_ID}    rule: {RULE_ID}")
    print(f"  thresholds: {SIMILAR_WORKS_THRESHOLDS}  (per MPW-040 NL verbatim)")
    print("=" * 76)

    n_f, n_e = _delete_prior_tier2_bid_similar_works(BID_ID)
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

    fact_row = load_statement_ii_fact(BID_ID)
    if fact_row is None:
        finding = _emit_gap_finding(BID_ID, bid_node, bidder_props, tender_id,
                                    bidder_profile_id, None,
                                    reason="missing_statement_ii_fact_sheet")
        print(f"  → GAP_INSUFFICIENT_DATA {finding['node_id']}")
        return 0

    ef = fact_row.get("extracted_facts") or {}
    similar_works = ef.get("similar_works") or []
    seed_meets_3_2_1 = ef.get("meets_3_2_1_rule")
    seed_threshold_pct = ef.get("threshold_pct")
    seed_threshold_cr  = ef.get("threshold_value_cr")
    seed_count_meeting = ef.get("works_meeting_threshold")
    designed_to_trip   = ef.get("_designed_to_trip")

    # Derive ECV from the Statement-X fact (same bid) OR from a fact_sheet
    # field; seed's threshold_value_cr / threshold_pct lets us back-derive
    # the ECV the seed used.
    ecv_cr = None
    if seed_threshold_cr and seed_threshold_pct:
        ecv_cr = round(seed_threshold_cr / (seed_threshold_pct / 100.0), 4)
    print(f"\n── Statement-II ── fact_sheet_id={fact_row['id']}  ecv_cr={ecv_cr}")
    print(f"  similar_works: {len(similar_works)} works")
    for w in similar_works:
        print(f"    • {(w.get('name','?'))[:40]:40s} ₹{w.get('ecv_cr')}cr  "
              f"client={w.get('client')}  completion={w.get('completion_date')}")
    print(f"  seed: meets_3_2_1_rule={seed_meets_3_2_1}  "
          f"works_meeting_threshold={seed_count_meeting}  threshold_pct={seed_threshold_pct}")

    print(f"\n── Rule selection ──")
    rule = select_rule(tender_state="AndhraPradesh", tender_type="Works")
    if rule is None:
        finding = _emit_skip_finding(BID_ID, bid_node, bidder_props, tender_id,
                                     bidder_profile_id, bidder_name)
        print(f"  → SKIP_NOT_APPLICABLE {finding['node_id']}")
        return 0

    verdict, calc = compute_verdict(similar_works, ecv_cr)
    consequence = evaluation_consequence_for(verdict)
    print(f"\n── Decision (recomputed from similar_works[]) ──")
    for b in calc.get("branches") or []:
        marker = "✓" if b["passed"] else "✗"
        print(f"  {marker} branch ({b['n_required']}@{int(b['fraction_of_ecv']*100)}%): "
              f"threshold ₹{b['threshold_cr']}cr  count_meeting {b['count_meeting']}")
    sb = calc.get("satisfying_branch")
    print(f"  satisfying_branch : {sb}")
    print(f"  verdict           : {verdict}    consequence: {consequence}")

    # RECOMPUTE-vs-SEED CROSS-CHECK (L64 surfacing)
    recompute_qualified = (verdict == "QUALIFIED")
    seed_says_qualified = bool(seed_meets_3_2_1) if seed_meets_3_2_1 is not None else None
    recompute_seed_agree = (recompute_qualified == seed_says_qualified
                            if seed_says_qualified is not None else None)
    print(f"  recompute={recompute_qualified}  seed_says={seed_says_qualified}  "
          f"agree={recompute_seed_agree}")
    if recompute_seed_agree is False:
        print(f"\n  ✗✗✗ L64 SEED DEFECT — recompute disagrees with seed boolean ✗✗✗")
        print(f"    bid_id={BID_ID}  works={len(similar_works)}  ecv={ecv_cr}cr")
        print(f"    branches: {calc.get('branches')}")
        print(f"    seed.meets_3_2_1_rule={seed_meets_3_2_1}  "
              f"seed.works_meeting_threshold={seed_count_meeting}")
        # Surface as RC=2 but still emit the finding so the audit trail is complete.

    # Ground-truth match uses the seed boolean (predicted_matches_ground_truth)
    matches_ground_truth = (recompute_qualified == seed_says_qualified
                            if seed_says_qualified is not None else None)
    if designed_to_trip:
        print(f"  designed_to_trip: {designed_to_trip}")

    rule_node_id = get_or_create_rule_node(BID_ID, RULE_ID)
    decision_reason = compose_decision_reason(verdict, calc)
    label = (f"{TYPOLOGY}: {bidder_name} {len(similar_works)} works "
             f"recompute_qualified={recompute_qualified} → {verdict}")

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
        "similar_works_count": len(similar_works),
        "similar_works": similar_works,
        "seed_meets_3_2_1_rule": seed_meets_3_2_1,
        "seed_works_meeting_threshold": seed_count_meeting,
        "seed_threshold_pct": seed_threshold_pct,
        "seed_threshold_value_cr": seed_threshold_cr,

        # tender criterion citation
        "tender_id": tender_id, "tender_nit_no": bid_props.get("tender_nit_no"),
        "ecv_cr": ecv_cr,
        "ecv_source": "back_derived_from_seed_threshold_pct_and_value_cr",

        # rule citation
        "rule_natural_language": rule.get("natural_language"),
        "rule_condition_when": rule.get("condition_when"),
        "rule_layer": rule.get("layer"),
        "rule_typology_code": rule.get("typology_code"),
        "rule_facts_evaluated": rule.get("_facts_evaluated"),
        "verdict_origin": rule.get("verdict_origin"),
        "severity_origin": rule.get("severity_origin"),
        "rule_thresholds_source": "MPW-040 NL verbatim (PQ Criterion 2)",
        "rule_thresholds": [
            {"n_required": n, "fraction_of_ecv": p}
            for n, p in SIMILAR_WORKS_THRESHOLDS
        ],

        # computation
        "branches": calc.get("branches"),
        "satisfying_branch": calc.get("satisfying_branch"),
        "meets_threshold": (verdict == "QUALIFIED"),

        # outcome
        "verdict": verdict, "decision_reason": decision_reason,

        # ground-truth + L64 recompute-vs-seed cross-check
        "ground_truth_meets": seed_says_qualified,
        "ground_truth_label": designed_to_trip,
        "predicted_matches_ground_truth": matches_ground_truth,
        "recompute_qualified": recompute_qualified,
        "seed_says_qualified": seed_says_qualified,
        "recompute_seed_agree": recompute_seed_agree,
        "l64_seed_defect_surfaced": (recompute_seed_agree is False),

        # extraction metadata
        "extracted_by": "tier2:bid_similar_works_check_v1",
        "rule_shape": "count_or_value_branching_bidder_side",
        "extraction_path": "structured_fact_sheets_branch_recompute",
        "input_contract": "fact_sheets.Statement-II-SimilarWorks",
        "defeated": False,
    }

    finding = rest_post("kg_nodes", [{
        "doc_id": BID_ID, "node_type": "BidEvaluationFinding",
        "label": label, "properties": finding_props,
        "source_ref": f"tier2:bid_similar_works_check:{RULE_ID}",
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
                "similar_works_count": len(similar_works),
                "ecv_cr": ecv_cr,
                "branches_summary": [{"n": b["n_required"],
                                      "pct": int(b["fraction_of_ecv"]*100),
                                      "count": b["count_meeting"],
                                      "passed": b["passed"]}
                                     for b in (calc.get("branches") or [])],
                "decision_reason": decision_reason,
                "finding_node_id": finding["node_id"], "defeated": False,
            },
        }])[0]
        print(f"  → BIDDER_VIOLATES_RULE {edge['edge_id']}")
    else:
        print(f"  → no edge ({verdict} is silent)")

    wall = time.perf_counter() - t_start
    print(f"\n  TIMING wall={wall*1000:.0f}ms verdict={verdict} gt_match={matches_ground_truth}")
    if recompute_seed_agree is False:
        return 2  # L64 seed defect — STOP-and-report per directive
    if matches_ground_truth is False:
        return 2
    return 0


def _emit_skip_finding(bid_id, bid_node, bidder_props, tender_id,
                       bidder_profile_id, bidder_name):
    return rest_post("kg_nodes", [{
        "doc_id": bid_id, "node_type": "BidEvaluationFinding",
        "label": f"{TYPOLOGY}: SKIP — MPW-040 does not fire",
        "properties": {
            "tier": TIER, "typology_code": TYPOLOGY, "rule_id": RULE_ID,
            "severity": "ADVISORY", "evaluation_consequence": "ADVISORY",
            "verdict": "SKIP_NOT_APPLICABLE",
            "decision_reason": "mpw_040_condition_when_did_not_fire",
            "bid_submission_id": bid_id, "bid_submission_node_id": bid_node["node_id"],
            "bidder_profile_id": bidder_profile_id, "bidder_name": bidder_name,
            "tender_id": tender_id,
            "extracted_by": "tier2:bid_similar_works_check_v1",
            "extraction_path": "skip_rule_inapplicable",
            "input_contract": "fact_sheets.Statement-II-SimilarWorks",
            "defeated": False,
        },
        "source_ref": f"tier2:bid_similar_works_check:{RULE_ID}",
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
            "extracted_by": "tier2:bid_similar_works_check_v1",
            "extraction_path": "gap_missing_input_facts",
            "input_contract": "fact_sheets.Statement-II-SimilarWorks",
            "defeated": False,
        },
        "source_ref": f"tier2:bid_similar_works_check:{RULE_ID}",
    }])[0]


if __name__ == "__main__":
    from modules.validation.verdict_emitter import main_with_crash_resilience
    raise SystemExit(main_with_crash_resilience(
        main, doc_id=BID_ID, typology=TYPOLOGY))
