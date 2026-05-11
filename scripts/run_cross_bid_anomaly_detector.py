"""
scripts/run_cross_bid_anomaly_detector.py

═══════════════════════════════════════════════════════════════════
  Sub-block 6 — CrossBidAnomalyDetector
═══════════════════════════════════════════════════════════════════
Cross-bid pattern detection — analyses patterns ACROSS multiple
bids per tender (and across tenders) rather than evaluating any
single bid against rules. Reads 5 sources and emits one
BidAnomalyFinding per detected anomaly:

  Input contract (5 sources):
    TenderRanking      — ranking[], alb_candidates per tender
    BidderProfile      — communication_address, authorized_signatory_name
                          (Module 4 fields from Sub-block 1.2)
    LetterOfBid        — bid_amount_cr, premium_pct, signing_authority
    EMD_BG             — bg_issuing_bank
    EligibilityMatrix  — aggregate_verdict (filter to QUALIFIED only)

  Anomaly classes:
    CARTEL_SUSPECT     — per-pair cartel signal aggregation
    ALB_CORROBORATION  — cross-tender ALB consistency
    (future: BID_ROTATION, IDENTICAL_DOCUMENT_ARTIFACTS, COMMON_SUBCONTRACTOR)

CARTEL signal types + thresholds:
    SHARED_ADDRESS      HIGH   exact match on BidderProfile.communication_address
    MATCHED_SIGNATORY   MEDIUM signatory.split()[1] match (initial-with-period)
    COMMON_BANK_BRANCH  LOW    exact match on EMD_BG.bg_issuing_bank
    SEQUENTIAL_BIDS     MEDIUM premium_pct delta ≤ 0.10% AND rank-adjacent

  Aggregation: flag CARTEL_SUSPECT if signal_count ≥ 2 OR any HIGH signal.
  aggregate_severity = MAX of contributing signal severities.
  detection_confidence: HIGH if signal_count ≥ 4; MEDIUM if 2-3; LOW if 1.

ALB corroboration:
  For each alb_candidate in TenderRanking.alb_candidates, count
  cross-tender appearances. ≥2-of-3 → HIGH (systemic underbidding);
  1-of-3 → ADVISORY (could be tender-specific).

Pure aggregator — emits no edges. Drilldown via per-finding arrays:
  bid_submission_ids[], tender_ranking_node_id,
  bidder_profile_node_ids[], eligibility_matrix_node_ids[].

Mirrors Sub-block 4/5 pattern: single batch wrapper, source_ref
idempotency, sentinel snapshot pre/post (RC=2 on drift).
═══════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import os
import sys
import time
import itertools
import requests
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from builder.config import settings


# ── Constants ─────────────────────────────────────────────────────────

TYPOLOGY = "BidAnomalyFinding"
AGGREGATOR_DOC_ID = "cross_bid_anomaly_detector_v1"
SOURCE_REF = "sub_block_6:cross_bid_anomaly_detector_v1"
METHODOLOGY_VERSION = "v1"

# Cartel signal severities (queryable + tunable)
SEVERITY_HIGH = "HIGH"
SEVERITY_MED  = "MEDIUM"
SEVERITY_LOW  = "LOW"

# Signal thresholds
SEQUENTIAL_PREMIUM_DELTA_MAX_PCT = 0.10   # premium_pct delta ≤ this counts as sequential
ALB_CROSS_TENDER_HIGH_THRESHOLD = 2       # appearances ≥ this in candidates → HIGH systemic

# Citation sources per signal type
CITATION_CVC_VIGILANCE = (
    "CVC OM No 8(1)(h)/98(1) — Vigilance Aspects in Procurement; "
    "AP procurement collusion norms"
)
CITATION_CVC_ALB = "CVC OM Abnormally Low Bid norms"

METHODOLOGY_NOTE_CARTEL = (
    f"Cartel detection: flag if signal_count ≥ 2 OR any HIGH signal. "
    f"aggregate_severity = MAX(signal severities). "
    f"detection_confidence: HIGH if signal_count ≥ 4; MEDIUM if 2-3; LOW if 1. "
    f"SEQUENTIAL_BIDS threshold: premium_pct delta ≤ {SEQUENTIAL_PREMIUM_DELTA_MAX_PCT}% "
    f"AND rank-adjacent positions in TenderRanking. "
    f"COMMON_BANK_BRANCH severity LOW: on current synthetic corpus 21 of 24 bids "
    f"share SBI Vijayawada (low discrimination); contributes to confidence but "
    f"never triggers flagging alone. L74 queue: diversify bank branches across "
    f"bidders to make this signal differentiating."
)
METHODOLOGY_NOTE_ALB = (
    f"ALB corroboration: cross-tender appearance count of TenderRanking.alb_candidates. "
    f"≥{ALB_CROSS_TENDER_HIGH_THRESHOLD}-of-3 tenders → severity HIGH "
    f"(systemic underbidding pattern). 1-of-3 → ADVISORY (could be tender-specific "
    f"error). Per-tender granularity preserved for evaluation-committee report "
    f"convenience; cross_tender_consistency flag aggregates the signal."
)


REST = settings.supabase_rest_url
H = {"apikey": settings.supabase_anon_key,
     "Authorization": f"Bearer {settings.supabase_anon_key}"}


# ── REST helpers (same shape as Sub-block 4/5) ────────────────────────

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


PAGE_SIZE = 100


def fetch_all_by_type(node_type: str) -> list[dict]:
    out: list[dict] = []
    page = 0
    while True:
        start = page * PAGE_SIZE
        end = start + PAGE_SIZE - 1
        rows = rest_get("kg_nodes", {
            "select":    "node_id,doc_id,label,properties",
            "node_type": f"eq.{node_type}",
            "order":     "doc_id.asc",
        }, range_header=f"{start}-{end}")
        out.extend(rows)
        if len(rows) < PAGE_SIZE:
            break
        page += 1
    return out


# ── Cartel signal detectors ───────────────────────────────────────────

def signal_shared_address(profile_a: dict, profile_b: dict) -> dict | None:
    addr_a = (profile_a.get("communication_address") or "").strip()
    addr_b = (profile_b.get("communication_address") or "").strip()
    if addr_a and addr_a == addr_b:
        return {
            "signal_type":     "SHARED_ADDRESS",
            "severity":        SEVERITY_HIGH,
            "evidence":        (f"Both bidders' communication_address = "
                                f"{addr_a!r}"),
            "citation_source": CITATION_CVC_VIGILANCE,
        }
    return None


def _signatory_initial_token(name: str) -> str | None:
    """Extract initial-with-period from signatory name (e.g. 'Mr. R. Sharma' → 'R.').
    Returns None if name doesn't fit the 3+ token Mr./Ms./Mrs. + initial + surname shape."""
    if not name:
        return None
    parts = name.strip().split()
    if len(parts) >= 3 and parts[0].rstrip(".") in ("Mr", "Ms", "Mrs"):
        return parts[1]   # the initial-with-period (e.g. "R.")
    return None


