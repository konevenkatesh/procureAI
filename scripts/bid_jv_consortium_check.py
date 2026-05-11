"""
scripts/bid_jv_consortium_check.py

═══════════════════════════════════════════════════════════════════
  Tier-2 Bid Submission Evaluator — Bidder-JV-Consortium-Compliance
  (Module 3 Extension 1 — JV/Consortium 8-Sub-Check Validator, Path A)
═══════════════════════════════════════════════════════════════════
JV-aware validator with cross-profile lookup. Last heavy sub-block
before terminal Ext-8 (B9 seed + full pipeline run).

3-path architecture:
  1. SOLE_BIDDER (B1-B8): early-return QUALIFIED-NOT_APPLICABLE
  2. JV / CONSORTIUM (B9 in Ext-8): cross-fetch Lead + Partners; run 8
     sub-checks; composite verdict via MAX-severity rule (per L80)
  3. JV_PARTNER: GAP-DATA_INTEGRITY (partners should not submit bids
     directly; only the JV entity submits)
  4. Unknown bidder_type: GAP fallback

8 sub-checks (JV/CONSORTIUM path):
  1. JV permit              — tender_type IN [Works, EPC]
  2. JV Agreement validity   — jv_agreement_validity_until ≥ submission_date
  3. Lead Partner identified — lead_partner_id resolvable + bidder_type=JV_PARTNER
  4. Joint-and-Several       — liability_terms == JOINT_AND_SEVERAL
  5. Lead Partner financial  — Lead's financial_turnover_3yr_avg_cr (NOT
                                collective) ≥ tender financial_pq_floor_cr
  6. POA Form-15 valid       — bidder.poa_status == VALID (reused from Ext-2)
  7. Partner count           — 2 ≤ len(partner_ids) ≤ 3 per AP norm
  8. Partners blacklist-clean — no active past_blacklist_events on any partner

Rule anchor:
  PRIMARY:    AP-PROC-JV-CONSORTIUM-V1 (HARD_BLOCK; seeded by Ext-1).
              condition_when: TenderState=AndhraPradesh AND TenderType IN [Works, EPC]
  SECONDARY citations in finding properties (audit chain depth):
              MPW-044 / AP-GO-002 / MPW25-119 / MPW25-120 / MPW25-121 / CVC-139

rule_shape: "composite_3_path_jv_consortium"
═══════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import os
import sys
import time
import datetime as _dt
import requests
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from builder.config import settings
from modules.validator.condition_evaluator import evaluate as evaluate_when, Verdict


BID_ID = sys.argv[1] if len(sys.argv) > 1 else "bid_synth_b1_kurnool"

TYPOLOGY = "Bidder-JV-Consortium-Compliance"
TIER = 2
RULE_ID = "AP-PROC-JV-CONSORTIUM-V1"
SECONDARY_RULE_IDS = ["MPW-044", "AP-GO-002", "MPW25-119", "MPW25-120",
                      "MPW25-121", "CVC-139"]
SOURCE_REF = "ext-1:bid_jv_consortium_check_v1"

# Synthetic tender catalog (for cross-validator access to financial PQ floor;
# mirrors bid_financial_turnover_check.SYNTHETIC_TENDER_CATALOG)
SYNTHETIC_TENDER_CATALOG: dict[str, dict] = {
    "tender_synth_kurnool": dict(
        tender_type="Works", financial_pq_floor_cr=25.5,
        nit_no="100/PROC/APIIC/1/2026", title="District Hospital, Kurnool",
        submission_date="2026-05-10",
    ),
    "tender_synth_ja": dict(
        tender_type="Works", financial_pq_floor_cr=37.65,
        nit_no="JA/2026/CW/001", title="AP Judicial Academy",
        submission_date="2026-05-10",
    ),
    "tender_synth_hc": dict(
        tender_type="Works", financial_pq_floor_cr=109.55,
        nit_no="HC/2026/CW/001", title="AP High Court",
        submission_date="2026-05-10",
    ),
}

JV_PERMITTED_TENDER_TYPES = ("Works", "EPC")
PARTNER_COUNT_MIN = 2
PARTNER_COUNT_MAX = 3


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


def _delete_prior_findings(bid_id: str) -> tuple[int, int]:
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


def load_bid_submission(bid_id: str) -> dict:
    rows = rest_get("kg_nodes", {
        "select":    "node_id,doc_id,label,properties",
        "doc_id":    f"eq.{bid_id}",
        "node_type": "eq.BidSubmission",
    })
    if not rows:
        raise RuntimeError(f"No BidSubmission for doc_id={bid_id!r}")
    return rows[0]


def load_bidder_profile(profile_id: str) -> dict | None:
    rows = rest_get("kg_nodes", {
        "select":    "node_id,doc_id,label,properties",
        "doc_id":    f"eq.{profile_id}",
        "node_type": "eq.BidderProfile",
    })
    return rows[0] if rows else None


def load_partner_profiles(partner_ids: list[str]) -> list[dict]:
    """Fetch BidderProfile rows for all listed partner profile_ids.
    Returns props lists in same order as partner_ids; None for missing."""
    out: list[dict] = []
    for pid in partner_ids or []:
        rows = rest_get("kg_nodes", {
            "select":    "node_id,doc_id,label,properties",
            "doc_id":    f"eq.{pid}",
            "node_type": "eq.BidderProfile",
        })
        out.append(rows[0]["properties"] if rows else {"_missing_profile_id": pid})
    return out


def select_rule(tender_state: str, tender_type: str) -> dict | None:
    rows = rest_get("rules", {
        "select":  "rule_id,condition_when,defeats,severity,layer,natural_language,typology_code",
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


# ── Verdict logic (3-path; testable via Approach E) ───────────────────

def _parse_date(s: str | None) -> _dt.date | None:
    if not s:
        return None
    if isinstance(s, _dt.date):
        return s
    try:
        return _dt.datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _sub_check(label: str, passed: bool, *, severity: str = "HARD_BLOCK",
               detail: str = "") -> dict:
    return {
        "sub_check": label,
        "passed":    passed,
        "severity":  severity if not passed else "ADVISORY",
        "compliance": "COMPLIANT" if passed else f"NON_COMPLIANT_{severity}",
        "detail":    detail,
    }


def compute_verdict(bidder_props: dict,
                    tender_props: dict,
                    lead_partner_props: dict | None = None,
                    partner_props_list: list[dict] | None = None
                    ) -> tuple[str, dict]:
    """3-path JV/Consortium composite verdict (L80 composite-finding pattern).

    Returns (verdict, calc_dict). calc_dict contains:
      bidder_type            — what path was taken
      jv_evaluation_summary  — list of {sub_check, passed, severity, detail}
      hard_block_sub_checks  — list of failing sub-check labels
      consequence_hint       — HARD_BLOCK / WARNING / ADVISORY
      decision_reason        — composite text
    """
    bidder_type = bidder_props.get("bidder_type", "SOLE_BIDDER")

    # ── Path 1: SOLE_BIDDER — early-return QUALIFIED-NOT_APPLICABLE ──
    if bidder_type == "SOLE_BIDDER":
        return "QUALIFIED", {
            "bidder_type": "SOLE_BIDDER",
            "jv_evaluation_summary": [{
                "sub_check": "JV_PERMIT_CHECK",
                "passed":    True,
                "severity":  "ADVISORY",
                "compliance": "NOT_APPLICABLE_SOLE_BIDDER",
                "detail":    "Bidder is SOLE_BIDDER; JV/Consortium criteria do not apply.",
            }],
            "hard_block_sub_checks": [],
            "consequence_hint":      "ADVISORY",
            "decision_reason":       "qualified_not_applicable_sole_bidder",
        }

    # ── Path 3: JV_PARTNER — data integrity catch ──
    if bidder_type == "JV_PARTNER":
        return "GAP_INSUFFICIENT_DATA", {
            "bidder_type": "JV_PARTNER",
            "jv_evaluation_summary": [{
                "sub_check": "DATA_INTEGRITY_CHECK",
                "passed":    False,
                "severity":  "WARNING",
                "compliance": "GAP_DATA_INTEGRITY",
                "detail":    ("JV_PARTNER profiles should not submit bids "
                              "directly; only the JV entity submits bids."),
            }],
            "hard_block_sub_checks": [],
            "consequence_hint":      "WARNING",
            "decision_reason":       "gap_jv_partner_should_not_submit_bids_directly",
        }

    # ── Path 4: unknown bidder_type ──
    if bidder_type not in ("JV", "CONSORTIUM"):
        return "GAP_INSUFFICIENT_DATA", {
            "bidder_type": bidder_type,
            "jv_evaluation_summary": [],
            "hard_block_sub_checks": [],
            "consequence_hint":      "WARNING",
            "decision_reason":       f"gap_unknown_bidder_type_{bidder_type!r}",
        }

    # ── Path 2: JV / CONSORTIUM — 8 sub-checks ──
    sub_checks: list[dict] = []

    # Sub-check 1: JV permit (tender type permits JV)
    tender_type = tender_props.get("tender_type") or "?"
    sc1 = _sub_check(
        "JV_PERMIT_TENDER_TYPE",
        passed=(tender_type in JV_PERMITTED_TENDER_TYPES),
        detail=f"tender_type={tender_type!r}; permitted: {JV_PERMITTED_TENDER_TYPES}",
    )
    sub_checks.append(sc1)

    # Sub-check 2: JV Agreement validity (validity_until ≥ submission_date)
    jv_validity_until = _parse_date(bidder_props.get("jv_agreement_validity_until"))
    submission_date = _parse_date(tender_props.get("submission_date"))
    if jv_validity_until is None:
        sc2_passed = False
        sc2_detail = "jv_agreement_validity_until is null/missing"
    elif submission_date is None:
        sc2_passed = False
        sc2_detail = "submission_date unparseable; cannot check validity"
    else:
        sc2_passed = jv_validity_until >= submission_date
        sc2_detail = (f"jv_agreement_validity_until={jv_validity_until.isoformat()} "
                      f"vs submission_date={submission_date.isoformat()}")
    sub_checks.append(_sub_check("JV_AGREEMENT_VALIDITY", sc2_passed, detail=sc2_detail))

    # Sub-check 3: Lead Partner identified
    lead_partner_id = bidder_props.get("lead_partner_id")
    if not lead_partner_id:
        sc3_passed = False
        sc3_detail = "lead_partner_id is null/empty"
    elif lead_partner_props is None:
        sc3_passed = False
        sc3_detail = (f"lead_partner_id={lead_partner_id!r} but lead_partner_props "
                      f"not supplied (lookup failed)")
    elif lead_partner_props.get("bidder_type") != "JV_PARTNER":
        sc3_passed = False
        sc3_detail = (f"lead_partner_id={lead_partner_id!r} resolves to bidder_type="
                      f"{lead_partner_props.get('bidder_type')!r} (expected JV_PARTNER)")
    else:
        sc3_passed = True
        sc3_detail = f"lead_partner_id={lead_partner_id!r} resolves to JV_PARTNER"
    sub_checks.append(_sub_check("LEAD_PARTNER_IDENTIFIED", sc3_passed, detail=sc3_detail))

    # Sub-check 4: Joint-and-Several liability
    lt = bidder_props.get("liability_terms")
    sc4_passed = (lt == "JOINT_AND_SEVERAL")
    sub_checks.append(_sub_check(
        "JOINT_AND_SEVERAL_LIABILITY",
        passed=sc4_passed,
        detail=f"liability_terms={lt!r}; required JOINT_AND_SEVERAL",
    ))

    # Sub-check 5: Lead Partner financial criterion (Lead alone; NOT collective)
    if lead_partner_props is None:
        sc5_passed = False
        sc5_detail = "lead_partner_props not supplied; cannot evaluate Lead financial"
    else:
        lead_fin = lead_partner_props.get("financial_turnover_3yr_avg_cr")
        tender_fin_floor = tender_props.get("financial_pq_floor_cr")
        if lead_fin is None or tender_fin_floor is None:
            sc5_passed = False
            sc5_detail = (f"Lead financial_3yr={lead_fin!r}, "
                          f"tender_financial_floor={tender_fin_floor!r}; "
                          f"missing inputs")
        else:
            sc5_passed = lead_fin >= tender_fin_floor
            sc5_detail = (f"Lead financial_3yr=₹{lead_fin}cr "
                          f"{'≥' if sc5_passed else '<'} "
                          f"tender financial floor ₹{tender_fin_floor}cr")
    sub_checks.append(_sub_check("LEAD_PARTNER_FINANCIAL", sc5_passed, detail=sc5_detail))

    # Sub-check 6: POA Form-15 valid (reuse Ext-2 poa_status field)
    poa = bidder_props.get("poa_status")
    sc6_passed = (poa == "VALID")
    sub_checks.append(_sub_check(
        "POA_FORM_15_VALID",
        passed=sc6_passed,
        detail=f"poa_status={poa!r}; required VALID",
    ))

    # Sub-check 7: Partner count (2-3 inclusive)
    partner_ids = bidder_props.get("partner_ids") or []
    n_partners = len(partner_ids)
    sc7_passed = (PARTNER_COUNT_MIN <= n_partners <= PARTNER_COUNT_MAX)
    sub_checks.append(_sub_check(
        "PARTNER_COUNT",
        passed=sc7_passed,
        detail=(f"partner_count={n_partners}; required "
                f"{PARTNER_COUNT_MIN}-{PARTNER_COUNT_MAX} inclusive"),
    ))

    # Sub-check 8: All partners blacklist-clean
    partner_props_list = partner_props_list or []
    blacklisted_partners: list[str] = []
    for pp in partner_props_list:
        if not isinstance(pp, dict):
            continue
        for evt in pp.get("past_blacklist_events") or []:
            if isinstance(evt, dict) and evt.get("current_status") == "ACTIVE":
                blacklisted_partners.append(pp.get("profile_id") or "?")
                break
    sc8_passed = (len(blacklisted_partners) == 0)
    sub_checks.append(_sub_check(
        "PARTNERS_BLACKLIST_CLEAN",
        passed=sc8_passed,
        detail=(f"blacklisted_partners={blacklisted_partners}" if blacklisted_partners
                else f"all {len(partner_props_list)} partner(s) blacklist-clean"),
    ))

    # MAX-severity composite aggregate
    hard_blocks = [sc["sub_check"] for sc in sub_checks if not sc["passed"]]
    if hard_blocks:
        verdict = "INELIGIBLE"
        consequence_hint = "HARD_BLOCK"
        reason = (f"ineligible_jv_consortium_sub_checks_failed:"
                  f"{'|'.join(hard_blocks)}")
    else:
        verdict = "QUALIFIED"
        consequence_hint = "ADVISORY"
        reason = (f"qualified_jv_consortium_all_8_sub_checks_passed_"
                  f"partner_count_{n_partners}")

    return verdict, {
        "bidder_type":            bidder_type,
        "jv_evaluation_summary":  sub_checks,
        "hard_block_sub_checks":  hard_blocks,
        "passed_count":           sum(1 for sc in sub_checks if sc["passed"]),
        "total_sub_checks":       len(sub_checks),
        "partner_count":          n_partners,
        "consequence_hint":       consequence_hint,
        "decision_reason":        reason,
    }


def evaluation_consequence_for(verdict: str, hint: str) -> str:
    if verdict == "INELIGIBLE":
        return hint
    return {"QUALIFIED": "ADVISORY",
            "GAP_INSUFFICIENT_DATA": "WARNING",
            "SKIP_NOT_APPLICABLE": "ADVISORY"}.get(verdict, "ADVISORY")


# ── Main ──────────────────────────────────────────────────────────────

def main() -> int:
    t_start = time.perf_counter()
    print("=" * 76)
    print(f"  Tier-2 Bid Evaluator — {TYPOLOGY} (Ext-1 Path A)")
    print(f"  bid_id : {BID_ID}    rule: {RULE_ID}")
    print("=" * 76)

    n_f, n_e = _delete_prior_findings(BID_ID)
    if n_f or n_e:
        print(f"  cleanup: removed {n_f} prior finding(s) + {n_e} edge(s)")

    bid_node = load_bid_submission(BID_ID)
    bid_props = bid_node["properties"] or {}
    bidder_profile_id = (bid_props.get("bidder_profile_id")
                         or bid_props.get("bidder_id"))
    tender_id = bid_props.get("tender_id")
    print(f"\n── Bid submission ──")
    print(f"  bidder_profile_id : {bidder_profile_id}")
    print(f"  tender_id         : {tender_id}")

    bidder_node = load_bidder_profile(bidder_profile_id)
    if bidder_node is None:
        raise RuntimeError(f"No BidderProfile for {bidder_profile_id!r}")
    bidder_props = bidder_node["properties"] or {}
    bidder_name = (bidder_props.get("company_name")
                   or bidder_props.get("bidder_name") or "(unnamed)")
    bidder_type = bidder_props.get("bidder_type", "SOLE_BIDDER")
    print(f"\n── Bidder ── {bidder_name}  (bidder_type={bidder_type!r})")

    tender = SYNTHETIC_TENDER_CATALOG.get(tender_id) or {}
    tender_props = {
        "tender_type":          tender.get("tender_type", "Works"),
        "financial_pq_floor_cr": tender.get("financial_pq_floor_cr"),
        "submission_date":      tender.get("submission_date", "2026-05-10"),
        "is_ap_tender":         True,
    }

    # Rule selection
    print(f"\n── Rule selection ──")
    rule = select_rule(tender_state="AndhraPradesh",
                       tender_type=tender_props["tender_type"])
    if rule is None:
        verdict = "SKIP_NOT_APPLICABLE"
        finding = _emit_skip_finding(BID_ID, bid_node, bidder_props, tender_id,
                                     bidder_profile_id, bidder_name)
        print(f"  → {verdict}  ({finding['node_id']})")
        return 0

    # JV path: cross-profile lookup for Lead Partner + Partners
    lead_partner_props: dict | None = None
    partner_props_list: list[dict] | None = None
    if bidder_type in ("JV", "CONSORTIUM"):
        lead_partner_id = bidder_props.get("lead_partner_id")
        partner_ids = bidder_props.get("partner_ids") or []
        if lead_partner_id:
            lead_node = load_bidder_profile(lead_partner_id)
            if lead_node:
                lead_partner_props = lead_node.get("properties") or {}
                print(f"  Lead Partner: {lead_partner_id!r} resolved "
                      f"(bidder_type={lead_partner_props.get('bidder_type')!r}, "
                      f"financial_3yr=₹{lead_partner_props.get('financial_turnover_3yr_avg_cr')}cr)")
            else:
                print(f"  ⚠ lead_partner_id={lead_partner_id!r} did not resolve to BidderProfile")
        partner_props_list = load_partner_profiles(partner_ids)
        print(f"  Partners: {len(partner_props_list)} of {len(partner_ids)} resolved")

    verdict, calc = compute_verdict(bidder_props, tender_props,
                                     lead_partner_props=lead_partner_props,
                                     partner_props_list=partner_props_list)
    consequence = evaluation_consequence_for(verdict, calc["consequence_hint"])

    print(f"\n── Decision ──")
    print(f"  bidder_type      : {calc['bidder_type']}")
    if calc.get("total_sub_checks"):
        print(f"  sub_check pass   : {calc['passed_count']}/{calc['total_sub_checks']}")
    for sc in calc["jv_evaluation_summary"]:
        marker = "✓" if sc["passed"] else "✗"
        print(f"  {marker} {sc['sub_check']:32s}  {sc['compliance']:30s}  {sc.get('detail','')[:80]}")
    print(f"  verdict          : {verdict}    consequence: {consequence}")

    # Ground-truth: SOLE_BIDDER predicted QUALIFIED for B1-B8
    expected_qualified = (bidder_type == "SOLE_BIDDER")
    matches_ground_truth = ((verdict == "QUALIFIED") == expected_qualified)
    print(f"  ground_truth     : {expected_qualified}    predicted_matches: {matches_ground_truth}")

    # Build finding
    rule_node_id = get_or_create_rule_node(BID_ID, RULE_ID)

    label = (
        f"{TYPOLOGY}: {bidder_name} ({calc['bidder_type']}) → {verdict}"
        if calc["bidder_type"] in ("SOLE_BIDDER", "JV_PARTNER")
        else f"{TYPOLOGY}: {bidder_name} ({calc['bidder_type']}) "
             f"{calc.get('passed_count', 0)}/{calc.get('total_sub_checks', 0)} sub-checks "
             f"→ {verdict}"
    )

    finding_props = {
        "tier":                       TIER,
        "typology_code":              TYPOLOGY,
        "rule_id":                    RULE_ID,
        "secondary_rule_ids":         SECONDARY_RULE_IDS,
        "severity":                   rule.get("severity"),
        "evaluation_consequence":     consequence,

        # bidder citation
        "bid_submission_id":          BID_ID,
        "bid_submission_node_id":     bid_node["node_id"],
        "bidder_profile_id":          bidder_profile_id,
        "bidder_profile_node_id":     bidder_node["node_id"],
        "bidder_name":                bidder_name,
        "bidder_contractor_class":    bidder_props.get("contractor_class"),
        "bidder_type":                calc["bidder_type"],

        # JV cross-profile citation
        "lead_partner_id":            bidder_props.get("lead_partner_id"),
        "partner_ids":                bidder_props.get("partner_ids"),
        "jv_agreement_node_id":       bidder_props.get("jv_agreement_node_id"),
        "jv_agreement_validity_until": bidder_props.get("jv_agreement_validity_until"),
        "liability_terms":            bidder_props.get("liability_terms"),
        "lead_partner_financial_3yr_cr": (lead_partner_props.get("financial_turnover_3yr_avg_cr")
                                          if lead_partner_props else None),
        "partner_count":              calc.get("partner_count"),

        # tender citation
        "tender_id":                  tender_id,
        "tender_nit_no":              bid_props.get("tender_nit_no"),
        "tender_financial_pq_floor_cr": tender_props.get("financial_pq_floor_cr"),

        # primary rule citation
        "rule_natural_language":      rule.get("natural_language"),
        "rule_condition_when":        rule.get("condition_when"),
        "rule_layer":                 rule.get("layer"),
        "rule_typology_code":         rule.get("typology_code"),
        "verdict_origin":             rule.get("verdict_origin"),
        "severity_origin":            rule.get("severity_origin"),

        # composite verdict per L80
        "jv_evaluation_summary":      calc["jv_evaluation_summary"],
        "hard_block_sub_checks":      calc["hard_block_sub_checks"],
        "passed_count":               calc.get("passed_count"),
        "total_sub_checks":           calc.get("total_sub_checks"),
        "consequence_hint":           calc["consequence_hint"],

        # outcome
        "verdict":                    verdict,
        "decision_reason":            calc["decision_reason"],

        # ground-truth cross-check
        "ground_truth_meets":              expected_qualified,
        "predicted_matches_ground_truth":  matches_ground_truth,

        # Ext-1 metadata
        "extension_id":               "Ext-1",
        "extension_name":             "JV/Consortium Compliance",
        "primary_rule":               RULE_ID,
        "secondary_rule_count":       len(SECONDARY_RULE_IDS),

        # extraction metadata
        "extracted_by":               SOURCE_REF,
        "rule_shape":                 "composite_3_path_jv_consortium",
        "extraction_path":            "structured_bidderprofile_3_path_with_cross_profile_lookup",
        "input_contract":             "kg_nodes.BidderProfile.properties (6 Ext-1 fields) + cross-profile Lead+Partners lookup",
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
                "tier":                    TIER,
                "rule_id":                 RULE_ID,
                "typology":                TYPOLOGY,
                "severity":                rule.get("severity"),
                "evaluation_consequence":  consequence,
                "verdict":                 verdict,
                "bid_submission_id":       BID_ID,
                "bidder_profile_id":       bidder_profile_id,
                "bidder_type":             calc["bidder_type"],
                "hard_block_sub_checks":   calc["hard_block_sub_checks"],
                "passed_count":            calc.get("passed_count"),
                "decision_reason":         calc["decision_reason"],
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
          f"consequence={consequence}  gt_match={matches_ground_truth}")
    print("=" * 76)
    if matches_ground_truth is False:
        return 2
    return 0


def _emit_skip_finding(bid_id, bid_node, bidder_props, tender_id,
                       bidder_profile_id, bidder_name) -> dict:
    return rest_post("kg_nodes", [{
        "doc_id":     bid_id,
        "node_type":  "BidEvaluationFinding",
        "label":      f"{TYPOLOGY}: SKIP — {RULE_ID} does not fire",
        "properties": {
            "tier": TIER, "typology_code": TYPOLOGY, "rule_id": RULE_ID,
            "secondary_rule_ids": SECONDARY_RULE_IDS,
            "severity": "ADVISORY", "evaluation_consequence": "ADVISORY",
            "verdict": "SKIP_NOT_APPLICABLE",
            "decision_reason": f"{RULE_ID}_condition_when_did_not_fire",
            "bid_submission_id": bid_id, "bid_submission_node_id": bid_node["node_id"],
            "bidder_profile_id": bidder_profile_id, "bidder_name": bidder_name,
            "tender_id": tender_id,
            "extension_id": "Ext-1",
            "extracted_by": SOURCE_REF,
            "extraction_path": "skip_rule_inapplicable",
            "input_contract": "kg_nodes.BidderProfile.properties (6 Ext-1 fields) + cross-profile lookup",
            "defeated": False,
        },
        "source_ref": SOURCE_REF,
    }])[0]


if __name__ == "__main__":
    from modules.validation.verdict_emitter import main_with_crash_resilience
    raise SystemExit(main_with_crash_resilience(
        main, doc_id=BID_ID, typology=TYPOLOGY))
