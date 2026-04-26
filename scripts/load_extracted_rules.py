"""Load all batch result files in data/extraction_results/ into Postgres."""
from __future__ import annotations

import sys

from loguru import logger

from builder.rule_extractor import load_extraction_results


def main() -> int:
    summary = load_extraction_results()
    logger.info("\n=== Extraction load summary ===")
    logger.info(f"  Files read:        {summary['files_read']}")
    logger.info(f"  Rules loaded:      {summary['rules_loaded']}")
    logger.info(f"  Validation errors: {summary['validation_errors']}")
    if summary["errors"]:
        logger.warning(f"\n  First {min(10, len(summary['errors']))} errors:")
        for fname, err in summary["errors"][:10]:
            logger.warning(f"    [{fname}] {err}")
    return 0 if summary["rules_loaded"] > 0 or summary["files_read"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
