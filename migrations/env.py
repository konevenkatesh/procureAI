"""Alembic env: drives migrations against either local Postgres or Supabase.

By default uses settings.supabase_url if set, else settings.postgres_url.
Override with ALEMBIC_TARGET_URL env var, or pass -x url=<conn_str>.
"""
from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make project importable
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from builder.config import settings           # noqa: E402
from knowledge_layer.models import Base       # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _resolve_url() -> str:
    """Pick connection string in priority order."""
    # 1. -x url=...
    x_args = context.get_x_argument(as_dictionary=True)
    if "url" in x_args:
        return x_args["url"]
    # 2. ALEMBIC_TARGET_URL env var
    if os.environ.get("ALEMBIC_TARGET_URL"):
        return os.environ["ALEMBIC_TARGET_URL"]
    # 3. Supabase if configured
    if settings.supabase_url:
        return settings.supabase_url
    # 4. Local Postgres fallback
    return settings.postgres_url


def run_migrations_offline() -> None:
    context.configure(
        url=_resolve_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    cfg = config.get_section(config.config_ini_section, {})
    cfg["sqlalchemy.url"] = _resolve_url()
    connectable = engine_from_config(
        cfg,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,        # short-lived connections, friendly to PgBouncer
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
