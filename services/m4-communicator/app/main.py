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


# ─── R12 — Chat-thread + AI-draft + Email outbound endpoints ──────────


from fastapi.responses import StreamingResponse as _SR
import asyncio as _asyncio
import smtplib as _smtplib
from email.mime.multipart import MIMEMultipart as _MIME
from email.mime.text import MIMEText as _MIMEText
from email.utils import formataddr as _formataddr
import threading as _threading

GMAIL_SMTP_USER         = os.environ.get("GMAIL_SMTP_USER", "")
GMAIL_SMTP_APP_PASSWORD = os.environ.get("GMAIL_SMTP_APP_PASSWORD", "")
SMTP_AVAILABLE = bool(GMAIL_SMTP_USER and GMAIL_SMTP_APP_PASSWORD)


@app.get("/m4/threads")
def list_threads(tender_id: str | None = None) -> dict:
    """List all communication threads, optionally filtered by tender."""
    params: dict[str, str] = {"select": "*", "order": "last_message_at.desc.nullslast"}
    if tender_id:
        params["tender_id"] = f"eq.{tender_id}"
    rows = _supabase_get("communication_thread", **params)
    return {"threads": rows, "smtp_available": SMTP_AVAILABLE}


@app.get("/m4/threads/{thread_id}")
def get_thread(thread_id: str) -> dict:
    """Get a single thread + all its Communications in chronological order."""
    threads = _supabase_get("communication_thread", select="*", thread_id=f"eq.{thread_id}")
    if not threads:
        raise HTTPException(404, detail="thread not found")
    thread = threads[0]
    msgs = _supabase_get(
        "kg_nodes",
        select="node_id,properties,created_at",
        node_type="eq.Communication",
        order="created_at.asc",
    )
    # Filter in Python — JSONB filter on nested keys is awkward via PostgREST URL params
    filtered = [
        m for m in msgs
        if (m.get("properties") or {}).get("tender_id") == thread["tender_id"]
        and (m.get("properties") or {}).get("recipient_bidder_profile_id") == thread["bidder_id"]
    ]
    return {
        "thread": thread,
        "messages": filtered,
        "smtp_available": SMTP_AVAILABLE,
    }


@app.post("/m4/threads/{thread_id}/draft")
async def ai_draft_reply(thread_id: str, req: dict) -> dict:
    """AI-drafted reply for an officer composing a message.

    Uses Vertex AI Gemini Flash with the thread context + officer intent.
    Falls back to a templated reply if Vertex is unavailable.
    """
    officer_intent = (req.get("officer_intent") or "").strip()
    if not officer_intent:
        raise HTTPException(400, detail="officer_intent required")
    # Pull thread + last 5 messages for context
    threads = _supabase_get("communication_thread", select="*", thread_id=f"eq.{thread_id}")
    if not threads:
        raise HTTPException(404, detail="thread not found")
    t = threads[0]
    msgs = _supabase_get(
        "kg_nodes",
        select="properties",
        node_type="eq.Communication",
        order="created_at.desc",
        limit="5",
    )
    msgs_for_this = [
        m for m in msgs
        if (m.get("properties") or {}).get("tender_id") == t["tender_id"]
        and (m.get("properties") or {}).get("recipient_bidder_profile_id") == t["bidder_id"]
    ]
    context_lines = [
        f"- {(m['properties'].get('communication_type') or '?')}: {(m['properties'].get('content_en') or '')[:200]}"
        for m in msgs_for_this[:3]
    ]
    context_block = "\n".join(context_lines) if context_lines else "(no prior messages)"

    prompt = (
        f"Thread context:\n"
        f"  Tender: {t.get('tender_id')}\n"
        f"  Bidder: {t.get('bidder_name') or t.get('bidder_id')}\n"
        f"  Recipient: {t.get('recipient_email')}\n"
        f"Recent messages:\n{context_block}\n\n"
        f"Officer's intent: {officer_intent}\n\n"
        f"Draft a professional reply (120-220 words). Plain English; no markdown. "
        f"Open with 'Dear Sir/Madam' and close with 'Regards, Procurement Officer'."
    )
    system = (
        "You are an Andhra Pradesh Government procurement officer drafting a formal "
        "reply in a bidder correspondence thread. Tone: respectful, precise, "
        "regulation-aware. Reference rule_ids (e.g. GFR-G-049, AP-GO-094) only if "
        "the officer's intent makes them directly relevant. Never invent facts."
    )

    # Call Vertex Gemini Flash via the m1-drafter Vertex pattern (uses metadata server)
    try:
        import urllib.request, urllib.error, subprocess, json as _json
        PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "procureai-prod")
        LOC = os.environ.get("VERTEX_LOCATION", "us-central1")
        url = f"https://{LOC}-aiplatform.googleapis.com/v1/projects/{PROJECT_ID}/locations/{LOC}/publishers/google/models/gemini-2.5-flash:generateContent"

        # Token: metadata server first, gcloud fallback
        token = None
        try:
            req_m = urllib.request.Request(
                "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
                headers={"Metadata-Flavor": "Google"},
            )
            with urllib.request.urlopen(req_m, timeout=2) as r:
                token = _json.loads(r.read())["access_token"]
        except Exception:
            try:
                token = subprocess.run(
                    ["gcloud", "auth", "print-access-token"],
                    capture_output=True, text=True, timeout=10, check=True,
                ).stdout.strip()
            except Exception:
                token = None
        if not token:
            raise RuntimeError("no Vertex token")

        body = {
            "contents":          [{"role": "user", "parts": [{"text": prompt}]}],
            "systemInstruction": {"parts": [{"text": system}]},
            "generationConfig": {
                "maxOutputTokens": 600,
                "temperature":     0.3,
                "thinkingConfig":  {"thinkingBudget": 0},
            },
        }
        req_v = urllib.request.Request(url, data=_json.dumps(body).encode("utf-8"),
                                       headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req_v, timeout=30) as r:
            data = _json.loads(r.read())
        text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
        return {"suggested_reply": text.strip(), "source": "gemini-flash"}
    except Exception as e:
        logger.warning(f"AI draft fallback: {e}")
        return {
            "suggested_reply": (
                "Dear Sir/Madam,\n\nThank you for your submission to "
                f"{t.get('tender_id')}. Regarding {officer_intent}, kindly provide the "
                "necessary clarification within seven (7) working days from the date "
                "of this notice to enable timely evaluation.\n\nRegards,\n"
                "Procurement Officer"
            ),
            "source": "fallback_template",
        }


