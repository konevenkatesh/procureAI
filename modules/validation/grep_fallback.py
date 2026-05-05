"""
modules/validation/grep_fallback.py

L36 source-grep fallback — typology-agnostic safety net for
presence-shape Tier-1 scripts. When the LLM rerank's top-K returns
no candidate (the ABSENCE branch), this module exhaustively greps
across the doc's full section_filter coverage (NOT just the
retrieved top-K) for typology-specific keywords. If any keyword
matches, the absence is downgraded to UNVERIFIED — likely a
retrieval-coverage gap (the section that holds the clause didn't
rank in top-K), not a real bypass.

Why this isn't redundant with the BGE-M3 + Qdrant top-K retrieval:
BGE-M3 ranks sections by semantic similarity and the script caps
at top-10. On long-tail docs with many ITB / Forms / GCC sections,
a clause that contains the exact keyword but is short or buried in
a larger section may rank below top-K. This grep is exhaustive
across the section_filter — slower but complete. Only invoked on
the ABSENCE path (already a rare outcome), so the cost is bounded.

Lifted from `tier1_blacklist_check.py` (L36 origin) and
`tier1_bg_validity_gap_check.py` after both copies proved out the
contract. Now shared so PVC / IP / LD / E-Proc can inherit the
same safety net without copy-paste.

Public API — exactly one function:

    grep_source_for_keywords(doc_id, section_types, keywords)
        → (any_hit: bool, hits: list[dict])

Each `hits` entry is:
    {
      "section_node_id":   str,
      "heading":           str,
      "source_file":       str,
      "line_start_local":  int,
      "line_end_local":    int,
      "section_type":      str,
      "keyword_matches":   list[str],   # subset of input keywords
      "snippet":           str,         # ~240 chars around first match
    }

Caller stores `hits[:10]` as `grep_fallback_audit` JSONB on the
UNVERIFIED ValidationFinding so the human reviewer can open the
listed sections directly.
"""
from __future__ import annotations

from pathlib import Path

import requests

from builder.config import settings


# Repo root is three parents up from this file
# (modules/validation/grep_fallback.py → modules/ → repo root).
_REPO = Path(__file__).resolve().parent.parent.parent

# Same set of processed_md roots all Tier-1 scripts use.
PROCESSED_MD_ROOTS = (
    _REPO / "source_documents" / "e_procurement" / "processed_md",
    _REPO / "source_documents" / "sample_tenders" / "processed_md",
)

_REST = settings.supabase_rest_url
_H = {"apikey":        settings.supabase_anon_key,
      "Authorization": f"Bearer {settings.supabase_anon_key}"}


def _slice_source_file(filename: str, ls: int, le: int) -> str:
    """Read lines [ls, le] from the named processed_md file. Tries
    both PROCESSED_MD_ROOTS in order. Raises FileNotFoundError if
    the filename doesn't resolve."""
    for root in PROCESSED_MD_ROOTS:
        p = root / filename
        if p.exists():
            lines = p.read_text(encoding="utf-8").splitlines()
            ls_i = max(1, int(ls))
            le_i = min(len(lines), int(le))
            return "\n".join(lines[ls_i - 1:le_i])
    raise FileNotFoundError(filename)


def grep_source_for_keywords(
    doc_id: str,
    section_types: list[str],
    keywords: list[str],
) -> tuple[bool, list[dict]]:
    """Exhaustive case-insensitive grep across the doc's
    section_filter coverage.

    Pulls every Section node for `doc_id` whose `section_type` is in
    `section_types`, slices each section's full_text from disk,
    case-insensitively searches each section for any of `keywords`,
    and returns (any_hit, hits_list).

    `keywords` is interpreted as plain-substring matchers (case-
    insensitive). Use short, unambiguous strings — e.g. for PVC
    `["price variation", "price adjustment", "escalation"]`. The
    keyword vocabulary is typology-specific; the helper is shape-
    agnostic.

    The snippet is ~240 chars around the first match (80 chars
    before + 160 chars after). Newlines are stripped for compactness.
    """
    sections = requests.get(
        f"{_REST}/rest/v1/kg_nodes",
        params={"select":   "node_id,properties",
                "doc_id":   f"eq.{doc_id}",
                "node_type": "eq.Section"},
        headers=_H, timeout=30,
    ).json()
    filtered = [
        s for s in sections
        if (s.get("properties") or {}).get("section_type") in section_types
    ]

    hits: list[dict] = []
    keyword_lc = [kw.lower() for kw in keywords]

    for s in filtered:
        p = s.get("properties") or {}
        source_file = p.get("source_file")
        ls          = p.get("line_start_local") or p.get("line_start")
        le          = p.get("line_end_local")   or p.get("line_end")
        if not (source_file and ls and le):
            continue
        try:
            full_text = _slice_source_file(source_file, ls, le)
        except FileNotFoundError:
            continue
        text_lc = full_text.lower()
        matched_kws = [keywords[i] for i, kw_lc in enumerate(keyword_lc)
                        if kw_lc in text_lc]
        if not matched_kws:
            continue
        first_kw_lc = matched_kws[0].lower()
        idx = text_lc.find(first_kw_lc)
        snippet_start = max(0, idx - 80)
        snippet_end   = min(len(full_text), idx + 160)
        snippet = full_text[snippet_start:snippet_end].replace("\n", " ").strip()
        hits.append({
            "section_node_id":   s["node_id"],
            "heading":           p.get("heading"),
            "source_file":       source_file,
            "line_start_local":  ls,
            "line_end_local":    le,
            "section_type":      p.get("section_type"),
            "keyword_matches":   matched_kws,
            "snippet":           snippet,
        })

    return (len(hits) > 0, hits)
