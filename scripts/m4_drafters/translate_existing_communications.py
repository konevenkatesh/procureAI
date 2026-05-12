"""M4.4 batch: translate existing Communications' content_en → content_te.

Iterates all Communications; for bidder-facing communications, calls
Sarvam-M /translate via _sarvam_client; updates Communication kg_node:
  - content_te = translated text (PII restored after pseudonymisation)
  - content_te_status = "rendered_via_sarvam_m" / "translation_failed" / "english_only_internal"
  - language = "EN+TE" for bidder-facing translated; "EN" for internal

Idempotent — cache means re-runs cost zero API calls after first run.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

import requests  # noqa: E402

from scripts.m4_drafters._common import (  # noqa: E402
    REST, H, rest_get_range, get_bidder_profile, get_tender_info,
    snapshot_sentinels, assert_sentinel_preserved,
)
from scripts.m4_drafters._sarvam_client import (  # noqa: E402
    translate_to_telugu, verify_no_pii_in_text,
)


# Bidder-facing communication types (per M4.1 spec §4 + run-2 directive)
BIDDER_FACING_TYPES = {
    "DISQUALIFICATION", "AWARD", "ALB_JUSTIFICATION",
    "FLAGGED", "DOC_REVIEW", "REGRET", "BID_ACK",
    "BIDDER_CLARIFICATION_QA",
}

# Internal-only communication types — stay English
INTERNAL_TYPES = {"CARTEL_REVIEW", "INTERNAL_ROUTING"}


def fetch_all_communications() -> list[dict]:
    return rest_get_range("kg_nodes", {
        "select": "node_id,doc_id,properties",
        "node_type": "eq.Communication",
    })


def patch_communication_properties(node_id: str, props_update: dict) -> None:
    """Fetch existing properties, merge update, PATCH full properties.
    (PostgREST doesn't natively merge nested JSONB — L86 pattern.)"""
    r = requests.get(f"{REST}/rest/v1/kg_nodes",
                     params={"select": "properties", "node_id": f"eq.{node_id}"},
                     headers=H, timeout=30).json()
    if not r:
        return
    props = r[0]["properties"] or {}
    props.update(props_update)
    requests.patch(f"{REST}/rest/v1/kg_nodes",
                   params={"node_id": f"eq.{node_id}"},
                   headers={**H, "Content-Type": "application/json"},
                   json={"properties": props}, timeout=30)


def main() -> int:
    t0 = time.perf_counter()
    print("=" * 76)
    print("  M4.4 — Translate existing Communications to Telugu (Sarvam-M)")
    print("=" * 76)

    sentinel_pre = snapshot_sentinels()
    print(f"\n── Pre sentinel ──")
    for k, v in sentinel_pre.items():
        print(f"  {k:25s}: {v}")

    comms = fetch_all_communications()
    print(f"\n── {len(comms)} Communication kg_nodes ──")

    n_translated = 0
    n_internal_skipped = 0
    n_already_translated = 0
    n_api_calls_total = 0
    n_cache_hits_total = 0
    pii_leaks: list[str] = []

    for c in comms:
        p = c["properties"] or {}
        ctype = p.get("communication_type", "?")
        bidder_id = p.get("recipient_bidder_profile_id")
        tender_id = p.get("tender_id")
        content_en = p.get("content_en") or ""

        if ctype in INTERNAL_TYPES:
            print(f"  ─ {ctype:24s} (internal, EN-only) — skip")
            patch_communication_properties(c["node_id"], {
                "content_te_status": "english_only_internal",
                "language":          "EN",
            })
            n_internal_skipped += 1
            continue
        if ctype not in BIDDER_FACING_TYPES:
            print(f"  ⚠ {ctype}: unknown type, skip")
            continue

        if p.get("content_te"):
            n_already_translated += 1
            print(f"  ✓ {ctype:24s} {bidder_id[-3:] if bidder_id else '':3s}  "
                  f"already translated, skip")
            continue

        # Look up bidder profile for pseudonymisation
        profile_node = get_bidder_profile(bidder_id) if bidder_id else {}
        profile = profile_node.get("properties") or {}
        tinfo = get_tender_info(tender_id) if tender_id else None

        result = translate_to_telugu(content_en, profile, tinfo)
        translated_te = result["translated_text"]

        # DPDP audit: verify no pseudonymisation tokens leaked into output
        leaks = verify_no_pii_in_text(translated_te, profile)
        if leaks:
            pii_leaks.append(f"{c['node_id']}: {leaks}")

        patch_communication_properties(c["node_id"], {
            "content_te":         translated_te,
            "content_te_status":  "rendered_via_sarvam_m",
            "language":           "EN+TE",
        })

        tender_key = (tender_id or "").replace("tender_synth_", "")
        bidder_key = (bidder_id or "").replace("bid_synth_profile_", "")
        print(f"  ✓ {ctype:24s} {bidder_key:8s} × {tender_key:8s}  "
              f"chunks={result['n_chunks']} api_calls={result['n_api_calls']} "
              f"cache_hits={result['n_cache_hits']} "
              f"te_length={len(translated_te)}")
        n_translated += 1
        n_api_calls_total += result["n_api_calls"]
        n_cache_hits_total += result["n_cache_hits"]

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
    print(f"  Summary: {n_translated} translated, {n_already_translated} skipped (already), "
          f"{n_internal_skipped} internal (EN-only)")
    print(f"  Sarvam API calls: {n_api_calls_total}, cache hits: {n_cache_hits_total}")
    print(f"  PII leaks detected: {len(pii_leaks)} {pii_leaks if pii_leaks else '(none)'}")
    print(f"  Wall: {wall:.2f}s")
    print("=" * 76)

    if pii_leaks:
        print("\n✗ PII LEAK DETECTED — investigate; DPDP discipline broken")
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
