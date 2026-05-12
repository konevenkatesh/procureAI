"""
m2-validator service.

Stub-mode for Phase 1: all 24 tier1 scripts in scripts/tier1_*_check.py
import Qdrant and BGE-M3 (`sentence-transformers`). Qdrant is not
deployed to GCP in this migration (Phase 2 scope), so the worker
cannot actually run the retrieval-augmented validators here.

The worker returns a GAP_INSUFFICIENT_DATA verdict consistent with
the L35/L61 three/four-state contract used elsewhere in the platform.
This way the frontend can wire the "Validate RFP" button against a
real service that produces a real verdict shape — the verdict is
just "Phase 2 pending" until Qdrant migrates.

Worker contract:
  params: {
    "tender_id":     "<doc_id>",
    "checks":        ["blacklist", "pvc", ...] | "all",
  }
  result: {
    "verdict":           "GAP_INSUFFICIENT_DATA",
    "decision_reason":   "qdrant_not_deployed_phase2",
    "checks_requested":  ["..."],
    "checks_run":        0,
    "message":           "<human-readable>",
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


def worker(job_id: str, params: dict) -> dict:
    requested = params.get("checks") or "all"
    return {
        "verdict":           "GAP_INSUFFICIENT_DATA",
        "decision_reason":   "qdrant_not_deployed_phase2",
        "checks_requested":  requested,
        "checks_run":        0,
        "tier1_available":   24,   # scripts/tier1_*_check.py count
        "message": (
            "m2-validator received the request. The 24 Tier-1 typology "
            "validators depend on Qdrant (BGE-M3 retrieval) which is "
            "not yet deployed to GCP. Migrating Qdrant to GCP is Phase "
            "2 — see NOT IN SCOPE in the GCP-2 directive."
        ),
    }


app = make_app(
    module     = "m2",
    worker_fn  = worker,
    title      = "ProcureAI m2-validator",
)
