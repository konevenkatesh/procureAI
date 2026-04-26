"""
Build rule + clause extraction batches from processed Markdown.

Outputs two parallel batch sets plus a top-level manifest:

    data/extraction_batches/
    ├── manifest.json                   # stats for both sets
    ├── rules/                          # all 9 source docs, ~4 sections per batch
    │   ├── batch_0001.json
    │   └── ...
    └── clauses/                        # MPW_2022 / MPG_2022 / MPS_2017 / MPS_2022
        ├── batch_0001.json             # ~2 sections per batch (clause text is denser)
        └── ...

Each batch is single-doc so the loader can attach `source_doc` to every rule.
The operator (Claude Code) processes each batch in conversation and writes a
flat JSON array result into data/extraction_results/{rules,clauses}/<batch>.json.

Usage:
    python scripts/prepare_extraction_batches.py
    python scripts/prepare_extraction_batches.py --rules-per-batch 5 --clauses-per-batch 3
    python scripts/prepare_extraction_batches.py --kind rules
    python scripts/prepare_extraction_batches.py --kind clauses
"""
from __future__ import annotations

import sys

import typer

from builder.rule_extractor import (
    prepare_clause_batches,
    prepare_rule_batches,
    write_manifest,
)


app = typer.Typer(add_completion=False)


@app.command()
def main(
    kind: str = typer.Option(
        "both",
        "--kind",
        help="Which batches to build: 'rules', 'clauses', or 'both' (default)",
    ),
    rules_per_batch: int = typer.Option(
        4, "--rules-per-batch", help="Sections per rule batch (3-5 recommended)"
    ),
    clauses_per_batch: int = typer.Option(
        2, "--clauses-per-batch", help="Sections per clause batch (2-3 recommended)"
    ),
):
    if kind not in ("rules", "clauses", "both"):
        typer.echo(f"--kind must be rules, clauses, or both (got {kind!r})", err=True)
        raise typer.Exit(2)

    rules_batches: list = []
    clauses_batches: list = []

    if kind in ("rules", "both"):
        rules_batches = prepare_rule_batches(sections_per_batch=rules_per_batch)

    if kind in ("clauses", "both"):
        clauses_batches = prepare_clause_batches(sections_per_batch=clauses_per_batch)

    manifest_path = write_manifest(rules_batches, clauses_batches)

    typer.echo("")
    typer.echo(f"Rules:    {len(rules_batches)} batch files in data/extraction_batches/rules/")
    typer.echo(f"Clauses:  {len(clauses_batches)} batch files in data/extraction_batches/clauses/")
    typer.echo(f"Manifest: {manifest_path.relative_to(manifest_path.parent.parent.parent)}")

    if not rules_batches and not clauses_batches:
        typer.echo(
            "\nNo batches created — no processed Markdown files found.\n"
            "Run `python scripts/process_all_documents.py` first.",
            err=True,
        )
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