@app.post("/m4/threads/{thread_id}/translate")
async def translate_message(thread_id: str, req: dict) -> dict:
    """Translate text between EN and TE via Sarvam-M."""
    text = (req.get("text") or "").strip()
    direction = req.get("direction", "en_to_te")
    if not text:
        raise HTTPException(400, detail="text required")
    if not SARVAM_API_KEY:
        return {"translated": text, "status": "sarvam_unavailable"}
    src, tgt = ("en-IN", "te-IN") if direction == "en_to_te" else ("te-IN", "en-IN")
    try:
        r = requests.post(
            "https://api.sarvam.ai/translate",
            headers={"api-subscription-key": SARVAM_API_KEY, "Content-Type": "application/json"},
            json={
                "input":            text[:1000],
                "source_language_code": src,
                "target_language_code": tgt,
                "speaker_gender":   "Male",
                "mode":             "formal",
                "model":            "mayura:v1",
                "enable_preprocessing": True,
            },
            timeout=20,
        )
        if r.ok:
            return {"translated": r.json().get("translated_text", text), "status": "ok"}
    except Exception as e:
        logger.warning(f"Sarvam translate failed: {e}")
    return {"translated": text, "status": "translation_failed"}


# ─── Email send (DEGRADED if no SMTP credentials) ─────────────────────


_send_buffers: dict[str, list[dict]] = {}
_send_done: dict[str, bool] = {}


def _publish_send(send_id: str, ev: dict) -> None:
    _send_buffers.setdefault(send_id, []).append(ev)


