"""
Run Docling on every PDF/DOCX in source_documents/**/raw_pdf|raw/.
Writes Markdown into the matching processed_md/ folder.

Idempotent: skips files whose processed_md is newer than the raw source.
"""
from __future__ import annotations

import sys

from loguru import logger

from builder.document_processor import process_all


def main() -> int:
    summary = process_all()

    logger.info("\n=== Document processing summary ===")
    logger.info(f"  Processed: {summary['processed']}")
    logger.info(f"  Skipped (up-to-date): {summary['skipped']}")
    logger.info(f"  Failed: {len(summary['failed'])}")

    if summary["by_doc"]:
        logger.info("\n  Output sizes:")
        for name, size in sorted(summary["by_doc"].items()):
            logger.info(f"    {name}: {size:,} chars")

    if summary["failed"]:
        logger.error("\n  Failed files:")
        for path, err in summary["failed"]:
            logger.error(f"    {path}: {err}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
