"""Create SHACL-generation batches for approved P1 rules."""
from __future__ import annotations

import sys

import typer

from builder.shacl_generator import prepare_shacl_batches


def main(rules_per_batch: int = typer.Option(12, help="Rules per batch file")):
    paths = prepare_shacl_batches(rules_per_batch=rules_per_batch)
    typer.echo(f"Created {len(paths)} SHACL batches.")
    if not paths:
        sys.exit(1)


if __name__ == "__main__":
    typer.run(main)
