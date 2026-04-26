"""
One-time database initialiser:
  1. Creates all tables from SQLAlchemy models.
  2. Loads risk typology seed data from data/risk_typology.json.

Run after `docker-compose up -d`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from loguru import logger

from builder.config import settings
from knowledge_layer.database import get_engine, get_session
from knowledge_layer.models import Base, RiskTypologyModel


def main() -> int:
    logger.info("Creating tables...")
    Base.metadata.create_all(bind=get_engine())
    logger.info("Tables created.")

    typology_path = settings.data_dir / "risk_typology.json"
    if not typology_path.exists():
        logger.error(f"Missing {typology_path}")
        return 1

    typologies = json.loads(typology_path.read_text(encoding="utf-8"))
    logger.info(f"Loading {len(typologies)} risk typologies...")

    inserted = 0
    with get_session() as session:
        existing = {t[0] for t in session.query(RiskTypologyModel.code).all()}
        for t in typologies:
            if t["code"] in existing:
                continue
            session.add(RiskTypologyModel(
                code=t["code"],
                name=t["name"],
                definition=t["definition"],
                rule_ids=t.get("rule_ids", []),
                severity=t["severity"],
                category=t["category"],
                alice_equivalent=t.get("alice_equivalent"),
            ))
            inserted += 1
    logger.info(f"Inserted {inserted} typologies (skipped {len(typologies) - inserted} existing).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
