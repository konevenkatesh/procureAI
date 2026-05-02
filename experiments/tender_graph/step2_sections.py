"""
experiments/tender_graph/step2_sections.py

Step 2 — Section processing for Vizag UGSS Pkg-2.

Pipeline:
    1. For each source file, run the existing builder.section_splitter on
       the full text → list of (reference, body) pairs.
    2. Classify each section's section_type by simple keyword matching on
       the heading. Taxonomy aligned with clause_templates.position_section
       so Step 3's two-pass match has a clean filter:
           NIT, ITB, Datasheet, Evaluation, Forms, GCC, SCC,
           Scope, Specifications, BOQ
    3. Store the COMPLETE (untruncated) section text in PostgreSQL via
       Supabase REST.
    4. Embed each section via VectorChecker (BGE-M3) and upsert into the
       shared `tender_sections` Qdrant collection with section_type and
       postgresql_id added to the payload.
"""
from __future__ import annotations

import re
import time
import uuid
from pathlib import Path

from _common import (
    DOC_ID, DOC_NAME, REPO, SOURCE_FILES,
    rest_select, rest_insert, rest_delete_doc,
)


# ── 1. Section-type classifier ──────────────────────────────────────────
#
# Design (Fix 1, post-Tirupathi generalisation test):
#
#   Heading-content is the PRIMARY signal. For each section, we test the
#   section's own heading (and, if needed, its first 200 chars of body)
#   against a set of type-word patterns. When a section hits a pattern
#   we LATCH that type as `current` and propagate it to subsequent
#   sub-clause sections (which usually have headings like "Force Majeure"
#   or "(ii) limit in any substantial way..." that don't themselves
#   carry a type word).
#
#   Filename hint is a STARTER DEFAULT — it pre-fills `current` before
#   the first explicit match, so e.g. Vizag Vol III's pre-SCC sections
#   inherit GCC even though no section heading literally says "General
#   Conditions of Contract" (the file IS the GCC chapter).
#
#   Roman-numeral patterns (`Section I`, `Section II`) are intentionally
#   NOT in the rule set: in Vizag Section I = ITB but in Tirupathi
#   Section I = INTRODUCTION. Type words ("Instructions to Bidders",
#   "Eligibility and Qualification") are stable across doc shapes; bare
#   Roman numerals are not.
#
# Type-word rules — order matters. The most-specific patterns come first
# so generic phrases (e.g. "letter of invitation" → NIT) don't shadow
# more discriminating type words ("eligibility and qualification" →
# Evaluation) when both happen to appear in the same heading.

_HEADING_OVERRIDE_RULES: list[tuple[str, list[str]]] = [
    ("Datasheet", [
        r"\bbds\b",
        r"bid\s+data\s+sheet",
    ]),
    ("Evaluation", [
        r"evaluation\s+and\s+qualification",
        r"\beligibility\s+and\s+qualification",
        r"qualification\s+criteria",
    ]),
    ("Forms", [
        r"bidding\s+forms?",
        r"letter\s+of\s+bid\b",
        r"power\s+of\s+attorney",
        r"bank\s+guarantee\s+for",
        r"^\s*annex(?:ure)?\s+\d",          # ANNEX 1 / ANNEXURE 2
        r"^\s*format\s+of\s+bid\s+letter",
        r"^\s*bidding\s+form\b",
    ]),
    ("GCC", [
        r"general\s+conditions?\s+of\s+(?:the\s+)?contract",
        r"^\s*GCC\b",
    ]),
    ("SCC", [
        r"special\s+conditions?\s+of\s+(?:the\s+)?contract",
        r"particular\s+conditions?\s+of\s+(?:the\s+)?contract",
        r"^\s*SCC\b",
    ]),
    ("ITB", [
        r"instruction[s]?\s+to\s+bidder",
        r"^\s*ITB\b",
    ]),
    ("Scope", [
        r"scope\s+of\s+(?:work|services?|the\s+project|rfp)",
        r"works.{0,3}requirements?",
        r"employer.{0,4}requirements?",
        r"project\s+description",
    ]),
    ("BOQ", [
        r"bill\s+of\s+quantit",
        r"^\s*boq\b",
        r"price\s+schedule",
    ]),
    ("Specifications", [
        r"technical\s+specification",
        r"^\s*specifications?\s*$",
    ]),
    ("NIT", [
        r"notice\s+inviting\s+(?:tender|bid)",
        r"\bnit\b",
        r"invitation\s+for\s+bid",
        r"letter\s+of\s+invitation",
        r"request\s+for\s+proposal\b",
        r"\brfp\b",
    ]),
]

