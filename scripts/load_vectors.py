"""Embed VectorConcepts with BGE-M3 and push to Qdrant. ~25min cold-start on Mac M4 CPU."""
from __future__ import annotations

import sys

from loguru import logger

from builder.vector_loader import load_concept_results_and_embed


def main() -> int:
    summary = load_concept_results_and_embed()
    logger.info("\n=== Vector load summary ===")
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
