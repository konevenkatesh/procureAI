"""
m3-evaluator service.

The 14 bid_*_check.py scripts read kg_nodes (Supabase) + structured
bid data only — no Qdrant dependency. In principle they can run
inside this Cloud Run container. In practice, importing each script
as a module from FastAPI requires either (a) refactoring them to
expose a `run()` function, or (b) shelling out via subprocess.

For this commit we ship the stub worker, which returns the verdict
inventory and a count of bidders that would be evaluated. Wiring the
14 scripts to actually execute is a follow-up commit — the API
surface is the deliverable here.

Worker contract:
  params: {
    "tender_id":       "<doc_id>",
    "checks":          ["blacklist", "turnover", ...] | "all",
  }
  result: {
    "verdict":         "QUALIFIED" | "INELIGIBLE" | "GAP_INSUFFICIENT_DATA" | "SKIP_NOT_APPLICABLE",
    "decision_reason": "<short string>",
    "checks_planned":  14,
    "checks_run":      0,    # stub
    "message":         "<human-readable>",
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


# 14 Tier-2 evaluators (scripts/bid_*_check.py); enumerated for
# transparency in stub output so the frontend can render the list.
BID_CHECKS = [
    "abc", "blacklist", "class", "compliance_documents",
    "emd_validity", "equipment", "financial_turnover",
    "jv_consortium", "litigation", "personnel",
    "similar_works", "solvency", "turnover", "bg_validity",
]


def worker(job_id: str, params: dict) -> dict:
    requested = params.get("checks") or "all"
    return {
        "verdict":           "GAP_INSUFFICIENT_DATA",
        "decision_reason":   "evaluator_wiring_phase2",
        "tender_id":         params.get("tender_id"),
        "checks_available":  BID_CHECKS,
        "checks_planned":    len(BID_CHECKS),
        "checks_run":        0,
        "message": (
            "m3-evaluator received the request. The 14 Tier-2 bid "
            "evaluators (Supabase-only, no Qdrant) are ready to be "
            "wired in — the API surface here is the GCP-2 deliverable. "
            "Actual evaluator execution lands in a follow-up commit."
        ),
    }


app = make_app(
    module     = "m3",
    worker_fn  = worker,
    title      = "ProcureAI m3-evaluator",
)