_HEADING_OVERRIDE_COMPILED: list[tuple[str, list[re.Pattern]]] = [
    (stype, [re.compile(p, re.IGNORECASE) for p in pats])
    for stype, pats in _HEADING_OVERRIDE_RULES
]


def classify_heading_override(text: str) -> str | None:
    """Return the section_type whose pattern set matches `text` first,
    or None. Caller passes either a section heading or the first ~200
    chars of body."""
    if not text:
        return None
    for stype, patterns in _HEADING_OVERRIDE_COMPILED:
        for pat in patterns:
            if pat.search(text):
                return stype
    return None


def _filename_default(source_file: str) -> str | None:
    """Filename-hint default for a file's sections. Pre-fills `current`
    so sub-clause sections that precede the first explicit type-word
    heading still inherit a sensible type. Returns None if filename
    gives no clue (Tirupathi-shape docs)."""
    fname = source_file.upper()
    # Order matters: "Volume I" prefixes "Volume III"
    if "VOLUME_III" in fname or "VOLUME III" in fname:
        return "GCC"   # Volume III (Vizag) is GCC + SCC; SCC latches via heading
    if "VOLUME IV" in fname or "VOLUME_IV" in fname:
        return "BOQ"
    if "VOLUME II " in fname or "VOLUME_II_" in fname or fname.startswith("2 VOLUME II"):
        return "Scope"
    if (fname.startswith("1 VOLUME I") or fname.startswith("VOLUME_I_")
            or fname.startswith("1_VOLUME_I_")):
        return "NIT"   # Vol I starts with NIT preamble; latches forward to ITB/BDS/...
    if "SCHEDULES" in fname:
        return "Forms"
    return None


# Per-default-file: which transitions are PLAUSIBLE within this file?
# Without this constraint, a GCC volume's `# SCOPE OF THE PROJECT`
# heading (which is GCC clause 5's title) gets re-classified as the
# Scope chapter type — which is wrong. The taxonomy describes which
# CHAPTER OF A TENDER a section belongs to, not which procurement word
# a heading uses.
#
# `None` value means no constraint (e.g. Tirupathi-shape files where
# we have no filename hint and must trust whatever the headings say).
_ALLOWED_TRANSITIONS: dict[str | None, set[str] | None] = {
    "GCC":            {"GCC", "SCC"},
    "SCC":            {"GCC", "SCC"},
    "Scope":          {"Scope", "Specifications"},   # Scope chapters sometimes embed Specs
    "Specifications": {"Scope", "Specifications"},
    "BOQ":            {"BOQ"},
    "NIT":            {"NIT", "ITB", "Datasheet", "Evaluation", "Forms"},
    "ITB":            {"NIT", "ITB", "Datasheet", "Evaluation", "Forms"},
    "Datasheet":      {"NIT", "ITB", "Datasheet", "Evaluation", "Forms"},
    "Evaluation":     {"NIT", "ITB", "Datasheet", "Evaluation", "Forms"},
    "Forms":          {"NIT", "ITB", "Datasheet", "Evaluation", "Forms"},
    None:             None,    # no constraint
}


def _allowed(transition_to: str, fname_default: str | None) -> bool:
    allowed = _ALLOWED_TRANSITIONS.get(fname_default)
    if allowed is None:
        return True
    return transition_to in allowed


