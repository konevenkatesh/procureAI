"""
scripts/bid_turnover_check.py

═══════════════════════════════════════════════════════════════════
  Tier-2 Bid Submission Evaluator
═══════════════════════════════════════════════════════════════════
This validator reads a BIDDER'S submitted Statement I (Annual
Turnover) and tests it against the tender's PQ turnover floor
(CVC-028 cap: avg-3-yr-turnover ≥ 30% of estimated cost AND PQ
floor itself ≤ 2× annual contract value).

The Tier-1 counterpart `tier1_turnover_check.py` asks:
  "Does the TENDER'S PQ floor exceed the CVC cap?"  (doc-side)

This Tier-2 evaluator asks:
  "Does THIS BIDDER'S turnover meet the tender's PQ floor?"  (bid-side)

Architectural pattern (pilot — first Tier-2 validator):
  - Input contract: structured fact_sheets row (Statement-I-AnnualTurnover)
  - No retrieval (Qdrant inert), no embedding (BGE-M3 inert),
    no LLM rerank, no L24 evidence guard, no L36 grep fallback.
  - Pure deterministic compare with full citation chain.
  - Emits BidEvaluationFinding (new node_type), not ValidationFinding.
  - Emits BIDDER_VIOLATES_RULE edge only on INELIGIBLE.

Verdict vocabulary (Tier-2 four-state):
  QUALIFIED              — bidder.turnover ≥ tender.pq_floor (silent edge)
  INELIGIBLE             — bidder.turnover < tender.pq_floor (edge emitted)
  GAP_INSUFFICIENT_DATA  — missing bidder fact or tender threshold
  SKIP_NOT_APPLICABLE    — CVC-028 condition_when does NOT fire on this tender

evaluation_consequence field (orthogonal to severity, per user spec):
  HARD_BLOCK   on INELIGIBLE — this bidder is disqualified, cannot proceed
  ADVISORY     on QUALIFIED  — informational, no action needed
  WARNING      on GAP_*      — reviewer must supply missing facts

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

TYPOLOGY = "Bidder-Turnover-Eligibility"
TIER = 2

# Rule whose threshold is being applied (reused from Tier-1)
# CVC-028: WARNING, condition_when=
#   TenderType=Works AND WorkType IN ('Civil','Electrical') AND PQTurnoverCriterion>0
RULE_ID = "CVC-028"


# ── Synthetic tender catalog ──────────────────────────────────────────
# No TenderDocument nodes exist for synthetic tenders today (only
# BidderProfile + BidSubmission). Tier-2-tender-criterion node
# extraction is queued for Sub-block 3b scaling. Until then, the
# pilot reads tender facts from this in-script catalog. The
# pq_turnover_floor_cr values mirror what each Statement-I fact_sheet
# row carries as `pq_floor_cr` (defensive cross-check below).

SYNTHETIC_TENDER_CATALOG: dict[str, dict] = {
    "tender_synth_kurnool": dict(
        tender_type="Works",
        work_type="Civil",
        is_ap_tender=True,
        estimated_value_cr=85.0,
        tenure_years=3,
        nit_no="100/PROC/APIIC/1/2026",
        title="District Hospital, Kurnool",
        pq_turnover_floor_cr=121.7,
    ),
    "tender_synth_ja": dict(
        tender_type="Works",
        work_type="Civil",
        is_ap_tender=True,
        estimated_value_cr=125.5,
        tenure_years=3,
        nit_no="JA/2026/CW/001",
        title="Andhra Pradesh Judicial Academy",
        pq_turnover_floor_cr=83.7,
    ),
    "tender_synth_hc": dict(
        tender_type="Works",
        work_type="Civil",
        is_ap_tender=True,
        estimated_value_cr=365.16,
        tenure_years=3,
        nit_no="HC/2026/CW/001",
        title="Andhra Pradesh High Court",
        pq_turnover_floor_cr=243.4,
    ),
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


# ── Idempotent re-run cleanup (Tier-2 BidEvaluationFinding shape) ─────

def _delete_prior_tier2_bid_turnover(bid_id: str) -> tuple[int, int]:
    """Tier-2 cleanup: remove prior BidEvaluationFinding + BIDDER_VIOLATES_RULE
    rows for this (bid_id, typology). Mirrors the Tier-1 idempotence
    pattern but on the Tier-2 node/edge types."""
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


# ── RuleNode get-or-create (reused from Tier-1 pattern) ───────────────

def get_or_create_rule_node(parent_doc_id: str, rule_id: str) -> str:
    """Parent for the RuleNode row is the bid submission (so the
    finding's audit graph stays scoped). Same shape as Tier-1."""
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


def load_statement_i_fact(bid_id: str) -> dict | None:
    rows = rest_get("fact_sheets", {
        "select":     "id,doc_id,fact_group,extracted_facts,section_heading,"
                      "source_file,line_start,line_end,extracted_by",
        "doc_id":     f"eq.{bid_id}",
        "fact_group": "eq.Statement-I-AnnualTurnover",
    })
    return rows[0] if rows else None


# ── Rule selection (reused condition_evaluator) ───────────────────────

def select_cvc028_for_tender(tender: dict) -> dict | None:
    """Pick CVC-028 if its condition_when fires for these tender facts.
    Tier-2 reuses the same condition_evaluator the Tier-1 path uses.
    Returns the rule dict or None (SKIP_NOT_APPLICABLE)."""
    rows = rest_get("rules", {
        "select":  "rule_id,condition_when,defeats,severity,layer,natural_language,typology_code",
        "rule_id": f"eq.{RULE_ID}",
    })
    if not rows:
        print(f"  [{RULE_ID}] not found in rules table")
        return None
    rule = rows[0]
    cw = rule.get("condition_when") or ""

    # Build the facts dict the evaluator needs. PQTurnoverCriterion is
    # "true" if the tender carries a PQ turnover floor at all — which
    # the synthetic catalog and the Statement-I fact_sheet both encode
    # via pq_turnover_floor_cr / pq_floor_cr. We treat presence-of-floor
    # as positive for the evaluator's >0 subterm.
    pq_floor = tender.get("pq_turnover_floor_cr")
    facts = {
        "TenderType":            tender.get("tender_type"),
        "tender_type":           tender.get("tender_type"),
        "WorkType":              tender.get("work_type"),
        "TenderState":           ("AndhraPradesh"
                                  if tender.get("is_ap_tender") else "Other"),
        "is_ap_tender":          bool(tender.get("is_ap_tender")),
        "PQTurnoverCriterion":   float(pq_floor) if pq_floor is not None else 0.0,
        "EstimatedValue":        (float(tender["estimated_value_cr"]) * 1e7
                                  if tender.get("estimated_value_cr") is not None
                                  else None),
        "_estimated_value_cr":   tender.get("estimated_value_cr"),
    }
    verdict = evaluate_when(cw, facts).verdict
    print(f"  [{RULE_ID}] condition={cw!r}")
    print(f"  facts: TenderType={facts['TenderType']!r}, WorkType={facts['WorkType']!r}, "
          f"PQTurnoverCriterion={facts['PQTurnoverCriterion']}")
    print(f"  → verdict={verdict.value}")

    # L27-style: SKIP = correct silence; UNKNOWN = fire-as-downgraded
    # (the doc-side condition_evaluator can't parse CVC-028's `IN (...)`
    # syntax today, so WorkType subterm resolves UNKNOWN even with a
    # known value. Tier-1 handles this identically — degrade severity
    # WARNING → ADVISORY and mark verdict_origin in audit fields.)
    if verdict == Verdict.SKIP:
        return None

    severity_origin = rule.get("severity")
    severity_effective = severity_origin
    verdict_origin = "FIRE"
    if verdict == Verdict.UNKNOWN:
        severity_effective = "ADVISORY"
        verdict_origin = "UNKNOWN"
        print(f"  ⚠ L27 downgrade: severity {severity_origin} → ADVISORY "
              f"(parser limitation on IN-syntax)")

    return {
        "rule_id":           rule["rule_id"],
        "severity":          severity_effective,
        "severity_origin":   severity_origin,
        "verdict_origin":    verdict_origin,
        "layer":             rule.get("layer"),
        "typology_code":     rule.get("typology_code"),
        "natural_language":  rule.get("natural_language"),
        "condition_when":    cw,
        "defeats":           rule.get("defeats") or [],
        "_facts_evaluated":  facts,
    }


# ── Verdict logic ─────────────────────────────────────────────────────

def compute_verdict(bidder_avg_5yr_cr: float | None,
                    pq_turnover_floor_cr: float | None) -> tuple[str, dict]:
    """Tier-2 four-state outcome compute."""
    if bidder_avg_5yr_cr is None or pq_turnover_floor_cr is None:
        return "GAP_INSUFFICIENT_DATA", {
            "delta_cr": None, "ratio": None, "meets_threshold": None,
        }
    delta = bidder_avg_5yr_cr - pq_turnover_floor_cr
    ratio = (bidder_avg_5yr_cr / pq_turnover_floor_cr
             if pq_turnover_floor_cr > 0 else None)
    meets = bidder_avg_5yr_cr >= pq_turnover_floor_cr
    return ("QUALIFIED" if meets else "INELIGIBLE"), {
        "delta_cr": round(delta, 4),
        "ratio": round(ratio, 4) if ratio is not None else None,
        "meets_threshold": meets,
    }


def evaluation_consequence_for(verdict: str) -> str:
    """Orthogonal to rule severity. Tells the orchestrator what to do
    with this bidder for downstream ranking / EligibilityMatrix."""
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
    print(f"  Tier-2 Bid Submission Evaluator — Bidder-Turnover-Eligibility")
    print(f"  bid_id : {BID_ID}")
    print(f"  rule   : {RULE_ID} (CVC-028)")
    print("=" * 76)

    # 1. Idempotent prior-row cleanup (Tier-2 shape)
    n_f, n_e = _delete_prior_tier2_bid_turnover(BID_ID)
    if n_f or n_e:
        print(f"  cleanup: removed {n_f} prior finding(s) + {n_e} edge(s)")

    # 2. Load BidSubmission + BidderProfile + tender catalog
    bid_node = load_bid_submission(BID_ID)
    bid_props = bid_node["properties"] or {}
    # BidSubmission carries `bidder_profile_id` (per synthetic seed schema)
    bidder_profile_id = (bid_props.get("bidder_profile_id")
                         or bid_props.get("bidder_id"))
    tender_id = bid_props.get("tender_id")
    print(f"\n── Bid submission ──")
    print(f"  bid_node_id       : {bid_node['node_id']}")
    print(f"  bidder_profile_id : {bidder_profile_id}")
    print(f"  tender_id         : {tender_id}")

    bidder_node = load_bidder_profile(bidder_profile_id)
    bidder_props = bidder_node["properties"] or {}
    print(f"\n── Bidder profile ──")
    bidder_name = (bidder_props.get("company_name")
                   or bidder_props.get("bidder_name") or "(unnamed)")
    print(f"  name              : {bidder_name}")
    print(f"  class             : {bidder_props.get('contractor_class')}")
    print(f"  pan               : {bidder_props.get('pan')}")

    tender = SYNTHETIC_TENDER_CATALOG.get(tender_id)
    if tender is None:
        raise RuntimeError(
            f"Tender {tender_id!r} not in SYNTHETIC_TENDER_CATALOG — "
            f"add an entry or wait for Tier-2 tender-criterion node extraction"
        )
    print(f"\n── Tender criterion ──")
    print(f"  title             : {tender.get('title')}")
    print(f"  estimated_value   : ₹{tender.get('estimated_value_cr')}cr")
    print(f"  tenure_years      : {tender.get('tenure_years')}")
    print(f"  pq_turnover_floor : ₹{tender.get('pq_turnover_floor_cr')}cr")

    # 3. Rule pick via condition_evaluator
    print(f"\n── Rule selection (Tier-2 reuses condition_evaluator) ──")
    rule = select_cvc028_for_tender(tender)
    if rule is None:
        # SKIP_NOT_APPLICABLE — CVC-028 doesn't fire for this tender shape.
        # Emit a record-keeping finding so the audit trail is complete.
        verdict = "SKIP_NOT_APPLICABLE"
        print(f"  → {verdict} (CVC-028 condition_when does not fire)")
        finding = _emit_skip_finding(BID_ID, bid_node, bidder_props, tender,
                                     tender_id, bidder_profile_id)
        print(f"  → BidEvaluationFinding {finding['node_id']}")
        return 0

    # 4. Load Statement-I bidder fact
    fact_row = load_statement_i_fact(BID_ID)
    if fact_row is None:
        print(f"\n── Statement-I fact NOT FOUND for {BID_ID} ──")
        finding = _emit_gap_finding(BID_ID, bid_node, bidder_props, tender,
                                    tender_id, bidder_profile_id, rule,
                                    reason="missing_statement_i_fact_sheet")
        print(f"  → BidEvaluationFinding {finding['node_id']}  (GAP_INSUFFICIENT_DATA)")
        return 0

    ef = fact_row.get("extracted_facts") or {}
    bidder_avg_5yr = ef.get("average_5yr_cr")
    pq_floor_from_fact = ef.get("pq_floor_cr")
    pq_floor_from_catalog = tender.get("pq_turnover_floor_cr")
    fy_data = ef.get("fy_data") or []
    ground_truth_meets = ef.get("meets_pq_threshold")
    designed_to_trip = ef.get("_designed_to_trip")

    print(f"\n── Statement-I fact ──")
    print(f"  fact_sheet_id     : {fact_row['id']}")
    print(f"  bidder_avg_5yr_cr : ₹{bidder_avg_5yr}cr")
    print(f"  pq_floor (fact)   : ₹{pq_floor_from_fact}cr")
    print(f"  pq_floor (catalog): ₹{pq_floor_from_catalog}cr")
    print(f"  fy_data           : {len(fy_data)} entries")

    # Defensive cross-check: the fact's pq_floor_cr should match the
    # catalog's pq_turnover_floor_cr. Drift would indicate the synthetic
    # seed and the validator disagreed on the threshold — bad.
    pq_floor_consistent = (pq_floor_from_fact == pq_floor_from_catalog)
    if not pq_floor_consistent:
        print(f"  ⚠ PQ floor mismatch (fact={pq_floor_from_fact}, "
              f"catalog={pq_floor_from_catalog}) — using fact value as source of truth")
    pq_floor_used = pq_floor_from_fact if pq_floor_from_fact is not None else pq_floor_from_catalog

    # 5. Compute verdict
    verdict, calc = compute_verdict(bidder_avg_5yr, pq_floor_used)
    consequence = evaluation_consequence_for(verdict)
    print(f"\n── Decision ──")
    print(f"  bidder_avg_5yr    : ₹{bidder_avg_5yr}cr")
    print(f"  pq_floor_used     : ₹{pq_floor_used}cr")
    print(f"  delta_cr          : {calc['delta_cr']}")
    print(f"  ratio             : {calc['ratio']}")
    print(f"  verdict           : {verdict}")
    print(f"  consequence       : {consequence}")
    print(f"  rule.severity     : {rule['severity']}")

    # Ground-truth cross-check
    predicted_meets = (verdict == "QUALIFIED")
    matches_ground_truth = (predicted_meets == ground_truth_meets
                            if ground_truth_meets is not None else None)
    print(f"  ground_truth_meets: {ground_truth_meets}")
    print(f"  predicted_matches : {matches_ground_truth}")
    if designed_to_trip:
        print(f"  designed_to_trip  : {designed_to_trip}")

    # 6. Build the finding
    rule_node_id = get_or_create_rule_node(BID_ID, RULE_ID)

    decision_reason = (
        f"qualified_avg_{bidder_avg_5yr}cr_at_or_above_pq_floor_{pq_floor_used}cr"
        if verdict == "QUALIFIED" else
        f"ineligible_avg_{bidder_avg_5yr}cr_below_pq_floor_{pq_floor_used}cr"
    )

    label = (
        f"{TYPOLOGY}: {bidder_name} avg ₹{bidder_avg_5yr}cr "
        f"{'≥' if verdict == 'QUALIFIED' else '<'} PQ floor ₹{pq_floor_used}cr "
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
        "bidder_name":                (bidder_props.get("company_name") or bidder_props.get("bidder_name")),
        "bidder_contractor_class":    bidder_props.get("contractor_class"),
        "bidder_pan":                 bidder_props.get("pan"),
        "fact_sheet_id":              fact_row["id"],
        "fact_sheet_fact_group":      fact_row["fact_group"],
        "fact_sheet_source_file":     fact_row.get("source_file"),
        "fact_sheet_extracted_by":    fact_row.get("extracted_by"),
        "bidder_avg_5yr_turnover_cr": bidder_avg_5yr,
        "bidder_fy_data":             fy_data,

        # — tender criterion citation —
        "tender_id":                  tender_id,
        "tender_nit_no":              tender.get("nit_no"),
        "tender_title":               tender.get("title"),
        "tender_estimated_value_cr":  tender.get("estimated_value_cr"),
        "tender_tenure_years":        tender.get("tenure_years"),
        "pq_turnover_floor_cr":       pq_floor_used,
        "pq_floor_source": (
            "fact_sheet.extracted_facts.pq_floor_cr"
            if pq_floor_consistent
            else "fact_sheet.extracted_facts.pq_floor_cr (catalog mismatch flagged)"
        ),
        "pq_floor_catalog_cr":        pq_floor_from_catalog,
        "pq_floor_consistent":        pq_floor_consistent,

        # — regulatory rule citation —
        "rule_natural_language":      rule.get("natural_language"),
        "rule_condition_when":        rule.get("condition_when"),
        "rule_layer":                 rule.get("layer"),
        "rule_typology_code":         rule.get("typology_code"),
        "rule_facts_evaluated":       rule.get("_facts_evaluated"),
        # L27 audit (UNKNOWN→ADVISORY downgrade trail; mirrors Tier-1)
        "verdict_origin":             rule.get("verdict_origin"),
        "severity_origin":            rule.get("severity_origin"),

        # — computation —
        "delta_cr":                   calc["delta_cr"],
        "ratio":                      calc["ratio"],
        "meets_threshold":            calc["meets_threshold"],

        # — outcome —
        "verdict":                    verdict,
        "decision_reason":            decision_reason,

        # — ground-truth cross-check (audit) —
        "ground_truth_meets":              ground_truth_meets,
        "ground_truth_label":              designed_to_trip,
        "predicted_matches_ground_truth":  matches_ground_truth,

        # — extraction metadata —
        "extracted_by":               "tier2:bid_turnover_check_v1",
        "rule_shape":                 "threshold_bidder_side",
        "extraction_path":            "structured_fact_sheets_direct_compare",
        "input_contract":             "fact_sheets.Statement-I-AnnualTurnover",
        "defeated":                   False,
    }

    finding = rest_post("kg_nodes", [{
        "doc_id":     BID_ID,
        "node_type":  "BidEvaluationFinding",
        "label":      label,
        "properties": finding_props,
        "source_ref": f"tier2:bid_turnover_check:{RULE_ID}",
    }])[0]
    print(f"\n  → BidEvaluationFinding {finding['node_id']}")

    # 7. Edge — INELIGIBLE only (mirrors Tier-1 silent-on-COMPLIANT pattern)
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
                "bidder_avg_5yr_turnover_cr": bidder_avg_5yr,
                "pq_turnover_floor_cr":       pq_floor_used,
                "delta_cr":                   calc["delta_cr"],
                "ratio":                      calc["ratio"],
                "decision_reason":            decision_reason,
                "finding_node_id":            finding["node_id"],
                "defeated":                   False,
            },
        }])[0]
        print(f"  → BIDDER_VIOLATES_RULE {edge['edge_id']} "
              f"(BidSubmission → RuleNode)")
    else:
        print(f"  → no edge emitted ({verdict} is silent on Tier-2)")

    # 8. Summary
    wall = time.perf_counter() - t_start
    print()
    print("=" * 76)
    print(f"  TIMING: wall={wall*1000:.0f}ms  verdict={verdict}  "
          f"consequence={consequence}  ground_truth_match={matches_ground_truth}")
    print("=" * 76)

    # Hard signal for batch harness — non-zero RC if ground truth disagrees,
    # so a wrapper loop can spot regressions immediately. Hard-fails are
    # ALSO the right behavior on validator bugs.
    if matches_ground_truth is False:
        print(f"  ✗ predicted_matches_ground_truth=False — RC=2")
        return 2
    return 0


