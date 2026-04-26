"""
Build extraction batch files from processed Markdown.

Outputs into data/extraction_batches/. The operator (Claude Code) reads each
batch and writes its result into data/extraction_results/.

Usage:
  python scripts/prepare_extraction_batches.py
  python scripts/prepare_extraction_batches.py --only-doc GFR_2017
  python scripts/prepare_extraction_batches.py --sections-per-batch 10
"""
from __future__ import annotations

import sys
from typing import Optional

import typer

from builder.rule_extractor import prepare_extraction_batches


app = typer.Typer(add_completion=False)


@app.command()
def main(
    sections_per_batch: int = typer.Option(15, help="Sections per batch file"),
    only_doc: Optional[str] = typer.Option(None, help="Restrict to this doc stem (e.g. GFR_2017)"),
):
    paths = prepare_extraction_batches(
        sections_per_batch=sections_per_batch,
        only_doc=only_doc,
    )
    typer.echo(f"\nCreated {len(paths)} batch files:")
    for p in paths[:10]:
        typer.echo(f"  {p.relative_to(p.parent.parent.parent)}")
    if len(paths) > 10:
        typer.echo(f"  ... and {len(paths) - 10} more")
    if not paths:
        sys.exit(1)


if __name__ == "__main__":
    app()
