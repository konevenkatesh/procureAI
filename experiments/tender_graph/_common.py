"""
experiments/tender_graph/_common.py

Shared helpers across all step scripts.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure repo root on sys.path so absolute imports resolve when run as script
REPO = Path(__file__).resolve().parent.parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from builder.config import settings


# Constant — used in EVERY step's WHERE clause
DOC_ID = "vizag_ugss_exp_001"
DOC_NAME = "Vizag UGSS Pkg-2"

SOURCE_FILES = [
    REPO / "source_documents" / "e_procurement" / "processed_md" /
        "1_Volume_I_NIT_&_Bidding_Process.md",
    REPO / "source_documents" / "e_procurement" / "processed_md" /
        "2 VOLUME II Scope of work.md",
    REPO / "source_documents" / "e_procurement" / "processed_md" /
        "3 Volume_III _GCC,_SCC.md",
    REPO / "source_documents" / "e_procurement" / "processed_md" /
        "3.3A_Schedules.md",
    REPO / "source_documents" / "e_procurement" / "processed_md" /
        "4 VOLUME IV Bill of Quantiites.md",
]


# ── Supabase REST helpers (uses anon key; RLS off on experiment tables) ──

def rest_url(table: str) -> str:
    return f"{settings.supabase_rest_url}/rest/v1/{table}"


def rest_headers(*, prefer: str | None = None) -> dict:
    h = {
        "apikey":        settings.supabase_anon_key,
        "Authorization": f"Bearer {settings.supabase_anon_key}",
        "Content-Type":  "application/json",
    }
    if prefer:
        h["Prefer"] = prefer
    return h


def rest_select(table: str, *, params: dict | None = None,
                page_size: int = 1000) -> list[dict]:
    """GET /rest/v1/{table}?{params}. Paginates with Range header until
    the server returns fewer than `page_size` rows. PostgREST caps at
    1000 by default so a single Range request misses larger result sets."""
    import requests
    out: list[dict] = []
    offset = 0
    while True:
        headers = rest_headers()
        headers["Range-Unit"] = "items"
        headers["Range"] = f"{offset}-{offset + page_size - 1}"
        r = requests.get(rest_url(table), params=params or {},
                          headers=headers, timeout=30)
        r.raise_for_status()
        batch = r.json()
        out.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
    return out


def rest_insert(table: str, rows: list[dict]) -> list[dict]:
    """POST /rest/v1/{table} with `Prefer: return=representation` so
    the auto-generated id comes back."""
    import requests
    if not rows:
        return []
    r = requests.post(
        rest_url(table),
        json=rows,
        headers=rest_headers(prefer="return=representation"),
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


def rest_delete_doc(table: str, doc_id: str) -> int:
    """DELETE rows for a doc_id. Returns count deleted (0 if Prefer=count
    not supported — we use Prefer=count=exact)."""
    import requests
    headers = rest_headers(prefer="count=exact")
    r = requests.delete(
        rest_url(table),
        params={"doc_id": f"eq.{doc_id}"},
        headers=headers,
        timeout=60,
    )
    r.raise_for_status()
    cr = r.headers.get("Content-Range", "")
    if "/" in cr:
        try:
            return int(cr.split("/")[1])
        except Exception:
            return 0
    return 0
