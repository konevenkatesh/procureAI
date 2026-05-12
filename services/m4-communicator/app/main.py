"""
m4-communicator service.

The 11 scripts/m4_drafters/draft_*.py scripts compose tender
communications (award notification, regret letter, clarification Q&A,
…) using Supabase REST + OpenRouter LLM. No Qdrant. They are
runnable in principle from this container.

For this commit we ship the stub worker that reports which drafters
are available. Actual drafter invocation (subprocess or refactor-to-
import) is the follow-up.

Worker contract:
  params: {
    "tender_id":       "<doc_id>",
    "drafter":         "<one of DRAFTERS>" | "all",
    "language":        "EN" | "TE" | "BOTH",
  }
  result: {
    "communications_drafted":  0,
    "drafters_available":      11,
    "language_requested":      "<EN|TE|BOTH>",
    "message":                 "<human-readable>",
  }
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVICES_ROOT = HERE.parent.parent
sys.path.insert(0, str(SERVICES_ROOT.parent))
sys.path.insert(0, str(SERVICES_ROOT))

from _shared import make_app  # noqa: E402


# 11 M4 drafters; matches scripts/m4_drafters/draft_*.py.
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


def worker(job_id: str, params: dict) -> dict:
    return {
        "tender_id":              params.get("tender_id"),
        "drafter_requested":      params.get("drafter") or "all",
        "language_requested":     params.get("language") or "BOTH",
        "drafters_available":     DRAFTERS,
        "communications_drafted": 0,
        "message": (
            "m4-communicator received the request. The 11 M4 drafters "
            "(award/regret/clarification-QA/etc.) are wired in scripts/"
            "m4_drafters/. Hooking them into this worker is the follow-"
            "up commit — the GCP-2 deliverable is the API surface."
        ),
    }


app = make_app(
    module     = "m4",
    worker_fn  = worker,
    title      = "ProcureAI m4-communicator",
)
