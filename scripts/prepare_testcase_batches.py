"""Create test-case batches (5 cases per approved rule)."""
from __future__ import annotations

import sys

import typer

from builder.test_case_generator import prepare_testcase_batches


def main(rules_per_batch: int = typer.Option(20, help="Rules per batch file")):
    paths = prepare_testcase_batches(rules_per_batch=rules_per_batch)
    typer.echo(f"Created {len(paths)} test-case batches.")
    if not paths:
        sys.exit(1)


if __name__ == "__main__":
    typer.run(main)
