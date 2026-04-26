"""Load clause-template results from data/clause_results/ into Postgres."""
from __future__ import annotations

import sys

from loguru import logger

from builder.clause_generator import load_clause_results


def main() -> int:
    summary = load_clause_results()
    logger.info("\n=== Clause load summary ===")
    for k, v in summary.items():
        if k == "errors":
            continue
        logger.info(f"  {k}: {v}")
    if summary["errors"]:
        for fname, err in summary["errors"][:10]:
            logger.warning(f"    [{fname}] {err}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
