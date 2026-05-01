"""
Direct upload of AP-state rule extraction results to Supabase via REST.

Reads `data/extraction_results/ap_state/rules/batch_*.json` + matching batch
metadata in `data/extraction_batches/ap_state/rules/`. Maps fields to the
RuleModel schema (rule_text → natural_language, category derived from typology
prefix), upserts to Supabase /rest/v1/rules with on_conflict=rule_id.

Idempotent: re-running merges duplicates by rule_id.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import requests
from loguru import logger

from builder.config import settings
from builder.rule_extractor import _category_from_typology

REPO = Path(__file__).resolve().parent.parent
RESULTS_DIR = REPO / "data" / "extraction_results" / "ap_state" / "rules"
BATCHES_DIR = REPO / "data" / "extraction_batches" / "ap_state" / "rules"
TODAY = dt.date.today().isoformat()


def _build_row(raw: dict, source_doc: str) -> dict:
    return {
        "rule_id": raw["rule_id"],
        "source_doc": source_doc,
        "source_chapter": None,
        "source_clause": raw.get("source_clause"),
        "source_url": None,
        "layer": raw.get("layer", "AP-State"),
        "category": _category_from_typology(raw.get("typology_code", "")),
        "pattern_type": raw.get("pattern_type", "P2"),
        "natural_language": raw["rule_text"],
        "verification_method": raw.get("verification_method", ""),
        "condition_when": raw.get("condition_when", ""),
        "severity": raw.get("severity", "WARNING"),
        "typology_code": raw.get("typology_code", "Missing-Mandatory-Field"),
        "generates_clause": bool(raw.get("generates_clause", False)),
        "defeats": raw.get("defeats", []),
        "defeated_by": raw.get("defeated_by", []),
        "shacl_shape_id": None,
        "vector_concept_id": None,
        "valid_from": TODAY,
        "valid_until": None,
        "extracted_from": "ap_state_rule_extraction_2026-04",
        "extraction_confidence": 0.9,
        "critic_verified": False,
        "critic_note": None,
        "human_status": "pending",
        "human_note": None,
        "created_at": dt.datetime.utcnow().isoformat(),
        "updated_at": dt.datetime.utcnow().isoformat(),
    }


def _gather_rows() -> list[dict]:
    """Read every result file, attach source_doc from batch metadata, return rows."""
    rows: list[dict] = []
    for result_file in sorted(RESULTS_DIR.glob("batch_*.json")):
        batch_id = result_file.stem
        batch_meta_path = BATCHES_DIR / f"{batch_id}.json"
        if not batch_meta_path.exists():
            logger.warning(f"  Skipping {batch_id}: no matching batch metadata")
            continue
        meta = json.loads(batch_meta_path.read_text())
        source_doc = meta["source_doc"]
        rules_raw = json.loads(result_file.read_text())
        for raw in rules_raw:
            rows.append(_build_row(raw, source_doc))
    return rows


def _post_in_chunks(rows: list[dict], chunk_size: int = 50) -> tuple[int, int]:
    """POST rows to Supabase /rules in chunks. Returns (success_count, fail_count)."""
    if not settings.supabase_anon_key:
        logger.error("SUPABASE_ANON_KEY missing in .env")
        return 0, len(rows)
    url = f"{settings.supabase_rest_url}/rest/v1/rules?on_conflict=rule_id"
    headers = {
        "apikey": settings.supabase_anon_key,
        "Authorization": f"Bearer {settings.supabase_anon_key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    ok = 0
    fail = 0
    for i in range(0, len(rows), chunk_size):
        chunk = rows[i : i + chunk_size]
        res = requests.post(url, headers=headers, data=json.dumps(chunk), timeout=60)
        if 200 <= res.status_code < 300:
            ok += len(chunk)
            logger.info(f"  Chunk {i//chunk_size + 1}: HTTP {res.status_code} ({len(chunk)} rows OK)")
        else:
            fail += len(chunk)
            logger.error(f"  Chunk {i//chunk_size + 1}: HTTP {res.status_code} — {res.text[:400]}")
    return ok, fail


def _count_supabase_rules(filter_layer: str | None = None) -> int:
    url = f"{settings.supabase_rest_url}/rest/v1/rules"
    if filter_layer:
        url += f"?layer=eq.{filter_layer}"
    headers = {
        "apikey": settings.supabase_anon_key,
        "Authorization": f"Bearer {settings.supabase_anon_key}",
        "Prefer": "count=exact",
        "Range-Unit": "items",
        "Range": "0-0",
    }
    res = requests.head(url, headers=headers, timeout=30)
    cr = res.headers.get("Content-Range", "")  # e.g. "0-0/123"
    if "/" in cr:
        return int(cr.split("/")[-1])
    return -1


def main() -> int:
    rows = _gather_rows()
    logger.info(f"Gathered {len(rows)} AP-state rule rows from {RESULTS_DIR}")
    if not rows:
        logger.warning("No rows to upload — aborting.")
        return 1

    before_total = _count_supabase_rules()
    before_apstate = _count_supabase_rules("AP-State")
    logger.info(f"Before upload — Supabase rules: total={before_total}, layer=AP-State={before_apstate}")

    ok, fail = _post_in_chunks(rows)

    after_total = _count_supabase_rules()
    after_apstate = _count_supabase_rules("AP-State")
    logger.info("")
    logger.info("=== Summary ===")
    logger.info(f"  Rows posted:     {ok} OK / {fail} FAIL")
    logger.info(f"  Total rules:     {before_total} → {after_total}  (Δ {after_total - before_total})")
    logger.info(f"  AP-State rules:  {before_apstate} → {after_apstate}  (Δ {after_apstate - before_apstate})")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
