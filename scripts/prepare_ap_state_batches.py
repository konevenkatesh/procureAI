"""
Build rule + clause extraction batches for the AP State corpus.

Reads only from `source_documents/ap_state/processed_md/*.md` and writes to
`data/extraction_batches/ap_state/{rules,clauses}/`. Each batch is single-doc
so the loader can attach `source_doc` to every extracted item.

Mirrors scripts/prepare_extraction_batches.py but corpus-scoped — does NOT
apply the central CLAUSE_SOURCE_DOCS filter (we want clause templates from
every AP GO / Code / Act, not just MPW/MPG/MPS).

Usage:
    python scripts/prepare_ap_state_batches.py
    python scripts/prepare_ap_state_batches.py --rules-per-batch 4 --clauses-per-batch 2
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import typer

from builder.config import settings
from builder.extractor_prompts import (
    CLAUSE_EXTRACTION_SYSTEM,
    RULE_EXTRACTION_SYSTEM,
)
from builder.section_splitter import sections_for_document

REPO_ROOT = settings.source_documents_dir.parent
SOURCE_DIR = settings.source_documents_dir / "ap_state" / "processed_md"
OUT_DIR = REPO_ROOT / "data" / "extraction_batches" / "ap_state"
RULES_DIR = OUT_DIR / "rules"
CLAUSES_DIR = OUT_DIR / "clauses"


def _word_count(chunk: list[tuple[str, str]]) -> int:
    return sum(len(text.split()) for _, text in chunk)


def _write_batch(
    out_dir: Path,
    batch_id: str,
    kind: str,
    source_doc: str,
    system_prompt: str,
    instructions: str,
    chunk: list[tuple[str, str]],
) -> Path:
    payload = {
        "batch_id": batch_id,
        "kind": kind,
        "corpus": "ap_state",
        "source_doc": source_doc,
        "section_count": len(chunk),
        "word_count": _word_count(chunk),
        "system_prompt": system_prompt,
        "instructions_for_operator": instructions,
        "sections": [{"reference": ref, "text": text} for ref, text in chunk],
    }
    path = out_dir / f"{batch_id}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return path


def _gather_sections() -> dict[str, list[tuple[str, str]]]:
    """{doc_stem: [(ref, body), ...]} — applies SKIP_PATTERNS via section_splitter."""
    by_doc: dict[str, list[tuple[str, str]]] = {}
    for md in sorted(SOURCE_DIR.glob("*.md")):
        secs = sections_for_document(md)
        if secs:
            by_doc[md.stem] = secs
    return by_doc


def _build(
    by_doc: dict[str, list[tuple[str, str]]],
    out_dir: Path,
    kind: str,
    sections_per_batch: int,
    system_prompt: str,
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    idx = 0
    for doc_name in sorted(by_doc):
        secs = by_doc[doc_name]
        for start in range(0, len(secs), sections_per_batch):
            idx += 1
            batch_id = f"batch_{idx:04d}"
            chunk = secs[start : start + sections_per_batch]
            instructions = (
                f"Extract every verifiable {kind[:-1]} from each section below. "
                f"Follow the system_prompt schema exactly. Write the result as a "
                f"flat JSON array (no markdown) to "
                f"data/extraction_results/ap_state/{kind}/{batch_id}.json"
            )
            paths.append(
                _write_batch(
                    out_dir=out_dir,
                    batch_id=batch_id,
                    kind=kind,
                    source_doc=doc_name,
                    system_prompt=system_prompt,
                    instructions=instructions,
                    chunk=chunk,
                )
            )
    return paths


def _summarise(paths: list[Path]) -> dict:
    per_doc_count: dict[str, int] = defaultdict(int)
    per_doc_sections: dict[str, int] = defaultdict(int)
    per_doc_words: dict[str, int] = defaultdict(int)
    batches: list[dict] = []
    total_sections = 0
    total_words = 0
    for p in paths:
        data = json.loads(p.read_text(encoding="utf-8"))
        doc = data["source_doc"]
        per_doc_count[doc] += 1
        per_doc_sections[doc] += data["section_count"]
        per_doc_words[doc] += data["word_count"]
        total_sections += data["section_count"]
        total_words += data["word_count"]
        batches.append({
            "batch_id": data["batch_id"],
            "source_doc": doc,
            "section_count": data["section_count"],
            "word_count": data["word_count"],
            "status": "pending",
        })
    return {
        "total_batches": len(paths),
        "total_sections": total_sections,
        "total_words": total_words,
        "per_document": {
            doc: {
                "batches": per_doc_count[doc],
                "sections": per_doc_sections[doc],
                "words": per_doc_words[doc],
            }
            for doc in sorted(per_doc_count)
        },
        "batches": batches,
    }


app = typer.Typer(add_completion=False)


@app.command()
def main(
    rules_per_batch: int = typer.Option(4, "--rules-per-batch"),
    clauses_per_batch: int = typer.Option(2, "--clauses-per-batch"),
):
    if not SOURCE_DIR.exists():
        typer.echo(f"Missing {SOURCE_DIR}", err=True)
        raise typer.Exit(2)

    by_doc = _gather_sections()
    if not by_doc:
        typer.echo(f"No processed_md files in {SOURCE_DIR}", err=True)
        raise typer.Exit(1)

    rule_paths = _build(by_doc, RULES_DIR, "rules", rules_per_batch, RULE_EXTRACTION_SYSTEM)
    clause_paths = _build(by_doc, CLAUSES_DIR, "clauses", clauses_per_batch, CLAUSE_EXTRACTION_SYSTEM)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "corpus": "ap_state",
        "source_md_dir": str(SOURCE_DIR.relative_to(REPO_ROOT)),
        "documents": [
            {
                "name": doc,
                "sections": len(secs),
                "rule_batches": (len(secs) + rules_per_batch - 1) // rules_per_batch,
                "clause_batches": (len(secs) + clauses_per_batch - 1) // clauses_per_batch,
            }
            for doc, secs in sorted(by_doc.items(), key=lambda kv: -len(kv[1]))
        ],
        "rules": _summarise(rule_paths),
        "clauses": _summarise(clause_paths),
    }
    (OUT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))

    typer.echo("")
    typer.echo(f"Source files:    {len(by_doc)}")
    typer.echo(f"Total sections:  {sum(len(s) for s in by_doc.values())}")
    typer.echo(f"Rule batches:    {len(rule_paths)} → {RULES_DIR.relative_to(REPO_ROOT)}")
    typer.echo(f"Clause batches:  {len(clause_paths)} → {CLAUSES_DIR.relative_to(REPO_ROOT)}")
    typer.echo(f"Manifest:        {(OUT_DIR / 'manifest.json').relative_to(REPO_ROOT)}")
    typer.echo("")
    typer.echo("Top documents by section count:")
    for d in manifest["documents"][:8]:
        typer.echo(f"  {d['sections']:>4} sections  →  {d['name']}  "
                   f"({d['rule_batches']} rule + {d['clause_batches']} clause batches)")


if __name__ == "__main__":
    app()
