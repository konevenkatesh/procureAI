"""
scripts/run_tender_ranking.py

═══════════════════════════════════════════════════════════════════
  Sub-block 5 — TenderRanking Aggregator (L1 + ALB)
═══════════════════════════════════════════════════════════════════
Reads 24 EligibilityMatrix rows + 24 LetterOfBid supplementary
nodes, groups by tender_id, filters to QUALIFIED bidders, sorts
ascending by bid_amount_cr, and emits one TenderRanking kg_node
per tender with L1/L2/L3/L4 ranking + ALB detection + excluded-
bidders drilldown.

Per-tender output (3 rows total):
  - ranking[] array of QUALIFIED bidders, ordered ascending by
    bid_amount_cr (tie-break: signature_date ASC → bid_submission_id
    lexical; tie_break_applied audit flag set when fallback fires)
  - L1 convenience fields (winner identity, amount, premium)
  - L1-L2 gap analysis (absolute + percentage)
  - ALB detection:
      threshold_method = "simple_average_times_0.80"  (per user spec)
      threshold_cr     = average_qualified_bid_cr × 0.80
      candidates[]     = bidders with bid_amount_cr < threshold
      action_required  = True if L1 is in candidates
      methodology_note  surfaces simple-average outlier sensitivity
                        (alternative ECV-anchored method documented
                        for future-method-comparison filtering)
  - excluded_bidders[] from non-QUALIFIED EligibilityMatrix rows
    (DISQUALIFIED + FLAGGED_FOR_COMMITTEE_REVIEW +
    MARK_FOR_DOCUMENTATION_REVIEW) with eligibility_matrix_node_id
    drilldown reference

Drilldown chain:
  TenderRanking
    → ranking[i].bid_submission_id        → BidSubmission node
    → ranking[i].eligibility_matrix_node_id → EligibilityMatrix
                                              → 10 BidEvaluationFinding

Pure aggregator — emits no edges. Mirrors Sub-block 4 EligibilityMatrix
pattern (L65 + L67 + L70):
  - source_ref idempotency
  - single batch-run main_with_crash_resilience wrapper
  - sentinel snapshot pre/post (RC=2 on drift)
═══════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import os
import sys
import time
import requests
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from builder.config import settings


# ── Constants ─────────────────────────────────────────────────────────

TYPOLOGY = "TenderRanking"
AGGREGATOR_DOC_ID = "tender_ranking_aggregator_v1"
SOURCE_REF = "sub_block_5:tender_ranking_aggregator_v1"

ALB_MULTIPLIER = 0.80   # per CVC standard (avg × 0.80)
ALB_THRESHOLD_METHOD = "simple_average_times_0.80"
ALB_METHODOLOGY_NOTE = (
    "ALB threshold computed as simple-average × 0.80 per CVC standard. "
    "The simple-average method is sensitive to outlier presence: an "
    "abnormally-low bid pulls the threshold DOWN, narrowing the gap "
    "between threshold and the outlier bid. Alternative methodology — "
    "ECV-anchored threshold (ECV × 0.80) — would catch the outlier with "
    "wider margin and is independent of the bid distribution. Both "
    "methods flag B8 in this corpus. The alb_threshold_method field "
    "allows downstream tooling to filter on method choice; future audit "
    "work may switch methodology without re-emitting historical findings."
)


REST = settings.supabase_rest_url
H = {"apikey": settings.supabase_anon_key,
     "Authorization": f"Bearer {settings.supabase_anon_key}"}


# ── REST helpers ──────────────────────────────────────────────────────

def rest_get(path, params=None, range_header=None):
    headers = {**H}
    if range_header:
        headers["Range"] = range_header
    r = requests.get(f"{REST}/rest/v1/{path}", params=params or {},
                     headers=headers, timeout=30)
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


def rest_count(path, params=None):
    r = requests.get(
        f"{REST}/rest/v1/{path}",
        params={**(params or {}),
                "select": "doc_id" if path == "kg_nodes" else "edge_id"},
        headers={**H, "Prefer": "count=exact", "Range": "0-0"},
        timeout=30,
    )
    cr = r.headers.get("Content-Range") or ""
    try:
        return int(cr.split("/")[-1])
    except ValueError:
        return -1


# ── Paginated reads ──────────────────────────────────────────────────

PAGE_SIZE = 100


def fetch_all_eligibility_matrix() -> list[dict]:
    out: list[dict] = []
    page = 0
    while True:
        start = page * PAGE_SIZE
        end = start + PAGE_SIZE - 1
        rows = rest_get("kg_nodes", {
            "select":    "node_id,doc_id,label,properties",
            "node_type": "eq.EligibilityMatrix",
            "order":     "doc_id.asc",
        }, range_header=f"{start}-{end}")
        out.extend(rows)
        if len(rows) < PAGE_SIZE:
            break
        page += 1
    return out


def fetch_all_letter_of_bid() -> list[dict]:
    out: list[dict] = []
    page = 0
    while True:
        start = page * PAGE_SIZE
        end = start + PAGE_SIZE - 1
        rows = rest_get("kg_nodes", {
            "select":    "node_id,doc_id,properties",
            "node_type": "eq.LetterOfBid",
            "order":     "doc_id.asc",
        }, range_header=f"{start}-{end}")
        out.extend(rows)
        if len(rows) < PAGE_SIZE:
            break
        page += 1
    return out


# ── Tender catalogue (per Sub-block 1.2 seed) ────────────────────────
# Used for tender_name + ECV lookup (TenderDocument nodes don't exist
# for synthetic tenders today — same shortcut as bid_turnover_check).

TENDER_CATALOG: dict[str, dict] = {
    "tender_synth_kurnool": dict(
        name="Construction of a new District Hospital at Kurnool",
        ecv_cr=85.0, nit_no="100/PROC/APIIC/1/2026",
    ),
    "tender_synth_ja": dict(
        name="Construction of Andhra Pradesh Judicial Academy",
        ecv_cr=125.5, nit_no="130/MAU61-USI0HB(BG)/7/2026",
    ),
    "tender_synth_hc": dict(
        name="Construction of the new Andhra Pradesh High Court complex",
        ecv_cr=365.16, nit_no="HC/APCRDA/2026/PROC/001",
    ),
}


# ── Ranking + ALB logic ──────────────────────────────────────────────

def sort_key(item: dict) -> tuple:
    """Tie-break: bid_amount_cr ASC → signature_date ASC → bid_submission_id."""
    return (item["bid_amount_cr"],
            item.get("signature_date") or "",
            item["bid_submission_id"])


def rank_qualified(qualified: list[dict]) -> list[dict]:
    """Sort + assign rank_position + compute distance_from_l1."""
    sorted_q = sorted(qualified, key=sort_key)
    if not sorted_q:
        return []
    l1_amount = sorted_q[0]["bid_amount_cr"]
    seen_keys: dict[float, list[int]] = defaultdict(list)
    for i, q in enumerate(sorted_q):
        seen_keys[q["bid_amount_cr"]].append(i)
    ranked = []
    for i, q in enumerate(sorted_q):
        delta = q["bid_amount_cr"] - l1_amount
        pct = (delta / l1_amount * 100) if l1_amount > 0 else 0.0
        # tie_break_applied: True iff this entry has an identical bid_amount_cr
        # to another QUALIFIED bidder (deterministic fallback fired)
        tied = len(seen_keys[q["bid_amount_cr"]]) > 1
        ranked.append({
            "rank_position":             f"L{i + 1}",
            "bidder_profile_id":         q["bidder_profile_id"],
            "bidder_name":               q["bidder_name"],
            "bid_submission_id":         q["bid_submission_id"],
            "bid_amount_cr":             round(q["bid_amount_cr"], 4),
            "premium_pct":               q.get("premium_pct"),
            "signature_date":            q.get("signature_date"),
            "alb_flag":                  None,    # filled below after threshold compute
            "distance_from_l1_cr":       round(delta, 4),
            "distance_from_l1_pct":      round(pct, 4),
            "eligibility_matrix_node_id": q["eligibility_matrix_node_id"],
            "tie_break_applied":         tied,
        })
    return ranked


def compute_alb(ranked: list[dict]) -> dict:
    """Average × 0.80 ALB threshold + candidate identification."""
    amounts = [r["bid_amount_cr"] for r in ranked]
    n = len(amounts)
    if n == 0:
        return dict(average_qualified_bid_cr=None,
                    alb_threshold_cr=None,
                    alb_candidates=[],
                    alb_action_required=False)
    avg = sum(amounts) / n
    threshold = avg * ALB_MULTIPLIER
    candidates: list[str] = []
    for r in ranked:
        is_alb = r["bid_amount_cr"] < threshold
        r["alb_flag"] = is_alb
        if is_alb:
            candidates.append(r["bidder_profile_id"])
    l1_is_alb = (ranked[0].get("alb_flag") is True)
    return dict(
        average_qualified_bid_cr=round(avg, 4),
        alb_threshold_cr=round(threshold, 4),
        alb_candidates=candidates,
        alb_action_required=l1_is_alb,
    )


# ── Excluded-bidder summary ──────────────────────────────────────────

def summarise_exclusion(em_props: dict) -> str:
    """Short human-readable exclusion reason from EligibilityMatrix props."""
    v = em_props.get("aggregate_verdict")
    n_hb = em_props.get("count_ineligible_hard_block", 0)
    n_w  = em_props.get("count_ineligible_warning", 0)
    n_g  = em_props.get("count_gap", 0)
    if v == "DISQUALIFIED":
        msg = f"DISQUALIFIED — {n_hb} HARD_BLOCK failure(s)"
        if n_w:
            msg += f" + {n_w} WARNING"
        return msg
    if v == "FLAGGED_FOR_COMMITTEE_REVIEW":
        return (f"FLAGGED — {n_w} WARNING finding(s) require committee review")
    if v == "MARK_FOR_DOCUMENTATION_REVIEW":
        return (f"MARK_FOR_DOC — {n_g} GAP finding(s) require bidder documentation")
    return v or "EXCLUDED"


# ── Idempotent cleanup ────────────────────────────────────────────────

def _delete_prior_tender_ranking_rows() -> int:
    rows = rest_get("kg_nodes", {
        "select":     "node_id",
        "node_type":  "eq.TenderRanking",
        "source_ref": f"eq.{SOURCE_REF}",
    })
    for row in rows:
        rest_delete("kg_nodes", {"node_id": f"eq.{row['node_id']}"})
    return len(rows)


# ── Main ──────────────────────────────────────────────────────────────

def main() -> int:
    t_start = time.perf_counter()
    print("=" * 76)
    print(f"  Sub-block 5 — TenderRanking Aggregator (L1 + ALB)")
    print(f"  source_ref : {SOURCE_REF}")
    print(f"  alb_method : {ALB_THRESHOLD_METHOD}")
    print("=" * 76)

    n_prior = _delete_prior_tender_ranking_rows()
    if n_prior:
        print(f"  cleanup: removed {n_prior} prior TenderRanking row(s)")

    # Sentinel pre-snapshot
    sentinel_pre = {
        "ValidationFinding":    rest_count("kg_nodes", {"node_type": "eq.ValidationFinding"}),
        "BidEvaluationFinding": rest_count("kg_nodes", {"node_type": "eq.BidEvaluationFinding"}),
        "BIDDER_VIOLATES_RULE": rest_count("kg_edges", {"edge_type": "eq.BIDDER_VIOLATES_RULE"}),
        "EligibilityMatrix":    rest_count("kg_nodes", {"node_type": "eq.EligibilityMatrix"}),
    }
    print(f"\n── Sentinel snapshot (pre) ──")
    for k, v in sentinel_pre.items():
        print(f"  {k:24s} : {v}")

    # Fetch EligibilityMatrix + LetterOfBid
    print(f"\n── Fetch EligibilityMatrix rows ──")
    em_rows = fetch_all_eligibility_matrix()
    print(f"  {len(em_rows)} EligibilityMatrix row(s)")
    print(f"── Fetch LetterOfBid rows ──")
    lob_rows = fetch_all_letter_of_bid()
    print(f"  {len(lob_rows)} LetterOfBid row(s)")

    # Index LetterOfBid by doc_id (== bid_submission_id)
    lob_by_bid: dict[str, dict] = {row["doc_id"]: row for row in lob_rows}
    missing_lob = []
    for em in em_rows:
        bid = (em.get("properties") or {}).get("bid_submission_id")
        if bid and bid not in lob_by_bid:
            missing_lob.append(bid)
    if missing_lob:
        print(f"  ⚠ {len(missing_lob)} EligibilityMatrix row(s) lack a LetterOfBid join: "
              f"{missing_lob[:3]}{'…' if len(missing_lob) > 3 else ''}")

    # Group EligibilityMatrix rows by tender_id
    by_tender: dict[str, list[dict]] = defaultdict(list)
    for em in em_rows:
        p = em.get("properties") or {}
        t = p.get("tender_id")
        if not t:
            continue
        by_tender[t].append({
            "em_node_id":        em["node_id"],
            "em_props":          p,
            "bid_submission_id": p.get("bid_submission_id"),
            "bidder_profile_id": p.get("bidder_profile_id"),
            "bidder_name":       p.get("bidder_name"),
            "aggregate_verdict": p.get("aggregate_verdict"),
        })
    print(f"\n── Grouped into {len(by_tender)} tender(s) ──")

    # Per-tender ranking + emit
    emitted: list[dict] = []
    print()
    print(f"  {'tender':28s} {'L1 bidder':30s} L1 amount  ALB_th  ALB_action")
    for tender_id, group in sorted(by_tender.items()):
        tender_meta = TENDER_CATALOG.get(tender_id, {})
        tender_name = tender_meta.get("name", tender_id)
        tender_ecv  = tender_meta.get("ecv_cr")
        tender_nit  = tender_meta.get("nit_no")

        # Split QUALIFIED vs excluded
        qualified_raw = []
        excluded = []
        for g in group:
            if g["aggregate_verdict"] == "QUALIFIED":
                lob = lob_by_bid.get(g["bid_submission_id"])
                if lob is None:
                    print(f"  ⚠ skipping QUALIFIED {g['bid_submission_id']} — no LetterOfBid")
                    continue
                lp = lob.get("properties") or {}
                qualified_raw.append({
                    "bidder_profile_id":          g["bidder_profile_id"],
                    "bidder_name":                g["bidder_name"],
                    "bid_submission_id":          g["bid_submission_id"],
                    "bid_amount_cr":              lp.get("bid_amount_cr"),
                    "premium_pct":                lp.get("premium_pct"),
                    "signature_date":             lp.get("signature_date"),
                    "eligibility_matrix_node_id": g["em_node_id"],
                })
            else:
                excluded.append({
                    "bidder_profile_id":          g["bidder_profile_id"],
                    "bidder_name":                g["bidder_name"],
                    "bid_submission_id":          g["bid_submission_id"],
                    "aggregate_verdict":          g["aggregate_verdict"],
                    "exclusion_summary":          summarise_exclusion(g["em_props"]),
                    "eligibility_matrix_node_id": g["em_node_id"],
                })

        # Filter out any QUALIFIED with null bid_amount_cr (defensive)
        qualified = [q for q in qualified_raw if q["bid_amount_cr"] is not None]
        if len(qualified) < len(qualified_raw):
            dropped = [q["bid_submission_id"] for q in qualified_raw
                       if q["bid_amount_cr"] is None]
            print(f"  ⚠ dropped {len(dropped)} QUALIFIED bid(s) with null bid_amount_cr: {dropped}")

        # Rank + ALB
        ranking = rank_qualified(qualified)
        alb = compute_alb(ranking)

        # Convenience fields
        l1_bidder_id = ranking[0]["bidder_profile_id"] if ranking else None
        l1_bidder_name = ranking[0]["bidder_name"]    if ranking else None
        l1_amount     = ranking[0]["bid_amount_cr"]    if ranking else None
        l1_premium    = ranking[0]["premium_pct"]      if ranking else None
        l1_bid_sub_id = ranking[0]["bid_submission_id"] if ranking else None
        l2_amount     = ranking[1]["bid_amount_cr"]    if len(ranking) >= 2 else None
        l2_premium    = ranking[1]["premium_pct"]      if len(ranking) >= 2 else None
        l1_l2_gap_cr  = (round(l2_amount - l1_amount, 4)
                         if l1_amount is not None and l2_amount is not None else None)
        l1_l2_gap_pct = (round((l2_amount - l1_amount) / l1_amount * 100, 4)
                         if l1_amount and l1_amount > 0 and l2_amount is not None else None)

        # Label
        ecv_str = f"₹{tender_ecv}cr" if tender_ecv else "?"
        if alb["alb_action_required"]:
            label = (f"TenderRanking: {tender_name[:45]} — ECV {ecv_str} — "
                     f"L1 ₹{l1_amount}cr ({l1_bidder_name[:25] if l1_bidder_name else '?'}) "
                     f"— ALB ACTION REQUIRED")
        elif ranking:
            label = (f"TenderRanking: {tender_name[:45]} — ECV {ecv_str} — "
                     f"L1 ₹{l1_amount}cr ({l1_bidder_name[:25] if l1_bidder_name else '?'})")
        else:
            label = (f"TenderRanking: {tender_name[:45]} — ECV {ecv_str} — "
                     f"NO QUALIFIED BIDDERS")

        # Diagnostic line
        l1_disp = (f"{l1_bidder_name[:28]}" if l1_bidder_name else "(none)")
        l1_amt_disp = f"₹{l1_amount}cr" if l1_amount is not None else "—"
        alb_disp = f"₹{alb['alb_threshold_cr']}cr" if alb['alb_threshold_cr'] is not None else "—"
        print(f"  {tender_id:28s} {l1_disp:30s} {l1_amt_disp:>10s}  {alb_disp:>8s}  "
              f"{'TRUE' if alb['alb_action_required'] else 'false'}")

        tr_props = {
            # identity
            "tender_id":           tender_id,
            "tender_name":         tender_name,
            "tender_ecv_cr":       tender_ecv,
            "tender_nit_no":       tender_nit,
            "tender_method":       "L1",
            "tier":                5,

            # ranking
            "ranking":             ranking,

            # L1 convenience
            "l1_winner_bidder_id":    l1_bidder_id,
            "l1_winner_bidder_name":  l1_bidder_name,
            "l1_amount_cr":           l1_amount,
            "l1_premium_pct":         l1_premium,
            "l1_bid_submission_id":   l1_bid_sub_id,

            # L1-L2 gap
            "l2_amount_cr":           l2_amount,
            "l2_premium_pct":         l2_premium,
            "l1_l2_gap_cr":           l1_l2_gap_cr,
            "l1_l2_gap_pct":          l1_l2_gap_pct,

            # ALB
            "total_qualified_bidders":     len(ranking),
            "average_qualified_bid_cr":    alb["average_qualified_bid_cr"],
            "alb_threshold_cr":            alb["alb_threshold_cr"],
            "alb_threshold_method":        ALB_THRESHOLD_METHOD,
            "alb_methodology_note":        ALB_METHODOLOGY_NOTE,
            "alb_candidates":              alb["alb_candidates"],
            "alb_action_required":         alb["alb_action_required"],

            # Exclusions (drilldown via eligibility_matrix_node_id)
            "excluded_bidders":            excluded,
            "total_excluded":              len(excluded),
            "total_bidders":               len(ranking) + len(excluded),

            # extraction metadata
            "extracted_by":                "sub_block_5:tender_ranking_aggregator_v1",
            "source_findings_count":       len(group),
            "alb_multiplier":              ALB_MULTIPLIER,
        }

        inserted = rest_post("kg_nodes", [{
            "doc_id":     tender_id,
            "node_type":  "TenderRanking",
            "label":      label,
            "properties": tr_props,
            "source_ref": SOURCE_REF,
        }])[0]
        emitted.append(inserted)

    # ALB distribution summary
    print(f"\n── ALB action distribution ──")
    alb_count = sum(1 for row in emitted
                    if row["properties"].get("alb_action_required"))
    print(f"  alb_action_required=True  : {alb_count} of {len(emitted)} tender(s)")
    print(f"  alb_action_required=False : {len(emitted) - alb_count} of {len(emitted)} tender(s)")

    # Sentinel post-snapshot
    sentinel_post = {
        "ValidationFinding":    rest_count("kg_nodes", {"node_type": "eq.ValidationFinding"}),
        "BidEvaluationFinding": rest_count("kg_nodes", {"node_type": "eq.BidEvaluationFinding"}),
        "BIDDER_VIOLATES_RULE": rest_count("kg_edges", {"edge_type": "eq.BIDDER_VIOLATES_RULE"}),
        "EligibilityMatrix":    rest_count("kg_nodes", {"node_type": "eq.EligibilityMatrix"}),
        "TenderRanking":        rest_count("kg_nodes", {"node_type": "eq.TenderRanking"}),
    }
    print(f"\n── Sentinel snapshot (post) ──")
    drift = False
    for k, v in sentinel_post.items():
        pre_v = sentinel_pre.get(k)
        marker = ""
        if pre_v is not None and pre_v != v:
            marker = f"  ⚠ DRIFT (was {pre_v})"
            drift = True
        print(f"  {k:24s} : {v}{marker}")
    if drift:
        print(f"  ✗ sentinel drift — upstream tables modified during aggregator run")
        return 2

    wall = time.perf_counter() - t_start
    print()
    print("=" * 76)
    print(f"  TenderRanking aggregator complete — emitted {len(emitted)} row(s) "
          f"in {wall:.2f}s")
    print("=" * 76)
    return 0


if __name__ == "__main__":
    from modules.validation.verdict_emitter import main_with_crash_resilience
    raise SystemExit(main_with_crash_resilience(
        main, doc_id=AGGREGATOR_DOC_ID, typology=TYPOLOGY))
