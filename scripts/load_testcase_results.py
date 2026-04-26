"""Load test-case results from data/testcase_results/ into Postgres."""
from __future__ import annotations

import sys

from loguru import logger

from builder.test_case_generator import load_testcase_results


def main() -> int:
    summary = load_testcase_results()
    logger.info("\n=== Test-case load summary ===")
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
