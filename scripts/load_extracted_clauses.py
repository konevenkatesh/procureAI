"""Load extracted clauses from data/extraction_results/clauses/ into Postgres."""
from __future__ import annotations

import sys

from loguru import logger

from builder.rule_extractor import load_extraction_results


def main() -> int:
    summary = load_extraction_results(kind="clauses")
    logger.info("\n=== Clause extraction load summary ===")
    logger.info(f"  Files read:        {summary['files_read']}")
    logger.info(f"  Clauses loaded:    {summary['clauses_loaded']}")
    logger.info(f"  Validation errors: {summary['validation_errors']}")
    if summary["errors"]:
        logger.warning(f"\n  First {min(10, len(summary['errors']))} errors:")
        for fname, err in summary["errors"][:10]:
            logger.warning(f"    [{fname}] {err}")
    return 0 if summary["clauses_loaded"] > 0 or summary["files_read"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
