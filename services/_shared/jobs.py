"""
Shared Job-state + Cloud Tasks dispatcher for the 4 ProcureAI services.

Job state contract:
  Stored as kg_nodes rows with node_type='Job'. The properties JSONB
  carries everything the worker needs and everything the frontend polls:

    properties = {
      "module":      "m1" | "m2" | "m3" | "m4",
      "status":      "QUEUED" | "RUNNING" | "DONE" | "ERROR",
      "tender_id":   "<doc_id or null>",
      "params":      {<request body>},
      "result":      <module-specific result | None>,
      "error":       "<message | None>",
      "queued_at":   "<ISO 8601 UTC>",
      "started_at":  "<ISO 8601 UTC | null>",
      "finished_at": "<ISO 8601 UTC | null>",
    }

  Why kg_nodes and not a new table:
    - Preserves the platform's "everything is a kg_node" pattern.
    - No Alembic migration required → keeps the sentinel
      (ValidationFinding 154 / Communication 75 / etc.) untouched.
    - node_type='Job' is additive — none of the existing typology
      queries (Tier-1, Tier-2, Aggregators) match it, so they keep
      returning the same row counts.

  doc_id rule: we set doc_id = the tender_id supplied by the caller
  when present, otherwise doc_id = '__job__' (sentinel for cross-
  tender jobs). This keeps the existing FK / RLS posture consistent.

Cloud Tasks dispatch contract:
  Each service self-dispatches via Cloud Tasks → its own `/worker`
  endpoint. The task carries an OIDC token signed by the runtime SA,
  and Cloud Run requires `roles/run.invoker` on the runtime SA for
  the target service (granted at deploy time in GCP-2.6).

  Worker URL is taken from env `SERVICE_URL` (set at deploy time to
  the service's own `*.run.app` URL).

  If `TASKS_QUEUE` env is missing (e.g. local dev) we fall back to
  running the worker inline so the API stays usable without GCP.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import requests

logger = logging.getLogger(__name__)


# ── Env-driven config (read once at process start) ────────────────────
SUPABASE_REST_URL = os.environ.get("SUPABASE_REST_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

# Prefer service role (bypasses RLS) for backend writes; fall back to anon.
_AUTH_KEY = SUPABASE_SERVICE_ROLE_KEY or SUPABASE_ANON_KEY
_HEADERS = {
    "apikey": _AUTH_KEY,
    "Authorization": f"Bearer {_AUTH_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

TASKS_PROJECT  = os.environ.get("TASKS_PROJECT", "procureai-prod")
TASKS_LOCATION = os.environ.get("TASKS_LOCATION", "asia-south1")
TASKS_QUEUE    = os.environ.get("TASKS_QUEUE", "procure-ai-jobs")
SERVICE_URL    = os.environ.get("SERVICE_URL", "")
RUNTIME_SA     = os.environ.get(
    "RUNTIME_SA",
    "procure-ai-runtime@procureai-prod.iam.gserviceaccount.com",
)

# Sentinel doc_id when a job isn't tied to a specific tender.
JOB_DOC_ID = "__job__"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── Supabase REST helpers (3-try with backoff) ────────────────────────
def _req(method: str, path: str, **kw: Any) -> requests.Response:
    if not SUPABASE_REST_URL:
        raise RuntimeError(
            "SUPABASE_REST_URL env not set — Supabase REST disabled."
        )
    url = f"{SUPABASE_REST_URL}/rest/v1/{path.lstrip('/')}"
    last: Exception | None = None
    for attempt in range(3):
        try:
            r = requests.request(
                method, url,
                headers=_HEADERS,
                timeout=30,
                **kw,
            )
            r.raise_for_status()
            return r
        except requests.RequestException as exc:
            last = exc
            time.sleep(0.5 * (2 ** attempt))
    assert last is not None
    raise last


# ── Public API ────────────────────────────────────────────────────────
def create_job(
    *,
    module: str,
    tender_id: str | None,
    params: dict,
) -> str:
    """Insert a QUEUED Job kg_node. Returns job_id."""
    job_id = uuid.uuid4().hex
    doc_id = tender_id or JOB_DOC_ID
    row = {
        "node_id":   job_id,
        "doc_id":    doc_id,
        "node_type": "Job",
        "properties": {
            "module":      module,
            "status":      "QUEUED",
            "tender_id":   tender_id,
            "params":      params,
            "result":      None,
            "error":       None,
            "queued_at":   _now_iso(),
            "started_at":  None,
            "finished_at": None,
        },
    }
    try:
        _req("POST", "kg_nodes", data=json.dumps(row))
    except Exception as e:
        logger.error(f"create_job INSERT failed for {job_id}: {e}")
        raise
    return job_id


def _patch_job(job_id: str, props_patch: dict) -> None:
    """PATCH the properties JSONB of an existing Job row."""
    current = get_job(job_id) or {}
    merged = {**(current.get("properties") or {}), **props_patch}
    _req(
        "PATCH",
        f"kg_nodes?node_id=eq.{job_id}",
        data=json.dumps({"properties": merged}),
    )


def update_job_status(
    job_id: str,
    *,
    status: str,
    result: Any = None,
    error: str | None = None,
) -> None:
    """Mark RUNNING / DONE / ERROR with timestamps."""
    patch: dict = {"status": status}
    now = _now_iso()
    if status == "RUNNING":
        patch["started_at"] = now
    elif status in ("DONE", "ERROR"):
        patch["finished_at"] = now
    if result is not None:
        patch["result"] = result
    if error is not None:
        patch["error"] = error
    _patch_job(job_id, patch)


def get_job(job_id: str) -> dict | None:
    """Fetch a Job row by node_id, or None if not found."""
    try:
        r = _req(
            "GET",
            f"kg_nodes?node_id=eq.{job_id}&select=node_id,doc_id,node_type,properties",
        )
        rows = r.json()
        return rows[0] if rows else None
    except Exception as e:
        logger.error(f"get_job lookup failed for {job_id}: {e}")
        return None


def enqueue_task(
    *,
    job_id: str,
    payload: dict,
    worker_path: str = "/worker",
) -> None:
    """Enqueue a Cloud Task that hits this service's /worker URL.

    If TASKS_QUEUE/SERVICE_URL are not configured, fall back to
    invoking the worker in-process synchronously — that way local
    `uvicorn` runs of the service keep working without GCP.
    """
    if not SERVICE_URL or not TASKS_QUEUE:
        logger.warning(
            "TASKS_QUEUE or SERVICE_URL missing — falling back to "
            "in-process worker execution (no Cloud Tasks)."
        )
        # The caller (router) decides what to do; raise so the
        # router can branch on this. We don't want a silent no-op.
        raise RuntimeError("cloud_tasks_unavailable")

    # Lazy import — keeps cold-start fast for local dev too.
    from google.cloud import tasks_v2  # type: ignore

    client = tasks_v2.CloudTasksClient()
    parent = client.queue_path(TASKS_PROJECT, TASKS_LOCATION, TASKS_QUEUE)
    url = f"{SERVICE_URL.rstrip('/')}{worker_path}"

    task = {
        "http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "url":         url,
            "headers":     {"Content-Type": "application/json"},
            "body":        json.dumps(payload).encode("utf-8"),
            "oidc_token":  {
                "service_account_email": RUNTIME_SA,
                "audience":              SERVICE_URL,
            },
        },
        "name": (
            f"{parent}/tasks/{job_id}"
        ),
    }
    client.create_task(parent=parent, task=task)


# ── Health-check probe ────────────────────────────────────────────────
def supabase_ping() -> bool:
    """Cheap reachability check used by /health."""
    if not SUPABASE_REST_URL:
        return False
    try:
        r = requests.get(
            f"{SUPABASE_REST_URL}/rest/v1/",
            headers={"apikey": _AUTH_KEY},
            timeout=5,
        )
        return r.status_code in (200, 401)  # 401 means reachable, just unauth
    except Exception:
        return False