# ── SKIP / GAP convenience emitters ──────────────────────────────────

def _emit_skip_finding(bid_id, bid_node, bidder_props, tender,
                       tender_id, bidder_profile_id) -> dict:
    finding_props = {
        "tier":                   TIER,
        "typology_code":          TYPOLOGY,
        "rule_id":                RULE_ID,
        "severity":               "ADVISORY",
        "evaluation_consequence": "ADVISORY",
        "verdict":                "SKIP_NOT_APPLICABLE",
        "decision_reason":        "cvc028_condition_when_did_not_fire",
        "bid_submission_id":      bid_id,
        "bid_submission_node_id": bid_node["node_id"],
        "bidder_profile_id":      bidder_profile_id,
        "bidder_name":            (bidder_props.get("company_name") or bidder_props.get("bidder_name")),
        "tender_id":              tender_id,
        "tender_title":           tender.get("title"),
        "extracted_by":           "tier2:bid_turnover_check_v1",
        "extraction_path":        "skip_rule_inapplicable",
        "input_contract":         "fact_sheets.Statement-I-AnnualTurnover",
        "defeated":               False,
    }
    return rest_post("kg_nodes", [{
        "doc_id":     bid_id,
        "node_type":  "BidEvaluationFinding",
        "label":      f"{TYPOLOGY}: SKIP — CVC-028 does not fire for {tender.get('title')!r}",
        "properties": finding_props,
        "source_ref": f"tier2:bid_turnover_check:{RULE_ID}",
    }])[0]


