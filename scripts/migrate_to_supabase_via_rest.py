"""
Migrate rules + risk_typology from local Postgres to Supabase via REST API.

Uses the Python `requests` library (no SQLAlchemy on the destination side) to
POST rows to PostgREST endpoints. Idempotent: uses Prefer: resolution=merge-duplicates
to upsert by primary key. Reads source rows from local Docker Postgres via SQLAlchemy.
"""
from __future__ import annotations

import datetime as dt
import json
import sys

import requests
from loguru import logger
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from builder.config import settings
from knowledge_layer.models import RiskTypologyModel, RuleModel


def _serialise_value(v):
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    return v


def _row_to_dict(row) -> dict:
    return {c.key: _serialise_value(getattr(row, c.key)) for c in row.__table__.columns}


def _post_batch(table: str, rows: list[dict], on_conflict: str) -> tuple[int, str]:
    if not rows:
        return 0, "no rows"
    url = f"{settings.supabase_rest_url}/rest/v1/{table}?on_conflict={on_conflict}"
    headers = {
        "apikey": settings.supabase_anon_key,
        "Authorization": f"Bearer {settings.supabase_anon_key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    res = requests.post(url, headers=headers, data=json.dumps(rows), timeout=60)
    return res.status_code, res.text[:300] if res.text else "OK"


def main() -> int:
    if not settings.supabase_anon_key:
        logger.error("SUPABASE_ANON_KEY missing in .env")
        return 2

    src_engine = create_engine(settings.postgres_url, pool_pre_ping=True, future=True)
    SrcSession = sessionmaker(bind=src_engine, future=True)

    summary = []
    with SrcSession() as session:
        # Rules
        rules = session.execute(select(RuleModel)).scalars().all()
        rule_rows = [_row_to_dict(r) for r in rules]
        logger.info(f"Read {len(rule_rows)} rules from local Postgres")
        status, body = _post_batch("rules", rule_rows, on_conflict="rule_id")
        ok = 200 <= status < 300
        logger.info(f"  POST /rules → HTTP {status}  ({'OK' if ok else 'FAIL'})")
        if not ok:
            logger.error(f"  body: {body}")
        summary.append(("rules", len(rule_rows), status, ok))

        # Risk typology
        typology = session.execute(select(RiskTypologyModel)).scalars().all()
        typ_rows = [_row_to_dict(t) for t in typology]
        logger.info(f"Read {len(typ_rows)} typologies from local Postgres")
        status, body = _post_batch("risk_typology", typ_rows, on_conflict="code")
        ok = 200 <= status < 300
        logger.info(f"  POST /risk_typology → HTTP {status}  ({'OK' if ok else 'FAIL'})")
        if not ok:
            logger.error(f"  body: {body}")
        summary.append(("risk_typology", len(typ_rows), status, ok))

    logger.info("\n=== REST migration summary ===")
    for table, n, status, ok in summary:
        logger.info(f"  {table:<16s} {n:>3d} rows → HTTP {status}  {'✓' if ok else '✗'}")
    return 0 if all(ok for _, _, _, ok in summary) else 1


if __name__ == "__main__":
    sys.exit(main())
