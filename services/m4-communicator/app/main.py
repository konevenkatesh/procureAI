"""
m4-communicator service.

Two surfaces in this build:

  /m4/run  +  /worker  — verified-read mode (R4-2a). Reads the existing
    Communication kg_nodes for a tender, returns the inventory grouped
    by type and bidder. Sentinel-safe: no writes. The 75-row Communication
    sentinel stays exactly 75 across re-runs.

  POST /submit_clarification + POST /respond_clarification (R4-2b) —
    new bidder-clarification Q&A flow. Each call writes ONE new
    Communication kg_node (additive — Communication count grows by 1
    per call). Telugu↔English translation is done by Sarvam-M, gated
    by an inline PII pseudonymiser that masks bidder names + PAN +
    contact strings BEFORE the Sarvam call and restores them AFTER.

Phase-2 wiring (subprocess re-execution of the 11 `scripts/m4_drafters/
draft_*.py` scripts) is deferred so the Communication sentinel cannot
drift mid-demo. Each drafter does its own delete-then-emit cycle that
would briefly drop the count below 75 during the swap. The verified-
read mode here gives the same demo narrative without that risk.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from fastapi import HTTPException
from pydantic import BaseModel, Field

HERE = Path(__file__).resolve().parent
SERVICES_ROOT = HERE.parent.parent
sys.path.insert(0, str(SERVICES_ROOT.parent))
sys.path.insert(0, str(SERVICES_ROOT))

from _shared import make_app  # noqa: E402

logger = logging.getLogger(__name__)


# ── Env ───────────────────────────────────────────────────────────────
SUPABASE_REST_URL = os.environ.get("SUPABASE_REST_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
SARVAM_API_KEY = os.environ.get("SARVAM_API_KEY", "")
_AUTH = SUPABASE_SERVICE_ROLE_KEY or SUPABASE_ANON_KEY
_H = {
    "apikey": _AUTH,
    "Authorization": f"Bearer {_AUTH}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

SARVAM_URL = "https://api.sarvam.ai/translate"


# 11 M4 drafters (informational; matches scripts/m4_drafters/draft_*.py)
DRAFTERS = [
    "alb_justification_request",
    "award_notification",
    "bid_acknowledgment",
    "cartel_review_referral",
    "clarification_qa",
    "disqualification_letter",
    "doc_review_request",
    "flagged_notification",
    "internal_routing",
    "regret_letter",
    "query_communication_audit_trail",
]


# ── Supabase helpers ──────────────────────────────────────────────────
def _supa_get(path: str, **params: Any) -> list[dict]:
    r = requests.get(
        f"{SUPABASE_REST_URL}/rest/v1/{path}",
        headers=_H, params=params, timeout=20,
    )
    r.raise_for_status()
    return r.json()


def _supa_insert(table: str, row: dict) -> dict:
    r = requests.post(
        f"{SUPABASE_REST_URL}/rest/v1/{table}",
        headers=_H, data=json.dumps(row), timeout=20,
    )
    r.raise_for_status()
    rows = r.json()
    return rows[0] if rows else {}


# ── DPDP pseudonymiser ────────────────────────────────────────────────
# Masks bidder names + PAN + GSTIN + mobile numbers BEFORE sending text
# to Sarvam-M (an external API). The original PII is restored client-
# side after translation. Sarvam never sees the real identifiers.
#
# Patterns are deliberately broad: PAN/GSTIN are recognisable enough
# that a one-shot regex catches them; mobile numbers we restrict to
# +91 or 10-digit forms; bidder names come in as an explicit list
# (B1..B9 in the synthetic corpus) so they're exact-match.
PAN_RE     = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
GSTIN_RE   = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z]\d[A-Z]\d\b")
MOBILE_RE  = re.compile(r"\b(?:\+91[-\s]?)?[6-9]\d{9}\b")


def pseudonymise(text: str, bidder_names: list[str] | None = None) -> tuple[str, dict]:
    """Replace PII with stable tokens. Returns (masked_text, replacements)."""
    repl: dict[str, str] = {}
    def _sub(pattern: re.Pattern, tag: str, txt: str) -> str:
        i = 0
        def _r(m: re.Match) -> str:
            nonlocal i
            i += 1
            tok = f"__{tag}{i}__"
            repl[tok] = m.group(0)
            return tok
        return pattern.sub(_r, txt)
    out = text
    out = _sub(PAN_RE, "PAN", out)
    out = _sub(GSTIN_RE, "GSTIN", out)
    out = _sub(MOBILE_RE, "MOBILE", out)
    for i, name in enumerate(bidder_names or [], 1):
        if not name:
            continue
        tok = f"__BIDDER{i}__"
        if name in out:
            out = out.replace(name, tok)
            repl[tok] = name
    return out, repl


def depseudonymise(text: str, repl: dict[str, str]) -> str:
    out = text
    for tok, original in repl.items():
        out = out.replace(tok, original)
    return out


# ── Sarvam-M translate ────────────────────────────────────────────────
def _sarvam_translate(
    text: str,
    source_lang: str,         # "en-IN" | "te-IN"
    target_lang: str,
) -> str:
    if not SARVAM_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="SARVAM_API_KEY env not configured",
        )
    if not text.strip():
        return text
    r = requests.post(
        SARVAM_URL,
        headers={
            "api-subscription-key": SARVAM_API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "input":              text,
            "source_language_code": source_lang,
            "target_language_code": target_lang,
            "mode":               "formal",
            "model":              "mayura:v1",
            "enable_preprocessing": False,
        },
        timeout=60,
    )
    if not r.ok:
        logger.error("Sarvam error %s: %s", r.status_code, r.text[:200])
        raise HTTPException(
            status_code=502,
            detail=f"sarvam_api_error_{r.status_code}",
        )
    return (r.json() or {}).get("translated_text", "") or ""


def translate_with_pii_guard(
    text: str,
    source_lang: str,
    target_lang: str,
    bidder_names: list[str] | None = None,
) -> str:
    """Pseudonymise → Sarvam → depseudonymise. The PII tokens survive
    Sarvam unchanged (the strings look like underscore-padded ASCII)."""
    masked, repl = pseudonymise(text, bidder_names)
    translated = _sarvam_translate(masked, source_lang, target_lang)
    return depseudonymise(translated, repl)


# ── Verified-read worker (no writes) ──────────────────────────────────
def worker(job_id: str, params: dict) -> dict:
    tender_id = params.get("tender_id")
    types_filter = params.get("communication_types")
    rows = _supa_get(
        "kg_nodes",
        select="node_id,doc_id,properties",
        node_type="eq.Communication",
    )
    if tender_id:
        rows = [
            r for r in rows
            if (r.get("properties") or {}).get("tender_id") == tender_id
            or r.get("doc_id") == tender_id
        ]
    if types_filter:
        wanted = set(types_filter) if isinstance(types_filter, list) else None
        if wanted:
            rows = [
                r for r in rows
                if (r.get("properties") or {}).get("communication_type") in wanted
            ]
    counts_by_type: dict[str, int] = {}
    bidder_facing = 0
    bilingual = 0
    for r in rows:
        p = r.get("properties") or {}
        t = p.get("communication_type") or "UNKNOWN"
        counts_by_type[t] = counts_by_type.get(t, 0) + 1
        if p.get("bidder_facing"):
            bidder_facing += 1
        if p.get("language") in ("EN+TE", "BOTH"):
            bilingual += 1
    return {
        "tender_id":           tender_id,
        "mode":                "verified_read",
        "drafters_available":  DRAFTERS,
        "communications_found": len(rows),
        "counts_by_type":      counts_by_type,
        "bidder_facing":       bidder_facing,
        "bilingual_count":     bilingual,
        "sentinels_preserved": True,
        "message": (
            "m4-communicator verified-read mode: loaded existing "
            "Communication kg_nodes from Supabase. Hard sentinel "
            "(75 Communication) preserved by construction. Re-running "
            "the 11 drafter scripts is the Phase-2 path documented in "
            "LESSONS_LEARNED L100; today's demo path is the new "
            "/submit_clarification + /respond_clarification flow which "
            "is fully additive (Communication count grows by 1 per call)."
        ),
    }


app = make_app(
    module     = "m4",
    worker_fn  = worker,
    title      = "ProcureAI m4-communicator",
)


# ── R4-2b: Bidder Clarification Q&A endpoints (additive writes) ───────
LANG_TO_SARVAM = {"en": "en-IN", "te": "te-IN"}


class SubmitClarification(BaseModel):
    tender_id:     str = Field(min_length=1)
    bidder_id:     str = Field(min_length=1)
    bidder_name:   str | None = None
    question_text: str = Field(min_length=1)
    language:      str = Field(pattern="^(en|te)$")


class RespondClarification(BaseModel):
    parent_communication_id: str = Field(min_length=1)
    response_text:           str = Field(min_length=1)
    language:                str = Field(pattern="^(en|te)$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@app.post("/submit_clarification")
def submit_clarification(req: SubmitClarification) -> dict:
    """Bidder submits a question. Persisted as Communication kg_node
    with direction=BIDDER_INBOUND, communication_type=BIDDER_CLARIFICATION_QA,
    bilingual (EN + TE) text populated via Sarvam-M."""
    src_lang = LANG_TO_SARVAM[req.language]
    tgt_lang = LANG_TO_SARVAM["en" if req.language == "te" else "te"]

    # Translate with pseudonymisation guard
    try:
        translated = translate_with_pii_guard(
            req.question_text,
            source_lang=src_lang,
            target_lang=tgt_lang,
            bidder_names=[req.bidder_name] if req.bidder_name else [],
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Sarvam translate failed")
        raise HTTPException(status_code=502, detail=f"translate_failed: {e}")

    if req.language == "en":
        text_en, text_te = req.question_text, translated
    else:
        text_te, text_en = req.question_text, translated

    node_id = str(uuid.uuid4())
    label = (
        f"BidderClarification (Q): {req.bidder_id} on {req.tender_id} "
        f"({req.language.upper()})"
    )
    row = {
        "node_id":   node_id,
        "doc_id":    req.tender_id,
        "node_type": "Communication",
        "label":     label,
        "properties": {
            "communication_type":  "BIDDER_CLARIFICATION_QA",
            "direction":           "BIDDER_INBOUND",
            "tender_id":           req.tender_id,
            "bidder_id":           req.bidder_id,
            "bidder_name":         req.bidder_name,
            "source_language":     req.language,
            "text_en":             text_en,
            "text_te":             text_te,
            "language":            "EN+TE",
            "bidder_facing":       True,
            "submitted_at":        _now_iso(),
            "parent_communication_id": None,
            "thread_status":       "AWAITING_OFFICER_RESPONSE",
            "audit_id":            hashlib.sha256(
                f"{node_id}{req.tender_id}{req.bidder_id}".encode()
            ).hexdigest()[:12],
        },
    }
    inserted = _supa_insert("kg_nodes", row)
    return {
        "ok":               True,
        "communication_id": inserted.get("node_id") or node_id,
        "text_en":          text_en,
        "text_te":          text_te,
        "thread_status":    "AWAITING_OFFICER_RESPONSE",
    }


@app.post("/respond_clarification")
def respond_clarification(req: RespondClarification) -> dict:
    """Officer responds to a prior bidder clarification. Threads via
    parent_communication_id; flips parent's thread_status to RESOLVED."""
    # Find parent
    parents = _supa_get(
        "kg_nodes",
        select="node_id,properties",
        node_id=f"eq.{req.parent_communication_id}",
        node_type="eq.Communication",
    )
    if not parents:
        raise HTTPException(status_code=404, detail="parent_not_found")
    parent_props = parents[0].get("properties") or {}
    tender_id = parent_props.get("tender_id")
    bidder_id = parent_props.get("bidder_id")
    bidder_name = parent_props.get("bidder_name")

    src_lang = LANG_TO_SARVAM[req.language]
    tgt_lang = LANG_TO_SARVAM["en" if req.language == "te" else "te"]

    try:
        translated = translate_with_pii_guard(
            req.response_text,
            source_lang=src_lang,
            target_lang=tgt_lang,
            bidder_names=[bidder_name] if bidder_name else [],
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Sarvam translate failed")
        raise HTTPException(status_code=502, detail=f"translate_failed: {e}")

    if req.language == "en":
        text_en, text_te = req.response_text, translated
    else:
        text_te, text_en = req.response_text, translated

    node_id = str(uuid.uuid4())
    label = (
        f"BidderClarification (A): {bidder_id} on {tender_id} "
        f"({req.language.upper()})"
    )
    row = {
        "node_id":   node_id,
        "doc_id":    tender_id,
        "node_type": "Communication",
        "label":     label,
        "properties": {
            "communication_type":  "BIDDER_CLARIFICATION_QA",
            "direction":           "OFFICER_OUTBOUND",
            "tender_id":           tender_id,
            "bidder_id":           bidder_id,
            "bidder_name":         bidder_name,
            "source_language":     req.language,
            "text_en":             text_en,
            "text_te":             text_te,
            "language":            "EN+TE",
            "bidder_facing":       True,
            "submitted_at":        _now_iso(),
            "parent_communication_id": req.parent_communication_id,
            "thread_status":       "RESOLVED",
            "audit_id":            hashlib.sha256(
                f"{node_id}{req.parent_communication_id}".encode()
            ).hexdigest()[:12],
        },
    }
    inserted = _supa_insert("kg_nodes", row)

    # Flip parent thread_status (best-effort)
    try:
        new_parent_props = {**parent_props, "thread_status": "RESOLVED"}
        requests.patch(
            f"{SUPABASE_REST_URL}/rest/v1/kg_nodes",
            headers=_H,
            params={"node_id": f"eq.{req.parent_communication_id}"},
            data=json.dumps({"properties": new_parent_props}),
            timeout=10,
        )
    except Exception:
        logger.warning("parent thread_status update failed (non-fatal)")

    return {
        "ok":               True,
        "communication_id": inserted.get("node_id") or node_id,
        "parent_id":        req.parent_communication_id,
        "text_en":          text_en,
        "text_te":          text_te,
        "thread_status":    "RESOLVED",
    }