def _emit_gap_finding(bid_id, bid_node, bidder_props, tender,
                      tender_id, bidder_profile_id, rule, reason: str) -> dict:
    finding_props = {
        "tier":                   TIER,
        "typology_code":          TYPOLOGY,
        "rule_id":                RULE_ID,
        "severity":               rule.get("severity"),
        "evaluation_consequence": "WARNING",
        "verdict":                "GAP_INSUFFICIENT_DATA",
        "decision_reason":        reason,
        "bid_submission_id":      bid_id,
        "bid_submission_node_id": bid_node["node_id"],
        "bidder_profile_id":      bidder_profile_id,
        "bidder_name":            (bidder_props.get("company_name") or bidder_props.get("bidder_name")),
        "tender_id":              tender_id,
        "tender_title":           tender.get("title"),
        "extracted_by":           "tier2:bid_turnover_check_v1",
        "extraction_path":        "gap_missing_input_facts",
        "input_contract":         "fact_sheets.Statement-I-AnnualTurnover",
        "defeated":               False,
    }
    return rest_post("kg_nodes", [{
        "doc_id":     bid_id,
        "node_type":  "BidEvaluationFinding",
        "label":      f"{TYPOLOGY}: GAP — {reason}",
        "properties": finding_props,
        "source_ref": f"tier2:bid_turnover_check:{RULE_ID}",
    }])[0]


if __name__ == "__main__":
    from modules.validation.verdict_emitter import main_with_crash_resilience
    # The crash-resilience wrapper's DeferredCleanup is hard-coded for
    # tier=1 ValidationFinding/VIOLATES_RULE rows. For Tier-2 it will
    # snapshot 0 rows + commit 0 deletes (harmless). Our own
    # _delete_prior_tier2_bid_turnover() inside main() handles Tier-2
    # idempotence. The wrapper still catches subprocess crashes and
    # emits a ValidationFinding subprocess_crashed audit row.
    raise SystemExit(main_with_crash_resilience(
        main, doc_id=BID_ID, typology=TYPOLOGY))
