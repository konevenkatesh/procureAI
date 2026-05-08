"""Convert a draft tender Markdown file into DOCX.

The Markdown produced by ``scripts/draft_tender.py`` uses a constrained
subset of GitHub-flavoured Markdown:

  - ATX headings (``#``..``######``)
  - Pipe tables (``| col | col |`` followed by ``|---|---|``)
  - Horizontal rules (``---``)
  - Blockquotes (``> ...``)  — used only for the cover/license panel
  - Inline emphasis: ``**bold**``, ``*italic*`` / ``_italic_``,
    backticks for ``code``
  - Inline links: ``[text](url)``
  - Plain paragraphs separated by blank lines
  - Bullet / numbered lists ("- item" or "1. item")

We do not need a full Markdown engine — just a deterministic mapping
from this subset to ``python-docx`` paragraph / run / table primitives.
The goal is a serviceable, reviewer-friendly DOCX, not a perfect render.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from docx import Document
from docx.enum.table import WD_ALIGN_VERTICAL
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor, Cm

# ──────────────────────────────────────────────────────────────────────
# Inline-token tokenizer: bold / italic / code / link
# ──────────────────────────────────────────────────────────────────────

_INLINE_RE = re.compile(
    r"(\*\*[^*]+\*\*"          # **bold**
    r"|__[^_]+__"              # __bold__
    r"|\*[^*]+\*"              # *italic*
    r"|_[^_]+_"                # _italic_
    r"|`[^`]+`"                # `code`
    r"|\[[^\]]+\]\([^)]+\))"   # [text](url)
)
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _add_runs(paragraph, text: str) -> None:
    """Split text into Markdown inline tokens and append runs to paragraph."""
    if not text:
        return
    for chunk in _INLINE_RE.split(text):
        if not chunk:
            continue
        if chunk.startswith("**") and chunk.endswith("**"):
            r = paragraph.add_run(chunk[2:-2]); r.bold = True
        elif chunk.startswith("__") and chunk.endswith("__"):
            r = paragraph.add_run(chunk[2:-2]); r.bold = True
        elif (chunk.startswith("*") and chunk.endswith("*")
              and len(chunk) >= 3):
            r = paragraph.add_run(chunk[1:-1]); r.italic = True
        elif (chunk.startswith("_") and chunk.endswith("_")
              and len(chunk) >= 3):
            r = paragraph.add_run(chunk[1:-1]); r.italic = True
        elif chunk.startswith("`") and chunk.endswith("`"):
            r = paragraph.add_run(chunk[1:-1])
            r.font.name = "Consolas"
            r.font.size = Pt(10)
        else:
            m = _LINK_RE.fullmatch(chunk)
            if m:
                # No real hyperlink (would need w:hyperlink XML); render
                # as styled blue text so it's visually a link.
                r = paragraph.add_run(m.group(1))
                r.font.color.rgb = RGBColor(0x1F, 0x49, 0x7D)
                r.underline = True
            else:
                paragraph.add_run(chunk)


# ──────────────────────────────────────────────────────────────────────
# Block parser
# ──────────────────────────────────────────────────────────────────────

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")
_BULLET_RE = re.compile(r"^[\-\*\+]\s+(.*)$")
_NUMLIST_RE = re.compile(r"^\d+\.\s+(.*)$")
_HR_RE = re.compile(r"^\s*-{3,}\s*$")


def _strip_pipes(line: str) -> list[str]:
    """Split a markdown table row, stripping leading/trailing pipes."""
    s = line.strip()
    if s.startswith("|"): s = s[1:]
    if s.endswith("|"):   s = s[:-1]
    # Don't split escaped pipes
    parts: list[str] = []
    cur = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s) and s[i+1] == "|":
            cur.append("|"); i += 2; continue
        if ch == "|":
            parts.append("".join(cur).strip())
            cur = []; i += 1; continue
        cur.append(ch); i += 1
    parts.append("".join(cur).strip())
    return parts


def _is_table_row(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") and s.endswith("|") and s.count("|") >= 2


# ──────────────────────────────────────────────────────────────────────
# DOCX construction helpers
# ──────────────────────────────────────────────────────────────────────

def _set_cell_shading(cell, hex_fill: str) -> None:
    """Apply a background fill colour (e.g. 'D9E1F2') to a table cell."""
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"),  "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"),  hex_fill)
    tc_pr.append(shd)


def _set_cell_borders(cell) -> None:
    """Add thin black borders on all four sides of a table cell."""
    tc_pr = cell._tc.get_or_add_tcPr()
    borders = OxmlElement("w:tcBorders")
    for edge in ("top", "left", "bottom", "right"):
        b = OxmlElement(f"w:{edge}")
        b.set(qn("w:val"),  "single")
        b.set(qn("w:sz"),   "4")
        b.set(qn("w:color"), "808080")
        borders.append(b)
    tc_pr.append(borders)


def _add_heading(doc: Document, level: int, text: str) -> None:
    # python-docx supports 0..9. Map markdown 1..6 to Word Heading 1..6.
    level = max(1, min(level, 6))
    h = doc.add_heading(level=level)
    _add_runs(h, text)


def _add_paragraph(doc: Document, text: str,
                   *, style: str | None = None) -> None:
    p = doc.add_paragraph(style=style) if style else doc.add_paragraph()
    _add_runs(p, text)


def _add_hr(doc: Document) -> None:
    p = doc.add_paragraph()
    pPr = p._p.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"),   "single")
    bottom.set(qn("w:sz"),    "8")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), "808080")
    pBdr.append(bottom)
    pPr.append(pBdr)


def _add_table(doc: Document, rows: list[list[str]]) -> None:
    if not rows:
        return
    n_cols = max(len(r) for r in rows)
    tbl = doc.add_table(rows=len(rows), cols=n_cols)
    tbl.autofit = True
    for ri, row in enumerate(rows):
        cells = tbl.rows[ri].cells
        for ci in range(n_cols):
            txt = row[ci] if ci < len(row) else ""
            cell = cells[ci]
            cell.vertical_alignment = WD_ALIGN_VERTICAL.TOP
            # Replace markdown <br> with paragraph break
            chunks = txt.split("<br>")
            cell.text = ""
            for k, chunk in enumerate(chunks):
                p = cell.paragraphs[0] if k == 0 else cell.add_paragraph()
                _add_runs(p, chunk.strip())
            if ri == 0:
                _set_cell_shading(cell, "D9E1F2")  # light blue header
                for run in cell.paragraphs[0].runs:
                    run.bold = True
            _set_cell_borders(cell)


# ──────────────────────────────────────────────────────────────────────
# Top-level entry point
# ──────────────────────────────────────────────────────────────────────

def md_to_docx(md_path: str | Path, docx_path: str | Path) -> str:
    """Read Markdown at ``md_path`` and write DOCX to ``docx_path``.
    Returns the absolute path of the DOCX file written."""
    md_path  = Path(md_path)
    docx_path = Path(docx_path)
    text = md_path.read_text(encoding="utf-8")

    doc = Document()

    # Page setup — A4 portrait with reasonable margins
    section = doc.sections[0]
    section.page_height = Cm(29.7)
    section.page_width  = Cm(21.0)
    section.top_margin = section.bottom_margin = Cm(2.0)
    section.left_margin = section.right_margin = Cm(2.0)

    # Default font tweaks
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    lines = text.splitlines()
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        # Blank line — just advance
        if not stripped:
            i += 1
            continue

        # Horizontal rule
        if _HR_RE.match(stripped):
            _add_hr(doc)
            i += 1
            continue

        # ATX heading
        m = _HEADING_RE.match(stripped)
        if m:
            level = len(m.group(1))
            _add_heading(doc, level, m.group(2))
            i += 1
            continue

        # Pipe table (header row + separator row + body)
        if _is_table_row(line) and i + 1 < n and _TABLE_SEP_RE.match(lines[i+1]):
            header = _strip_pipes(line)
            body_rows: list[list[str]] = []
            j = i + 2
            while j < n and _is_table_row(lines[j]):
                body_rows.append(_strip_pipes(lines[j]))
                j += 1
            _add_table(doc, [header] + body_rows)
            i = j
            continue

        # Bullet list
        if _BULLET_RE.match(stripped):
            while i < n and (m2 := _BULLET_RE.match(lines[i].strip())):
                _add_paragraph(doc, m2.group(1), style="List Bullet")
                i += 1
            continue

        # Numbered list
        if _NUMLIST_RE.match(stripped):
            while i < n and (m2 := _NUMLIST_RE.match(lines[i].strip())):
                _add_paragraph(doc, m2.group(1), style="List Number")
                i += 1
            continue

        # Blockquote — render bold-italic indented, single para per run
        if stripped.startswith(">"):
            buf: list[str] = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip().lstrip(">").strip())
                i += 1
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.75)
            _add_runs(p, " ".join(buf))
            for r in p.runs:
                r.italic = True
            continue

        # Plain paragraph (collect contiguous non-blank lines)
        buf = [line]
        i += 1
        while i < n and lines[i].strip() and not (
            _HEADING_RE.match(lines[i].strip())
            or _is_table_row(lines[i])
            or _BULLET_RE.match(lines[i].strip())
            or _NUMLIST_RE.match(lines[i].strip())
            or _HR_RE.match(lines[i].strip())
            or lines[i].strip().startswith(">")
        ):
            buf.append(lines[i])
            i += 1
        # Join with space (Markdown soft-wrap convention)
        para_text = " ".join(s.strip() for s in buf).strip()
        if para_text:
            _add_paragraph(doc, para_text)

    doc.save(str(docx_path))
    return str(docx_path.resolve())


# ──────────────────────────────────────────────────────────────────────
# CLI for ad-hoc conversion
# ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: python -m modules.draft_export.md_to_docx <md> [<docx>]")
        sys.exit(2)
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2]) if len(sys.argv) >= 3 else src.with_suffix(".docx")
    out = md_to_docx(src, dst)
    print(f"wrote {out}")