def _is_heading_shaped(line: str) -> bool:
    """True if a single line looks like a chapter header — restrictive
    enough to reject inline mentions like '4. Bill of Quantities' or
    'Schedule-H Bill of Quantities' that appear in body text without
    being chapter-defining.

    Bold-underscore lines must contain ALL CAPS content to qualify as
    chapter headers — Vol III has both
       line 1021: `__Special Conditions of the Contract__` (TOC ref)
       line 2828: `__SPECIAL CONDITIONS OF THE CONTRACT__` (chapter)
    Without this constraint, line 1021 would prematurely latch SCC for
    the entire middle of the GCC body.
    """
    s = line.strip()
    if not s or len(s) > 100:
        return False
    if s.startswith("#"):
        return True
    if s.startswith("__") and s.endswith("__"):
        inner = s.strip("_").strip()
        return inner.isupper() and len(inner) >= 5
    if s.isupper() and 5 <= len(s) <= 100:
        return True
    return False


def _scan_chapter_markers(file_text: str) -> list[tuple[int, str]]:
    """Find every line in the file that (a) looks like a chapter header
    and (b) matches one of the type-word patterns. Returns
    [(line_no, type), ...] in file order.

    Used as a SUPPLEMENT to per-section heading classification: the
    `__SPECIAL CONDITIONS OF THE CONTRACT__` line at Vizag Vol III
    line 2828 falls inside an "ARTICLE 29 (part 1)" section whose
    heading doesn't carry a type word — without this scan, the SCC
    boundary is invisible to the section-level classifier."""
    out: list[tuple[int, str]] = []
    if not file_text:
        return out
    for line_no, line in enumerate(file_text.split("\n"), start=1):
        if not _is_heading_shaped(line):
            continue
        cleaned = re.sub(r"^[#_*\s]+|[#_*\s]+$", "", line.strip())
        hit = classify_heading_override(cleaned)
        if hit is not None:
            out.append((line_no, hit))
    return out


def classify_sections(
    sections: list[dict], source_file: str, file_text: str = ""
) -> list[str]:
    """Walk sections in document order. For each section, the latched
    `current` type is updated by, in priority order:

       1. Any chapter-marker lines (heading-shaped + type-word match)
          whose line_no falls within this section's [line_start, line_end].
          Catches in-body chapter heads the splitter merged into the
          previous section.
       2. The section's own heading matched against type-word patterns.
          Highest priority; overrides any chapter marker.

    Both transitions are filtered through `_ALLOWED_TRANSITIONS[
    fname_default]` so a GCC-volume file's `# SCOPE OF THE PROJECT`
    heading (which is GCC clause 5's title, not a chapter-type change)
    cannot reclassify the file as Scope-type.

    `current` initialises to the filename-default (NIT for Vol I, GCC
    for Vol III, etc.) so sub-clause sections that precede the first
    explicit type-word heading still inherit a sensible type.

    Returns one section_type per input section (same order)."""
    fname_default = _filename_default(source_file)
    chapter_markers = _scan_chapter_markers(file_text)
    current: str | None = fname_default
    marker_idx = 0
    out: list[str] = []
    for s in sections:
        line_start = int(s.get("line_start", 0) or 0)
        line_end   = int(s.get("line_end",   line_start) or line_start)
        # Consume markers whose line_no is within this section's range,
        # subject to the per-file allowed-transitions set.
        while (marker_idx < len(chapter_markers)
                and chapter_markers[marker_idx][0] <= line_end):
            candidate = chapter_markers[marker_idx][1]
            if _allowed(candidate, fname_default):
                current = candidate
            marker_idx += 1
        # Section heading wins over inherited / chapter-marker state,
        # also subject to allowed transitions.
        hit = classify_heading_override(s.get("heading", ""))
        if hit is not None and _allowed(hit, fname_default):
            current = hit
        out.append(current or "Other")
    return out


# ── Legacy interval API (kept so kg_builder._split_and_classify still
# imports cleanly during the refactor). New callers should prefer
# `classify_sections(...)` which is section-aware and doesn't need a
# parent-marker scan over the full file. ──

def _scan_parent_intervals(file_text: str, source_file: str) -> list[tuple[int, str]]:
    """Deprecated. Returns a single starter interval from filename hint
    so any caller still using it gets the same default we now apply
    inside `classify_sections`. Kept for back-compat only."""
    return [(1, _filename_default(source_file) or "Other")]


def _type_for_line(intervals: list[tuple[int, str]], line: int) -> str:
    """Deprecated. Returns the first interval's type."""
    return intervals[0][1] if intervals else "Other"