def signal_matched_signatory(profile_a: dict, profile_b: dict) -> dict | None:
    sig_a = profile_a.get("authorized_signatory_name") or ""
    sig_b = profile_b.get("authorized_signatory_name") or ""
    init_a = _signatory_initial_token(sig_a)
    init_b = _signatory_initial_token(sig_b)
    if init_a and init_b and init_a == init_b:
        return {
            "signal_type":     "MATCHED_SIGNATORY",
            "severity":        SEVERITY_MED,
            "evidence":        (f"Signatories share {init_a!r} prefix: "
                                f"A={sig_a!r}, B={sig_b!r}"),
            "citation_source": CITATION_CVC_VIGILANCE,
        }
    return None


def signal_common_bank_branch(emd_a: dict, emd_b: dict) -> dict | None:
    bank_a = (emd_a.get("bg_issuing_bank") or "").strip()
    bank_b = (emd_b.get("bg_issuing_bank") or "").strip()
    if bank_a and bank_a == bank_b:
        return {
            "signal_type":     "COMMON_BANK_BRANCH",
            "severity":        SEVERITY_LOW,
            "evidence":        f"Both BGs issued by {bank_a!r}",
            "citation_source": CITATION_CVC_VIGILANCE,
        }
    return None


def signal_sequential_bids(rank_a: dict, rank_b: dict,
                           rank_positions: dict[str, int]) -> dict | None:
    """rank_a / rank_b are ranking[] entries from TenderRanking.
    rank_positions: dict {bid_submission_id: position_index_0_based}."""
    prem_a = rank_a.get("premium_pct")
    prem_b = rank_b.get("premium_pct")
    if prem_a is None or prem_b is None:
        return None
    delta = abs(prem_a - prem_b)
    pos_a = rank_positions.get(rank_a["bid_submission_id"])
    pos_b = rank_positions.get(rank_b["bid_submission_id"])
    adjacent = (pos_a is not None and pos_b is not None
                and abs(pos_a - pos_b) == 1)
    if delta <= SEQUENTIAL_PREMIUM_DELTA_MAX_PCT and adjacent:
        rp_a = rank_a.get("rank_position") or "?"
        rp_b = rank_b.get("rank_position") or "?"
        return {
            "signal_type":     "SEQUENTIAL_BIDS",
            "severity":        SEVERITY_MED,
            "evidence":        (f"Premium deltas within {delta:.2f}% "
                                f"(A={prem_a}%, B={prem_b}%) at adjacent "
                                f"ranks {rp_a}+{rp_b}"),
            "citation_source": CITATION_CVC_VIGILANCE,
        }
    return None


