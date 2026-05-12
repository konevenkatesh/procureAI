"""R7.4 — Backfill Vertex AI text-embedding-005 vectors for all corpus kg_nodes.

Targets: SBDSection (30) + BoQItemSpec (993) + TechSpecTemplate (72) = 1095 rows.

Strategy:
  - Fetch nodes in pages of 50
  - Build embed_text per node from its key content (content_md for SBDSection,
    spec_text for BoQItemSpec, item_category+sample_descs for TechSpecTemplate)
  - Batch Vertex embed call (up to 25 per request to keep payload sane)
  - PATCH each row's embedding column

Total cost estimate: ~1095 × ~200 tokens avg × $0.000025 / 1k = ~$0.005 = ~₹0.40
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "services" / "m1-drafter"))

from builder.config import settings  # noqa: E402
from app.vertex_client import embed_texts_batch  # noqa: E402

REST = settings.supabase_rest_url
# Prefer service role key for PATCH operations; fall back to anon when service role isn't seeded.
KEY = settings.supabase_service_role_key or settings.supabase_anon_key
H = {
    "apikey": KEY,
    "Authorization": f"Bearer {KEY}",
    "Content-Type": "application/json",
}


def extract_embed_text(row: dict) -> str:
    """Build the text to embed based on node_type."""
    props = row.get("properties") or {}
    nt = row.get("node_type")

    if nt == "SBDSection":
        # Embed the cleaned section content (limit to ~10K chars)
        return f"{props.get('section_id', '')} {props.get('name', '')}\n\n{(props.get('content_md', '') or '')[:10000]}"

    if nt == "BoQItemSpec":
        # Embed the spec text + discipline tag
        parts = [
            props.get("discipline", ""),
            props.get("sub_discipline", ""),
            props.get("work_type", ""),
            props.get("short_desc", ""),
            props.get("spec_text", "")[:8000],
        ]
        # Include citations to boost retrieval relevance
        cites = props.get("citations") or []
        if cites:
            parts.append("Standards: " + ", ".join(cites))
        return " | ".join(p for p in parts if p)

    if nt == "TechSpecTemplate":
        return " | ".join(filter(None, [
            props.get("discipline", ""),
            props.get("sub_discipline", ""),
            props.get("item_category", ""),
            props.get("typical_short_desc", ""),
            "Samples: " + ", ".join(props.get("sample_short_descs", [])),
            "Standards: " + ", ".join(props.get("expected_citations", [])),
            props.get("retrieval_query_template", ""),
        ]))

    return row.get("label", "") or ""


def fetch_unembedded_batch(node_type: str, limit: int = 100) -> list[dict]:
    r = requests.get(
        f"{REST}/rest/v1/kg_nodes",
        params={
            "select":    "node_id,node_type,label,properties",
            "node_type": f"eq.{node_type}",
            "embedding": "is.null",
            "limit":     str(limit),
        },
        headers={**H, "Range": f"0-{limit-1}"},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


def update_embedding(node_id: str, embedding: list[float]) -> None:
    # pgvector format: '[1.2,3.4,...]' string
    vec_str = "[" + ",".join(f"{v:.7f}" for v in embedding) + "]"
    r = requests.patch(
        f"{REST}/rest/v1/kg_nodes",
        params={"node_id": f"eq.{node_id}"},
        headers=H,
        json={"embedding": vec_str},
        timeout=30,
    )
    r.raise_for_status()


def update_embeddings_batch(updates: list[tuple[str, list[float]]]) -> int:
    """PATCH each row sequentially. Sequential because each row has different vector."""
    count = 0
    for node_id, emb in updates:
        update_embedding(node_id, emb)
        count += 1
    return count


def backfill_for_type(node_type: str, batch_size: int = 25) -> int:
    print(f"\n── Backfilling embeddings for {node_type} ──")
    total = 0
    while True:
        rows = fetch_unembedded_batch(node_type, limit=batch_size)
        if not rows:
            break
        texts = [extract_embed_text(r) for r in rows]
        try:
            embeds = embed_texts_batch(texts)
        except Exception as e:
            print(f"  ! Vertex embed batch failed: {e}")
            # Retry one-by-one
            embeds = []
            for t in texts:
                try:
                    from app.vertex_client import embed_text
                    embeds.append(embed_text(t))
                except Exception as e2:
                    print(f"    individual embed failed: {e2}")
                    embeds.append(None)
        updates = [(r["node_id"], e) for r, e in zip(rows, embeds) if e is not None]
        n = update_embeddings_batch(updates)
        total += n
        print(f"  embedded {total} so far")
        # Rate-limit gently to avoid Vertex API quotas
        time.sleep(0.5)
    print(f"  ✓ {node_type}: {total} embedded")
    return total


def main() -> None:
    print("R7.4 — Backfilling embeddings for SBDSection + BoQItemSpec + TechSpecTemplate")
    print("=" * 76)

    n_sbd = backfill_for_type("SBDSection")
    n_boq = backfill_for_type("BoQItemSpec")
    n_tst = backfill_for_type("TechSpecTemplate")

    print(f"\n  TOTAL embedded: {n_sbd + n_boq + n_tst}")


if __name__ == "__main__":
    main()
