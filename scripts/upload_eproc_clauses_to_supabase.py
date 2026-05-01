"""Direct upload of e_procurement clause extraction results to Supabase via REST."""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import requests
from loguru import logger

from builder.config import settings

REPO = Path(__file__).resolve().parent.parent
RESULTS_DIR = REPO / "data" / "extraction_results" / "e_procurement" / "clauses"
TODAY = dt.date.today().isoformat()


def _build_row(raw: dict) -> dict:
    return {
        "clause_id": raw["clause_id"],
        "title": raw.get("title", ""),
        "text_english": raw.get("text_english", ""),
        "text_telugu": raw.get("text_telugu"),
        "parameters": raw.get("parameters", []),
        "applicable_tender_types": raw.get("applicable_tender_types", []),
        "mandatory": bool(raw.get("mandatory", True)),
        "position_section": raw.get("position_section"),
        "position_order": raw.get("position_order"),
        "cross_references": raw.get("cross_references", []),
        "rule_ids": raw.get("rule_ids", []),
        "valid_from": TODAY,
        "valid_until": None,
        "human_verified": False,
        "created_at": dt.datetime.utcnow().isoformat(),
        "updated_at": dt.datetime.utcnow().isoformat(),
    }


def _gather_rows() -> list[dict]:
    rows: list[dict] = []
    for f in sorted(RESULTS_DIR.glob("batch_*.json")):
        try:
            cl = json.loads(f.read_text())
        except Exception as e:
            logger.warning(f"Skipping {f.name}: {e}")
            continue
        for raw in cl:
            rows.append(_build_row(raw))
    return rows


def _post_in_chunks(rows: list[dict], chunk_size: int = 50) -> tuple[int, int]:
    if not settings.supabase_anon_key:
        logger.error("SUPABASE_ANON_KEY missing")
        return 0, len(rows)
    url = f"{settings.supabase_rest_url}/rest/v1/clause_templates?on_conflict=clause_id"
    headers = {
        "apikey": settings.supabase_anon_key,
        "Authorization": f"Bearer {settings.supabase_anon_key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    ok = fail = 0
    for i in range(0, len(rows), chunk_size):
        chunk = rows[i : i + chunk_size]
        res = requests.post(url, headers=headers, data=json.dumps(chunk), timeout=60)
        if 200 <= res.status_code < 300:
            ok += len(chunk)
            logger.info(f"  Chunk {i//chunk_size + 1}: HTTP {res.status_code} ({len(chunk)} OK)")
        else:
            fail += len(chunk)
            logger.error(f"  Chunk {i//chunk_size + 1}: HTTP {res.status_code} — {res.text[:300]}")
    return ok, fail


def _count_clauses() -> int:
    url = f"{settings.supabase_rest_url}/rest/v1/clause_templates"
    headers = {
        "apikey": settings.supabase_anon_key,
        "Authorization": f"Bearer {settings.supabase_anon_key}",
        "Prefer": "count=exact",
        "Range-Unit": "items",
        "Range": "0-0",
    }
    res = requests.head(url, headers=headers, timeout=30)
    cr = res.headers.get("Content-Range", "")
    return int(cr.split("/")[-1]) if "/" in cr else -1


def main() -> int:
    rows = _gather_rows()
    logger.info(f"Gathered {len(rows)} e_procurement clause rows from {RESULTS_DIR}")
    if not rows:
        logger.warning("No rows to upload — aborting.")
        return 1
    before = _count_clauses()
    logger.info(f"Before upload — clause_templates: {before}")
    ok, fail = _post_in_chunks(rows)
    after = _count_clauses()
    logger.info("=== Summary ===")
    logger.info(f"  Rows posted:   {ok} OK / {fail} FAIL")
    logger.info(f"  Total clauses: {before} → {after}  (Δ {after - before})")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
