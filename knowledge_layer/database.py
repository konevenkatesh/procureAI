"""SQLAlchemy engine + session management.

Engine + session factory are lazily initialised so callers that don't actually
touch the database (e.g. scripts/prepare_extraction_batches.py reading source
sections) can import this package without psycopg2 installed.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from builder.config import settings


_engine: Optional[Engine] = None
_SessionLocal: Optional[sessionmaker] = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(settings.postgres_url, pool_pre_ping=True, future=True)
    return _engine


def _get_session_factory() -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=get_engine(), autoflush=False, autocommit=False, future=True
        )
    return _SessionLocal


@contextmanager
def get_session() -> Iterator[Session]:
    """Context-managed session with auto-commit / rollback."""
    session = _get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
