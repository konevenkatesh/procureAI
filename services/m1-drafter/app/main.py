"""
m1-drafter service.

Stub-mode for Phase 1: the worker returns a deterministic "draft
queued" payload tagged with the input parameters. The actual
LangGraph drafter pipeline (api/drafter_api.py) requires a stateful
checkpointer that doesn't fit a stateless Cloud Run revision —
threading it through Supabase/Memorystore is its own commit and is
explicitly out of scope per the workflow's NOT IN SCOPE list:
  "Module 1 (Drafter) actual implementation (stub only; full build
   deferred)".

Worker contract:
  params: {
    "sector":           "Works" | "Goods" | "Services" | "PPP",
    "ecv":              <int INR>,
    "location":         "<district>",
    "contractor_class": "<class A/B/C/...>",
  }
  result: {
    "draft_status":      "queued_phase2",
    "received_params":   <echo>,
    "message":           "<human-readable>",
  }
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make `services._shared` importable when this file runs as the
# Cloud Run entrypoint (`python -m uvicorn app.main:app`).
HERE = Path(__file__).resolve().parent
SERVICES_ROOT = HERE.parent.parent          # services/
sys.path.insert(0, str(SERVICES_ROOT.parent))  # repo root
sys.path.insert(0, str(SERVICES_ROOT))         # services/ (for _shared)

from _shared import make_app  # noqa: E402


def worker(job_id: str, params: dict) -> dict:
    return {
        "draft_status":    "queued_phase2",
        "received_params": params,
        "message": (
            "m1-drafter received the request. The full LangGraph "
            "drafter pipeline (3 human-in-the-loop gates) is deferred "
            "to Phase 2 — see NOT IN SCOPE in the GCP-2 directive."
        ),
    }


app = make_app(
    module     = "m1",
    worker_fn  = worker,
    title      = "ProcureAI m1-drafter",
)
