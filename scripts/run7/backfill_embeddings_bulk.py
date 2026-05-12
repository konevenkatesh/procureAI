"""R7.4 — Backfill embeddings using bulk SQL UPDATE via MCP execute_sql.

Faster than 1095 individual PATCH calls (which were timing out).
Strategy:
  1. Fetch unembedded rows in pages of 50
  2. Compute Vertex AI embeddings in batches
  3. Write to /tmp/embeddings_<batch>.sql with single UPDATE FROM VALUES
  4. Caller runs via Supabase MCP execute_sql

This script writes the SQL files; the calling agent executes them via MCP.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "services" / "m1-drafter"))

from builder.config import settings  # noqa: E402
from app.vertex_client import embed_texts_batch  # noqa: E402

REST = settings.supabase_rest_url
KEY = settings.supabase_service_role_key or settings.supabase_anon_key
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}

OUT_DIR = Path("/tmp/embeddings_sql")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def extract_embed_text(row: dict) -> str:
    props = row.get("properties") or {}
    nt = row.get("node_type")
    if nt == "SBDSection":
        return f"{props.get('section_id', '')} {props.get('name', '')}\n\n{(props.get('content_md', '') or '')[:10000]}"
    if nt == "BoQItemSpec":
        parts = [
            props.get("discipline", ""), props.get("sub_discipline", ""),
            props.get("work_type", ""), props.get("short_desc", ""),
            props.get("spec_text", "")[:8000],
        ]
        cites = props.get("citations") or []
        if cites:
            parts.append("Standards: " + ", ".join(cites))
        return " | ".join(p for p in parts if p)
    if nt == "TechSpecTemplate":
        return " | ".join(filter(None, [
            props.get("discipline", ""), props.get("sub_discipline", ""),
            props.get("item_category", ""), props.get("typical_short_desc", ""),
            "Samples: " + ", ".join(props.get("sample_short_descs", [])),
            "Standards: " + ", ".join(props.get("expected_citations", [])),
            props.get("retrieval_query_template", ""),
        ]))
    return row.get("label", "") or ""


def fetch_unembedded(node_type: str, limit: int = 50) -> list[dict]:
    r = requests.get(
        f"{REST}/rest/v1/kg_nodes",
        params={
            "select":    "node_id,node_type,label,properties",
            "node_type": f"eq.{node_type}",
            "embedding": "is.null",
            "limit":     str(limit),
        },
        headers={**H, "Range": f"0-{limit-1}"}, timeout=60,
    )
    r.raise_for_status()
    return r.json()


def build_update_sql(updates: list[tuple[str, list[float]]]) -> str:
    """Single multi-row UPDATE using FROM VALUES."""
    if not updates:
        return ""
    values_parts = []
    for node_id, emb in updates:
        vec_str = "[" + ",".join(f"{v:.7f}" for v in emb) + "]"
        # Quote node_id (UUID) and vector literal
        values_parts.append(f"('{node_id}'::uuid, '{vec_str}'::vector)")
    values_sql = ",\n  ".join(values_parts)
    return f"""UPDATE kg_nodes AS k
SET embedding = v.embedding
FROM (VALUES
  {values_sql}
) AS v(node_id, embedding)
WHERE k.node_id = v.node_id;"""


def write_batches(node_type: str, batch_idx_start: int = 0) -> tuple[int, int]:
    """Return (n_batches_written, n_rows_embedded). One file per batch of 50."""
    n_batches = 0
    n_rows = 0
    batch_idx = batch_idx_start
    while True:
        rows = fetch_unembedded(node_type, limit=50)
        if not rows:
            break
        texts = [extract_embed_text(r) for r in rows]
        embeds = embed_texts_batch(texts)
        updates = [(r["node_id"], e) for r, e in zip(rows, embeds)]
        sql = build_update_sql(updates)
        outfile = OUT_DIR / f"{node_type.lower()}_{batch_idx:03d}.sql"
        outfile.write_text(sql)
        n_batches += 1
        n_rows += len(updates)
        batch_idx += 1
        print(f"  wrote {outfile.name}  ({len(updates)} rows)")
        # NOTE: SQL file accumulates; caller agent must apply each one via MCP
        # then re-fetch unembedded to continue. We stop here to let caller batch-apply.
        # For now, just write 5 batches per node_type and let agent apply in chunks.
        if n_batches >= 25:  # safety cap; ~1250 rows per script invocation
            break
    return n_batches, n_rows


def main() -> None:
    print("R7.4 — Generating SQL files for bulk embedding UPDATE")
    print(f"  Output dir: {OUT_DIR}")
    for nt in ["SBDSection", "BoQItemSpec", "TechSpecTemplate"]:
        print(f"\n── {nt} ──")
        nb, nr = write_batches(nt, batch_idx_start=0)
        print(f"  ✓ {nt}: {nb} batches, {nr} rows")


if __name__ == "__main__":
    main()
