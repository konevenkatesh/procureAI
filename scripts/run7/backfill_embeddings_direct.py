"""R7.4 — Backfill embeddings via direct psycopg + Vertex AI.

Much faster than REST PATCH (single connection, bulk INSERT via VALUES tables).
Uses settings.supabase_url (Postgres pooler connection string).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import psycopg

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "services" / "m1-drafter"))

from builder.config import settings  # noqa: E402
from app.vertex_client import embed_texts_batch  # noqa: E402


def extract_embed_text(node_type: str, props: dict, label: str) -> str:
    if node_type == "SBDSection":
        return f"{props.get('section_id', '')} {props.get('name', '')}\n\n{(props.get('content_md', '') or '')[:10000]}"
    if node_type == "BoQItemSpec":
        parts = [
            props.get("discipline", ""), props.get("sub_discipline", ""),
            props.get("work_type", ""), props.get("short_desc", ""),
            props.get("spec_text", "")[:8000],
        ]
        cites = props.get("citations") or []
        if cites:
            parts.append("Standards: " + ", ".join(cites))
        return " | ".join(p for p in parts if p)
    if node_type == "TechSpecTemplate":
        return " | ".join(filter(None, [
            props.get("discipline", ""), props.get("sub_discipline", ""),
            props.get("item_category", ""), props.get("typical_short_desc", ""),
            "Samples: " + ", ".join(props.get("sample_short_descs", [])),
            "Standards: " + ", ".join(props.get("expected_citations", [])),
            props.get("retrieval_query_template", ""),
        ]))
    return label or ""


def backfill_for_type(conn: psycopg.Connection, node_type: str, batch_size: int = 50) -> int:
    total = 0
    while True:
        # Fetch unembedded rows
        with conn.cursor() as cur:
            cur.execute(
                "SELECT node_id, properties, label FROM kg_nodes "
                "WHERE node_type = %s AND embedding IS NULL "
                "ORDER BY node_id LIMIT %s",
                (node_type, batch_size),
            )
            rows = cur.fetchall()
        if not rows:
            break

        texts = [extract_embed_text(node_type, r[1], r[2]) for r in rows]
        embeddings = embed_texts_batch(texts)

        # Bulk update via single multi-VALUES UPDATE
        update_args = []
        for (node_id, _, _), emb in zip(rows, embeddings):
            vec_str = "[" + ",".join(f"{v:.7f}" for v in emb) + "]"
            update_args.append((node_id, vec_str))

        with conn.cursor() as cur:
            # Use executemany with parameterised UPDATE
            cur.executemany(
                "UPDATE kg_nodes SET embedding = %s::vector WHERE node_id = %s::uuid",
                [(vec, nid) for nid, vec in update_args],
            )
        conn.commit()
        total += len(rows)
        print(f"  {node_type}: {total} embedded")
        time.sleep(0.2)  # gentle rate limit
    return total


def main() -> None:
    print("R7.4 — Direct psycopg embedding backfill")
    print(f"  Connection: {settings.supabase_url[:50]}...")

    with psycopg.connect(settings.supabase_url, sslmode="require", connect_timeout=30) as conn:
        # Set statement_timeout to 5 min for safety
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = '300000'")
        conn.commit()

        for nt in ["SBDSection", "BoQItemSpec", "TechSpecTemplate"]:
            print(f"\n── {nt} ──")
            n = backfill_for_type(conn, nt)
            print(f"  ✓ {nt}: {n} rows total")


if __name__ == "__main__":
    main()
