"""Push all production-ready SHACL shapes from Postgres into Apache Jena Fuseki."""
from __future__ import annotations

import sys

from loguru import logger

from builder.config import settings
from knowledge_layer.shacl_store import get_production_ready_shapes, upload_turtle


def main() -> int:
    shapes = get_production_ready_shapes()
    if not shapes:
        logger.warning("No production-ready shapes to load.")
        return 0

    logger.info(f"Loading {len(shapes)} SHACL shapes into Fuseki at {settings.fuseki_url}")
    success = 0
    failed = 0
    for shape in shapes:
        try:
            upload_turtle(
                turtle_content=shape["turtle_content"],
                graph_name=f"https://procurement.ap.gov.in/shapes/{shape['shape_id']}",
            )
            success += 1
        except Exception as e:
            logger.error(f"  {shape['shape_id']}: {e}")
            failed += 1

    logger.info(f"\n  Loaded: {success}    Failed: {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
