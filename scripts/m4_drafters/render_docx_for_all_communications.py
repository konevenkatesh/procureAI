"""M4.3 batch: render DOCX for every Communication kg_node.

Iterates all Communication rows; for each, reads content_en + artifact_path_md;
renders DOCX next to the .md (same basename); updates kg_node
properties.artifact_path_docx field via fetch-modify-patch.

Idempotent — re-running overwrites DOCX files + the kg_node field.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from scripts.m4_drafters._common import (  # noqa: E402
    rest_get_range, render_docx_for_communication, snapshot_sentinels,
    assert_sentinel_preserved,
)


def main() -> int:
    t0 = time.perf_counter()
    print("=" * 76)
    print("  M4.3 — render DOCX artifacts for all Communication kg_nodes")
    print("=" * 76)

    sentinel_pre = snapshot_sentinels()
    print(f"\n── Pre sentinel ──")
    for k, v in sentinel_pre.items():
        print(f"  {k:25s}: {v}")

    rows = rest_get_range("kg_nodes", {
        "select": "node_id,properties",
        "node_type": "eq.Communication",
    })
    print(f"\n── {len(rows)} Communication kg_nodes ──")

    n_rendered = 0
    for c in rows:
        node_id = c["node_id"]
        p = c["properties"] or {}
        ctype = p.get("communication_type", "?")
        artifact_md = p.get("artifact_path_md")
        content_en = p.get("content_en") or ""
        if not artifact_md or not content_en:
            print(f"  ⚠ skipping {node_id}: no md path or content")
            continue
        bidder_key = (p.get("recipient_bidder_profile_id") or "").replace("bid_synth_profile_", "")
        tender_key = (p.get("tender_id") or "").replace("tender_synth_", "")
        title = f"{ctype} — {p.get('bidder_name', bidder_key)[:60]} × {p.get('tender_name', tender_key)[:40]}"
        docx_path = render_docx_for_communication(node_id, content_en, artifact_md, title)
        print(f"  ✓ {ctype:18s} {bidder_key:8s} × {tender_key:8s}  → {docx_path}")
        n_rendered += 1

    sentinel_post = snapshot_sentinels()
    print(f"\n── Post sentinel ──")
    for k, v in sentinel_post.items():
        delta = v - sentinel_pre[k]
        marker = "" if delta == 0 else f" (Δ={delta:+d})"
        print(f"  {k:25s}: {v}{marker}")
    assert_sentinel_preserved(sentinel_pre, sentinel_post, excluded_keys=())

    wall = time.perf_counter() - t0
    print()
    print("=" * 76)
    print(f"  M4.3 complete — {n_rendered} DOCX rendered in {wall:.2f}s; sentinel preserved")
    print("=" * 76)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
