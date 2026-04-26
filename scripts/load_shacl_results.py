"""Load SHACL Turtle results from data/shacl_results/ into Postgres + .ttl files."""
from __future__ import annotations

import sys

from loguru import logger

from builder.shacl_generator import load_shacl_results


def main() -> int:
    summary = load_shacl_results()
    logger.info("\n=== SHACL load summary ===")
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