def _send_email_thread(send_id: str, thread_id: str, payload: dict) -> None:
    """Background sender. Emits SSE events; persists Communication on success.

    DEGRADED mode (no SMTP credentials): emits events as if sending, but ends
    with `send_degraded` instead of `send_complete`. New Communication row is
    still created with status=DRAFT so the message is captured.
    """
    _publish_send(send_id, {"type": "send_started", "thread_id": thread_id, "to": payload.get("to")})

    to_addr   = payload.get("to") or ""
    subject   = payload.get("subject") or "Procurement correspondence"
    body_en   = payload.get("body_en") or ""
    body_te   = payload.get("body_te") or ""
    body_html = body_en.replace("\n", "<br>")
    if body_te:
        body_html += f"<hr><h3>తెలుగు అనువాదం</h3>{body_te.replace(chr(10), '<br>')}"

    # Always: persist Communication first (DRAFT if degraded, SENT if real)
    threads = _supabase_get("communication_thread", select="*", thread_id=f"eq.{thread_id}")
    if not threads:
        _publish_send(send_id, {"type": "send_failed", "error": "thread not found"})
        _run_done_mark(send_id)
        return
    t = threads[0]
    final_status = "DRAFT" if not SMTP_AVAILABLE else "SENT"

    if not SMTP_AVAILABLE:
        _publish_send(send_id, {"type": "smtp_degraded",
                                "message": "SMTP credentials not configured — saving as DRAFT"})
    else:
        _publish_send(send_id, {"type": "smtp_connecting", "host": "smtp.gmail.com"})
        try:
            msg = _MIME("alternative")
            msg["Subject"] = subject
            msg["From"]    = _formataddr(("ProcureAI", GMAIL_SMTP_USER))
            msg["To"]      = to_addr
            msg.attach(_MIMEText(body_en + ("\n\n--- తెలుగు ---\n\n" + body_te if body_te else ""), "plain"))
            msg.attach(_MIMEText(body_html, "html"))

            _publish_send(send_id, {"type": "smtp_authenticating"})
            with _smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
                smtp.login(GMAIL_SMTP_USER, GMAIL_SMTP_APP_PASSWORD)
                _publish_send(send_id, {"type": "smtp_sending"})
                smtp.sendmail(GMAIL_SMTP_USER, [to_addr], msg.as_string())
        except Exception as e:
            logger.error(f"SMTP send failed: {e}")
            _publish_send(send_id, {"type": "send_failed", "error": str(e)[:200]})
            final_status = "FAILED"

    # Persist new Communication kg_node (additive — Communication sentinel grows by 1)
    node_id = str(uuid.uuid4())
    new_props = {
        "tender_id":                  t["tender_id"],
        "recipient_bidder_profile_id": t["bidder_id"],
        "bidder_name":                t.get("bidder_name"),
        "recipient_email":            to_addr,
        "channel":                    "EMAIL",
        "communication_type":         payload.get("communication_type") or "OFFICER_REPLY",
        "subject":                    subject,
        "content_en":                 body_en,
        "content_te":                 body_te,
        "language":                   "EN+TE" if body_te else "EN",
        "sender_role":                "PROCUREMENT_OFFICER",
        "status":                     final_status,
        "ai_drafted":                 bool(payload.get("ai_drafted")),
        "audit_id":                   hashlib.sha256(node_id.encode()).hexdigest()[:12],
        "extracted_by":               "module4:officer_reply_v1",
    }
    try:
        _supa_insert("kg_nodes", {
            "node_id":    node_id,
            "node_type":  "Communication",
            "doc_id":     t["tender_id"],
            "label":      f"OfficerReply · {t['tender_id']} · {t['bidder_id']} · {final_status}",
            "properties": new_props,
            "source_ref": "module4:officer_reply_v1",
        })
        # Update thread metadata
        requests.patch(
            f"{SUPABASE_REST_URL}/rest/v1/communication_thread",
            headers=_H,
            params={"thread_id": f"eq.{thread_id}"},
            json={
                "last_message_at":      datetime.now(timezone.utc).isoformat(),
                "last_message_snippet": body_en[:240],
            },
            timeout=10,
        )
    except Exception as e:
        logger.error(f"persist new Communication failed: {e}")

    if final_status == "SENT":
        _publish_send(send_id, {"type": "send_complete", "message_id": node_id,
                                "sent_at": datetime.now(timezone.utc).isoformat()})
    elif final_status == "DRAFT":
        _publish_send(send_id, {"type": "send_degraded", "communication_id": node_id})
    _run_done_mark(send_id)


def _run_done_mark(sid: str) -> None:
    _send_done[sid] = True


@app.post("/m4/threads/{thread_id}/send")
def send_message(thread_id: str, req: dict) -> dict:
    """Kick off email send (background). Returns send_id + stream_url."""
    if not req.get("to"):
        raise HTTPException(400, detail="recipient `to` required")
    send_id = str(uuid.uuid4())
    _send_buffers[send_id] = []
    _send_done[send_id]    = False
    _threading.Thread(
        target=_send_email_thread, args=(send_id, thread_id, req),
        daemon=True, name=f"m4-send-{send_id[:8]}",
    ).start()
    return {"send_id": send_id, "thread_id": thread_id,
            "stream_url": f"/m4/send/{send_id}/stream",
            "smtp_available": SMTP_AVAILABLE}


@app.get("/m4/send/{send_id}/stream")
async def stream_send(send_id: str) -> _SR:
    async def gen():
        cursor = 0; idle = 0
        while True:
            buf = _send_buffers.get(send_id, [])
            if cursor < len(buf):
                for ev in buf[cursor:]:
                    yield f"data: {_json_dumps(ev)}\n\n"
                cursor = len(buf)
                idle = 0
            else:
                if _send_done.get(send_id): return
                idle += 1
                if idle > 60:
                    yield 'data: {"type":"error","message":"send idle timeout"}\n\n'
                    return
                await _asyncio.sleep(0.5)

    return _SR(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })


def _json_dumps(d: dict) -> str:
    return json.dumps(d, default=str)