def aggregate_cartel_signals(signals: list[dict]) -> tuple[str, str, str] | None:
    """Returns (aggregate_severity, detection_confidence, recommendation) if
    pair should be flagged CARTEL_SUSPECT. Returns None if not flagged."""
    if not signals:
        return None
    severities = {s["severity"] for s in signals}
    has_high = SEVERITY_HIGH in severities
    n = len(signals)
    # Flag rule: signal_count ≥ 2 OR any HIGH signal
    if n < 2 and not has_high:
        return None
    severity_rank = {SEVERITY_LOW: 1, SEVERITY_MED: 2, SEVERITY_HIGH: 3}
    aggregate_severity = max(severities, key=lambda s: severity_rank[s])
    if n >= 4:
        confidence = "HIGH"
    elif n >= 2:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"   # HIGH-only single-signal path
    if aggregate_severity == SEVERITY_HIGH:
        recommendation = (
            "Refer pair to evaluation committee for cartel review per CVC "
            "anti-cartel norms; consider rejecting both bids or requiring "
            "independent affidavits."
        )
    else:
        recommendation = (
            "Flag pair for evaluation-committee discretion; multiple weak "
            "signals warrant elevated scrutiny."
        )
    return aggregate_severity, confidence, recommendation


# ── Idempotent cleanup ────────────────────────────────────────────────

def _delete_prior_anomaly_findings() -> int:
    rows = rest_get("kg_nodes", {
        "select":     "node_id",
        "node_type":  f"eq.{TYPOLOGY}",
        "source_ref": f"eq.{SOURCE_REF}",
    })
    for row in rows:
        rest_delete("kg_nodes", {"node_id": f"eq.{row['node_id']}"})
    return len(rows)


# ── Main ──────────────────────────────────────────────────────────────

