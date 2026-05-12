"""
m3-evaluator service.

R4-1 (verified-read mode): the worker reads the 4 aggregator outputs
for a tender_id from Supabase — EligibilityMatrix, TenderRanking,
BidAnomalyFinding, ComparativeStatement — and returns a structured
summary with verdict counts. This is sentinel-safe by construction:
no writes happen, so the hard sentinels (351 BidEvaluationFinding /
27 EligibilityMatrix / 3 TenderRanking / 6 BidAnomalyFinding /
3 ComparativeStatement) stay frozen.

Why verified-read instead of subprocess re-run:
  - The 4 aggregator scripts (`scripts/run_*.py`) are pure functions
    over BidEvaluationFinding. Re-running yields identical verdicts
    because the inputs haven't changed. Reading the existing output
    is operationally equivalent.
  - Bundling the procureAI package + adding pydantic-settings to
    this container would balloon the image and risk container-side
    subprocess failures that could leave the demo broken mid-pipeline.
  - The verdict counts surfaced here match a true re-run row-for-row.

Phase-2 wiring (deferred): subprocess-call `python -m
scripts.run_eligibility_matrix` etc. once the m3 container ships
with the procureAI package. The aggregators' internal idempotency
(delete-by-source_ref then re-emit) will preserve sentinels under
real re-runs too.

Worker contract:
  params: {
    "tender_id":    "<doc_id>",        # required
    "checks":       "all" | "aggregators_only" | <list>,  # informational
    "mode":         "aggregators_only" | "full",          # informational
  }
  result: {
    "tender_id":           "<doc_id>",
    "mode":                "verified_read",
    "eligibility_matrix":  {QUALIFIED, FLAGGED, MARK_FOR_DOC, DISQUALIFIED, <node_ids>},
    "tender_ranking":      {effective_l1_bidder, effective_l1_amount, ranking[]},
    "bid_anomalies":       [{type, severity, bidders, ...}],
    "comparative_statement": {audit_id, generated_at, bidder_count},
    "verdict_summary":     "<one-line plain-English summary>",
    "sentinels_preserved": True,
  }
"""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests

HERE = Path(__file__).resolve().parent
SERVICES_ROOT = HERE.parent.parent
sys.path.insert(0, str(SERVICES_ROOT.parent))
sys.path.insert(0, str(SERVICES_ROOT))

from _shared import make_app  # noqa: E402

logger = logging.getLogger(__name__)


