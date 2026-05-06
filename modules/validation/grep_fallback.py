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


def grep_full_source_for_keywords(
    doc_id: str,
    keywords: list[str],
) -> tuple[bool, list[dict]]:
    """Tier-2 whole-file fallback when `grep_source_for_keywords`
    returns empty. Distinguishes a true absence from a KG-coverage
    gap.

    `grep_source_for_keywords` is bounded by Section-node coverage —
    it slices each Section's `[line_start_local, line_end_local]`
    range from disk and searches inside that slice. If the kg_builder
    left a gap in its section parse (e.g. Kakinada SBD lines 59–312
    are not covered by any Section node — `INSTRUCTIONS TO TENDERERS
    (part 1)` ends at line 58 and the next section starts at line
    313), text in that gap is invisible to the Section-bounded grep
    even though it exists in the source markdown.

    This whole-file fallback aggregates the unique `source_file` set
    referenced by ANY Section node for `doc_id`, scans each entire
    file for the keyword vocabulary, and returns a hit list with a
    `kg_coverage_gap` boolean indicating whether each match line falls
    outside every Section node's range.

    A True `kg_coverage_gap` is meaningful audit signal — it tells
    the reviewer "the kg_builder missed indexing this region; the
    finding is genuine but the KG needs a re-build to surface this
    text via Qdrant retrieval next time".

    Each `hits` entry is:
        {
          "source_file":      str,
          "line_no":          int,           # 1-indexed match line
          "keyword_matches":  list[str],
          "snippet":          str,           # ~240 chars
          "kg_coverage_gap":  bool,          # True = no Section covers this line
          "covering_section": dict | None,   # Section ref if covered
        }

    Use after `grep_source_for_keywords` returns empty. If THIS
    fallback also returns empty, the absence is genuine.
    """
    sections = requests.get(
        f"{_REST}/rest/v1/kg_nodes",
        params={"select":   "node_id,properties",
                "doc_id":   f"eq.{doc_id}",
                "node_type": "eq.Section"},
        headers=_H, timeout=30,
    ).json()

    # Build (source_file → list of (ls, le, section_ref)) index for
    # the kg_coverage_gap check.
    by_file: dict[str, list[tuple[int, int, dict]]] = {}
    source_files: set[str] = set()
    for s in sections:
        p = s.get("properties") or {}
        src = p.get("source_file")
        ls  = p.get("line_start_local") or p.get("line_start")
        le  = p.get("line_end_local")   or p.get("line_end")
        if not (src and ls and le):
            continue
        try:
            ls_i, le_i = int(ls), int(le)
        except (TypeError, ValueError):
            continue
        source_files.add(src)
        by_file.setdefault(src, []).append((ls_i, le_i, {
            "section_node_id": s["node_id"],
            "heading":         p.get("heading"),
            "section_type":    p.get("section_type"),
            "line_start_local": ls_i,
            "line_end_local":   le_i,
        }))

    keyword_lc = [kw.lower() for kw in keywords]
    hits: list[dict] = []

    for src in sorted(source_files):
        # Resolve the file across processed_md roots.
        full_text = None
        for root in PROCESSED_MD_ROOTS:
            p = root / src
            if p.exists():
                full_text = p.read_text(encoding="utf-8")
                break
        if full_text is None:
            continue

        text_lc = full_text.lower()
        lines   = full_text.splitlines()

        # Prefix sums of byte offsets per line, for offset → line_no.
        line_offsets = [0]
        running = 0
        for ln in lines:
            running += len(ln) + 1   # +1 for the newline
            line_offsets.append(running)

        def offset_to_line_no(off: int) -> int:
            # Binary search would be faster; linear is fine for the
            # rare absence-fallback path.
            for i in range(1, len(line_offsets)):
                if line_offsets[i] > off:
                    return i      # 1-indexed
            return len(lines)

        for kw_idx, kw_lc in enumerate(keyword_lc):
            idx = text_lc.find(kw_lc)
            if idx < 0:
                continue
            line_no = offset_to_line_no(idx)
            # Determine kg_coverage_gap by checking all Section ranges
            # for this file.
            covering: dict | None = None
            for ls_i, le_i, ref in by_file.get(src, []):
                if ls_i <= line_no <= le_i:
                    covering = ref
                    break

            snippet_start = max(0, idx - 80)
            snippet_end   = min(len(full_text), idx + 160)
            snippet = full_text[snippet_start:snippet_end].replace("\n", " ").strip()
            hits.append({
                "source_file":      src,
                "line_no":          line_no,
                "keyword_matches":  [keywords[kw_idx]],
                "snippet":          snippet,
                "kg_coverage_gap":  covering is None,
                "covering_section": covering,
            })

    return (len(hits) > 0, hits)
