"""
Shared FastAPI app factory for the 4 ProcureAI services.

Each service module imports `make_app(MODULE_NAME, worker_fn)` and
gets a ready FastAPI instance with the standard 4-endpoint contract:

    GET  /health           — liveness + Supabase ping
    POST /<module>/run     — accept job, persist, enqueue, return 202
    POST /worker           — Cloud Tasks callback; runs `worker_fn`
    GET  /jobs/{job_id}    — current status from Supabase

The worker signature is:
    def worker(job_id: str, params: dict) -> dict | None
Returning a dict marks the job DONE with that as `result`.
Raising marks it ERROR with the exception message.

CORS is wide-open for the demo; tighten to Cloud Run frontend origin
in a follow-up commit if needed.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import jobs

logger = logging.getLogger(__name__)


class RunRequest(BaseModel):
    tender_id: str | None = None
    params: dict = {}


WorkerFn = Callable[[str, dict], Any]


def make_app(
    module: str,
    worker_fn: WorkerFn,
    *,
    title: str | None = None,
) -> FastAPI:
    """Build the FastAPI app for a service.

    `module` is one of {"m1","m2","m3","m4"}; the /run path is
    `/<module>/run` so the frontend can keep distinct routes.
    """
    app = FastAPI(
        title=title or f"ProcureAI {module.upper()}",
        version="0.1.0",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],   # FE on Cloud Run will set its own list
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict:
        return {
            "ok":            True,
            "service":       module,
            "supabase_reachable": jobs.supabase_ping(),
            "tasks_queue":   os.environ.get("TASKS_QUEUE") or None,
            "service_url":   os.environ.get("SERVICE_URL") or None,
        }

    run_path = f"/{module}/run"

    @app.post(run_path)
    def run(req: RunRequest) -> dict:
        try:
            job_id = jobs.create_job(
                module    = module,
                tender_id = req.tender_id,
                params    = req.params or {},
            )
        except Exception as e:
            logger.exception("create_job failed")
            raise HTTPException(
                status_code=503,
                detail=f"job-store unavailable: {e}",
            )

        # Enqueue; fall back to inline execution if Cloud Tasks isn't
        # configured (local dev or first boot before SERVICE_URL is set).
        # tender_id is merged into params so workers receive everything
        # the original POST body carried (the top-level RunRequest splits
        # tender_id out for Job persistence, but workers want it inline).
        merged_params = {**(req.params or {}), "tender_id": req.tender_id} \
            if req.tender_id else (req.params or {})
        try:
            jobs.enqueue_task(
                job_id  = job_id,
                payload = {"job_id": job_id, "params": merged_params},
            )
            return {
                "job_id":   job_id,
                "status":   "QUEUED",
                "poll_url": f"/jobs/{job_id}",
            }
        except RuntimeError as e:
            if str(e) != "cloud_tasks_unavailable":
                raise
            # Inline fallback (synchronous) — keeps local dev usable.
            logger.warning(
                "running worker inline for job %s (no Cloud Tasks)", job_id,
            )
            try:
                jobs.update_job_status(job_id, status="RUNNING")
                result = worker_fn(job_id, merged_params)
                jobs.update_job_status(
                    job_id, status="DONE", result=result,
                )
            except Exception as ex:
                logger.exception("inline worker failed for %s", job_id)
                jobs.update_job_status(
                    job_id, status="ERROR", error=str(ex),
                )
            return {
                "job_id":   job_id,
                "status":   "COMPLETED_INLINE",
                "poll_url": f"/jobs/{job_id}",
            }

    @app.post("/worker")
    async def worker(req: Request) -> dict:
        """Cloud Tasks callback. Body is {job_id, params}."""
        try:
            body = await req.json()
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="invalid json")
        job_id = body.get("job_id")
        params = body.get("params") or {}
        if not job_id:
            raise HTTPException(status_code=400, detail="missing job_id")

        try:
            jobs.update_job_status(job_id, status="RUNNING")
            result = worker_fn(job_id, params)
            jobs.update_job_status(
                job_id, status="DONE", result=result,
            )
            return {"ok": True, "job_id": job_id}
        except Exception as ex:
            logger.exception("worker failed for %s", job_id)
            jobs.update_job_status(
                job_id, status="ERROR", error=str(ex),
            )
            return {"ok": False, "job_id": job_id, "error": str(ex)}

    @app.get("/jobs/{job_id}")
    def job_status(job_id: str) -> dict:
        row = jobs.get_job(job_id)
        if not row:
            raise HTTPException(status_code=404, detail="job not found")
        props = row.get("properties") or {}
        return {
            "job_id":      row.get("node_id"),
            "module":      props.get("module"),
            "status":      props.get("status"),
            "tender_id":   props.get("tender_id"),
            "queued_at":   props.get("queued_at"),
            "started_at":  props.get("started_at"),
            "finished_at": props.get("finished_at"),
            "result":      props.get("result"),
            "error":       props.get("error"),
        }

    return app
