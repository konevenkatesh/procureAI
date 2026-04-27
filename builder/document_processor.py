"""
PDF → Markdown conversion via pymupdf4llm.

Why pymupdf4llm and not Docling/pypdf:
  - Docling: 2-3 GB of ML deps (torch, transformers), 5-10s per page on CPU.
  - pypdf:   no formatting, no tables, no Markdown — line-noise output.
  - pymupdf4llm: real Markdown, table detection, fast (~1s per page on CPU),
    minimal install (~50 MB).

Header / footer suppression:
  pymupdf4llm 1.27.x has no `header=False`/`footer=False` kwargs. The
  documented mechanism is `margins=(left, top, right, bottom)` which crops
  a band off each page before extraction. We use 50 pt top/bottom which
  catches running headers + page numbers in 95% of government PDFs.
  Stray page numbers that escape the crop (e.g. centred mid-page numbers)
  are filtered at the section_splitter stage.
"""
from __future__ import annotations

from pathlib import Path

from loguru import logger

from builder.config import settings


SUBDIRS = [
    ("central", "raw_pdf", "processed_md"),
    ("ap_state", "raw_pdf", "processed_md"),
    ("sample_tenders", "raw", "processed_md"),
]

PDF_PATTERNS = ("*.pdf", "*.PDF")

# Crop bands (in PDF points) to drop running headers + footers.
# Order: (left, top, right, bottom)
DEFAULT_MARGINS = (0, 50, 0, 50)


# ─────────────────────────────────────────────────────────────────────────────
# Per-document table_strategy config
# ─────────────────────────────────────────────────────────────────────────────
# Some PDFs (GFR, CVC circulars) lay rules out in two-column page format that
# pymupdf4llm misinterprets as data tables. Disable table detection for those.
# Manual-style PDFs (MPW/MPG/MPS) DO contain real tables (BoQs, rate schedules,
# evaluation matrices) that we WANT preserved as Markdown tables.
#
# Lookup is by document stem (filename without extension). CVC_* prefix matches
# all CVC documents.

DOC_TABLE_STRATEGY: dict[str, str | None] = {
    "GFR_2017":         None,
    "MPW_2022":         "lines_strict",
    "MPW_2025_draft":   "lines_strict",
    "MPG_2022":         "lines_strict",
    "MPS_2017":         "lines_strict",
    "MPS_2022":         "lines_strict",
}

DOC_PREFIX_TABLE_STRATEGY: dict[str, str | None] = {
    "CVC_": None,
}

DEFAULT_TABLE_STRATEGY: str | None = "lines_strict"


# Documents whose PDF is image-only (scanned, no embedded text). Force OCR via
# Tesseract — otherwise pymupdf4llm produces a 0-char file. Requires the
# `tesseract` binary on PATH.
DOC_FORCE_OCR: set[str] = {
    "CVC_integrity_pact",
}


def get_table_strategy(doc_stem: str) -> str | None:
    """Return the table_strategy to use for a given document filename stem."""
    if doc_stem in DOC_TABLE_STRATEGY:
        return DOC_TABLE_STRATEGY[doc_stem]
    for prefix, strategy in DOC_PREFIX_TABLE_STRATEGY.items():
        if doc_stem.startswith(prefix):
            return strategy
    return DEFAULT_TABLE_STRATEGY


def convert_pdf_to_markdown(pdf_path: str, doc_name: str = "") -> str:
    """Convert a single PDF to Markdown via pymupdf4llm.

    Uses the per-document table_strategy from DOC_TABLE_STRATEGY and the
    per-document OCR flag from DOC_FORCE_OCR. Pass `doc_name` explicitly to
    override the filename-based lookup.
    """
    import pymupdf4llm  # lazy-imported so prepare_extraction_batches works without it
    if not doc_name:
        doc_name = Path(pdf_path).stem
    strategy = get_table_strategy(doc_name)
    force_ocr = doc_name in DOC_FORCE_OCR
    return pymupdf4llm.to_markdown(
        pdf_path,
        margins=DEFAULT_MARGINS,
        table_strategy=strategy,
        force_ocr=force_ocr,
    )


def process_all() -> dict:
    """Process every PDF under source_documents/**/raw_pdf/ to processed_md/.

    Idempotent: skips files whose processed_md is newer than the raw source.
    Note: DOCX inputs (used by sample_tenders) are NOT handled here yet —
    pymupdf4llm is PDF-only. Add a separate path if/when needed.
    """
    summary: dict = {"processed": 0, "skipped": 0, "failed": [], "by_doc": {}}

    for subdir, raw_name, out_name in SUBDIRS:
        raw_dir = settings.source_documents_dir / subdir / raw_name
        out_dir = settings.source_documents_dir / subdir / out_name
        out_dir.mkdir(parents=True, exist_ok=True)

        files = [f for p in PDF_PATTERNS for f in raw_dir.glob(p)]
        if not files:
            logger.warning(f"No PDF source files in {raw_dir}")
            continue

        for doc_path in sorted(files):
            out_path = out_dir / doc_path.with_suffix(".md").name

            if out_path.exists() and out_path.stat().st_mtime > doc_path.stat().st_mtime:
                logger.info(f"  [skip] {doc_path.name} already processed")
                summary["skipped"] += 1
                continue

            strategy = get_table_strategy(doc_path.stem)
            ocr = doc_path.stem in DOC_FORCE_OCR
            logger.info(
                f"  Processing {doc_path.name}  "
                f"(table_strategy={strategy!r}, force_ocr={ocr})..."
            )
            try:
                md = convert_pdf_to_markdown(str(doc_path), doc_path.stem)
                out_path.write_text(md, encoding="utf-8")
                summary["processed"] += 1
                summary["by_doc"][doc_path.name] = len(md)
                logger.info(f"    → {out_path.name} ({len(md):,} chars)")
            except Exception as e:
                logger.error(f"    FAILED: {e}")
                summary["failed"].append((str(doc_path), str(e)))

    return summary


def list_processed_documents() -> list[Path]:
    """Return all processed Markdown files across all subdirs."""
    return sorted(settings.source_documents_dir.rglob("processed_md/*.md"))