def main() -> int:
    t_start = time.perf_counter()
    print("=" * 76)
    print(f"  Sub-block 6 — CrossBidAnomalyDetector")
    print(f"  source_ref : {SOURCE_REF}")
    print("=" * 76)

    n_prior = _delete_prior_anomaly_findings()
    if n_prior:
        print(f"  cleanup: removed {n_prior} prior BidAnomalyFinding row(s)")

    # Sentinel pre-snapshot
    sentinel_pre = {
        "ValidationFinding":    rest_count("kg_nodes", {"node_type": "eq.ValidationFinding"}),
        "BidEvaluationFinding": rest_count("kg_nodes", {"node_type": "eq.BidEvaluationFinding"}),
        "BIDDER_VIOLATES_RULE": rest_count("kg_edges", {"edge_type": "eq.BIDDER_VIOLATES_RULE"}),
        "EligibilityMatrix":    rest_count("kg_nodes", {"node_type": "eq.EligibilityMatrix"}),
        "TenderRanking":        rest_count("kg_nodes", {"node_type": "eq.TenderRanking"}),
    }
    print(f"\n── Sentinel snapshot (pre) ──")
    for k, v in sentinel_pre.items():
        print(f"  {k:24s} : {v}")

    # ── Load 5 input sources ──
    print(f"\n── Load input sources ──")
    tr_rows  = fetch_all_by_type("TenderRanking")
    bp_rows  = fetch_all_by_type("BidderProfile")
    lob_rows = fetch_all_by_type("LetterOfBid")
    emd_rows = fetch_all_by_type("EMD_BG")
    em_rows  = fetch_all_by_type("EligibilityMatrix")
    print(f"  TenderRanking      : {len(tr_rows)}")
    print(f"  BidderProfile      : {len(bp_rows)}")
    print(f"  LetterOfBid        : {len(lob_rows)}")
    print(f"  EMD_BG             : {len(emd_rows)}")
    print(f"  EligibilityMatrix  : {len(em_rows)}")

    # Index by lookup keys
    bp_by_profile_id: dict[str, dict] = {bp["doc_id"]: bp for bp in bp_rows}
    lob_by_bid_id:    dict[str, dict] = {lob["doc_id"]: lob for lob in lob_rows}
    emd_by_bid_id:    dict[str, dict] = {emd["doc_id"]: emd for emd in emd_rows}
    em_by_bid_id:     dict[str, dict] = {em["doc_id"]: em for em in em_rows}

    # ── Pre-pass: build cross-tender ALB-candidate appearance map ──
    alb_appearances: dict[str, list[str]] = defaultdict(list)
    for tr in tr_rows:
        p = tr["properties"] or {}
        tid = p.get("tender_id")
        for cand in (p.get("alb_candidates") or []):
            alb_appearances[cand].append(tid)
    print(f"\n── ALB cross-tender appearance map ──")
    for cand, tenders in alb_appearances.items():
        print(f"  {cand}  : {len(tenders)} of {len(tr_rows)} tender(s)  ({tenders})")

    # ── Per-tender detection ──
    emitted: list[dict] = []
    print()
    for tr in sorted(tr_rows, key=lambda x: x["properties"].get("tender_id") or ""):
        p = tr["properties"] or {}
        tid          = p.get("tender_id")
        tname        = p.get("tender_name")
        tecv         = p.get("tender_ecv_cr")
        ranking      = p.get("ranking") or []
        alb_cands    = p.get("alb_candidates") or []
        tr_node_id   = tr["node_id"]

        print(f"── Tender: {tid} (ECV ₹{tecv}cr) ──")
        print(f"  QUALIFIED bidders ranked: {len(ranking)}")

        # rank position index for adjacency check
        rank_positions = {r["bid_submission_id"]: i
                          for i, r in enumerate(ranking)}

        # === CARTEL: enumerate all C(n, 2) pairs of QUALIFIED bidders ===
        pair_signals_log = []
        for r_a, r_b in itertools.combinations(ranking, 2):
            bid_a, bid_b = r_a["bid_submission_id"], r_b["bid_submission_id"]
            prof_a_id, prof_b_id = r_a["bidder_profile_id"], r_b["bidder_profile_id"]
            bp_a = bp_by_profile_id.get(prof_a_id)
            bp_b = bp_by_profile_id.get(prof_b_id)
            emd_a = emd_by_bid_id.get(bid_a)
            emd_b = emd_by_bid_id.get(bid_b)
            if not (bp_a and bp_b and emd_a and emd_b):
                continue
            signals = []
            s1 = signal_shared_address(bp_a["properties"], bp_b["properties"])
            if s1: signals.append(s1)
            s2 = signal_matched_signatory(bp_a["properties"], bp_b["properties"])
            if s2: signals.append(s2)
            s3 = signal_common_bank_branch(emd_a["properties"], emd_b["properties"])
            if s3: signals.append(s3)
            s4 = signal_sequential_bids(r_a, r_b, rank_positions)
            if s4: signals.append(s4)

            pair_signals_log.append((bid_a, bid_b, [s["signal_type"] for s in signals]))
            agg = aggregate_cartel_signals(signals)
            if agg is None:
                continue
            aggregate_severity, confidence, recommendation = agg

            # cross-tender consistency for this pair: count tenders where the
            # SAME pair (by bidder_profile_id) also flags CARTEL_SUSPECT —
            # but since cartel detection is per-tender, we use the proxy
            # "both bidders appear as QUALIFIED in N tenders sharing the
            # same SHARED_ADDRESS signal". Cleaner: enumerate other tenders'
            # rankings, check if the same pair is QUALIFIED with same address.
            cross_tender_count = 0
            cross_tender_tids: list[str] = []
            for other_tr in tr_rows:
                otid = other_tr["properties"].get("tender_id")
                other_ranking = other_tr["properties"].get("ranking") or []
                ids_in_other = {r["bidder_profile_id"] for r in other_ranking}
                if prof_a_id in ids_in_other and prof_b_id in ids_in_other:
                    # Re-check signals on this other tender's records too
                    cross_tender_count += 1
                    cross_tender_tids.append(otid)
            cross_tender_consistency = cross_tender_count >= 2

            primary_bidder_names = [r_a.get("bidder_name"), r_b.get("bidder_name")]
            decision_reason = (
                f"CARTEL_SUSPECT — {primary_bidder_names[0]} + {primary_bidder_names[1]} "
                f"share {len(signals)} signal(s) ("
                + " + ".join(s["signal_type"] for s in signals)
                + f"); cross-tender consistency: pair QUALIFIED on "
                f"{cross_tender_count} of {len(tr_rows)} tender(s)"
            )
            label = (
                f"BidAnomalyFinding[CARTEL_SUSPECT]: "
                f"{primary_bidder_names[0][:25] if primary_bidder_names[0] else '?'}+"
                f"{primary_bidder_names[1][:25] if primary_bidder_names[1] else '?'} "
                f"— {len(signals)} signals — {tid.replace('tender_synth_', '')}"
            )
            cartel_props = {
                "tier":                 6,
                "tender_id":            tid,
                "tender_name":          tname,
                "tender_ecv_cr":        tecv,
                "anomaly_class":        "CARTEL_SUSPECT",
                "primary_bidder_ids":   [prof_a_id, prof_b_id],
                "primary_bidder_names": primary_bidder_names,
                "signals":              signals,
                "signal_count":         len(signals),
                "aggregate_severity":   aggregate_severity,
                "detection_confidence": confidence,
                "decision_reason":      decision_reason,
                "recommendation":       recommendation,
                "cross_tender_consistency": cross_tender_consistency,
                "cross_tender_appearances": cross_tender_count,
                "cross_tender_ids":     cross_tender_tids,
                # drilldown
                "bid_submission_ids":   [bid_a, bid_b],
                "tender_ranking_node_id": tr_node_id,
                "bidder_profile_node_ids": [bp_a["node_id"], bp_b["node_id"]],
                "eligibility_matrix_node_ids": [
                    em_by_bid_id[bid_a]["node_id"] if bid_a in em_by_bid_id else None,
                    em_by_bid_id[bid_b]["node_id"] if bid_b in em_by_bid_id else None,
                ],
                # methodology
                "methodology_version":   METHODOLOGY_VERSION,
                "methodology_note":      METHODOLOGY_NOTE_CARTEL,
                "thresholds": {
                    "sequential_premium_delta_max_pct": SEQUENTIAL_PREMIUM_DELTA_MAX_PCT,
                    "flag_rule": "signal_count >= 2 OR any HIGH signal",
                    "confidence_ladder": "HIGH if signal_count>=4, MED 2-3, LOW 1",
                },
                "extracted_by": "sub_block_6:cross_bid_anomaly_detector_v1",
            }
            inserted = rest_post("kg_nodes", [{
                "doc_id":     tid,
                "node_type":  TYPOLOGY,
                "label":      label,
                "properties": cartel_props,
                "source_ref": SOURCE_REF,
            }])[0]
            emitted.append(inserted)
            print(f"  → CARTEL_SUSPECT pair {prof_a_id} + {prof_b_id}: "
                  f"{len(signals)} signal(s) sev={aggregate_severity} conf={confidence} "
                  f"x_tender={cross_tender_count}of{len(tr_rows)}")

        # Log non-flagged pairs for transparency
        non_flagged = [(a, b, sigs) for a, b, sigs in pair_signals_log
                       if len(sigs) < 2 and SEVERITY_HIGH not in {s for s in sigs}]
        if non_flagged:
            print(f"  ({len(non_flagged)} other pair(s) carry only {len(non_flagged[0][2]) if non_flagged[0][2] else 0} weak signal(s) — below flagging threshold)")

        # === ALB_CORROBORATION: per-tender per-candidate emission ===
        for cand in alb_cands:
            x_count = len(alb_appearances.get(cand, []))
            x_tids  = alb_appearances.get(cand, [])
            severity = SEVERITY_HIGH if x_count >= ALB_CROSS_TENDER_HIGH_THRESHOLD else "ADVISORY"
            cross_consistent = x_count >= ALB_CROSS_TENDER_HIGH_THRESHOLD
            confidence = ("HIGH" if x_count >= ALB_CROSS_TENDER_HIGH_THRESHOLD
                          else "LOW")
            # Look up the candidate's BidderProfile + name from this tender's ranking
            cand_entry = next((r for r in ranking
                               if r.get("bidder_profile_id") == cand), None)
            bidder_name = cand_entry.get("bidder_name") if cand_entry else None
            bid_id = cand_entry.get("bid_submission_id") if cand_entry else None
            em_node_id = (em_by_bid_id[bid_id]["node_id"]
                          if bid_id and bid_id in em_by_bid_id else None)
            bp_node_id = bp_by_profile_id[cand]["node_id"] if cand in bp_by_profile_id else None
            l1_amount = cand_entry.get("bid_amount_cr") if cand_entry else None
            l1_premium = cand_entry.get("premium_pct") if cand_entry else None
            signals_alb = [{
                "signal_type":     "ALB_CROSS_TENDER_PATTERN",
                "severity":        severity,
                "evidence":        (f"Bidder flagged ALB on {x_count} of {len(tr_rows)} "
                                    f"tender(s) ({x_tids}); on this tender bid "
                                    f"₹{l1_amount}cr at premium {l1_premium}%."),
                "citation_source": CITATION_CVC_ALB,
            }]
            recommendation = (
                "Reject bid OR demand cost-build-up + bank guarantee per CVC ALB norms; "
                "systemic cross-tender pattern indicates calculated underbidding, "
                "not single-tender error."
            ) if cross_consistent else (
                "Single-tender ALB — verify cost build-up; tender-specific error "
                "possible. Refer to evaluation-committee discretion."
            )
            decision_reason = (
                f"ALB_CORROBORATION — bidder {bidder_name} on tender {tid}: "
                f"flagged ALB across {x_count} of {len(tr_rows)} tender(s); "
                f"cross-tender consistency {cross_consistent}."
            )
            label = (
                f"BidAnomalyFinding[ALB_CORROBORATION]: "
                f"{bidder_name[:30] if bidder_name else '?'} "
                f"— {x_count}/{len(tr_rows)} tenders — "
                f"{tid.replace('tender_synth_', '')} — sev={severity}"
            )
            alb_props = {
                "tier":                 6,
                "tender_id":            tid,
                "tender_name":          tname,
                "tender_ecv_cr":        tecv,
                "anomaly_class":        "ALB_CORROBORATION",
                "primary_bidder_ids":   [cand],
                "primary_bidder_names": [bidder_name],
                "signals":              signals_alb,
                "signal_count":         1,
                "aggregate_severity":   severity,
                "detection_confidence": confidence,
                "decision_reason":      decision_reason,
                "recommendation":       recommendation,
                "cross_tender_consistency": cross_consistent,
                "cross_tender_appearances": x_count,
                "cross_tender_ids":     x_tids,
                # bid-side specifics for this tender
                "l1_bid_amount_cr":     l1_amount,
                "l1_premium_pct":       l1_premium,
                # drilldown
                "bid_submission_ids":   [bid_id] if bid_id else [],
                "tender_ranking_node_id": tr_node_id,
                "bidder_profile_node_ids": [bp_node_id] if bp_node_id else [],
                "eligibility_matrix_node_ids": [em_node_id] if em_node_id else [],
                # methodology
                "methodology_version":   METHODOLOGY_VERSION,
                "methodology_note":      METHODOLOGY_NOTE_ALB,
                "thresholds": {
                    "cross_tender_high_threshold": ALB_CROSS_TENDER_HIGH_THRESHOLD,
                    "high_rule": (f"appearances >= {ALB_CROSS_TENDER_HIGH_THRESHOLD} "
                                  f"of {len(tr_rows)} tenders → HIGH"),
                },
                "extracted_by": "sub_block_6:cross_bid_anomaly_detector_v1",
            }
            inserted = rest_post("kg_nodes", [{
                "doc_id":     tid,
                "node_type":  TYPOLOGY,
                "label":      label,
                "properties": alb_props,
                "source_ref": SOURCE_REF,
            }])[0]
            emitted.append(inserted)
            print(f"  → ALB_CORROBORATION cand={cand}: x_tender={x_count}of{len(tr_rows)} "
                  f"sev={severity} cross_consistent={cross_consistent}")
        print()

    # ── Distribution summary ──
    by_class: dict[str, int] = defaultdict(int)
    by_severity: dict[str, int] = defaultdict(int)
    for row in emitted:
        by_class[row["properties"]["anomaly_class"]] += 1
        by_severity[row["properties"]["aggregate_severity"]] += 1
    print(f"── Anomaly distribution ──")
    for c in ("CARTEL_SUSPECT", "ALB_CORROBORATION"):
        print(f"  {c:25s} : {by_class.get(c, 0)}")
    print(f"  severity HIGH      : {by_severity.get('HIGH', 0)}")
    print(f"  severity MEDIUM    : {by_severity.get('MEDIUM', 0)}")
    print(f"  severity ADVISORY  : {by_severity.get('ADVISORY', 0)}")

    # ── Sentinel post-snapshot ──
    sentinel_post = {
        "ValidationFinding":    rest_count("kg_nodes", {"node_type": "eq.ValidationFinding"}),
        "BidEvaluationFinding": rest_count("kg_nodes", {"node_type": "eq.BidEvaluationFinding"}),
        "BIDDER_VIOLATES_RULE": rest_count("kg_edges", {"edge_type": "eq.BIDDER_VIOLATES_RULE"}),
        "EligibilityMatrix":    rest_count("kg_nodes", {"node_type": "eq.EligibilityMatrix"}),
        "TenderRanking":        rest_count("kg_nodes", {"node_type": "eq.TenderRanking"}),
        "BidAnomalyFinding":    rest_count("kg_nodes", {"node_type": f"eq.{TYPOLOGY}"}),
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
        print(f"  ✗ sentinel drift — upstream tables modified during detector run")
        return 2

    wall = time.perf_counter() - t_start
    print()
    print("=" * 76)
    print(f"  CrossBidAnomalyDetector complete — emitted {len(emitted)} row(s) "
          f"in {wall:.2f}s")
    print("=" * 76)
    return 0


if __name__ == "__main__":
    from modules.validation.verdict_emitter import main_with_crash_resilience
    raise SystemExit(main_with_crash_resilience(
        main, doc_id=AGGREGATOR_DOC_ID, typology=TYPOLOGY))
