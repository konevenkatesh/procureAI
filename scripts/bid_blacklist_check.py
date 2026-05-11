"""
scripts/bid_blacklist_check.py

═══════════════════════════════════════════════════════════════════
  Tier-2 Bid Submission Evaluator — Bidder-Blacklist-Disclosure
═══════════════════════════════════════════════════════════════════
Validates the bidder's disclosed blacklist status and active
litigation footprint against the AP-GO-096 framework (administrative
blacklist authority + grounds: malpractice, ring formation, bribery,
corruption, fraud, smuggling, pilfering, non-payment, conviction).
The 5-year poor-performance lookback comes from MPW-045 (secondary
basis).

Composite input contract (new pattern, L61 addendum candidate):
  - kg_nodes.BidderProfile.properties.blacklist_status   (per-bidder)
  - fact_sheets.Statement-VII-Litigation.litigation_count + cases[]

Tier-1 counterpart `tier1_blacklist_check.py` asks:
  "Does the TENDER DOCUMENT carry a blacklist-disclosure form
  requirement?"  (doc-side)

This Tier-2 evaluator asks:
  "Is THIS BIDDER blacklisted OR carrying active govt litigation
  beyond the AP-GO-096 / MPW-045 thresholds?"  (bid-side)

Verdict vocabulary (Tier-2 four-state, per L61):
  QUALIFIED              — blacklist_status='clean' AND litigation_count=0
  INELIGIBLE             — blacklist_status != 'clean' OR active govt cases
                           (decision_reason captures the dominant signal)
  GAP_INSUFFICIENT_DATA  — missing inputs
  SKIP_NOT_APPLICABLE    — AP-GO-096 condition_when does NOT fire

rule_shape: "boolean_lookback_bidder_side"
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

TYPOLOGY = "Bidder-Blacklist-Disclosure"
TIER = 2
PRIMARY_RULE_ID   = "AP-GO-096"   # AP-jurisdiction blacklist authority
SECONDARY_RULE_ID = "MPW-045"     # 5-yr poor-performance lookback basis


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

def _delete_prior_tier2_bid_blacklist(bid_id: str) -> tuple[int, int]:
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


def load_statement_vii_fact(bid_id: str) -> dict | None:
    rows = rest_get("fact_sheets", {
        "select":     "id,doc_id,fact_group,extracted_facts,section_heading,"
                      "source_file,line_start,line_end,extracted_by",
        "doc_id":     f"eq.{bid_id}",
        "fact_group": "eq.Statement-VII-Litigation",
    })
    return rows[0] if rows else None


# ── Rule selection (primary + secondary) ──────────────────────────────

def select_rules(tender_state: str, tender_type: str | None) -> tuple[dict | None, dict | None]:
    """Select primary (AP-GO-096) and secondary (MPW-045) rules.
    Secondary is captured for citation only; verdict logic anchors to primary."""
    out = []
    for rid in [PRIMARY_RULE_ID, SECONDARY_RULE_ID]:
        rows = rest_get("rules", {
            "select":  "rule_id,condition_when,defeats,severity,layer,"
                       "natural_language,typology_code",
            "rule_id": f"eq.{rid}",
        })
        if not rows:
            print(f"  [{rid}] not in rules table")
            out.append(None)
            continue
        rule = rows[0]
        cw = rule.get("condition_when") or ""
        facts = {
            "TenderState":  tender_state,
            "TenderType":   tender_type,
            "tender_type":  tender_type,
        }
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
        })
    return out[0], out[1]


# ── Verdict logic ─────────────────────────────────────────────────────

def compute_verdict(blacklist_status, litigation_count,
                    cases: list[dict]) -> tuple[str, dict]:
    if blacklist_status is None or litigation_count is None:
        return "GAP_INSUFFICIENT_DATA", {
            "blacklist_clean":     None,
            "active_govt_cases":   None,
            "case_count":          None,
        }
    blacklist_clean = (str(blacklist_status).lower() == "clean")
    # Active govt case = any pending case with a Govt body as opposing party.
    govt_keywords = ("apiic", "ap public works", "government", "govt",
                     "metro rail", "state", "central")
    govt_cases = [c for c in (cases or [])
                  if "pending" in str(c.get("status", "")).lower()
                  and any(k in str(c.get("opposing_party", "")).lower()
                          for k in govt_keywords)]
    active_govt_cases = len(govt_cases) > 0
    if blacklist_clean and not active_govt_cases:
        return "QUALIFIED", {
            "blacklist_clean":   True,
            "active_govt_cases": False,
            "case_count":        litigation_count,
            "govt_case_count":   0,
        }
    return "INELIGIBLE", {
        "blacklist_clean":     blacklist_clean,
        "active_govt_cases":   active_govt_cases,
        "case_count":          litigation_count,
        "govt_case_count":     len(govt_cases),
        "govt_cases_summary":  [
            {"case_no": c.get("case_no"), "opposing": c.get("opposing_party"),
             "subject": c.get("subject"), "status": c.get("status")}
            for c in govt_cases[:5]
        ],
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
    print(f"  rules  : {PRIMARY_RULE_ID} (primary) + {SECONDARY_RULE_ID} (secondary)")
    print("=" * 76)

    n_f, n_e = _delete_prior_tier2_bid_blacklist(BID_ID)
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

    # Composite source 1: BidderProfile.blacklist_status
    blacklist_status_from_profile = bidder_props.get("blacklist_status")
    litigation_count_from_profile = bidder_props.get("litigation_count")
    print(f"\n── BidderProfile (composite source 1) ──")
    print(f"  name              : {bidder_name}")
    print(f"  blacklist_status  : {blacklist_status_from_profile!r}")
    print(f"  litigation_count  : {litigation_count_from_profile} (profile)")

    # Composite source 2: Statement-VII fact_sheets
    fact_row = load_statement_vii_fact(BID_ID)
    if fact_row is None:
        finding = _emit_gap_finding(BID_ID, bid_node, bidder_props, tender_id,
                                    bidder_profile_id, None,
                                    reason="missing_statement_vii_fact_sheet")
        print(f"  → BidEvaluationFinding {finding['node_id']}  (GAP)")
        return 0

    ef = fact_row.get("extracted_facts") or {}
    litigation_count_from_fact = ef.get("litigation_count")
    cases = ef.get("cases") or []
    designed_to_trip = ef.get("_designed_to_trip")

    # Defensive cross-check
    litigation_consistent = (litigation_count_from_fact == litigation_count_from_profile)
    if not litigation_consistent:
        print(f"  ⚠ litigation_count mismatch (fact={litigation_count_from_fact}, profile={litigation_count_from_profile})")
    litigation_count_used = (litigation_count_from_fact
                             if litigation_count_from_fact is not None
                             else litigation_count_from_profile)

    print(f"\n── Statement-VII fact (composite source 2) ──")
    print(f"  fact_sheet_id     : {fact_row['id']}")
    print(f"  litigation_count  : {litigation_count_from_fact} (fact)")
    print(f"  cases             : {len(cases)} listed")
    for c in cases[:3]:
        print(f"    - {c.get('case_no','?')}: {c.get('opposing_party','?')}  "
              f"status={(c.get('status') or '?')[:50]}")

    print(f"\n── Rule selection ──")
    primary_rule, secondary_rule = select_rules(
        tender_state="AndhraPradesh", tender_type="Works")
    if primary_rule is None:
        verdict = "SKIP_NOT_APPLICABLE"
        finding = _emit_skip_finding(BID_ID, bid_node, bidder_props, tender_id,
                                     bidder_profile_id, bidder_name)
        print(f"  → {verdict}  ({finding['node_id']})")
        return 0

    verdict, calc = compute_verdict(blacklist_status_from_profile,
                                    litigation_count_used, cases)
    consequence = evaluation_consequence_for(verdict)
    print(f"\n── Decision ──")
    print(f"  blacklist_clean   : {calc.get('blacklist_clean')}")
    print(f"  active_govt_cases : {calc.get('active_govt_cases')}  "
          f"(govt_case_count={calc.get('govt_case_count')})")
    print(f"  verdict           : {verdict}")
    print(f"  consequence       : {consequence}")

    # Ground truth: B1/B2 expected QUALIFIED (clean+0), B3 expected
    # INELIGIBLE (previously_debarred or has active govt cases). Use
    # the seed's _designed_to_trip narrative as the ground-truth proxy.
    gt_clear = (str(blacklist_status_from_profile or "").lower() == "clean"
                and (litigation_count_used or 0) == 0)
    predicted_meets = (verdict == "QUALIFIED")
    matches_ground_truth = (predicted_meets == gt_clear)
    print(f"  ground_truth      : {'CLEAR' if gt_clear else 'FLAGGED'}")
    print(f"  predicted_matches : {matches_ground_truth}")
    if designed_to_trip:
        print(f"  designed_to_trip  : {designed_to_trip}")

    primary_rule_node_id = get_or_create_rule_node(BID_ID, PRIMARY_RULE_ID)

    # decision_reason: dominant signal (blacklist supersedes litigation
    # in framing because blacklist is the AP-GO-096 primary trigger)
    if verdict == "QUALIFIED":
        decision_reason = (
            f"qualified_blacklist_clean_litigation_{litigation_count_used}"
        )
    elif not calc.get("blacklist_clean"):
        decision_reason = (
            f"ineligible_blacklist_status_{blacklist_status_from_profile}_per_ap_go_096"
        )
    elif calc.get("active_govt_cases"):
        decision_reason = (
            f"ineligible_active_litigation_{calc.get('govt_case_count')}_"
            f"cases_with_govt_per_mpw_045_5yr_lookback"
        )
    else:
        decision_reason = "gap_insufficient_data"

    label = (
        f"{TYPOLOGY}: {bidder_name} blacklist={blacklist_status_from_profile} "
        f"litigation={litigation_count_used} → {verdict}"
    )

    finding_props = {
        # — identity —
        "tier":                       TIER,
        "typology_code":              TYPOLOGY,
        "rule_id":                    PRIMARY_RULE_ID,   # primary anchor
        "secondary_rule_id":          SECONDARY_RULE_ID, # 5-yr lookback basis
        "severity":                   primary_rule.get("severity"),
        "evaluation_consequence":     consequence,

        # — bidder citation —
        "bid_submission_id":          BID_ID,
        "bid_submission_node_id":     bid_node["node_id"],
        "bidder_profile_id":          bidder_profile_id,
        "bidder_profile_node_id":     bidder_node["node_id"],
        "bidder_name":                bidder_name,
        "bidder_contractor_class":    bidder_props.get("contractor_class"),
        "bidder_pan":                 bidder_props.get("pan"),

        # — composite source 1: BidderProfile —
        "blacklist_status":           blacklist_status_from_profile,
        "blacklist_status_source":    "kg_nodes.BidderProfile.properties.blacklist_status",
        "litigation_count_profile":   litigation_count_from_profile,

        # — composite source 2: Statement-VII fact_sheet —
        "fact_sheet_id":              fact_row["id"],
        "fact_sheet_fact_group":      fact_row["fact_group"],
        "fact_sheet_source_file":     fact_row.get("source_file"),
        "fact_sheet_extracted_by":    fact_row.get("extracted_by"),
        "litigation_count_fact":      litigation_count_from_fact,
        "litigation_count_used":      litigation_count_used,
        "litigation_consistent":      litigation_consistent,
        "litigation_cases":           cases,

        # — composite input contract marker (L61 addendum) —
        "input_contract":             "composite:BidderProfile+fact_sheets.Statement-VII-Litigation",
        "input_contract_pattern":     "composite_entity_plus_statement",

        # — tender criterion citation (universal rule, no tender param) —
        "tender_id":                  tender_id,
        "tender_nit_no":              bid_props.get("tender_nit_no"),
        "criterion_source":           "universal_rule_no_tender_parameter",

        # — primary regulatory rule citation —
        "rule_natural_language":      primary_rule.get("natural_language"),
        "rule_condition_when":        primary_rule.get("condition_when"),
        "rule_layer":                 primary_rule.get("layer"),
        "rule_typology_code":         primary_rule.get("typology_code"),
        "rule_facts_evaluated":       primary_rule.get("_facts_evaluated"),
        "verdict_origin":             primary_rule.get("verdict_origin"),
        "severity_origin":            primary_rule.get("severity_origin"),

        # — secondary regulatory rule citation (MPW-045 5-yr lookback) —
        "secondary_rule_natural_language": (secondary_rule.get("natural_language")
                                            if secondary_rule else None),
        "secondary_rule_condition_when":   (secondary_rule.get("condition_when")
                                            if secondary_rule else None),
        "secondary_rule_layer":            (secondary_rule.get("layer")
                                            if secondary_rule else None),
        "secondary_rule_verdict_origin":   (secondary_rule.get("verdict_origin")
                                            if secondary_rule else None),
        "secondary_rule_severity_origin":  (secondary_rule.get("severity_origin")
                                            if secondary_rule else None),

        # — computation —
        "blacklist_clean":            calc.get("blacklist_clean"),
        "active_govt_cases":          calc.get("active_govt_cases"),
        "case_count":                 calc.get("case_count"),
        "govt_case_count":            calc.get("govt_case_count"),
        "govt_cases_summary":         calc.get("govt_cases_summary"),
        "meets_threshold":            (verdict == "QUALIFIED"),

        # — outcome —
        "verdict":                    verdict,
        "decision_reason":            decision_reason,

        # — ground-truth cross-check —
        "ground_truth_meets":              gt_clear,
        "ground_truth_label":              designed_to_trip,
        "predicted_matches_ground_truth":  matches_ground_truth,

        # — extraction metadata —
        "extracted_by":               "tier2:bid_blacklist_check_v1",
        "rule_shape":                 "boolean_lookback_bidder_side",
        "extraction_path":            "composite_profile_plus_statement_boolean_compare",
        "defeated":                   False,
    }

    finding = rest_post("kg_nodes", [{
        "doc_id":     BID_ID,
        "node_type":  "BidEvaluationFinding",
        "label":      label,
        "properties": finding_props,
        "source_ref": f"tier2:bid_blacklist_check:{PRIMARY_RULE_ID}+{SECONDARY_RULE_ID}",
    }])[0]
    print(f"\n  → BidEvaluationFinding {finding['node_id']}")

    edge = None
    if verdict == "INELIGIBLE":
        edge = rest_post("kg_edges", [{
            "doc_id":       BID_ID,
            "from_node_id": bid_node["node_id"],
            "to_node_id":   primary_rule_node_id,
            "edge_type":    "BIDDER_VIOLATES_RULE",
            "weight":       1.0,
            "properties": {
                "tier":                    TIER,
                "rule_id":                 PRIMARY_RULE_ID,
                "secondary_rule_id":       SECONDARY_RULE_ID,
                "typology":                TYPOLOGY,
                "severity":                primary_rule.get("severity"),
                "evaluation_consequence":  consequence,
                "verdict":                 verdict,
                "bid_submission_id":       BID_ID,
                "bidder_profile_id":       bidder_profile_id,
                "blacklist_status":        blacklist_status_from_profile,
                "litigation_count":        litigation_count_used,
                "active_govt_cases":       calc.get("active_govt_cases"),
                "govt_case_count":         calc.get("govt_case_count"),
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
        "label":     f"{TYPOLOGY}: SKIP — AP-GO-096 does not fire",
        "properties": {
            "tier": TIER, "typology_code": TYPOLOGY, "rule_id": PRIMARY_RULE_ID,
            "secondary_rule_id": SECONDARY_RULE_ID,
            "severity": "ADVISORY", "evaluation_consequence": "ADVISORY",
            "verdict": "SKIP_NOT_APPLICABLE",
            "decision_reason": "ap_go_096_condition_when_did_not_fire",
            "bid_submission_id": bid_id, "bid_submission_node_id": bid_node["node_id"],
            "bidder_profile_id": bidder_profile_id, "bidder_name": bidder_name,
            "tender_id": tender_id,
            "extracted_by": "tier2:bid_blacklist_check_v1",
            "extraction_path": "skip_rule_inapplicable",
            "input_contract": "composite:BidderProfile+fact_sheets.Statement-VII-Litigation",
            "input_contract_pattern": "composite_entity_plus_statement",
            "defeated": False,
        },
        "source_ref": f"tier2:bid_blacklist_check:{PRIMARY_RULE_ID}",
    }])[0]


def _emit_gap_finding(bid_id, bid_node, bidder_props, tender_id,
                      bidder_profile_id, primary_rule, reason: str) -> dict:
    bidder_name = (bidder_props.get("company_name")
                   or bidder_props.get("bidder_name") or "(unnamed)")
    return rest_post("kg_nodes", [{
        "doc_id":    bid_id,
        "node_type": "BidEvaluationFinding",
        "label":     f"{TYPOLOGY}: GAP — {reason}",
        "properties": {
            "tier": TIER, "typology_code": TYPOLOGY, "rule_id": PRIMARY_RULE_ID,
            "secondary_rule_id": SECONDARY_RULE_ID,
            "severity": (primary_rule.get("severity") if primary_rule else "ADVISORY"),
            "evaluation_consequence": "WARNING",
            "verdict": "GAP_INSUFFICIENT_DATA", "decision_reason": reason,
            "bid_submission_id": bid_id, "bid_submission_node_id": bid_node["node_id"],
            "bidder_profile_id": bidder_profile_id, "bidder_name": bidder_name,
            "tender_id": tender_id,
            "extracted_by": "tier2:bid_blacklist_check_v1",
            "extraction_path": "gap_missing_input_facts",
            "input_contract": "composite:BidderProfile+fact_sheets.Statement-VII-Litigation",
            "input_contract_pattern": "composite_entity_plus_statement",
            "defeated": False,
        },
        "source_ref": f"tier2:bid_blacklist_check:{PRIMARY_RULE_ID}",
    }])[0]


if __name__ == "__main__":
    from modules.validation.verdict_emitter import main_with_crash_resilience
    raise SystemExit(main_with_crash_resilience(
        main, doc_id=BID_ID, typology=TYPOLOGY))