# ── 2. line-start / line-end helpers ───────────────────────────────────

def find_line_range(full_text: str, body: str) -> tuple[int, int]:
    """Find the 1-indexed line where body starts and ends in full_text.

    body is the raw section body returned by section_splitter — its first
    line should appear verbatim in full_text. We anchor on the first
    non-empty line of body.
    """
    body_lines = [l for l in body.split("\n") if l.strip()]
    if not body_lines:
        return (1, 1)
    anchor = body_lines[0].strip()
    full_lines = full_text.split("\n")
    line_start = 1
    for i, l in enumerate(full_lines, 1):
        if anchor and anchor in l:
            line_start = i
            break
    n_body_lines = len(body.split("\n"))
    return (line_start, line_start + n_body_lines - 1)


# ── 3. Main pipeline ────────────────────────────────────────────────────

def process_one_file(md_path: Path) -> list[dict]:
    """Split a single Markdown file into section-rows ready to insert.

    Section-type assignment runs `classify_sections` (heading-content
    primary, filename hint as starter default) on the splitter's output."""
    from builder.section_splitter import split_into_sections

    text = md_path.read_text(encoding="utf-8")
    doc_stem = md_path.stem
    print(f"\n  Splitting: {md_path.name}")
    sections = split_into_sections(text, doc_stem)
    print(f"    raw sections from splitter: {len(sections)}")

    # Build the section-row list first (without section_type) so we can
    # call classify_sections in one pass with full heading + body context.
    rows: list[dict] = []
    for ref, body in sections:
        heading = ref.split("/", 1)[1] if "/" in ref else ref
        line_start, line_end = find_line_range(text, body)
        rows.append({
            "doc_id":         DOC_ID,
            "document_name":  DOC_NAME,
            "section_type":   None,           # filled in below
            "heading":        heading,
            "line_start":     line_start,
            "line_end":       line_end,
            "word_count":     len(body.split()),
            "full_text":      body,
            "source_file":    md_path.name,
        })
    types = classify_sections(rows, md_path.name, file_text=text)
    print(f"    filename default: {_filename_default(md_path.name)!r}")
    for r, t in zip(rows, types):
        r["section_type"] = t
    return rows


def insert_sections(rows: list[dict]) -> list[dict]:
    """Insert in batches of 50 (REST body cap). Returns rows-with-id."""
    inserted: list[dict] = []
    BATCH = 50
    for i in range(0, len(rows), BATCH):
        chunk = rows[i:i + BATCH]
        out = rest_insert("document_sections", chunk)
        inserted.extend(out)
    return inserted


