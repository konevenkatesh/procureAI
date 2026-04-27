"""
Read rule_reviews + reviewers from Supabase via REST and print a progress
summary: total reviews, unique rules reviewed, breakdown by reviewer,
breakdown by action (approved/skipped/modified), audio attachments count.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict

import requests

from builder.config import settings


def _get(table: str) -> list[dict]:
    url = f"{settings.supabase_rest_url}/rest/v1/{table}?select=*"
    headers = {
        "apikey": settings.supabase_anon_key,
        "Authorization": f"Bearer {settings.supabase_anon_key}",
    }
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()


def main() -> int:
    reviews = _get("rule_reviews")
    reviewers = {r["id"]: r for r in _get("reviewers")}

    print(f"\n=== Rule review progress (Supabase) ===\n")
    print(f"Total review records:    {len(reviews)}")
    print(f"Unique rules reviewed:   {len({r['rule_id'] for r in reviews})}")
    print(f"Registered reviewers:    {len(reviewers)}")
    print(f"Audio submissions:       {sum(1 for r in reviews if r.get('audio_url'))}")

    by_action = Counter(r["action"] for r in reviews)
    print(f"\n--- By action ---")
    for act, n in by_action.most_common():
        print(f"  {act:<12s} {n:>3d}")

    print(f"\n--- By reviewer ---")
    by_reviewer = defaultdict(list)
    for r in reviews:
        by_reviewer[r["reviewer_id"]].append(r)
    for rid, rs in sorted(by_reviewer.items(), key=lambda kv: -len(kv[1])):
        info = reviewers.get(rid, {})
        name = info.get("full_name") or "?"
        org = info.get("organization") or "—"
        email = info.get("email") or "—"
        unique = len({x["rule_id"] for x in rs})
        actions = Counter(x["action"] for x in rs)
        actions_str = ", ".join(f"{a}={n}" for a, n in actions.most_common())
        print(f"  {name} ({org})")
        print(f"    {email}")
        print(f"    {len(rs)} reviews / {unique} unique rules — {actions_str}")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
