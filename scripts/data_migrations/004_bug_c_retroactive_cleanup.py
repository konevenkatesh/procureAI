"""004 — Bug C retroactive cleanup (Phase 2A).

One-time data migration that aligns the original 6 Bug C migrations
(commit edc68bd: PBG / EMD / BV / LD / MII / JP) with the severity-
aware tagging standard established in expansion Batches 1-3.

Two fixes:

  L58 — severity-tagging on original 6
    Before: HARD_BLOCK-severity rules were tagged
            verdict='GAP_VIOLATION' because the original migration
            used a binary `verdict = "UNVERIFIED" if is_unverified
            else "GAP_VIOLATION"` mapping that ignored severity.
    After:  rows where severity='HARD_BLOCK' AND verdict='GAP_VIOLATION'
            are flipped to verdict='HARD_BLOCK'. UUIDs and all other
            properties preserved.
    Affected: 11 rows (5 JP + 6 MII).

  L59 — Mandatory-Fields per-sub-check failure_path discriminator
    Before: 9 sub-check UNVERIFIED rows had verdict='UNVERIFIED' but
            failure_path=NULL because they routed through the
            _materialise_finding auto-injection helper which sets
            verdict but not failure_path.
    After:  failure_path derived from each row's evidence_match_method
            audit field (all 9 carry _l36_grep_promoted or
            _l40_grep_promoted markers, mapping to the canonical
            'retrieval_coverage_gap'). UUIDs preserved.
    Affected: 9 rows.

Idempotent: re-running this script after the fix is safe — both
queries return zero matching rows post-fix, so the loops are no-ops.

Run via:
    set -a && . ./.env && set +a
    /opt/homebrew/bin/python3.11 scripts/data_migrations/004_bug_c_retroactive_cleanup.py
"""
from __future__ import annotations
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

import requests
from builder.config import settings


REST = settings.supabase_rest_url
H = {"apikey": settings.supabase_anon_key,
     "Authorization": f"Bearer {settings.supabase_anon_key}"}


# ── L58 ──────────────────────────────────────────────────────────────

ORIGINAL_6 = ["PBG-Shortfall", "EMD-Shortfall", "Bid-Validity-Short",
              "Missing-LD-Clause", "MakeInIndia-LCC-Missing",
              "Judicial-Preview-Bypass"]


def apply_l58() -> int:
    """For each ValidationFinding row from the original 6 typologies
    where severity='HARD_BLOCK' AND verdict='GAP_VIOLATION', flip
    verdict to 'HARD_BLOCK'. Preserves UUID and all other props."""
    targets: list[dict] = []
    for typ in ORIGINAL_6:
        r = requests.get(f"{REST}/rest/v1/kg_nodes", params={
            "node_type":                "eq.ValidationFinding",
            "properties->>typology_code": f"eq.{typ}",
            "properties->>verdict":      "eq.GAP_VIOLATION",
            "properties->>severity":     "eq.HARD_BLOCK",
            "select":                    "node_id,properties",
        }, headers=H)
        targets.extend(r.json())

    print(f"L58: found {len(targets)} inconsistent rows")
    updated = 0
    for row in targets:
        nid = row["node_id"]
        props = dict(row.get("properties") or {})
        props["verdict"] = "HARD_BLOCK"
        r = requests.patch(
            f"{REST}/rest/v1/kg_nodes",
            params={"node_id": f"eq.{nid}"},
            json={"properties": props},
            headers={**H, "Content-Type": "application/json",
                     "Prefer": "return=representation"},
            timeout=30,
        )
        if r.ok:
            updated += 1
        else:
            print(f"  FAIL {nid}: {r.status_code} {r.text[:200]}")
    print(f"L58: updated {updated}/{len(targets)} rows")
    return updated


# ── L59 ──────────────────────────────────────────────────────────────

