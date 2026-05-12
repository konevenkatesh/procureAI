"""
m1-drafter service — real LangGraph workflow + 4-gate state machine.

Replaces the R3 stub. Endpoints (in addition to the standard /health,
/m1/run, /worker, /jobs/{id} from services._shared):

  POST   /m1/run                          — Dealing Officer initiates draft (worker creates TenderDraft + runs workflow)
  GET    /m1/draft/{draft_id}             — current TenderDraftState
  GET    /m1/draft/{draft_id}/stream      — SSE event stream of the live workflow
  GET    /m1/draft/{draft_id}/versions    — list of DraftVersionSnapshot rows
  GET    /m1/draft/{draft_id}/audit       — list of GateTransition rows
  POST   /m1/draft/{draft_id}/approve     — gate approve (role-checked)
  POST   /m1/draft/{draft_id}/revise      — gate revise → INITIATION (comments required)
  POST   /m1/draft/{draft_id}/publish     — AUTHORITY publish (assigns tender_id)
  POST   /m1/draft/{draft_id}/sendback    — AUTHORITY sendback to prior gate
  GET    /m1/draft/{draft_id}/artifacts   — list rendered artifact paths (post-publish)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import sys
import threading
import time
from pathlib import Path
from typing import AsyncIterator

# Make `services._shared` importable when this file runs as the Cloud Run
# entrypoint (`python -m uvicorn app.main:app`).
HERE = Path(__file__).resolve().parent
SERVICES_ROOT = HERE.parent.parent          # services/
sys.path.insert(0, str(SERVICES_ROOT.parent))  # repo root
sys.path.insert(0, str(SERVICES_ROOT))         # services/ (for _shared)

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from _shared import make_app  # noqa: E402

from .gates import (
    GateError,
    approve as gate_approve,
    publish as gate_publish,
    revise as gate_revise,
    sendback as gate_sendback,
)
from .langgraph_workflow import run_workflow
from .persistence import (
    delete_draft_completely,
    insert_version_snapshot,
    list_gate_transitions,
    list_version_snapshots,
    load_tender_draft,
    upsert_tender_draft,
)
from .schemas import (
    DraftVersionSnapshotProps,
    GateName,
    GateTransitionEdit,
    InitialPayload,
    M1GateActionRequest,
    M1RunParams,
    RoleName,
    TenderDraftState,
    now_iso,
)


logger = logging.getLogger(__name__)


# ─── In-process SSE event bus (per-draft) ────────────────────────────
# Cloud Run revisions are stateless but SSE consumers reconnect; we
# keep an in-memory ring buffer per draft_id so a reconnecting client
# can replay the last N events. For local dev, this is sufficient.

_event_buffers: dict[str, list[dict]] = {}
_event_locks: dict[str, threading.Lock] = {}


def _publish_event(draft_id: str, event: dict) -> None:
    lock = _event_locks.setdefault(draft_id, threading.Lock())
    with lock:
        buf = _event_buffers.setdefault(draft_id, [])
        buf.append(event)
        # Cap at 1000 events per draft
        if len(buf) > 1000:
            buf.pop(0)


def _get_events_since(draft_id: str, since_index: int) -> list[dict]:
    lock = _event_locks.setdefault(draft_id, threading.Lock())
    with lock:
        buf = _event_buffers.get(draft_id, [])
        if since_index >= len(buf):
            return []
        return buf[since_index:]


# ─── Worker fn (Cloud Tasks callback) ────────────────────────────────


def worker(job_id: str, params: dict) -> dict:
    """Handle a /m1/run job dispatched via Cloud Tasks.

    Flow:
      1. Parse params → InitialPayload + draft_id
      2. Build TenderDraftState (gate=AI_GENERATION)
      3. UPSERT TenderDraft kg_node
      4. Snapshot v1 (post-form, pre-AI)
      5. Run LangGraph workflow → emits SSE events into in-memory buffer
      6. After workflow: bump gate to TECHNICAL, version++, snapshot v2
      7. UPSERT final TenderDraft kg_node

    Returns {draft_id, current_gate, version, n_events_emitted}.
    """
    try:
        m1_params = M1RunParams.model_validate(params)
    except Exception as e:
        raise ValueError(f"invalid m1 params: {e}")

    draft_id = m1_params.draft_id or f"m1_draft_{job_id[:18]}"
    ip = m1_params.initial_payload

    ts = now_iso()
    state = TenderDraftState(
        draft_id=draft_id,
        enquiry_particulars=ip.enquiry_particulars,
        classification=ip.classification,
        financial=ip.financial,
        geography=ip.geography,
        evaluation=ip.evaluation,
        documents=ip.documents,
        dates=ip.dates,
        enquiry_forms=ip.enquiry_forms,
        current_gate=GateName.AI_GENERATION,
        current_assignee_role=None,
        version=1,
        created_by=f"{m1_params.initiator_role}:{m1_params.initiator_id}",
        created_at=ts,
        last_updated_at=ts,
    )

    # 1) Persist initial TenderDraft + v1 snapshot
    upsert_tender_draft(state)
    insert_version_snapshot(DraftVersionSnapshotProps(
        snapshot_id=f"{draft_id}_v1",
        draft_id=draft_id,
        version=1,
        payload=state,
        created_by_role=RoleName.DEALING_OFFICER,
        created_at=ts,
    ))

    # 2) Run workflow (synchronously inside the worker) — emits SSE events
    event_count = 0
    for event in run_workflow(state):
        _publish_event(draft_id, event)
        event_count += 1

    # 3) Workflow complete: transition to TECHNICAL gate, version 2, snapshot
    state.current_gate = GateName.TECHNICAL
    state.current_assignee_role = RoleName.SENIOR_ENGINEER
    state.version = 2
    state.last_updated_at = now_iso()
    upsert_tender_draft(state)
    insert_version_snapshot(DraftVersionSnapshotProps(
        snapshot_id=f"{draft_id}_v2",
        draft_id=draft_id,
        version=2,
        payload=state,
        created_by_role=RoleName.DEALING_OFFICER,
        created_at=state.last_updated_at,
    ))

    return {
        "draft_id": draft_id,
        "current_gate": state.current_gate.value,
        "version": state.version,
        "n_events_emitted": event_count,
        "stream_url": f"/m1/draft/{draft_id}/stream",
    }


# ─── App + custom routes ─────────────────────────────────────────────


app = make_app(
    module="m1",
    worker_fn=worker,
    title="ProcureAI m1-drafter",
)


# Override the /m1/run behaviour to allow returning draft_id directly?
# Keep the existing /m1/run (jobs flow); add convenience GETs + gate actions.


@app.get("/m1/draft/{draft_id}")
def get_draft(draft_id: str) -> dict:
    state = load_tender_draft(draft_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"draft {draft_id} not found")
    return state.model_dump(mode="json")


@app.get("/m1/draft/{draft_id}/stream")
async def stream_draft(draft_id: str) -> StreamingResponse:
    """Server-Sent Events stream of workflow events for this draft.

    The stream replays buffered events from index 0, then long-polls
    for new events as they arrive. Closes when workflow_complete OR
    after 5 minutes of inactivity.
    """

    async def event_generator() -> AsyncIterator[str]:
        cursor = 0
        last_activity = time.time()
        while True:
            new_events = _get_events_since(draft_id, cursor)
            if new_events:
                last_activity = time.time()
                for ev in new_events:
                    payload = json.dumps(ev)
                    yield f"data: {payload}\n\n"
                    if ev.get("type") == "workflow_complete":
                        return
                cursor += len(new_events)
            else:
                # No new events; check timeout
                if time.time() - last_activity > 300:
                    yield "data: {\"type\": \"error\", \"node\": \"system\", \"message\": \"stream timeout\"}\n\n"
                    return
                await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable nginx buffering
        },
    )


@app.get("/m1/draft/{draft_id}/versions")
def get_versions(draft_id: str) -> dict:
    snapshots = list_version_snapshots(draft_id)
    return {"draft_id": draft_id, "versions": snapshots}


@app.get("/m1/draft/{draft_id}/audit")
def get_audit_trail(draft_id: str) -> dict:
    transitions = list_gate_transitions(draft_id)
    return {"draft_id": draft_id, "transitions": transitions}


# ─── Gate action endpoints ───────────────────────────────────────────


def _gate_response(state: TenderDraftState) -> dict:
    return {
        "draft_id": state.draft_id,
        "current_gate": state.current_gate.value,
        "current_assignee_role": (
            state.current_assignee_role.value if state.current_assignee_role else None
        ),
        "version": state.version,
        "tender_id": state.tender_id,
        "last_updated_at": state.last_updated_at,
    }


@app.post("/m1/draft/{draft_id}/approve")
def post_approve(draft_id: str, req: M1GateActionRequest) -> dict:
    try:
        state = gate_approve(
            draft_id=draft_id,
            actor_role=req.actor_role,
            actor_id=req.actor_id,
            comments=req.comments or "",
            edits=req.edits or [],
        )
    except GateError as e:
        raise HTTPException(status_code=e.http_status, detail=str(e))
    return _gate_response(state)


@app.post("/m1/draft/{draft_id}/revise")
def post_revise(draft_id: str, req: M1GateActionRequest) -> dict:
    try:
        state = gate_revise(
            draft_id=draft_id,
            actor_role=req.actor_role,
            actor_id=req.actor_id,
            comments=req.comments or "",
        )
    except GateError as e:
        raise HTTPException(status_code=e.http_status, detail=str(e))
    return _gate_response(state)


@app.post("/m1/draft/{draft_id}/publish")
def post_publish(draft_id: str, req: M1GateActionRequest) -> dict:
    try:
        state = gate_publish(
            draft_id=draft_id,
            actor_role=req.actor_role,
            actor_id=req.actor_id,
            comments=req.comments or "",
        )
    except GateError as e:
        raise HTTPException(status_code=e.http_status, detail=str(e))
    # Trigger artifact rendering (M1.7)
    try:
        from .renderers import render_all_for_publish
        artifacts = render_all_for_publish(state)
    except Exception as e:
        logger.warning(f"renderers failed for {draft_id}: {e}")
        artifacts = {"error": str(e)}
    resp = _gate_response(state)
    resp["artifacts"] = artifacts
    return resp


@app.post("/m1/draft/{draft_id}/sendback")
def post_sendback(draft_id: str, req: M1GateActionRequest) -> dict:
    if not req.send_back_to:
        raise HTTPException(status_code=400, detail="send_back_to required for SENDBACK")
    try:
        state = gate_sendback(
            draft_id=draft_id,
            actor_role=req.actor_role,
            actor_id=req.actor_id,
            target_gate=req.send_back_to,
            comments=req.comments or "",
        )
    except GateError as e:
        raise HTTPException(status_code=e.http_status, detail=str(e))
    return _gate_response(state)


@app.get("/m1/draft/{draft_id}/artifacts")
def get_artifacts(draft_id: str) -> dict:
    """List artifact paths (DOCX/PDF/XLSX/MD). Only available post-publish."""
    state = load_tender_draft(draft_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"draft {draft_id} not found")
    if state.current_gate != GateName.PUBLISHED:
        return {"draft_id": draft_id, "artifacts": [], "note": "artifacts available only after publish"}
    artifact_dir = Path(f"/tmp/m1_artifacts/{draft_id}/v{state.version}")
    if not artifact_dir.exists():
        return {"draft_id": draft_id, "artifacts": [], "note": "artifacts not yet rendered"}
    files = []
    for p in sorted(artifact_dir.iterdir()):
        files.append({
            "filename": p.name,
            "path": str(p),
            "size_bytes": p.stat().st_size,
        })
    return {"draft_id": draft_id, "artifacts": files}


@app.delete("/m1/draft/{draft_id}")
def delete_draft(draft_id: str) -> dict:
    """Test/cleanup endpoint — removes draft + audit trail + snapshots.

    Useful for local smoke-test re-runs. Production may restrict this
    via RBAC; for now it's open.
    """
    count = delete_draft_completely(draft_id)
    return {"draft_id": draft_id, "deleted_rows": count}
