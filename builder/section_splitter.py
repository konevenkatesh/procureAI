"""
Split processed Markdown documents into sections suitable for rule extraction.

Two splitting patterns, auto-detected per document:

  Pattern A (rule)    — Rule-numbered docs like GFR, CVC circulars.
                        Sections start at lines matching r"^Rule\\s+\\d+".
                        Triggered when "Rule 1." or "Rule 2." appears in the
                        first 3000 characters of the document.

  Pattern B (heading) — Manual-style docs like MPW, MPG, MPS.
                        Sections start at Markdown headings (`#`, `##`, `###`).
                        Default if Pattern A is not triggered.

Pre-processing (both patterns):
  - Lone-numeric / roman-numeral lines are stripped (page numbers that
    escaped the header/footer crop in document_processor.py).
  - Sections under MIN_SECTION_CHARS are dropped.
  - Sections over MAX_SECTION_CHARS are further split at paragraph boundaries.

A section's "reference" is `{doc_name}/{heading}` — used as `extracted_from`
on every CandidateRule.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Literal

from builder.document_processor import list_processed_documents


MIN_SECTION_CHARS = 150          # below this → drop (likely fragment)
MAX_SECTION_CHARS = 8_000        # ~2K tokens; above this → split further

# Documents that contain actual tender clause text (used by clause extraction).
# Other docs (GFR, CVC circulars) describe rules but don't contain clause templates.
CLAUSE_SOURCE_DOCS: set[str] = {"MPW_2022", "MPG_2022", "MPS_2017", "MPS_2022"}

# GLOBAL skip list — sections whose heading OR opening body matches any of
# these are dropped from BOTH rules and clauses batches. Used for table-of-
# contents pages and similar non-rule navigation content that pymupdf4llm
# can't cleanly suppress.
SKIP_PATTERNS: tuple[str, ...] = (
    "table of contents",
    "contents",
)


# Heading keywords that mark sections to skip during clause extraction.
EXCLUDE_HEADING_KEYWORDS_FOR_CLAUSES: tuple[str, ...] = (
    "introduction",
    "objective",
    "preamble",
    "preface",
    "foreword",
    "definitions",
    "definition of",
    "scope of manual",
    "table of contents",
    "appendix",
    "annexure",
    "annex ",
    "abbreviation",
    "glossary",
    "acknowledgement",
    "list of figures",
    "list of tables",
)

# ─────────────────────────────────────────────────────────────────────────────
# Regex patterns
# ─────────────────────────────────────────────────────────────────────────────

# A single-token line consisting only of digits or roman numerals
# (post-crop page-number cleanup).
LONE_PAGE_NUMBER = re.compile(r"^\s*[ivxlcdm\d]+\s*$", re.IGNORECASE)

# Pattern A line-start markers — covers both:
#   plain prose:   "Rule 25. Title"  /  "**Rule 25** Title"  /  "Rule 25 -"
#   table row:     "|**Rule**|**25**|"   (pymupdf4llm sometimes preserves
#                                          GFR's table layout)
RULE_HEADING_PROSE = re.compile(
    r"^\s*(?:\*\*)?Rule\s+(\d+(?:\([a-z0-9]+\))?)(?:[\.\-:\s\*])",
    re.IGNORECASE,
)
RULE_HEADING_TABLE = re.compile(
    r"^\s*\|\s*\*\*Rule\*\*\s*\|\s*\*\*(\d+(?:\([a-z0-9]+\))?)\*\*\s*\|",
    re.IGNORECASE,
)

# Pattern B markdown headings.
MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")

# Pattern auto-detector: explicit "Rule 1." / "Rule 2." mention in first 3000 chars.
# Both prose and table-cell forms are checked.
RULE_DETECTOR_PROSE = re.compile(r"\bRule\s+[12]\b", re.IGNORECASE)
RULE_DETECTOR_TABLE = re.compile(r"\|\s*\*\*Rule\*\*\s*\|\s*\*\*[12]\*\*\s*\|", re.IGNORECASE)


# Whole-word matcher for SKIP_PATTERNS.
_SKIP_MATCHER = re.compile(
    r"\b(?:" + "|".join(re.escape(p) for p in SKIP_PATTERNS) + r")\b",
    re.IGNORECASE,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _strip_page_numbers(text: str) -> str:
    """Remove lines that are just a number / roman numeral (stray page numbers)."""
    return "\n".join(
        line for line in text.split("\n") if not LONE_PAGE_NUMBER.match(line)
    )


def _matches_skip_pattern(reference: str, body: str) -> bool:
    """True if section's heading or first ~150 chars of body match a SKIP_PATTERN.

    For headings: word-boundary match (avoids false positives like "Contentment").
    For body: ALL whitespace + table-syntax stripped before substring check.
    pymupdf4llm leaks TOC pages as "C  ONTENTS" (mid-word spaces) or
    "|C<br>O<br>N<br>T<br>E<br>N<br>T<br>S|" — stripping all whitespace
    collapses both to "CONTENTS" so the patterns match.
    """
    heading = reference.split("/", 1)[1] if "/" in reference else reference
    if _SKIP_MATCHER.search(heading):
        return True
    # Strip ALL whitespace, pipes, and <br> tags from the first 300 body chars.
    body_head = body[:300].lower()
    body_stripped = re.sub(r"<br\s*/?>", "", body_head)
    body_stripped = re.sub(r"[\s|]+", "", body_stripped)
    for p in SKIP_PATTERNS:
        p_stripped = p.replace(" ", "").lower()
        if p_stripped in body_stripped[:120]:
            return True
    return False


def _drop_skipped_sections(sections: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return [(ref, body) for ref, body in sections if not _matches_skip_pattern(ref, body)]


def _detect_pattern(text: str) -> Literal["rule", "heading"]:
    """Return 'rule' if Rule 1/Rule 2 pattern appears in first 3000 chars, else 'heading'."""
    head = text[:3000]
    if RULE_DETECTOR_PROSE.search(head) or RULE_DETECTOR_TABLE.search(head):
        return "rule"
    return "heading"


def _flush(
    sections: list[tuple[str, str]],
    doc_name: str,
    heading: str,
    lines: list[str],
) -> None:
    """Append (reference, text) to sections if body meets MIN_SECTION_CHARS."""
    body = "\n".join(lines).strip()
    if len(body) >= MIN_SECTION_CHARS:
        sections.append((f"{doc_name}/{heading}", body))


def _normalise_heading(raw: str) -> str:
    """Clean a heading line for use as a reference."""
    # Strip leading markdown markers, asterisks, table pipes
    cleaned = raw.strip()
    cleaned = cleaned.lstrip("#").lstrip()
    cleaned = cleaned.strip("*").strip()
    cleaned = cleaned.replace("|", " ").strip()
    # Collapse whitespace
    cleaned = re.sub(r"\s+", " ", cleaned)
    # Cap length
    if len(cleaned) > 120:
        cleaned = cleaned[:120].rstrip() + "…"
    return cleaned or "Section"


# ─────────────────────────────────────────────────────────────────────────────
# Pattern-A splitter (Rule N markers)
# ─────────────────────────────────────────────────────────────────────────────

def _split_by_rule_pattern(text: str, doc_name: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    current_heading = "Preamble"
    current_lines: list[str] = []

    for line in text.split("\n"):
        prose_match = RULE_HEADING_PROSE.match(line)
        table_match = RULE_HEADING_TABLE.match(line)
        if prose_match or table_match:
            _flush(sections, doc_name, current_heading, current_lines)
            rule_no = (prose_match or table_match).group(1)
            current_heading = _normalise_heading(f"Rule {rule_no} — {line}")
            current_lines = [line]
        else:
            current_lines.append(line)

    _flush(sections, doc_name, current_heading, current_lines)
    return sections


# ─────────────────────────────────────────────────────────────────────────────
# Pattern-B splitter (Markdown headings)
# ─────────────────────────────────────────────────────────────────────────────

def _split_by_heading_pattern(text: str, doc_name: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    current_heading = "Preamble"
    current_lines: list[str] = []

    for line in text.split("\n"):
        m = MD_HEADING.match(line)
        if m:
            _flush(sections, doc_name, current_heading, current_lines)
            current_heading = _normalise_heading(m.group(2))
            current_lines = []
        else:
            current_lines.append(line)

    _flush(sections, doc_name, current_heading, current_lines)
    return sections


# ─────────────────────────────────────────────────────────────────────────────
# Oversize splitter (paragraph boundaries)
# ─────────────────────────────────────────────────────────────────────────────

def _chunk_text(text: str, max_chars: int) -> Iterable[str]:
    paragraphs = text.split("\n\n")
    buf: list[str] = []
    buf_len = 0
    for p in paragraphs:
        p_len = len(p) + 2
        if buf_len + p_len > max_chars and buf:
            yield "\n\n".join(buf)
            buf = [p]
            buf_len = p_len
        else:
            buf.append(p)
            buf_len += p_len
    if buf:
        yield "\n\n".join(buf)


def _expand_oversize(sections: list[tuple[str, str]]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for ref, body in sections:
        if len(body) <= MAX_SECTION_CHARS:
            out.append((ref, body))
            continue
        # Split big section into part-1 / part-2 / ...
        if "/" in ref:
            base, head = ref.split("/", 1)
        else:
            base, head = ref, ""
        for i, chunk in enumerate(_chunk_text(body, MAX_SECTION_CHARS), 1):
            new_ref = f"{base}/{head} (part {i})" if head else f"{base} (part {i})"
            out.append((new_ref, chunk))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def split_into_sections(markdown_text: str, doc_name: str) -> list[tuple[str, str]]:
    """Split a Markdown document into (reference, text) pairs.

    Auto-detects Rule-N vs heading pattern, strips stray page numbers, and
    further-splits any section longer than MAX_SECTION_CHARS.
    """
    cleaned = _strip_page_numbers(markdown_text)
    pattern = _detect_pattern(cleaned)
    if pattern == "rule":
        raw_sections = _split_by_rule_pattern(cleaned, doc_name)
    else:
        raw_sections = _split_by_heading_pattern(cleaned, doc_name)
    raw_sections = _drop_skipped_sections(raw_sections)
    return _expand_oversize(raw_sections)


def detect_pattern(markdown_text: str) -> str:
    """Public wrapper around the pattern detector (for diagnostics / manifest)."""
    return _detect_pattern(markdown_text)


def get_all_sections() -> list[tuple[str, str]]:
    """All sections from every processed_md/*.md across all subdirs."""
    all_sections: list[tuple[str, str]] = []
    for md_file in list_processed_documents():
        text = md_file.read_text(encoding="utf-8")
        all_sections.extend(split_into_sections(text, md_file.stem))
    return all_sections


def sections_for_document(md_path: Path) -> list[tuple[str, str]]:
    """Return sections for a single processed Markdown file."""
    text = md_path.read_text(encoding="utf-8")
    return split_into_sections(text, md_path.stem)


def get_clause_sections() -> list[tuple[str, str]]:
    """Sections suitable for clause extraction (MPW/MPG/MPS only, intro/appendix excluded)."""
    out: list[tuple[str, str]] = []
    for ref, text in get_all_sections():
        doc_name = ref.split("/", 1)[0]
        if doc_name not in CLAUSE_SOURCE_DOCS:
            continue
        heading = ref.split("/", 1)[1].lower() if "/" in ref else ""
        if any(kw in heading for kw in EXCLUDE_HEADING_KEYWORDS_FOR_CLAUSES):
            continue
        out.append((ref, text))
    return out