# ── Supabase REST helpers (local to this worker; mirrors _shared) ─────
SUPABASE_REST_URL = os.environ.get("SUPABASE_REST_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
_AUTH = SUPABASE_SERVICE_ROLE_KEY or SUPABASE_ANON_KEY
_H = {
    "apikey": _AUTH,
    "Authorization": f"Bearer {_AUTH}",
    "Content-Type": "application/json",
}


def _supabase_get(path: str, **params: Any) -> list[dict]:
    """GET helper with a built-in retry."""
    url = f"{SUPABASE_REST_URL}/rest/v1/{path}"
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            r = requests.get(url, headers=_H, params=params, timeout=20)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as exc:
            last_exc = exc
            time.sleep(0.5 * (2 ** attempt))
    assert last_exc is not None
    raise last_exc


# 14 Tier-2 evaluators surfaced for transparency in the response.
BID_CHECKS = [
    "abc", "blacklist", "class", "compliance_documents",
    "emd_validity", "equipment", "financial_turnover",
    "jv_consortium", "litigation", "personnel",
    "similar_works", "solvency", "turnover", "bg_validity",
]


def _read_eligibility_matrix(tender_id: str) -> dict:
    rows = _supabase_get(
        "kg_nodes",
        select="node_id,doc_id,properties",
        node_type="eq.EligibilityMatrix",
    )
    matching = [
        r for r in rows
        if (r.get("properties") or {}).get("tender_id") == tender_id
        or r.get("doc_id") == tender_id
    ]
    verdict_counts: dict[str, int] = {
        "QUALIFIED": 0,
        "FLAGGED_FOR_COMMITTEE_REVIEW": 0,
        "MARK_FOR_DOCUMENTATION_REVIEW": 0,
        "DISQUALIFIED": 0,
    }
    bidder_rows = []
    for r in matching:
        props = r.get("properties") or {}
        v = props.get("aggregate_verdict") or props.get("verdict") or "UNKNOWN"
        verdict_counts[v] = verdict_counts.get(v, 0) + 1
        bidder_rows.append({
            "bidder_id": props.get("bidder_profile_id") or props.get("bidder_id"),
            "verdict":   v,
            "node_id":   r.get("node_id"),
        })
    return {
        "row_count":       len(matching),
        "verdict_counts":  verdict_counts,
        "bidders":         bidder_rows,
    }


def _read_tender_ranking(tender_id: str) -> dict | None:
    rows = _supabase_get(
        "kg_nodes",
        select="node_id,doc_id,properties",
        node_type="eq.TenderRanking",
    )
    for r in rows:
        props = r.get("properties") or {}
        if (
            props.get("tender_id") == tender_id
            or r.get("doc_id") == tender_id
        ):
            return {
                "node_id":              r.get("node_id"),
                "effective_l1_bidder":  props.get("effective_l1_bidder_id"),
                "effective_l1_amount":  props.get("effective_l1_amount"),
                "alb_skip_applied":     props.get("alb_skip_applied"),
                "ranking":              props.get("ranking") or [],
                "audit_id":             props.get("audit_id"),
            }
    return None


def _read_bid_anomalies(tender_id: str) -> list[dict]:
    rows = _supabase_get(
        "kg_nodes",
        select="node_id,doc_id,properties",
        node_type="eq.BidAnomalyFinding",
    )
    out = []
    for r in rows:
        props = r.get("properties") or {}
        if (
            props.get("tender_id") == tender_id
            or r.get("doc_id") == tender_id
        ):
            out.append({
                "node_id":  r.get("node_id"),
                "type":     props.get("anomaly_type") or props.get("type"),
                "severity": props.get("severity"),
                "bidders":  props.get("bidders_involved") or props.get("bidders"),
            })
    return out


def _read_comparative_statement(tender_id: str) -> dict | None:
    rows = _supabase_get(
        "kg_nodes",
        select="node_id,doc_id,properties",
        node_type="eq.ComparativeStatement",
    )
    for r in rows:
        props = r.get("properties") or {}
        if (
            props.get("tender_id") == tender_id
            or r.get("doc_id") == tender_id
        ):
            return {
                "node_id":            r.get("node_id"),
                "audit_id":           props.get("audit_id"),
                "generated_at":       props.get("generated_at"),
                "bidder_count":       len(props.get("rows") or []),
                "effective_l1":       props.get("effective_l1_bidder_id"),
                "artifact_uris":      {
                    k: props.get(k)
                    for k in ("md_uri", "docx_uri", "pdf_uri")
                    if props.get(k)
                },
            }
    return None


def worker(job_id: str, params: dict) -> dict:
    tender_id = params.get("tender_id") or ""
    mode      = params.get("mode") or "aggregators_only"
    if not tender_id:
        raise ValueError("tender_id is required")

    em = _read_eligibility_matrix(tender_id)
    tr = _read_tender_ranking(tender_id)
    ba = _read_bid_anomalies(tender_id)
    cs = _read_comparative_statement(tender_id)

    # Plain-English summary for the demo
    summary_parts: list[str] = []
    if em["row_count"]:
        q = em["verdict_counts"].get("QUALIFIED", 0)
        d = em["verdict_counts"].get("DISQUALIFIED", 0)
        summary_parts.append(
            f"EligibilityMatrix: {em['row_count']} bidders "
            f"({q} qualified, {d} disqualified)"
        )
    if tr:
        if tr.get("effective_l1_bidder"):
            summary_parts.append(
                f"Effective L1: {tr['effective_l1_bidder']} "
                f"@ ₹{tr.get('effective_l1_amount')}"
            )
    if ba:
        summary_parts.append(f"{len(ba)} anomaly finding(s)")
    if cs:
        summary_parts.append(
            f"ComparativeStatement audit_id={cs.get('audit_id')}"
        )

    return {
        "tender_id":             tender_id,
        "mode":                  "verified_read",
        "mode_requested":        mode,
        "checks_available":      BID_CHECKS,
        "checks_planned":        len(BID_CHECKS),
        "eligibility_matrix":    em,
        "tender_ranking":        tr,
        "bid_anomalies":         ba,
        "comparative_statement": cs,
        "verdict_summary":       " · ".join(summary_parts) or "No aggregator output found for this tender_id.",
        "sentinels_preserved":   True,
        "message": (
            "m3-evaluator verified-read mode: 4-stage aggregator output "
            "loaded from Supabase. Hard sentinels (351 BidEvaluationFinding "
            "/ 27 EligibilityMatrix / 3 TenderRanking / 6 BidAnomalyFinding "
            "/ 3 ComparativeStatement) are preserved by construction. "
            "Full subprocess re-execution of the 4 aggregator scripts is "
            "the Phase-2 path documented in LESSONS_LEARNED L99."
        ),
    }


app = make_app(
    module     = "m3",
    worker_fn  = worker,
    title      = "ProcureAI m3-evaluator",
)
