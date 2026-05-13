"""R10 follow-up — embed RuleNode + Section rows so BOT chat retrieval surfaces them.

R7.4's backfill only covered the 3 new R7-additive node_types (SBDSection,
BoQItemSpec, TechSpecTemplate). The pre-existing RuleNode (611 regulatory
rules) + Section (1577 draft clauses) rows have NULL embeddings. The
`kb_chat_retrieve` RPC's `WHERE embedding IS NOT NULL` filters them out
entirely, so the chat couldn't answer "PBG for ₹100cr" — the actual CVC-112,
MPW-112, GFR-G-051 rules existed in the corpus but were invisible.

Backfill cost (~2188 embeddings × ~200 tokens × $0.000025/1k × 83 INR/USD):
  ≈ ₹0.91 — well within budget.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import psycopg

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "services" / "m1-drafter"))

from builder.config import settings                # noqa: E402
from app.vertex_client import embed_texts_batch    # noqa: E402


def extract_embed_text(node_type: str, label: str, props: dict) -> str:
    """Build the text we embed per node_type."""
    if node_type == "RuleNode":
        parts = [label]                              # label carries the rule statement
        if props.get("verification_method"):
            parts.append(f"Verification: {props['verification_method']}")
        if props.get("typology_code"):
            parts.append(f"Typology: {props['typology_code']}")
        if props.get("layer"):
            parts.append(f"Layer: {props['layer']}")
        if props.get("severity"):
            parts.append(f"Severity: {props['severity']}")
        return "\n".join(parts)[:8000]
    if node_type == "Section":
        # Section = a clause/section from a drafted document. label has the heading + section type.
        parts = [label]
        if props.get("heading"):
            parts.append(props["heading"])
        if props.get("section_type"):
            parts.append(f"Section type: {props['section_type']}")
        return "\n".join(parts)[:8000]
    return label or ""


def backfill_for_type(conn: psycopg.Connection, node_type: str, batch_size: int = 50) -> int:
    total = 0
    print(f"\n── {node_type} ──")
    while True:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT node_id, label, properties FROM kg_nodes "
                "WHERE node_type = %s AND embedding IS NULL "
                "ORDER BY node_id LIMIT %s",
                (node_type, batch_size),
            )
            rows = cur.fetchall()
        if not rows:
            break

        texts = [extract_embed_text(node_type, r[1] or "", r[2] or {}) for r in rows]
        embeddings = embed_texts_batch(texts)

        update_args = []
        for (node_id, _, _), emb in zip(rows, embeddings):
            if not emb:
                continue
            vec_str = "[" + ",".join(f"{v:.7f}" for v in emb) + "]"
            update_args.append((vec_str, node_id))

        if update_args:
            with conn.cursor() as cur:
                cur.executemany(
                    "UPDATE kg_nodes SET embedding = %s::vector WHERE node_id = %s::uuid",
                    update_args,
                )
            conn.commit()
            total += len(update_args)
            print(f"  embedded {total}/{total + 0} so far ({len(update_args)} this batch)", flush=True)
        time.sleep(0.2)        # gentle rate limit
    return total


def main() -> int:
    print("R10 follow-up — RuleNode + Section embedding backfill")
    print(f"  pooler: {settings.supabase_url[:50]}...")

    with psycopg.connect(settings.supabase_url, sslmode="require", connect_timeout=30) as conn:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = '300000'")     # 5 min hard cap
        conn.commit()
        n_rules    = backfill_for_type(conn, "RuleNode")
        n_sections = backfill_for_type(conn, "Section")

    print(f"\n=== Done: {n_rules} RuleNode + {n_sections} Section embedded ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
