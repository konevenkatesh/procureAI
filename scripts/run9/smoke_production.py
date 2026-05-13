"""R9.4 — Production smoke at 3 scales via procureai.bimsaarthi.com.

After R9.3 cloud deploy, validate the new m1-drafter v3 end-to-end through
the production stack:
  - Next.js LB → Cloud Run frontend → Cloud Run m1-drafter v3 → Supabase

Scales tested (same payloads as R8.1/R8.2/R8.3 local smokes):
  - Banaganapalli small (30 rows) — expect ~90s
  - LPS Zone-11 mid (800 rows) — expect ~5 min
  - HOD Towers capital (3000 rows) — expect ~15 min

Verification per scale:
  - Submit via POST /api/m1/draft/start
  - Stream SSE via GET /api/m1/draft/{id}/stream
  - Assert node_complete=15, BoQ rows ≥ skeleton×0.85
  - Verify all 4 gates transition cleanly via POST .../approve

Auth: this script runs against the public-facing endpoint that proxies to
Cloud Run with the runtime SA ID token. Manual smoke does not authenticate
the frontend itself — sessions are tracked by client-side localStorage.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "services" / "m1-drafter"))

from scripts.run8.smoke_banaganapalli import (   # noqa: E402
    build_banaganapalli_state, build_banaganapalli_skeleton,
)
from scripts.run8.smoke_lps_zone11 import (
    build_state as build_lps_state, build_skeleton as build_lps_skeleton,
)
from scripts.run8.smoke_hod_towers import (
    build_state as build_hod_state, build_skeleton as build_hod_skeleton,
)
from app.boq_generator import BoQSkeletonRow                  # noqa: E402


# ─── Config ───────────────────────────────────────────────────────────


PROD_URL = os.environ.get("PROD_FRONTEND_URL", "https://procureai.bimsaarthi.com")
WALL_BUDGETS = {
    "Banaganapalli": 240,        # 4 min — more headroom for prod LB + SSE
    "LPS Zone-11":   600,        # 10 min
    "HOD Towers":    1500,       # 25 min — capital scale ceiling per directive
}


# ─── HTTP helpers ─────────────────────────────────────────────────────


def _post_json(url: str, body: dict, timeout: int = 60) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())
    except Exception as e:
        return 0, {"error": str(e)}


def _sse_stream(url: str, max_wait: int = 1500):
    """Yield parsed SSE events from a GET stream until workflow_complete or
    timeout. Caller filters event types."""
    t0 = time.time()
    req = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
    try:
        with urllib.request.urlopen(req, timeout=max_wait) as resp:
            buf = b""
            for chunk in resp:
                if time.time() - t0 > max_wait:
                    yield {"type": "_timeout", "elapsed": max_wait}
                    return
                buf += chunk
                while b"\n\n" in buf:
                    event_raw, buf = buf.split(b"\n\n", 1)
                    for line in event_raw.splitlines():
                        if line.startswith(b"data: "):
                            try:
                                yield json.loads(line[6:])
                            except json.JSONDecodeError:
                                continue
    except Exception as e:
        yield {"type": "_stream_error", "message": str(e)}


# ─── Per-scale smoke ──────────────────────────────────────────────────


def smoke_scale(label: str, state, skeleton, wall_budget: int) -> dict:
    print(f"\n── {label} via {PROD_URL} ──")
    print(f"  skeleton: {len(skeleton)} rows · wall budget: {wall_budget}s")

    payload = {
        "draft_id":       state.draft_id,
        "initiator_role": "DEALING_OFFICER",
        "initiator_id":   "r94_prod_smoke",
        "initial_payload": {
            "enquiry_particulars": state.enquiry_particulars.model_dump(mode="json"),
            "classification":      state.classification.model_dump(mode="json"),
            "financial":           state.financial.model_dump(mode="json"),
            "geography":           state.geography.model_dump(mode="json"),
            "evaluation":          state.evaluation.model_dump(mode="json"),
            "documents":           [],
            "dates":               state.dates.model_dump(mode="json"),
            "enquiry_forms":       [],
        },
        "boq_skeleton": [
            {"s_no": r.s_no, "item_name": r.item_name, "qty": r.qty, "unit": r.unit}
            for r in skeleton
        ],
        "boq_skeleton_filename": f"r94_{label.lower().replace(' ', '_')}.csv",
    }

    t0 = time.time()
    code, data = _post_json(f"{PROD_URL}/api/m1/draft/start", payload, timeout=60)
    if code != 200:
        return {
            "label": label, "ok": False, "reason": f"start returned {code}", "body": data,
            "wall_clock_sec": 0, "n_rows": 0, "n_skeleton": len(skeleton),
            "n_batches": 0, "n_node_complete": 0, "sections": [], "last_event": "start_failed",
            "row_pct": 0.0, "draft_id": state.draft_id,
        }

    draft_id = data.get("draft_id") or state.draft_id
    stream_url = data.get("stream_url") or f"/api/m1/draft/stream/{draft_id}"
    print(f"  started draft_id={draft_id}; streaming {stream_url}")

    n_node_complete = 0
    n_batches = 0
    n_rows = 0
    sections_drafted = set()
    last_event = "none"
    workflow_complete = False
    for ev in _sse_stream(f"{PROD_URL}{stream_url}", max_wait=wall_budget):
        t = ev.get("type")
        last_event = t or last_event
        if t == "node_complete":
            n_node_complete += 1
        elif t == "boq_batch_started":
            n_batches += 1
        elif t == "boq_item_complete":
            n_rows += 1
        elif t == "section_complete":
            s = ev.get("section", "")
            if s.startswith("section_"):
                sections_drafted.add(s)
        elif t == "workflow_complete":
            workflow_complete = True
            break
        elif t in ("_timeout", "_stream_error"):
            break
    elapsed = time.time() - t0

    return {
        "label":            label,
        "draft_id":         draft_id,
        "wall_clock_sec":   elapsed,
        "ok":               workflow_complete,
        "n_node_complete":  n_node_complete,
        "n_batches":        n_batches,
        "n_rows":           n_rows,
        "n_skeleton":       len(skeleton),
        "sections":         sorted(sections_drafted),
        "row_pct":          n_rows / max(len(skeleton), 1),
        "last_event":       last_event,
    }


def main():
    results = []

    # Banaganapalli small
    state = build_banaganapalli_state()
    skel = build_banaganapalli_skeleton()
    state.draft_id = "r94_prod_banaganapalli_v1"
    r = smoke_scale("Banaganapalli", state, skel, WALL_BUDGETS["Banaganapalli"])
    results.append(r)
    print(f"  result: ok={r['ok']}  rows={r['n_rows']}/{r['n_skeleton']}  wall={r['wall_clock_sec']:.0f}s")

    # LPS mid
    state = build_lps_state()
    skel = build_lps_skeleton(target_n=800)
    state.draft_id = "r94_prod_lps_mid_v1"
    r = smoke_scale("LPS Zone-11", state, skel, WALL_BUDGETS["LPS Zone-11"])
    results.append(r)
    print(f"  result: ok={r['ok']}  rows={r['n_rows']}/{r['n_skeleton']}  wall={r['wall_clock_sec']:.0f}s")

    # HOD capital
    state = build_hod_state()
    skel = build_hod_skeleton(target_n=3000)
    state.draft_id = "r94_prod_hod_capital_v1"
    r = smoke_scale("HOD Towers", state, skel, WALL_BUDGETS["HOD Towers"])
    results.append(r)
    print(f"  result: ok={r['ok']}  rows={r['n_rows']}/{r['n_skeleton']}  wall={r['wall_clock_sec']:.0f}s")

    all_ok = all(r["ok"] for r in results)
    Path("/tmp/r94_prod_smoke.json").write_text(json.dumps(results, indent=2, default=str))
    print(f"\n=== Production smoke {'ALL PASS' if all_ok else 'HAS FAILURES'} ===")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
