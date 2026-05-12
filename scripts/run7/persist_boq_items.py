"""R7.2 — Persist deduplicated BoQItemSpec records to Supabase.

Reads data/extracted/boq_items_dedup.jsonl (993 items).
Inserts in batches of 50.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from builder.config import settings  # noqa: E402

REST = settings.supabase_rest_url
H = {
    "apikey": settings.supabase_anon_key,
    "Authorization": f"Bearer {settings.supabase_anon_key}",
    "Content-Type": "application/json",
}

SOURCE_REF = "module1:boq_item_spec_v1"
INPUT = REPO / "data" / "extracted" / "boq_items_dedup.jsonl"


def _delete_prior() -> int:
    rows = requests.get(
        f"{REST}/rest/v1/kg_nodes",
        params={"select": "node_id", "node_type": "eq.BoQItemSpec", "source_ref": f"eq.{SOURCE_REF}"},
        headers=H,
        timeout=30,
    ).json()
    for row in rows:
        requests.delete(
            f"{REST}/rest/v1/kg_nodes",
            params={"node_id": f"eq.{row['node_id']}"},
            headers=H,
            timeout=30,
        )
    return len(rows)


def _post(batch: list[dict]) -> list[dict]:
    r = requests.post(
        f"{REST}/rest/v1/kg_nodes",
        json=batch,
        headers={**H, "Prefer": "return=representation"},
        timeout=120,
    )
    r.raise_for_status()
    return r.json()


def main() -> None:
    items = [json.loads(line) for line in INPUT.read_text().splitlines() if line.strip()]
    print(f"R7.2 — Persisting {len(items)} BoQItemSpec rows")

    n_cleaned = _delete_prior()
    if n_cleaned:
        print(f"  cleanup: removed {n_cleaned} prior BoQItemSpec row(s)")

    rows: list[dict] = []
    for i, it in enumerate(items):
        rows.append({
            "doc_id":    f"boq_item_spec_{i:04d}",
            "node_type": "BoQItemSpec",
            "label":     f"BoQ {it['discipline']}/{it['sub_discipline']}: {it['spec_text'][:80].strip()}",
            "properties": {
                "item_pattern_id":  f"hod_mep_{it['sno']:04d}",
                "discipline":       it["discipline"],
                "sub_discipline":   it["sub_discipline"],
                "spec_text":        it["spec_text"],
                "spec_chars":       len(it["spec_text"]),
                "work_type":        it["work_type"],
                "short_desc":       it["short_desc"],
                "apss_cl_no":       it["apss_cl_no"],
                "default_uom":      it["uom"],
                "default_rate_inr": it["rate"],
                "est_qty":          it["est_qty"],
                "amount_inr":       it["amount"],
                "citations":        it["citations"],
                "citation_count":   len(it["citations"]),
                "scale_signals":    it["scale_signals"],
                "dedup_count":      it.get("dedup_count", 1),
                "total_qty":        it.get("total_qty", it["est_qty"]),
                "source_pdf":       it["source_pdf"],
                "source_page":      it["source_page"],
            },
            "source_ref": SOURCE_REF,
        })

    # Insert in batches
    total = 0
    batch_size = 50
    for start in range(0, len(rows), batch_size):
        batch = rows[start:start + batch_size]
        inserted = _post(batch)
        total += len(inserted)
        if (start // batch_size) % 5 == 0:
            print(f"  inserted {total}/{len(rows)}")

    print(f"\n  ✓ Total BoQItemSpec inserted: {total}")


if __name__ == "__main__":
    main()
