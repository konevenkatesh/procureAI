"""End-to-end tests for the (b)-prime crash resilience wrapper.

These tests exercise `main_with_crash_resilience` and `DeferredCleanup`
by spawning a synthetic Tier-1 validator script via `subprocess.run()`
— same call-shape that `_run_one_check()` uses in production. This
catches the full subprocess-crash path: rc=1 propagation, stderr
capture, deferred-cleanup non-commit on exception, etc.

Three scenarios covered:

  test_crash_path
      Validator's main() raises → wrapper emits UNVERIFIED
      subprocess_crashed row, process exits rc=1, pre-seeded
      prior row survives (DeferredCleanup never committed).

  test_success_path
      Validator's main() returns 0 normally → DeferredCleanup commits;
      pre-seeded prior row is deleted; new row from main() is the
      only one for the (doc_id, typology).

  test_no_prior_row
      Empty capture (no prior row exists) → wrapper still works;
      crash path emits a single subprocess_crashed row.

Tests write to real Supabase under a unique `test_crash_<uuid>`
doc_id and clean up afterwards. They require .env to be sourced
(supabase_anon_key + supabase_rest_url).
"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path
from textwrap import dedent

import pytest
import requests

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from builder.config import settings  # noqa: E402

REST = settings.supabase_rest_url
H = {"apikey":        settings.supabase_anon_key,
     "Authorization": f"Bearer {settings.supabase_anon_key}",
     "Content-Type":  "application/json"}

PY = sys.executable

TYPOLOGY = "TEST-Crash-Resilience"


# ── Helpers ──────────────────────────────────────────────────────────

def _seed_prior_row(doc_id: str, marker: str = "prior") -> str:
    """Insert a pre-existing ValidationFinding row to verify it
    survives a crash (deferred cleanup non-commit). Returns node_id."""
    r = requests.post(
        f"{REST}/rest/v1/kg_nodes",
        headers={**H, "Prefer": "return=representation"},
        json=[{
            "doc_id":     doc_id,
            "node_type":  "ValidationFinding",
            "label":      f"{TYPOLOGY} · prior · {marker}",
            "properties": {
                "verdict":       "COMPLIANT_FIRED",
                "status":        "COMPLIANT",
                "typology_code": TYPOLOGY,
                "tier":          1,
                "marker":        marker,
            },
            "source_ref": "test:crash_resilience:seed",
        }],
        timeout=15,
    )
    r.raise_for_status()
    return r.json()[0]["node_id"]


def _fetch_rows(doc_id: str) -> list[dict]:
    r = requests.get(f"{REST}/rest/v1/kg_nodes",
                     params={"doc_id":    f"eq.{doc_id}",
                             "node_type": "eq.ValidationFinding",
                             "select":    "node_id,label,properties"},
                     headers=H, timeout=15)
    r.raise_for_status()
    return r.json()


def _cleanup(doc_id: str) -> None:
    rows = _fetch_rows(doc_id)
    for row in rows:
        try:
            requests.delete(f"{REST}/rest/v1/kg_nodes",
                            params={"node_id": f"eq.{row['node_id']}"},
                            headers=H, timeout=15)
        except Exception:
            pass


def _write_fake_validator(tmp_path: Path, doc_id: str, *,
                          mode: str) -> Path:
    """Write a synthetic Tier-1 validator script to tmp_path. The
    script wraps a main() function with `main_with_crash_resilience`,
    using the same shape as the real tier1_*_check.py scripts.

    `mode` is one of:
      'crash'   → main() deliberately raises ValueError
      'success' → main() emits a COMPLIANT_FIRED row and returns 0
    """
    script_src = dedent(f'''
        import sys
        from pathlib import Path
        sys.path.insert(0, "{REPO}")

        from modules.validation.verdict_emitter import (
            main_with_crash_resilience,
            emit_verdict_row,
        )

        DOC_ID   = "{doc_id}"
        TYPOLOGY = "{TYPOLOGY}"


        def main() -> int:
            mode = {mode!r}
            if mode == "crash":
                raise ValueError("synthetic crash for test")
            if mode == "success":
                emit_verdict_row(
                    doc_id=DOC_ID, typology=TYPOLOGY, rule_id=None,
                    verdict="COMPLIANT_FIRED",
                    severity="ADVISORY",
                    evidence_quote="synthetic success quote",
                    evidence_line_no_local=1,
                    extra_props={{"marker": "new_run"}},
                )
                return 0
            raise RuntimeError(f"unexpected mode: {{mode!r}}")


        if __name__ == "__main__":
            raise SystemExit(main_with_crash_resilience(
                main, doc_id=DOC_ID, typology=TYPOLOGY))
    ''').strip()
    script_path = tmp_path / f"fake_validator_{mode}.py"
    script_path.write_text(script_src, encoding="utf-8")
    return script_path


def _run(script_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PY, str(script_path)],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ},
    )


# ── Tests ────────────────────────────────────────────────────────────

@pytest.fixture
def doc_id() -> str:
    return f"test_crash_{uuid.uuid4().hex[:12]}"


def test_crash_path(tmp_path: Path, doc_id: str) -> None:
    """Validator main() raises → rc=1, prior row survives, new
    UNVERIFIED subprocess_crashed row exists with crash class +
    message in evidence_quote."""
    try:
        prior_id = _seed_prior_row(doc_id, marker="seed-A")
        script = _write_fake_validator(tmp_path, doc_id, mode="crash")
        proc = _run(script)

        # 1. Process exited with rc=1 (ops signal preserved)
        assert proc.returncode == 1, (
            f"expected rc=1, got rc={proc.returncode}; "
            f"stderr={proc.stderr[-300:]}")
        # 2. Crash traceback printed to stderr (re-raise visible to ops)
        assert "ValueError" in proc.stderr
        assert "synthetic crash for test" in proc.stderr

        # 3. KG state — exactly two rows (prior survived + new
        #    subprocess_crashed UNVERIFIED row).
        rows = _fetch_rows(doc_id)
        assert len(rows) == 2, (
            f"expected 2 rows (prior + crash), got {len(rows)}: "
            f"{[r.get('label') for r in rows]}")

        # Prior row still present
        prior_rows = [r for r in rows if r["node_id"] == prior_id]
        assert len(prior_rows) == 1, "prior row was deleted on crash!"

        # Crash row has expected verdict + failure_path
        crash_rows = [r for r in rows
                      if (r.get("properties") or {}).get("failure_path")
                         == "subprocess_crashed"]
        assert len(crash_rows) == 1, (
            f"expected 1 subprocess_crashed row, got {len(crash_rows)}")
        crash_props = crash_rows[0]["properties"]
        assert crash_props["verdict"] == "UNVERIFIED"
        assert crash_props["typology_code"] == TYPOLOGY
        assert "ValueError" in (crash_props.get("evidence_quote") or "")
        assert "synthetic crash for test" in (
            crash_props.get("evidence_quote") or "")
        debug = crash_props.get("retrieval_debug") or {}
        assert debug.get("exception_class") == "ValueError"
    finally:
        _cleanup(doc_id)


def test_success_path(tmp_path: Path, doc_id: str) -> None:
    """Validator main() returns 0 → DeferredCleanup commits,
    pre-seeded prior row is deleted, new row is the only one."""
    try:
        _seed_prior_row(doc_id, marker="seed-B")
        # Sanity: prior is present
        assert len(_fetch_rows(doc_id)) == 1

        script = _write_fake_validator(tmp_path, doc_id, mode="success")
        proc = _run(script)

        # 1. Process exited rc=0
        assert proc.returncode == 0, (
            f"expected rc=0, got rc={proc.returncode}; "
            f"stderr={proc.stderr[-300:]}")

        # 2. KG state — exactly one row (prior deleted, new committed).
        rows = _fetch_rows(doc_id)
        assert len(rows) == 1, (
            f"expected 1 row (prior deleted, new committed), got "
            f"{len(rows)}: {[r.get('label') for r in rows]}")

        new = rows[0]
        props = new["properties"]
        assert props["verdict"] == "COMPLIANT_FIRED"
        assert props.get("marker") == "new_run", (
            "row carries `seed-B` marker — prior row was NOT deleted "
            "(DeferredCleanup commit failed)")
    finally:
        _cleanup(doc_id)


def test_no_prior_row(tmp_path: Path, doc_id: str) -> None:
    """Empty capture (no prior row) → wrapper works; crash path
    emits a single subprocess_crashed row, no prior to preserve."""
    try:
        # Sanity: doc_id is empty
        assert _fetch_rows(doc_id) == []

        script = _write_fake_validator(tmp_path, doc_id, mode="crash")
        proc = _run(script)

        assert proc.returncode == 1
        rows = _fetch_rows(doc_id)
        assert len(rows) == 1, (
            f"expected 1 crash row only, got {len(rows)}")
        props = rows[0]["properties"]
        assert props["verdict"] == "UNVERIFIED"
        assert props.get("failure_path") == "subprocess_crashed"
    finally:
        _cleanup(doc_id)
