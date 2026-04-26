"""Create Telugu translation batches for clauses missing text_telugu."""
from __future__ import annotations

import sys

import typer

from builder.telugu_generator import prepare_telugu_batches


def main(clauses_per_batch: int = typer.Option(40, help="Clauses per batch file")):
    paths = prepare_telugu_batches(clauses_per_batch=clauses_per_batch)
    typer.echo(f"Created {len(paths)} Telugu batches.")
    if not paths:
        sys.exit(0)


if __name__ == "__main__":
    typer.run(main)
