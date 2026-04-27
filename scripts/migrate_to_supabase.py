"""
One-shot migration: copy rules + risk_typology rows from local Postgres to Supabase.

Reads from settings.postgres_url, writes to settings.supabase_url.
Idempotent: rows with an existing primary key on the target are skipped.

Usage:
    python scripts/migrate_to_supabase.py                    # rules + typology
    python scripts/migrate_to_supabase.py --tables rules     # rules only

Requires the destination tables to already exist on Supabase (run
`migrations/supabase_schema.sql` in the SQL Editor first).
"""
from __future__ import annotations

import sys

import typer
from loguru import logger
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from builder.config import settings
from knowledge_layer.models import RiskTypologyModel, RuleModel


app = typer.Typer(add_completion=False)


TABLE_REGISTRY = {
    "rules":          RuleModel,
    "risk_typology":  RiskTypologyModel,
}


def _engine_for(url: str, label: str):
    if not url:
        raise SystemExit(f"Missing connection URL for {label}")
    logger.info(f"  {label}: {url[:60]}…")
    return create_engine(url, pool_pre_ping=True, future=True)


def _row_to_dict(row) -> dict:
    return {c.key: getattr(row, c.key) for c in row.__table__.columns}


def migrate_table(model, src_session, dst_session) -> tuple[int, int]:
    """Returns (copied, skipped) counts."""
    pk_col = next(iter(model.__table__.primary_key.columns)).name

    src_rows = src_session.execute(select(model)).scalars().all()
    if not src_rows:
        return 0, 0

    existing_pks = {
        getattr(r, pk_col)
        for r in dst_session.execute(select(getattr(model, pk_col))).scalars().all()
    }

    copied = 0
    skipped = 0
    for src in src_rows:
        if getattr(src, pk_col) in existing_pks:
            skipped += 1
            continue
        payload = _row_to_dict(src)
        dst_session.add(model(**payload))
        copied += 1
    dst_session.commit()
    return copied, skipped


@app.command()
def main(
    tables: str = typer.Option(
        "rules,risk_typology",
        "--tables",
        help="Comma-separated table names from: " + ", ".join(TABLE_REGISTRY),
    ),
):
    requested = [t.strip() for t in tables.split(",") if t.strip()]
    unknown = [t for t in requested if t not in TABLE_REGISTRY]
    if unknown:
        raise SystemExit(f"Unknown tables: {unknown}. Choose from {list(TABLE_REGISTRY)}")

    logger.info("Connecting…")
    src_engine = _engine_for(settings.postgres_url, "source (local)")
    dst_engine = _engine_for(settings.supabase_url, "destination (Supabase)")

    SrcSession = sessionmaker(bind=src_engine, future=True)
    DstSession = sessionmaker(bind=dst_engine, future=True)

    summary = []
    with SrcSession() as src_session, DstSession() as dst_session:
        for tname in requested:
            model = TABLE_REGISTRY[tname]
            copied, skipped = migrate_table(model, src_session, dst_session)
            summary.append((tname, copied, skipped))
            logger.info(f"  {tname}: copied {copied}, skipped {skipped} (already present)")

    logger.info("\n=== Migration summary ===")
    for tname, copied, skipped in summary:
        logger.info(f"  {tname:<20s} +{copied:>4d}   skipped {skipped}")


if __name__ == "__main__":
    app()
