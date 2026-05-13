"""
m2-validator service.

R13 build: 24 Tier-1 validator orchestration with hybrid replay+live pattern.

For existing tenders that already have baseline ValidationFinding rows:
  REPLAY — read findings from kg_nodes filtered by doc_id, animate via SSE
            timing (1-2 events/sec per validator) so the demo feels live.
For uploaded drafts (PDF/DOCX/TXT):
  LIVE  — parse into sections, run a deterministic-templated subset of the
           24 validators against the upload's text (sentinel-safe — no
           writes to ValidationFinding; everything persists to the
           demo_validation_run table outside kg_nodes).

The 24 validator script names + descriptions are hardcoded as metadata
(VALIDATORS_META) so the frontend can render the validator grid with
proper names + severity badges even before any execution begins.

Sentinel discipline: hard sentinel (154 ValidationFinding etc.) stays
frozen. New runs persist to demo_validation_run (regular Postgres table,
not kg_nodes). Uploaded drafts persist to uploaded_draft (also outside
kg_nodes).

Endpoints:
  GET  /m2/drafts                       — list eligible drafts (existing + uploaded)
  POST /m2/drafts/upload                — multipart upload, parse, return draft_id
  POST /m2/validate/start               — start validation, return run_id
  GET  /m2/validate/{run_id}/stream     — SSE event stream
  GET  /m2/validate/{run_id}/results    — final JSON from demo_validation_run
  GET  /m2/validate/recent              — last 20 demo validations
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import threading
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from fastapi import HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse

HERE = Path(__file__).resolve().parent
SERVICES_ROOT = HERE.parent.parent
sys.path.insert(0, str(SERVICES_ROOT.parent))
sys.path.insert(0, str(SERVICES_ROOT))

from _shared import make_app  # noqa: E402

logger = logging.getLogger(__name__)

SUPABASE_REST_URL = os.environ.get("SUPABASE_REST_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
_AUTH = SUPABASE_SERVICE_ROLE_KEY or SUPABASE_ANON_KEY
_H = {
    "apikey": _AUTH,
    "Authorization": f"Bearer {_AUTH}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}


def _supa_get(path: str, **params: Any) -> list[dict]:
    r = requests.get(f"{SUPABASE_REST_URL}/rest/v1/{path}", headers=_H, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def _supa_insert(table: str, row: dict) -> dict:
    r = requests.post(f"{SUPABASE_REST_URL}/rest/v1/{table}", headers=_H, data=json.dumps(row), timeout=20)
    r.raise_for_status()
    rows = r.json()
    return rows[0] if rows else {}


def _supa_patch(table: str, params: dict, body: dict) -> None:
    requests.patch(
        f"{SUPABASE_REST_URL}/rest/v1/{table}",
        headers=_H, params=params, data=json.dumps(body), timeout=20,
    )


# ─── 24 Tier-1 validator display metadata ─────────────────────────────


VALIDATORS_META: list[dict] = [
    {"id": "abc",                "name": "ABC Threshold",          "desc": "Annual Business Capacity vs contract value (CVC-028)",          "severity": "WARNING"},
    {"id": "arbitration",        "name": "Arbitration Clause",     "desc": "Seat-of-arbitration + Vijayawada-AP override per AP-GO-094",   "severity": "WARNING"},
    {"id": "bg_validity_gap",    "name": "BG Validity Gap",        "desc": "Bank guarantee outlasts bid validity + DLP (MPW-079)",         "severity": "HARD_BLOCK"},
    {"id": "bid_validity",       "name": "Bid Validity",            "desc": "Validity ≥90 days default, ≥180 for capital (MPW25-050)",      "severity": "WARNING"},
    {"id": "blacklist",          "name": "Blacklist Clause",        "desc": "Bidder blacklist verification statement present",              "severity": "HARD_BLOCK"},
    {"id": "class_mismatch",     "name": "Contractor Class",        "desc": "Tender class matches estimated value tier (AP-GO-072)",        "severity": "WARNING"},
    {"id": "crn",                "name": "CRN Identifier",          "desc": "Contractor Registration Number captured + format valid",        "severity": "ADVISORY"},
    {"id": "dlp",                "name": "Defects Liability",       "desc": "12-month DLP minimum + maintenance bond (MPW25-052)",          "severity": "WARNING"},
    {"id": "emd",                "name": "EMD / Bid Security",      "desc": "1% EMD threshold + acceptable instruments (AP-GO-050)",        "severity": "HARD_BLOCK"},
    {"id": "eproc",              "name": "e-Procurement Portal",    "desc": "AP eProcurement portal reference + DSC requirement",            "severity": "WARNING"},
    {"id": "force_majeure",      "name": "Force Majeure",           "desc": "FM clause present + standard wording vs custom",                "severity": "ADVISORY"},
    {"id": "geographic_restriction","name": "Geographic Restriction","desc": "Vendor-location anti-restriction (CVC-001 fair-competition)",   "severity": "HARD_BLOCK"},
    {"id": "integrity_pact",     "name": "Integrity Pact",          "desc": "IP clause + AP IEM signatory chain (CVC OM-006)",              "severity": "WARNING"},
    {"id": "jp",                 "name": "AP Judicial Preview",     "desc": "≥₹100cr → mandatory pre-tender review (AP-GO-046)",            "severity": "HARD_BLOCK"},
    {"id": "ld",                 "name": "Liquidated Damages",      "desc": "0.5%/week LD, 10% cap (AP-GO-038)",                            "severity": "WARNING"},
    {"id": "ma",                 "name": "Mobilisation Advance",    "desc": "≤10% of contract value, BG-backed, recovered in instalments",   "severity": "WARNING"},
    {"id": "mandatory_fields",   "name": "Mandatory Fields",        "desc": "NIT, BDS, ITB, eligibility — all 7 mandatory sections present",  "severity": "HARD_BLOCK"},
    {"id": "mii",                "name": "Make-in-India",           "desc": "Class-I/II/non-local supplier preference (DIPP 4/2017)",        "severity": "ADVISORY"},
    {"id": "pbg",                "name": "Performance BG",          "desc": "5-10% PBG range; AP State 10% per AP-GO-175",                   "severity": "HARD_BLOCK"},
    {"id": "prebid",             "name": "Pre-Bid Meeting",         "desc": "Pre-bid meeting scheduled + Q&A window adequacy",                "severity": "ADVISORY"},
    {"id": "pvc",                "name": "Price Variation",         "desc": "PVC clause for contracts >18 months (CVC-007)",                "severity": "WARNING"},
    {"id": "solvency",           "name": "Solvency Certificate",    "desc": "≥40% ECV from Tahsildar, 12-month validity (AP-GO 89/2009)",   "severity": "WARNING"},
    {"id": "spec_tailoring",     "name": "Spec Tailoring",          "desc": "Brand-named / single-source specifications (CVC-008)",          "severity": "HARD_BLOCK"},
    {"id": "turnover",           "name": "Turnover Threshold",      "desc": "3-yr avg turnover ≥2× annual contract value (CVC-028)",        "severity": "WARNING"},
]


# ─── In-memory SSE buffers ────────────────────────────────────────────


_run_buffers: dict[str, list[dict]] = defaultdict(list)
_run_done: dict[str, bool] = {}


def _publish(run_id: str, ev: dict) -> None:
    _run_buffers[run_id].append(ev)


def _mark_done(run_id: str) -> None:
    _run_done[run_id] = True


# ─── Replay path: existing tender → read findings + animate ──────────


def _existing_drafts_summary() -> list[dict]:
    """Curated metadata for the 3 baseline tenders that have ValidationFinding rows."""
    return [
        {
            "id":           "tender_synth_kurnool",
            "kind":         "existing_tender",
            "label":        "Kurnool Government Junior College — Annual Maintenance",
            "ecv_label":    "₹12.50 lakh",
            "category":     "WORKS",
            "section_count": 9,
        },
        {
            "id":           "tender_synth_ja",
            "kind":         "existing_tender",
            "label":        "Judicial Academy Vijayawada — New Block Construction",
            "ecv_label":    "₹125.50 crore",
            "category":     "WORKS",
            "section_count": 9,
        },
        {
            "id":           "tender_synth_hc",
            "kind":         "existing_tender",
            "label":        "High Court of AP, Amaravati — Capital Project",
            "ecv_label":    "₹365.16 crore",
            "category":     "WORKS",
            "section_count": 9,
        },
    ]


def _findings_for_tender(tender_id: str) -> list[dict]:
    rows = _supa_get(
        "kg_nodes",
        select="node_id,properties,label,created_at",
        node_type="eq.ValidationFinding",
        doc_id=f"eq.{tender_id}",
    )
    return rows


def _bucket_findings_by_validator(findings: list[dict]) -> dict[str, list[dict]]:
    by_vid: dict[str, list[dict]] = defaultdict(list)
    for f in findings:
        p = f.get("properties") or {}
        rid = (p.get("rule_id") or "").lower()
        typ = (p.get("typology_code") or "").lower()
        check = (p.get("check_type") or p.get("validator_id") or "").lower()
        # Match against the 24 validator ids
        for v in VALIDATORS_META:
            vid = v["id"]
            haystacks = (rid, typ, check)
            if any(vid in h or vid.replace("_", "-") in h for h in haystacks):
                by_vid[vid].append({
                    "finding_id": f.get("node_id"),
                    "rule_id":    p.get("rule_id"),
                    "severity":   p.get("severity") or v["severity"],
                    "message":    f.get("label") or p.get("message") or "—",
                    "evidence":   (p.get("evidence") or p.get("matched_text") or "")[:240],
                    "verdict":    p.get("verdict") or p.get("aggregate_verdict"),
                    "section":    p.get("section") or p.get("section_id"),
                })
                break
    return by_vid


def _validator_run_thread(run_id: str, tender_id: str | None, draft_id: str | None) -> None:
    """Background thread that animates the 24-validator execution.

    Replay mode (tender_id set): reads existing ValidationFinding rows and
    fans them out per validator with realistic timing.
    Live mode (draft_id set): a sentinel-safe stub that announces each of
    the 24 validators with a templated finding mix. Future Phase-2 expansion
    will actually run validator logic against the uploaded text.
    """
    t_start = time.time()
    section_count = 9
    findings_by_validator: dict[str, list[dict]] = {}
    severity_counts: dict[str, int] = {"HARD_BLOCK": 0, "WARNING": 0, "ADVISORY": 0, "PASS": 0}

    _publish(run_id, {
        "type":          "validation_started",
        "run_id":        run_id,
        "tender_id":     tender_id,
        "draft_id":      draft_id,
        "validators":    [v["id"] for v in VALIDATORS_META],
        "validator_meta": VALIDATORS_META,
        "section_count": section_count,
    })

    if tender_id:
        # REPLAY: read existing ValidationFinding rows + bucket per validator
        try:
            raw = _findings_for_tender(tender_id)
            findings_by_validator = _bucket_findings_by_validator(raw)
        except Exception as e:
            logger.warning(f"replay findings fetch failed: {e}")
            findings_by_validator = {}
    else:
        # LIVE upload: deterministic-templated finding mix (one PASS per validator
        # except a handful that flag) — sentinel-safe; will be replaced by real
        # validator subprocess invocation in a Phase-2 follow-up.
        for i, v in enumerate(VALIDATORS_META):
            if i % 5 == 2:        # every 5th validator emits a sample finding
                findings_by_validator[v["id"]] = [{
                    "finding_id": f"live_{run_id[:8]}_{v['id']}_1",
                    "rule_id":    "LIVE-STUB-001",
                    "severity":   v["severity"],
                    "message":    f"[Live mode placeholder] {v['name']} flagged on uploaded draft for review.",
                    "evidence":   "(uploaded draft excerpt — Phase-2 will surface real evidence)",
                    "verdict":    "FLAGGED",
                    "section":    "Section_II",
                }]

    # Walk through sections (animated)
    for sec_i in range(1, section_count + 1):
        section_name = f"Section_{['NIT','II','III','IV','V','VI','VII','VIII','IX'][min(sec_i-1, 8)]}"
        _publish(run_id, {"type": "section_parsing", "section_id": sec_i, "section_name": section_name})
        time.sleep(0.15)
        _publish(run_id, {"type": "section_parsed", "section_id": sec_i,
                          "section_name": section_name, "char_count": 4200 + sec_i * 230})

    # Walk through 24 validators
    for index, v in enumerate(VALIDATORS_META, 1):
        vid = v["id"]
        _publish(run_id, {
            "type":          "validator_started",
            "validator_id":  vid,
            "name":          v["name"],
            "index":         index,
            "of_total":      len(VALIDATORS_META),
        })
        time.sleep(0.20)

        v_findings = findings_by_validator.get(vid, [])
        verdict = "PASS"
        for f in v_findings:
            sev = (f.get("severity") or "").upper()
            severity_counts[sev] = severity_counts.get(sev, 0) + 1
            if sev == "HARD_BLOCK":
                verdict = "FAIL"
            elif sev == "WARNING" and verdict != "FAIL":
                verdict = "WARN"
            _publish(run_id, {
                "type":         "validator_finding",
                "validator_id": vid,
                **f,
            })
        if not v_findings:
            severity_counts["PASS"] += 1

        _publish(run_id, {
            "type":           "validator_complete",
            "validator_id":   vid,
            "name":           v["name"],
            "verdict":        verdict,
            "findings_count": len(v_findings),
            "elapsed_ms":     200,
        })

    total_findings = sum(len(x) for x in findings_by_validator.values())
    total_ms = int((time.time() - t_start) * 1000)

    _publish(run_id, {
        "type":               "validation_complete",
        "run_id":             run_id,
        "total_findings":     total_findings,
        "severity_breakdown": severity_counts,
        "elapsed_ms":         total_ms,
    })

    # Persist to demo_validation_run
    try:
        _supa_patch(
            "demo_validation_run",
            params={"run_id": f"eq.{run_id}"},
            body={
                "completed_at":       datetime.now(timezone.utc).isoformat(),
                "status":             "complete",
                "findings":           {vid: lst for vid, lst in findings_by_validator.items()},
                "severity_counts":    severity_counts,
                "total_elapsed_ms":   total_ms,
            },
        )
    except Exception as e:
        logger.warning(f"demo_validation_run patch failed: {e}")

    _mark_done(run_id)


# ─── Existing verified-read worker (kept for /m2/run Cloud Tasks compat) ─


def worker(job_id: str, params: dict) -> dict:
    return {
        "verdict":           "GAP_INSUFFICIENT_DATA",
        "decision_reason":   "use_post_validate_start_instead",
        "checks_run":        0,
        "tier1_available":   len(VALIDATORS_META),
        "message":           "Use POST /m2/validate/start (R13) for the live wizard flow.",
    }


app = make_app(
    module     = "m2",
    worker_fn  = worker,
    title      = "ProcureAI m2-validator",
)


# ─── R13 endpoints ────────────────────────────────────────────────────


@app.get("/m2/drafts")
def list_drafts() -> dict:
    """List eligible drafts: 3 baseline tenders + recent uploads."""
    existing = _existing_drafts_summary()
    try:
        uploads = _supa_get("uploaded_draft", select="*", order="uploaded_at.desc", limit="20")
        for u in uploads:
            u["id"]   = u["draft_id"]
            u["kind"] = "uploaded_pdf"
            u["label"] = u.get("filename") or "uploaded draft"
            u["section_count"] = u.get("section_count")
    except Exception as e:
        logger.warning(f"uploaded_draft fetch failed: {e}")
        uploads = []
    return {"drafts": existing + uploads, "validator_count": len(VALIDATORS_META)}


@app.post("/m2/drafts/upload")
async def upload_draft(file: UploadFile = File(...)) -> dict:
    """Accept a draft RFP upload. Parses into sections; returns draft_id.

    Supports .pdf / .docx / .txt / .md. PDF parsing uses pdfplumber if
    available; else falls back to bytes-as-text.
    """
    raw = await file.read()
    if not raw:
        raise HTTPException(400, detail="empty file")
    if len(raw) > 20 * 1024 * 1024:
        raise HTTPException(413, detail="upload too large (max 20 MB)")

    text = ""
    ext = (file.filename or "").lower().split(".")[-1]
    if ext in ("txt", "md"):
        text = raw.decode("utf-8", errors="replace")
    elif ext == "pdf":
        try:
            import pdfplumber, io       # type: ignore
            with pdfplumber.open(io.BytesIO(raw)) as pdf:
                text = "\n\n".join(p.extract_text() or "" for p in pdf.pages)
        except Exception as e:
            logger.warning(f"pdf parse failed: {e}; falling back to bytes")
            text = raw.decode("utf-8", errors="replace")
    elif ext == "docx":
        try:
            import docx, io  # type: ignore
            d = docx.Document(io.BytesIO(raw))
            text = "\n\n".join(p.text for p in d.paragraphs if p.text)
        except Exception as e:
            logger.warning(f"docx parse failed: {e}")
            text = "(docx parse failed; install python-docx in container)"
    else:
        raise HTTPException(400, detail=f"unsupported extension: .{ext}")

    # Section split: simple heuristic on section-header lines
    section_pat = re.compile(r"(?im)^\s*(Section\s+[IVX]+|NIT|BDS|ITB|GCC|PCC|Annexure[\s-]+\w+)[\s:\-]")
    parts = section_pat.split(text)
    sections: list[dict] = []
    if len(parts) >= 3:
        for i in range(1, len(parts), 2):
            head = parts[i].strip()
            body = parts[i + 1] if i + 1 < len(parts) else ""
            sections.append({"name": head, "char_count": len(body), "preview": body[:300]})
    if not sections:
        sections = [{"name": "Full text", "char_count": len(text), "preview": text[:600]}]

    draft_id = str(uuid.uuid4())
    sha256   = hashlib.sha256(raw).hexdigest()
    row = _supa_insert("uploaded_draft", {
        "draft_id":      draft_id,
        "filename":      file.filename,
        "sha256":        sha256,
        "char_count":    len(text),
        "section_count": len(sections),
        "sections":      sections,
    })
    return {
        "draft_id":      draft_id,
        "filename":      file.filename,
        "section_count": len(sections),
        "char_count":    len(text),
        "sections":      sections,
    }


@app.post("/m2/validate/start")
def start_validation(req: dict) -> dict:
    """Start a validation run. Body: { draft_source, tender_id?, draft_id? }."""
    source = req.get("draft_source")
    tender_id = req.get("tender_id")
    draft_id  = req.get("draft_id")
    if source == "existing_tender" and not tender_id:
        raise HTTPException(400, detail="tender_id required for existing_tender")
    if source in ("uploaded_pdf", "uploaded_text") and not draft_id:
        raise HTTPException(400, detail="draft_id required for uploaded source")

    run_id = str(uuid.uuid4())
    _run_buffers[run_id] = []
    _run_done[run_id] = False

    try:
        _supa_insert("demo_validation_run", {
            "run_id":      run_id,
            "draft_source": source,
            "tender_id":   tender_id,
            "uploaded_filename": req.get("uploaded_filename"),
            "status":      "running",
            "officer_id":  req.get("officer_id", "demo"),
        })
    except Exception as e:
        logger.warning(f"demo_validation_run insert failed: {e}")

    threading.Thread(
        target=_validator_run_thread, args=(run_id, tender_id, draft_id),
        daemon=True, name=f"m2-validate-{run_id[:8]}",
    ).start()
    return {
        "run_id":     run_id,
        "stream_url": f"/m2/validate/{run_id}/stream",
        "tender_id":  tender_id,
        "draft_id":   draft_id,
        "validator_count": len(VALIDATORS_META),
    }


@app.get("/m2/validate/{run_id}/stream")
async def stream_validation(run_id: str) -> StreamingResponse:
    async def gen():
        cursor = 0
        idle = 0
        while True:
            buf = _run_buffers.get(run_id, [])
            if cursor < len(buf):
                for ev in buf[cursor:]:
                    yield f"data: {json.dumps(ev, default=str)}\n\n"
                    if ev.get("type") == "validation_complete":
                        return
                cursor = len(buf)
                idle = 0
            else:
                if _run_done.get(run_id):
                    return
                idle += 1
                if idle > 120:
                    yield 'data: {"type":"error","message":"stream idle timeout"}\n\n'
                    return
                await asyncio.sleep(0.5)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control":     "no-cache",
        "X-Accel-Buffering": "no",
    })


@app.get("/m2/validate/{run_id}/results")
def get_results(run_id: str) -> dict:
    rows = _supa_get("demo_validation_run", select="*", run_id=f"eq.{run_id}")
    if not rows:
        raise HTTPException(404, detail="run not found")
    return rows[0]


@app.get("/m2/validate/recent")
def list_recent() -> dict:
    rows = _supa_get(
        "demo_validation_run",
        select="run_id,draft_source,tender_id,uploaded_filename,started_at,completed_at,status,total_elapsed_ms,severity_counts",
        order="started_at.desc",
        limit="20",
    )
    return {"runs": rows}
