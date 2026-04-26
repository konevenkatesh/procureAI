"""
Split processed Markdown documents into sections suitable for rule extraction.

Strategy:
  1. Split by Markdown headings (any level).
  2. Skip sections under MIN_SECTION_CHARS (definitions, ToC fragments).
  3. Further split sections that exceed MAX_SECTION_CHARS at paragraph boundaries
     so each chunk fits comfortably in a single extraction batch (~2K tokens).

A section's "reference" is `{doc_name}/{heading}` — used as the
`extracted_from` field on every CandidateRule.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from builder.document_processor import list_processed_documents


MIN_SECTION_CHARS = 150          # below this → drop (likely fragment)
MAX_SECTION_CHARS = 8_000        # ~2K tokens; above this → split further


def split_into_sections(markdown_text: str, doc_name: str) -> list[tuple[str, str]]:
    """Split a single Markdown document into (reference, text) pairs."""
    raw_sections: list[tuple[str, str]] = []
    current_heading = "Preamble"
    current_lines: list[str] = []

    for line in markdown_text.split("\n"):
        if line.lstrip().startswith("#"):
            text = "\n".join(current_lines).strip()
            if len(text) >= MIN_SECTION_CHARS:
                raw_sections.append((current_heading, text))
            current_heading = line.lstrip("#").strip() or current_heading
            current_lines = []
        else:
            current_lines.append(line)

    tail = "\n".join(current_lines).strip()
    if len(tail) >= MIN_SECTION_CHARS:
        raw_sections.append((current_heading, tail))

    # Further split oversized sections
    expanded: list[tuple[str, str]] = []
    for heading, text in raw_sections:
        if len(text) <= MAX_SECTION_CHARS:
            expanded.append((f"{doc_name}/{heading}", text))
        else:
            for i, chunk in enumerate(_chunk_text(text, MAX_SECTION_CHARS), 1):
                expanded.append((f"{doc_name}/{heading} (part {i})", chunk))

    return expanded


def _chunk_text(text: str, max_chars: int) -> Iterable[str]:
    """Split a long block at paragraph boundaries, keeping chunks ≤ max_chars."""
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


def get_all_sections() -> list[tuple[str, str]]:
    """Walk every processed_md/*.md and return all (reference, text) pairs."""
    all_sections: list[tuple[str, str]] = []
    for md_file in list_processed_documents():
        text = md_file.read_text(encoding="utf-8")
        sections = split_into_sections(text, md_file.stem)
        all_sections.extend(sections)
    return all_sections


def sections_for_document(md_path: Path) -> list[tuple[str, str]]:
    """Return sections for a single processed Markdown file."""
    text = md_path.read_text(encoding="utf-8")
    return split_into_sections(text, md_path.stem)
