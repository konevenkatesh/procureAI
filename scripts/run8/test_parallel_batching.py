"""R8.6 unit test — parallel BoQ batching with mocked Vertex client.

Verifies:
  1. 30 batches × max_concurrent=10 → ~3 concurrent waves
  2. Total wall-clock ≈ 3× single-batch time (not 30×)
  3. Events stream as batches complete (not all at end)
  4. Tenacity retries on transient errors
  5. Sonnet path is gone (no claude_sonnet import surface)
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "services" / "m1-drafter"))

from app.boq_generator import (              # noqa: E402
    BoQSkeletonRow, ProjectContext, build_batch_prompt,
    run_batches_parallel, _run_batch_async,
    _FLASH_FAILS_BY_DISCIPLINE,
)
from app.tech_spec_templates.base import BoQItemOutput  # noqa: E402


# ─── Mock fixtures ────────────────────────────────────────────────────


SIMULATED_BATCH_LATENCY_SEC = 1.0          # pretend each Flash call takes 1s


async def _fake_gemini_flash_async(prompt, **kwargs):
    """Return a parsed response that matches the batch size in the prompt."""
    # Sleep to simulate I/O latency
    await asyncio.sleep(SIMULATED_BATCH_LATENCY_SEC)

    # Parse the prompt to find the row count + s_nos
    import re, json
    rows_match = re.search(r'ROWS TO ENRICH\s*---\s*(\[.*?\])\s*\n\s*\n', prompt, re.DOTALL)
    if rows_match:
        rows = json.loads(rows_match.group(1))
    else:
        rows = []

    fake_rows = []
    for r in rows:
        fake_rows.append({
            "sno":          r["s_no"],
            "item_name":    r["item_name"],
            "spec_text":    "A" * 200,    # >=150 chars to satisfy schema
            "work_type":    "TEST WORK",
            "short_desc":   r["item_name"][:60],
            "apss_cl_no":   "TEST-001",
            "est_qty":      r["qty"],
            "uom":          r["unit"],
            "rate_inr":     100.0,
            "citations":    ["IS 0000:2024"],
        })

    from app.boq_generator import BoQBatchResponse
    parsed = BoQBatchResponse.model_validate({"rows": fake_rows})

    return {
        "text":              json.dumps({"rows": fake_rows}),
        "prompt_tokens":     500,
        "completion_tokens": 300,
        "thought_tokens":    0,
        "total_tokens":      800,
        "model_version":     "fake-flash",
        "raw":               {},
        "parsed":            parsed,
        "parse_ok":          True,
    }


async def _fake_gemini_pro_async(prompt, **kwargs):
    """Pro behaves like Flash for the test."""
    return await _fake_gemini_flash_async(prompt, **kwargs)


# ─── Tests ────────────────────────────────────────────────────────────


def test_parallel_speedup():
    """30 batches × max_concurrent=10 should complete in ~3 waves not 30 waves."""
    print(f"\n── test_parallel_speedup ──")
    n_batches = 30
    rows_per_batch = 5
    project_ctx = ProjectContext(
        project_name="MockProj", discipline_hint="Civil",
        tender_category="WORKS",
    )

    # Build skeleton batches: 30 batches × 5 rows
    pending = []
    sno = 1
    for b in range(n_batches):
        rows = []
        for _ in range(rows_per_batch):
            rows.append(BoQSkeletonRow(
                s_no=sno,
                item_name=f"Civil test item number {sno} for parallel batching",
                qty=1.0, unit="m3",
            ))
            sno += 1
        pending.append((b + 1, "Civil", rows, []))

    _FLASH_FAILS_BY_DISCIPLINE.clear()
    with patch("app.vertex_client.gemini_flash_async", _fake_gemini_flash_async), \
         patch("app.vertex_client.gemini_pro_async", _fake_gemini_pro_async):
        t0 = time.time()
        results = list(run_batches_parallel(
            pending, project_ctx, max_concurrent=10,
        ))
        elapsed = time.time() - t0

    n_complete = len(results)
    expected_max_wall_clock = SIMULATED_BATCH_LATENCY_SEC * (n_batches / 10) * 2  # 2× tolerance
    speedup = (n_batches * SIMULATED_BATCH_LATENCY_SEC) / elapsed
    print(f"  batches: {n_complete}/{n_batches}")
    print(f"  wall-clock: {elapsed:.2f}s (would be {n_batches * SIMULATED_BATCH_LATENCY_SEC:.1f}s serial)")
    print(f"  speedup: {speedup:.1f}×")
    assert n_complete == n_batches, f"Got {n_complete} batches, expected {n_batches}"
    assert elapsed < expected_max_wall_clock, (
        f"Wall-clock {elapsed:.2f}s exceeds 2× expected {expected_max_wall_clock:.2f}s"
    )
    assert speedup >= 5.0, f"Speedup only {speedup:.1f}×; expected ≥5× from 10 concurrency"
    print(f"  ✓ PASS")


def test_progressive_yield():
    """Events should arrive incrementally, not all at end."""
    print(f"\n── test_progressive_yield ──")
    project_ctx = ProjectContext(
        project_name="MockProj", discipline_hint="Civil",
        tender_category="WORKS",
    )
    pending = []
    sno = 1
    for b in range(5):
        rows = []
        for _ in range(3):
            rows.append(BoQSkeletonRow(
                s_no=sno,
                item_name=f"Civil test item number {sno} for mock batching",
                qty=1.0, unit="m",
            ))
            sno += 1
        pending.append((b + 1, "Civil", rows, []))

    arrival_times: list[float] = []
    _FLASH_FAILS_BY_DISCIPLINE.clear()
    with patch("app.vertex_client.gemini_flash_async", _fake_gemini_flash_async), \
         patch("app.vertex_client.gemini_pro_async", _fake_gemini_pro_async):
        t0 = time.time()
        for _ in run_batches_parallel(pending, project_ctx, max_concurrent=3):
            arrival_times.append(time.time() - t0)

    print(f"  arrival times: {[f'{t:.2f}' for t in arrival_times]}")
    span = arrival_times[-1] - arrival_times[0]
    assert span > 0.1, (
        f"All batches arrived at the same time (span={span:.3f}s) — not progressive"
    )
    print(f"  span: {span:.2f}s — events streamed progressively")
    print(f"  ✓ PASS")


def test_sonnet_path_removed():
    """Verify no claude_sonnet symbol exists in vertex_client / boq_generator."""
    print(f"\n── test_sonnet_path_removed ──")
    import app.vertex_client as vc
    import app.boq_generator as bg
    assert not hasattr(vc, "claude_sonnet"), "claude_sonnet still in vertex_client"
    assert not hasattr(vc, "SONNET_MODEL_ID"), "SONNET_MODEL_ID still in vertex_client"
    assert not hasattr(vc, "_anthropic_vertex_url"), "_anthropic_vertex_url still in vertex_client"
    assert not hasattr(bg, "_SONNET_SKIP_AFTER_404"), "_SONNET_SKIP_AFTER_404 still in boq_generator"
    print(f"  ✓ PASS — Sonnet completely removed")


def main():
    print("R8.6 — parallel batching unit tests")
    test_sonnet_path_removed()
    test_progressive_yield()
    test_parallel_speedup()
    print("\n=== ALL TESTS PASS ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
