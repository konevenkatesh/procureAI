"""Update clause_templates.text_telugu from data/telugu_results/."""
from __future__ import annotations

import sys

from loguru import logger

from builder.telugu_generator import load_telugu_results


def main() -> int:
    summary = load_telugu_results()
    logger.info("\n=== Telugu load summary ===")
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
