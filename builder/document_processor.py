"""
Docling pipeline: convert raw PDFs/DOCX into structured Markdown.

Called by `scripts/process_all_documents.py`.
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

PATTERNS = ("*.pdf", "*.PDF", "*.docx", "*.DOCX")


def process_all() -> dict:
    """Process every raw document into Markdown. Returns summary stats."""
    # Lazy-imported so the rest of the package can be used without the heavy
    # docling/torch ML stack installed (e.g. for prepare_extraction_batches).
    from docling.document_converter import DocumentConverter

    converter = DocumentConverter()
    summary = {"processed": 0, "skipped": 0, "failed": [], "by_doc": {}}

    for subdir, raw_name, out_name in SUBDIRS:
        raw_dir = settings.source_documents_dir / subdir / raw_name
        out_dir = settings.source_documents_dir / subdir / out_name
        out_dir.mkdir(parents=True, exist_ok=True)

        files = [f for p in PATTERNS for f in raw_dir.glob(p)]
        if not files:
            logger.warning(f"No source files in {raw_dir}")
            continue

        for doc_path in sorted(files):
            out_path = out_dir / doc_path.with_suffix(".md").name

            if out_path.exists() and out_path.stat().st_mtime > doc_path.stat().st_mtime:
                logger.info(f"  [skip] {doc_path.name} already processed")
                summary["skipped"] += 1
                continue

            logger.info(f"  Processing {doc_path.name}...")
            try:
                result = converter.convert(str(doc_path))
                md = result.document.export_to_markdown()
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
