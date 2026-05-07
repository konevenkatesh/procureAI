"""
api/drafter_api.py

FastAPI server exposing the procureAI Drafter LangGraph workflow to
the portal. Three primary endpoints + one download endpoint:

    POST  /draft/start
        Body: {"project_brief_text": "...", "project_brief_path": "..."}
        Either field can be supplied; the brief text wins if both.
        Returns: {"thread_id": "...", "gate": "FactVerification",
                  "payload": {...}}

    POST  /draft/resume
        Body: {"thread_id": "...", "response": {...}}
        The "response" shape depends on the current gate:
          Gate 1 (FactVerification):
            {"status": "confirmed" | "abandoned",
             "confirmed_facts": {...}}
          Gate 2 (ClauseReview):
            {"status": "confirmed" | "abandoned",
             "accept_all": true,
             "rejected_clause_ids": []}
          Gate 3 (FinalApproval):
            {"action": "approve" | "revise" | "abandon",
             "revision_notes": "...",
             "output_format": "md"}
        Returns: {"gate": "<next gate name>", "payload": {...}}
                 OR  {"status": "completed", "final_state": {...}}

    GET   /draft/status?thread_id=...
        Returns the current gate payload OR the final state if the
        workflow has reached END.

    GET   /draft/<thread_id>/download
        Returns the rendered draft markdown (text/markdown).

Architecture:
  - One global compiled graph (MemorySaver checkpointer). Each draft
    session is keyed by thread_id, persisted across requests.
  - CORS allowed from any origin so the portal (served from
    localhost:8765) can call this API on a different port.
  - The graph itself does the work — this server is a thin REST
    adapter.

Run:
    python3 api/drafter_api.py
    # or
    uvicorn api.drafter_api:app --port 8766 --reload
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agents.graphs.drafter_graph import (
    build_drafter_graph, start_session, resume_session,
    _extract_interrupt_payload,
)
from langgraph.types import Command
from langgraph.checkpoint.memory import MemorySaver


# ── Global graph instance ─────────────────────────────────────────────
# Single MemorySaver-backed graph for the lifetime of the server.
# Each draft session is a separate thread_id.

_CHECKPOINTER = MemorySaver()
_GRAPH        = build_drafter_graph(checkpointer=_CHECKPOINTER)

# Registered draft sessions (thread_id → metadata for status endpoint)
_SESSIONS: dict[str, dict] = {}


# ── FastAPI app ───────────────────────────────────────────────────────

app = FastAPI(
    title="procureAI Drafter API",
    description=(
        "Multi-phase tender-drafting workflow with 3 human-in-the-loop "
        "gates. Drives the 'Draft Tender' tab in the procureAI portal."
    ),
    version="0.4.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Pydantic request models ───────────────────────────────────────────

class StartRequest(BaseModel):
    project_brief_text: Optional[str] = None
    project_brief_path: Optional[str] = None


class ResumeRequest(BaseModel):
    thread_id: str
    response:  dict


# ── Helper: invoke + serialise interrupt payload ──────────────────────

def _invoke_safely(state_or_command: Any, config: dict) -> dict:
    """Wrap graph.invoke + extract interrupt payload OR final state.
    Returns a JSON-serialisable dict for the response."""
    result = _GRAPH.invoke(state_or_command, config=config)
    payload = _extract_interrupt_payload(result)
    if payload is not None:
        # Determine the gate from payload
        gate = payload.get("gate") if isinstance(payload, dict) else None
        return {"status": "paused", "gate": gate, "payload": payload}
    # Workflow completed — return final state (sanitised)
    final = _sanitise_for_json(result)
    return {"status": "completed", "final_state": final}


def _sanitise_for_json(state: dict) -> dict:
    """Strip non-JSON-serialisable fields (e.g. very large markdown
    bodies) and keep the keys the portal needs."""
    if not isinstance(state, dict):
        return {}
    out: dict = {}
    keep_full = {
        "extracted_facts", "facts_confidence", "facts_source",
        "facts_evidence", "facts_checklist", "extractor_summary",
        "officer_facts", "selected_clauses", "clauses_by_status",
        "clauses_by_section", "accepted_clause_ids",
        "rejected_clause_ids", "validation_findings",
        "n_hard_blocks", "n_warnings", "n_clauses_in_draft",
        "n_placeholders_filled", "n_placeholders_unresolved",
        "draft_path", "final_output_path", "officer_approved",
        "revision_notes", "output_format", "gate_1_status",
        "gate_2_status", "gate_3_status", "workflow_status",
        "thread_id", "timings_ms", "extractor_model",
    }
    for k, v in state.items():
        if k == "draft_markdown":
            # truncate to first 2000 chars in status responses; full
            # version available via /draft/<thread_id>/download
            out["draft_markdown_preview"] = (v or "")[:2000]
            continue
        if k == "selected_clauses":
            # Heavy — only count + by-status aggregation
            out["selected_clauses_count"] = len(v) if v else 0
            continue
        if k in keep_full:
            out[k] = v
    return out


# ── Endpoints ─────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict:
    return {
        "status":  "ok",
        "service": "drafter_api",
        "graph":   "ap_works_drafter_v0.4",
        "active_sessions": len(_SESSIONS),
    }


@app.post("/draft/start")
def draft_start(req: StartRequest) -> dict:
    if not (req.project_brief_text or req.project_brief_path):
        raise HTTPException(
            status_code=400,
            detail="Either project_brief_text or project_brief_path is required",
        )
    try:
        # Use start_session helper but with our shared graph + checkpointer
        import uuid
        thread_id = f"draft-{uuid.uuid4().hex[:12]}"
        config = {"configurable": {"thread_id": thread_id}}
        init_state: dict = {
            "project_brief_text":   req.project_brief_text,
            "project_brief_path":   req.project_brief_path,
            "project_brief_source": ("pdf" if req.project_brief_path else "text"),
            "thread_id":            thread_id,
            "workflow_status":      "in_progress",
        }
        _SESSIONS[thread_id] = {
            "thread_id":           thread_id,
            "project_brief_text":  req.project_brief_text,
            "project_brief_path":  req.project_brief_path,
        }
        result = _GRAPH.invoke(init_state, config=config)
        payload = _extract_interrupt_payload(result)
        if payload is None:
            return {"thread_id": thread_id, "status": "completed",
                    "final_state": _sanitise_for_json(result)}
        gate = payload.get("gate") if isinstance(payload, dict) else None
        return {
            "thread_id": thread_id,
            "status":    "paused",
            "gate":      gate,
            "payload":   payload,
        }
    except Exception as e:
        raise HTTPException(status_code=500,
                            detail=f"draft/start failed: {type(e).__name__}: {e}")


@app.post("/draft/start_pdf")
async def draft_start_pdf(file: UploadFile = File(...)) -> dict:
    """Upload a PDF / DOCX / MD project brief; saved to /tmp and the
    workflow runs against it. Returns same shape as /draft/start."""
    suffix = Path(file.filename or "brief").suffix.lower() or ".bin"
    import uuid
    upload_id = uuid.uuid4().hex[:8]
    tmp_path = Path(f"/tmp/drafter_brief_{upload_id}{suffix}")
    try:
        content = await file.read()
        tmp_path.write_bytes(content)
    except Exception as e:
        raise HTTPException(status_code=400,
                            detail=f"upload save failed: {e}")
    return draft_start(StartRequest(project_brief_path=str(tmp_path)))


@app.post("/draft/resume")
def draft_resume(req: ResumeRequest) -> dict:
    if req.thread_id not in _SESSIONS:
        # Allow resuming sessions whose start request happened before
        # this server restarted (they live in MemorySaver's process
        # memory; if the process restarted, they're gone). We surface
        # the missing-thread case as 404.
        raise HTTPException(status_code=404,
                            detail=f"thread_id {req.thread_id} not found "
                                   "(may have expired on server restart)")
    config = {"configurable": {"thread_id": req.thread_id}}
    try:
        result = _GRAPH.invoke(Command(resume=req.response), config=config)
        payload = _extract_interrupt_payload(result)
        if payload is None:
            return {"status": "completed",
                    "final_state": _sanitise_for_json(result)}
        gate = payload.get("gate") if isinstance(payload, dict) else None
        return {"status": "paused", "gate": gate, "payload": payload}
    except Exception as e:
        raise HTTPException(status_code=500,
                            detail=f"draft/resume failed: {type(e).__name__}: {e}")


@app.get("/draft/status")
def draft_status(thread_id: str) -> dict:
    """Read the current state of a session without resuming. Useful
    for the portal to recover after a page reload."""
    if thread_id not in _SESSIONS:
        raise HTTPException(status_code=404, detail="thread_id not found")
    config = {"configurable": {"thread_id": thread_id}}
    snap = _GRAPH.get_state(config)
    state = snap.values if hasattr(snap, "values") else {}
    next_nodes = list(snap.next) if hasattr(snap, "next") else []
    interrupts = []
    if hasattr(snap, "tasks"):
        for t in snap.tasks:
            for it in (getattr(t, "interrupts", None) or []):
                interrupts.append(getattr(it, "value", None) or it)
    if interrupts:
        payload = interrupts[-1]
        gate = payload.get("gate") if isinstance(payload, dict) else None
        return {
            "status":     "paused",
            "gate":       gate,
            "payload":    payload,
            "next_nodes": next_nodes,
            "state":      _sanitise_for_json(state),
        }
    return {
        "status":     "in_progress" if next_nodes else "completed",
        "next_nodes": next_nodes,
        "state":      _sanitise_for_json(state),
    }


@app.get("/draft/{thread_id}/download")
def draft_download(thread_id: str) -> Any:
    """Download the rendered draft markdown for a session. Returns the
    file (Content-Disposition: attachment) for browsers."""
    if thread_id not in _SESSIONS:
        raise HTTPException(status_code=404, detail="thread_id not found")
    config = {"configurable": {"thread_id": thread_id}}
    snap = _GRAPH.get_state(config)
    state = snap.values if hasattr(snap, "values") else {}
    draft_path = state.get("draft_path") or state.get("final_output_path")
    if not draft_path:
        raise HTTPException(
            status_code=404,
            detail="No draft has been generated yet for this session",
        )
    p = Path(draft_path)
    if not p.exists():
        raise HTTPException(status_code=404,
                            detail=f"Draft file missing on disk: {draft_path}")
    return FileResponse(
        p,
        media_type="text/markdown",
        filename=f"draft_{thread_id}.md",
    )


@app.get("/draft/{thread_id}/preview")
def draft_preview(thread_id: str) -> PlainTextResponse:
    """Inline preview (text/plain rendering of the markdown). Used by
    the portal's Final Review screen."""
    if thread_id not in _SESSIONS:
        raise HTTPException(status_code=404, detail="thread_id not found")
    config = {"configurable": {"thread_id": thread_id}}
    snap = _GRAPH.get_state(config)
    state = snap.values if hasattr(snap, "values") else {}
    draft_md = state.get("draft_markdown") or ""
    if not draft_md:
        draft_path = state.get("draft_path") or ""
        if draft_path and Path(draft_path).exists():
            draft_md = Path(draft_path).read_text(encoding="utf-8")
    return PlainTextResponse(draft_md, media_type="text/markdown")


# ── CLI ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("DRAFTER_API_PORT", "8766"))
    print(f"procureAI Drafter API on http://localhost:{port}")
    print(f"Endpoints:")
    print(f"  GET  /health")
    print(f"  POST /draft/start         (body: text or path)")
    print(f"  POST /draft/start_pdf     (multipart upload)")
    print(f"  POST /draft/resume        (body: thread_id + response)")
    print(f"  GET  /draft/status?thread_id=...")
    print(f"  GET  /draft/{{thread_id}}/download")
    print(f"  GET  /draft/{{thread_id}}/preview")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
