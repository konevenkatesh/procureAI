"""
Unified loader: reads extraction results from data/extraction_results/{rules,clauses}/
and pushes them into Postgres.

Usage:
    python scripts/load_extraction_results.py --type rules
    python scripts/load_extraction_results.py --type clauses
    python scripts/load_extraction_results.py --type rules --batch batch_0001
"""
from __future__ import annotations

import sys

import typer
from loguru import logger

from builder.rule_extractor import load_extraction_results


app = typer.Typer(add_completion=False)


@app.command()
def main(
    type: str = typer.Option(..., "--type", help="rules or clauses"),
    batch: str = typer.Option(None, "--batch", help="Single batch id (e.g. batch_0001)"),
):
    if type not in ("rules", "clauses"):
        typer.echo("--type must be 'rules' or 'clauses'", err=True)
        raise typer.Exit(2)

    glob = f"{batch}.json" if batch else "*.json"
    summary = load_extraction_results(kind=type, batch_glob=glob)

    logger.info(f"\n=== {type.title()} extraction load summary ===")
    logger.info(f"  Files read:        {summary['files_read']}")
    if type == "rules":
        logger.info(f"  Rules loaded:      {summary['rules_loaded']}")
    else:
        logger.info(f"  Clauses loaded:    {summary['clauses_loaded']}")
    logger.info(f"  Validation errors: {summary['validation_errors']}")
    if summary["errors"]:
        logger.warning(f"\n  First {min(20, len(summary['errors']))} errors:")
        for fname, err in summary["errors"][:20]:
            logger.warning(f"    [{fname}] {err}")


if __name__ == "__main__":
    app()