def apply_l59() -> int:
    """For each Mandatory-Fields ValidationFinding row with
    verdict='UNVERIFIED' AND failure_path=NULL, derive failure_path
    from evidence_match_method audit field. Maps to canonical
    `retrieval_coverage_gap` for grep-promoted rows; preserves UUIDs."""
    r = requests.get(f"{REST}/rest/v1/kg_nodes", params={
        "node_type":                  "eq.ValidationFinding",
        "properties->>typology_code": "eq.Works-Universal-Mandatory-Fields",
        "properties->>verdict":       "eq.UNVERIFIED",
        "properties->>failure_path":  "is.null",
        "select":                     "node_id,properties",
    }, headers=H)
    targets = r.json()
    print(f"L59: found {len(targets)} Mandatory-Fields UNVERIFIED rows "
          f"with unset failure_path")

    updated = 0
    distribution: dict[str, int] = {}
    for row in targets:
        nid = row["node_id"]
        props = dict(row.get("properties") or {})
        method = (props.get("evidence_match_method") or "")
        reason = (props.get("violation_reason") or "")
        if "grep_promoted" in method or "grep_promoted" in reason:
            failure_path = "retrieval_coverage_gap"
        elif "l24" in method.lower() or "L24" in reason:
            failure_path = "L24_evidence_guard"
        elif "rule_lookup_missing" in method or "rule_lookup_missing" in reason:
            failure_path = "rule_lookup_missing"
        else:
            failure_path = "unknown_legacy_batch1"
        distribution[failure_path] = distribution.get(failure_path, 0) + 1
        props["failure_path"] = failure_path
        r = requests.patch(
            f"{REST}/rest/v1/kg_nodes",
            params={"node_id": f"eq.{nid}"},
            json={"properties": props},
            headers={**H, "Content-Type": "application/json",
                     "Prefer": "return=representation"},
            timeout=30,
        )
        if r.ok:
            updated += 1
        else:
            print(f"  FAIL {nid}: {r.status_code} {r.text[:200]}")
    print(f"L59: updated {updated}/{len(targets)} rows")
    print(f"L59: failure_path assignment distribution: {distribution}")
    return updated


def verify() -> None:
    """Post-fix verification."""
    H2 = dict(H, **{"Prefer": "count=exact"})
    r = requests.get(f"{REST}/rest/v1/kg_nodes", params={
        "node_type": "eq.ValidationFinding",
        "select":    "doc_id", "limit": "1",
    }, headers=H2)
    print(f"\nVerify: ValidationFinding total — {r.headers.get('Content-Range')}")

    n_inconsistent = 0
    for typ in ORIGINAL_6:
        r = requests.get(f"{REST}/rest/v1/kg_nodes", params={
            "node_type":                  "eq.ValidationFinding",
            "properties->>typology_code": f"eq.{typ}",
            "properties->>verdict":       "eq.GAP_VIOLATION",
            "properties->>severity":      "eq.HARD_BLOCK",
            "select":                     "node_id",
        }, headers=H)
        n_inconsistent += len(r.json())
    print(f"Verify: L58 inconsistent rows remaining — {n_inconsistent} (target 0)")

    r = requests.get(f"{REST}/rest/v1/kg_nodes", params={
        "node_type":                  "eq.ValidationFinding",
        "properties->>typology_code": "eq.Works-Universal-Mandatory-Fields",
        "properties->>verdict":       "eq.UNVERIFIED",
        "properties->>failure_path":  "is.null",
        "select":                     "node_id",
    }, headers=H)
    print(f"Verify: L59 Mandatory-Fields rows with unset failure_path — "
          f"{len(r.json())} (target 0)")


if __name__ == "__main__":
    print("Running 004 — Bug C retroactive cleanup (L58 + L59)")
    print("=" * 70)
    n_l58 = apply_l58()
    print()
    n_l59 = apply_l59()
    verify()
    print(f"\nTotal rows updated: {n_l58 + n_l59}")
