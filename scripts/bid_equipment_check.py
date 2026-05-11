"""
scripts/bid_equipment_check.py

═══════════════════════════════════════════════════════════════════
  Tier-2 Bid Submission Evaluator — Bidder-Equipment-Coverage
═══════════════════════════════════════════════════════════════════
Validates the bidder's Statement V Critical Equipment register
against MPW-042.

MPW-042 NL verbatim:
  "PQ Criterion (Equipment Capabilities): the applicant must own
  OR have ASSURED ACCESS (hire/lease/purchase agreement) to the
  specified key items of equipment — in full working order AND
  available for the proposed contract. Pass-fail criteria must be
  limited ONLY to bulky/specialised items critical to the project
  (heavy lift cranes, piling barges, dredgers, asphalt plants, etc.).
  Contractors may rely on hiring specialised items they do not own."

Rule-strict reading:
  - status='owned'      counts as QUALIFIED (owned)
  - status='leased'     counts as QUALIFIED (assured access via lease)
  - status='procurable' does NOT count (intent ≠ assured access)

This validator emits rule-strict verdicts. Where seed's
`completeness_assessment` framing diverges from the rule (e.g. B2
seed says "PARTIAL" but rule MPW-042 explicitly permits leased
items as assured access — so rule says QUALIFIED), the seed's
softer framing is surfaced via `ground_truth_label` audit field
for reviewer visibility, but the verdict follows the rule.

L27 downgrade EXPECTED: MPW-042 condition_when has PQB=true
unknown subterm → severity WARNING → ADVISORY (rule severity);
evaluation_consequence is HARD_BLOCK on INELIGIBLE regardless
(per Tier-2 semantic blocking convention).

rule_shape: "set_coverage_bidder_side"
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

TYPOLOGY = "Bidder-Equipment-Coverage"
TIER = 2
RULE_ID = "MPW-042"

# Per MPW-042 NL: "owned OR assured access (hire/lease/purchase agreement)"
QUALIFIED_EQUIPMENT_STATUSES = {"owned", "leased"}
DISQUALIFYING_STATUSES = {"procurable"}


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


def _delete_prior_tier2_bid_equipment(bid_id):
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


def load_statement_v_fact(bid_id):
    rows = rest_get("fact_sheets", {
        "select": "id,doc_id,fact_group,extracted_facts,section_heading,"
                  "source_file,line_start,line_end,extracted_by",
        "doc_id": f"eq.{bid_id}",
        "fact_group": "eq.Statement-V-CriticalEquipment",
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


def compute_verdict(equipment_register, completeness_assessment):
    """Rule-strict per MPW-042: owned OR leased = QUALIFIED;
    any procurable-only items = INELIGIBLE."""
    if not isinstance(equipment_register, list) or len(equipment_register) == 0:
        return "GAP_INSUFFICIENT_DATA", {
            "total_items": 0, "owned": 0, "leased": 0, "procurable": 0,
            "items_with_access": 0, "procurable_items_list": None,
            "assessment_present": (completeness_assessment is not None),
        }
    by_status = {"owned": [], "leased": [], "procurable": [], "other": []}
    for e in equipment_register:
        if not isinstance(e, dict):
            continue
        s = (e.get("status") or "").lower().strip()
        if s in by_status:
            by_status[s].append(e)
        else:
            by_status["other"].append(e)
    n_owned     = len(by_status["owned"])
    n_leased    = len(by_status["leased"])
    n_procurable = len(by_status["procurable"])
    n_with_access = n_owned + n_leased
    total = len(equipment_register)
    if n_procurable > 0:
        verdict = "INELIGIBLE"
    elif n_with_access == total:
        verdict = "QUALIFIED"
    else:
        verdict = "GAP_INSUFFICIENT_DATA"  # unknown statuses
    return verdict, {
        "total_items": total,
        "owned": n_owned, "leased": n_leased, "procurable": n_procurable,
        "items_with_access": n_with_access,
        "procurable_items_list": [
            {"type": e.get("type"), "count": e.get("count"),
             "note": e.get("note")} for e in by_status["procurable"]
        ],
        "owned_items_summary": [
            {"type": e.get("type"), "count": e.get("count"),
             "ref": e.get("invoice_ref")} for e in by_status["owned"]
        ],
        "leased_items_summary": [
            {"type": e.get("type"), "count": e.get("count"),
             "ref": e.get("lease_ref")} for e in by_status["leased"]
        ],
        "assessment_present": (completeness_assessment is not None),
    }


def evaluation_consequence_for(verdict):
    return {"INELIGIBLE": "HARD_BLOCK", "QUALIFIED": "ADVISORY",
            "GAP_INSUFFICIENT_DATA": "WARNING",
            "SKIP_NOT_APPLICABLE": "ADVISORY"}.get(verdict, "ADVISORY")


def compose_decision_reason(verdict, calc):
    if verdict == "QUALIFIED":
        return (f"qualified_all_{calc['total_items']}_items_with_access"
                f"_owned_{calc['owned']}_leased_{calc['leased']}_"
                f"per_mpw_042_owned_or_assured_access")
    if verdict == "GAP_INSUFFICIENT_DATA":
        return (f"gap_empty_register_or_unknown_statuses_total_"
                f"{calc.get('total_items')}_with_access_{calc.get('items_with_access')}")
    # INELIGIBLE — list procurable items
    types = ",".join((e.get("type") or "?") for e in (calc.get("procurable_items_list") or []))
    return (f"ineligible_{calc['procurable']}_procurable_only_items"
            f"_lacking_assured_access_per_mpw_042_items_{types}")


def main():
    t_start = time.perf_counter()
    print("=" * 76)
    print(f"  Tier-2 Bid Submission Evaluator — {TYPOLOGY}")
    print(f"  bid_id : {BID_ID}    rule: {RULE_ID}")
    print(f"  qualifying statuses: {QUALIFIED_EQUIPMENT_STATUSES}")
    print("=" * 76)

    n_f, n_e = _delete_prior_tier2_bid_equipment(BID_ID)
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

    fact_row = load_statement_v_fact(BID_ID)
    if fact_row is None:
        finding = _emit_gap_finding(BID_ID, bid_node, bidder_props, tender_id,
                                    bidder_profile_id, None,
                                    reason="missing_statement_v_fact_sheet")
        print(f"  → GAP {finding['node_id']}")
        return 0

    ef = fact_row.get("extracted_facts") or {}
    equipment_register = ef.get("equipment_register") or []
    completeness_assessment = ef.get("completeness_assessment")
    designed_to_trip = ef.get("_designed_to_trip")

    print(f"\n── Statement-V ── fact_sheet_id={fact_row['id']}")
    print(f"  equipment_register: {len(equipment_register)} items")
    for e in equipment_register:
        print(f"    • {(e.get('type','?'))[:40]:40s}  status={e.get('status'):11s}  count={e.get('count')}")
    print(f"  seed completeness_assessment: {completeness_assessment!r}")

    print(f"\n── Rule selection ──")
    rule = select_rule(tender_state="AndhraPradesh", tender_type="Works")
    if rule is None:
        finding = _emit_skip_finding(BID_ID, bid_node, bidder_props, tender_id,
                                     bidder_profile_id, bidder_name)
        print(f"  → SKIP {finding['node_id']}")
        return 0

    verdict, calc = compute_verdict(equipment_register, completeness_assessment)
    consequence = evaluation_consequence_for(verdict)
    print(f"\n── Decision (rule-strict per MPW-042) ──")
    print(f"  total={calc['total_items']}  owned={calc['owned']}  leased={calc['leased']}  procurable={calc['procurable']}")
    print(f"  items_with_access (owned+leased) = {calc['items_with_access']}")
    print(f"  verdict   : {verdict}    consequence: {consequence}")

    # Ground truth: seed's completeness_assessment maps as:
    #   full_owned       → QUALIFIED (rule + seed agree)
    #   mixed_owned_leased → QUALIFIED (rule QUALIFIED; seed framing "PARTIAL" softer but still acceptable)
    #   procurable_only  → INELIGIBLE (rule + seed agree)
    seed_to_rule_expected = {
        "full_owned": "QUALIFIED",
        "mixed_owned_leased": "QUALIFIED",  # rule-strict: leased counts
        "procurable_only": "INELIGIBLE",
    }
    seed_rule_expected = seed_to_rule_expected.get(completeness_assessment)
    matches_ground_truth = (verdict == seed_rule_expected
                            if seed_rule_expected else None)
    print(f"  seed_assessment   : {completeness_assessment!r}")
    print(f"  seed_rule_expected: {seed_rule_expected}")
    print(f"  predicted_matches : {matches_ground_truth}")
    if designed_to_trip:
        print(f"  designed_to_trip  : {designed_to_trip}")

    rule_node_id = get_or_create_rule_node(BID_ID, RULE_ID)
    decision_reason = compose_decision_reason(verdict, calc)
    label = (f"{TYPOLOGY}: {bidder_name} {calc['items_with_access']}/{calc['total_items']} "
             f"items with access ({calc['owned']}o+{calc['leased']}l, "
             f"{calc['procurable']}p) → {verdict}")

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
        "equipment_register": equipment_register,
        "seed_completeness_assessment": completeness_assessment,

        # tender criterion citation
        "tender_id": tender_id,
        "tender_nit_no": bid_props.get("tender_nit_no"),
        "criterion_source": "rule_universal_no_tender_specific_critical_equipment_list",

        # rule citation
        "rule_natural_language": rule.get("natural_language"),
        "rule_condition_when": rule.get("condition_when"),
        "rule_layer": rule.get("layer"),
        "rule_typology_code": rule.get("typology_code"),
        "rule_facts_evaluated": rule.get("_facts_evaluated"),
        "verdict_origin": rule.get("verdict_origin"),
        "severity_origin": rule.get("severity_origin"),
        "qualifying_statuses_per_rule": sorted(QUALIFIED_EQUIPMENT_STATUSES),

        # computation
        "total_items": calc["total_items"],
        "items_owned": calc["owned"], "items_leased": calc["leased"],
        "items_procurable": calc["procurable"],
        "items_with_access": calc["items_with_access"],
        "procurable_items_list": calc.get("procurable_items_list"),
        "owned_items_summary": calc.get("owned_items_summary"),
        "leased_items_summary": calc.get("leased_items_summary"),
        "meets_threshold": (verdict == "QUALIFIED"),

        # outcome
        "verdict": verdict, "decision_reason": decision_reason,

        # ground-truth (seed framing vs rule-strict verdict)
        "ground_truth_meets": (seed_rule_expected == "QUALIFIED"
                               if seed_rule_expected else None),
        "ground_truth_label": designed_to_trip,
        "seed_completeness_softer_than_rule": (
            completeness_assessment == "mixed_owned_leased"
            and ("PARTIAL" in (designed_to_trip or "")
                 or "partial" in (designed_to_trip or ""))
        ),
        "predicted_matches_ground_truth": matches_ground_truth,

        # extraction metadata
        "extracted_by": "tier2:bid_equipment_check_v1",
        "rule_shape": "set_coverage_bidder_side",
        "extraction_path": "structured_fact_sheets_status_set_coverage",
        "input_contract": "fact_sheets.Statement-V-CriticalEquipment",
        "defeated": False,
    }

    finding = rest_post("kg_nodes", [{
        "doc_id": BID_ID, "node_type": "BidEvaluationFinding",
        "label": label, "properties": finding_props,
        "source_ref": f"tier2:bid_equipment_check:{RULE_ID}",
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
                "total_items": calc["total_items"],
                "items_with_access": calc["items_with_access"],
                "items_procurable": calc["procurable"],
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
        "label": f"{TYPOLOGY}: SKIP — MPW-042 does not fire",
        "properties": {
            "tier": TIER, "typology_code": TYPOLOGY, "rule_id": RULE_ID,
            "severity": "ADVISORY", "evaluation_consequence": "ADVISORY",
            "verdict": "SKIP_NOT_APPLICABLE",
            "decision_reason": "mpw_042_condition_when_did_not_fire",
            "bid_submission_id": bid_id, "bid_submission_node_id": bid_node["node_id"],
            "bidder_profile_id": bidder_profile_id, "bidder_name": bidder_name,
            "tender_id": tender_id,
            "extracted_by": "tier2:bid_equipment_check_v1",
            "extraction_path": "skip_rule_inapplicable",
            "input_contract": "fact_sheets.Statement-V-CriticalEquipment",
            "defeated": False,
        },
        "source_ref": f"tier2:bid_equipment_check:{RULE_ID}",
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
            "extracted_by": "tier2:bid_equipment_check_v1",
            "extraction_path": "gap_missing_input_facts",
            "input_contract": "fact_sheets.Statement-V-CriticalEquipment",
            "defeated": False,
        },
        "source_ref": f"tier2:bid_equipment_check:{RULE_ID}",
    }])[0]


if __name__ == "__main__":
    from modules.validation.verdict_emitter import main_with_crash_resilience
    raise SystemExit(main_with_crash_resilience(
        main, doc_id=BID_ID, typology=TYPOLOGY))
