"""Create clause-generation batches from approved rules where generates_clause=true."""
from __future__ import annotations

import sys

import typer

from builder.clause_generator import prepare_clause_batches


def main(rules_per_batch: int = typer.Option(25, help="Rules per batch file")):
    paths = prepare_clause_batches(rules_per_batch=rules_per_batch)
    typer.echo(f"Created {len(paths)} clause batches.")
    if not paths:
        sys.exit(1)


if __name__ == "__main__":
    typer.run(main)
