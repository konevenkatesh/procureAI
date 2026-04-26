"""Create vector-concept batches for approved P2 rules."""
from __future__ import annotations

import sys

import typer

from builder.vector_loader import prepare_concept_batches


def main(rules_per_batch: int = typer.Option(20, help="Rules per batch file")):
    paths = prepare_concept_batches(rules_per_batch=rules_per_batch)
    typer.echo(f"Created {len(paths)} concept batches.")
    if not paths:
        sys.exit(1)


if __name__ == "__main__":
    typer.run(main)