def index_in_qdrant(rows_with_id: list[dict]) -> int:
    """Embed full_text via VectorChecker BGE-M3 and upsert into the
    shared tender_sections collection. Adds section_type and
    postgresql_id to payload so STEP 6 hybrid retrieval can fetch
    full text from Postgres after a SPARQL hit."""
    from modules.validator.vector_checker import VectorChecker, sac_summary, Section, make_embed_text
    from qdrant_client.http import models as qm

    vec = VectorChecker()
    print(f"    VectorChecker BGE-M3 dim: {vec.dim}")

    # Idempotent: delete prior Qdrant points for this doc_id so reruns
    # don't accumulate stale vectors with old postgresql_id values.
    try:
        vec.client.delete(
            collection_name=vec.SHARED_COLLECTION,
            points_selector=qm.Filter(must=[
                qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=DOC_ID)),
            ]),
        )
        print(f"    Cleared prior Qdrant points for doc_id={DOC_ID}")
    except Exception as e:
        print(f"    Qdrant clear skipped: {e}")

    # Build Section dataclass for SAC + embed_text helpers
    sec_objs = [
        Section(
            heading=r["heading"],
            body=r["full_text"],
            char_start=r["line_start"],          # repurposed — line index, not chars
            char_end=r["line_end"],
        )
        for r in rows_with_id
    ]
    sacs        = [sac_summary(s) for s in sec_objs]
    embed_texts = [make_embed_text(s, sac) for s, sac in zip(sec_objs, sacs)]

    print(f"    Embedding {len(embed_texts)} sections via BGE-M3...")
    t0 = time.perf_counter()
    vectors = vec.model.encode(
        embed_texts,
        normalize_embeddings=True,
        show_progress_bar=False,
        batch_size=4,
    ).tolist()
    print(f"    Embed wall: {int((time.perf_counter()-t0)*1000)} ms")

    # Deterministic point IDs so reruns are idempotent
    ns = uuid.uuid5(uuid.NAMESPACE_URL, "procureai/tender_graph")
    points = [
        qm.PointStruct(
            id=str(uuid.uuid5(ns, f"{DOC_ID}:{r['id']}")),
            vector=vectors[i],
            payload={
                "doc_id":          DOC_ID,
                "section_heading": r["heading"],
                "section_text":    r["full_text"][:500],
                "section_full_word_count": r["word_count"],
                "section_type":    r["section_type"],         # NEW
                "postgresql_id":   r["id"],                   # NEW
                "source_file":     r["source_file"],
                "char_position":   r["line_start"],
                "sac_summary":     sacs[i],
            },
        )
        for i, r in enumerate(rows_with_id)
    ]
    t0 = time.perf_counter()
    vec.client.upsert(collection_name=vec.SHARED_COLLECTION, points=points, wait=True)
    print(f"    Upsert wall: {int((time.perf_counter()-t0)*1000)} ms")

    # Verify count under doc_id filter
    n = vec.client.count(
        collection_name=vec.SHARED_COLLECTION,
        count_filter=qm.Filter(must=[
            qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=DOC_ID)),
        ]),
        exact=True,
    ).count
    return n


def main() -> int:
    print("=" * 70)
    print(f"STEP 2 — Section processing for {DOC_NAME}")
    print(f"doc_id = {DOC_ID}")
    print("=" * 70)

    t_step = time.perf_counter()

    # Idempotent: blow away any prior rows/relationships under this doc_id.
    # Order matters: relationships → instances → sections (FK chain).
    print("\nClearing prior rows for this doc_id...")
    n_rel = rest_delete_doc("clause_relationships", DOC_ID)
    n_inst = rest_delete_doc("clause_instances", DOC_ID)
    n_sec = rest_delete_doc("document_sections", DOC_ID)
    print(f"  deleted: {n_rel} relationships, {n_inst} instances, {n_sec} sections")

    # 1. Split + classify
    all_rows: list[dict] = []
    for f in SOURCE_FILES:
        rows = process_one_file(f)
        print(f"    after splitter+classifier: {len(rows)} sections")
        all_rows.extend(rows)
    print(f"\nTotal rows ready to insert: {len(all_rows)}")

    # 2. Insert into PostgreSQL (with batching)
    t0 = time.perf_counter()
    inserted = insert_sections(all_rows)
    pg_ms = int((time.perf_counter() - t0) * 1000)
    print(f"\nPostgres insert: {len(inserted)} rows in {pg_ms} ms")

    # 3. Embed + Qdrant upsert
    n_qdrant = index_in_qdrant(inserted)

    # 4. Summary
    print("\n" + "=" * 70)
    print("STEP 2 — RESULTS")
    print("=" * 70)
    print(f"Total sections in PostgreSQL:     {len(inserted)}")

    # Section type distribution (read back from PG to confirm)
    rows = rest_select(
        "document_sections",
        params={"select": "section_type", "doc_id": f"eq.{DOC_ID}"},
    )
    from collections import Counter
    dist = Counter(r["section_type"] for r in rows)
    print("\nSection type distribution:")
    for st, n in dist.most_common():
        print(f"  {st:20s} {n}")

    # Largest section
    largest = max(inserted, key=lambda r: r["word_count"])
    print(f"\nLargest section: {largest['word_count']} words")
    print(f"  heading: {largest['heading'][:80]}")
    print(f"  type:    {largest['section_type']}")
    print(f"  source:  {largest['source_file']}")

    print(f"\nQdrant vectors for doc_id={DOC_ID}: {n_qdrant}")

    elapsed = int((time.perf_counter() - t_step) * 1000)
    print(f"\nStep 2 wall time: {elapsed} ms ({elapsed/1000:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
