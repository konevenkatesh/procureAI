"""
Rule + clause extraction pipeline (batch-prep + result loaders).

ARCHITECTURE: there is no LLM SDK call in this module. Instead, batch JSON
files are prepared into data/extraction_batches/{rules,clauses}/ — each file
contains the system prompt (from builder/extractor_prompts.py) and the source
sections to extract from. The operator (Claude Code) reads each batch in
conversation, performs the extraction, and writes a flat JSON array result
into data/extraction_results/{rules,clauses}/. The loader reads result files,
cross-references batch metadata to inject `source_doc`, infers `category` from
typology code, and saves CandidateRule / ClauseTemplate rows to Postgres.

Two modes:
  rules    → all 9 source documents, ~4 sections per batch, RULE_EXTRACTION_SYSTEM
  clauses  → MPW_2022 / MPG_2022 / MPS_2017 / MPS_2022 only,
             ~2 sections per batch (clause text is denser),
             intro/definition/appendix sections excluded,
             CLAUSE_EXTRACTION_SYSTEM
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from loguru import logger
from pydantic import ValidationError

from builder.config import settings
from builder.extractor_prompts import (
    CLAUSE_EXTRACTION_SYSTEM,
    RULE_EXTRACTION_SYSTEM,
)
from builder.section_splitter import get_all_sections, get_clause_sections
from knowledge_layer.rule_store import save_candidate_rules
from knowledge_layer.schemas import CandidateRule, ClauseTemplate


# ─────────────────────────────────────────────────────────────────────────────
# Typology → category mapping (loader uses this to set CandidateRule.category)
# ─────────────────────────────────────────────────────────────────────────────

_TYPOLOGY_PREFIX_TO_CATEGORY: list[tuple[str, str]] = [
    # Order matters: longest / most-specific prefixes first.
    ("Bid-Validity",      "Financial"),
    ("BG-Validity",       "Financial"),
    ("Available-Bid",     "Financial"),
    ("Mobilisation",      "Financial"),
    ("EMD",               "Financial"),
    ("PBG",               "Financial"),
    ("Solvency",          "Financial"),

    ("Missing",           "Completeness"),
    ("Duplicate",         "Completeness"),

    ("Corrigendum",       "Process"),
    ("Stale",             "Process"),
    ("Jurisdiction",      "Process"),
    ("Pre-Bid",           "Process"),
    ("Financial-Proposal","Process"),
    ("Technical-In",      "Process"),

    ("Judicial",          "Governance"),
    ("Reverse-Tender",    "Governance"),
    ("E-Procurement",     "Governance"),
    ("Post-Tender",       "Governance"),
    ("Single-Source",     "Governance"),
    ("COI",               "Governance"),
    ("GeM",               "Governance"),
    ("Blacklist",         "Governance"),

    ("Spec-Tailoring",    "Eligibility"),
    ("Criteria",          "Eligibility"),
    ("Turnover",          "Eligibility"),
    ("Geographic",        "Eligibility"),
    ("Certification",     "Eligibility"),
    ("Startup",           "Eligibility"),
    ("Key-Personnel",     "Eligibility"),
    ("Multiple-CVs",      "Eligibility"),

    ("Bid-Splitting",     "Collusion"),
    ("Cover-Bidding",     "Collusion"),

    ("MSE",               "Compliance"),
    ("MakeInIndia",       "Compliance"),
    ("Sub-Consultant",    "Compliance"),
    ("Arbitration",       "Compliance"),
    ("DLP",               "Compliance"),
]


def _category_from_typology(typology_code: str) -> str:
    """Best-effort category inference from typology code prefix.

    Falls back to 'Process' if no prefix matches — operator can override at
    review time. (Was 'General' in the original draft, but that isn't in the
    RuleCategory enum.)
    """
    for prefix, category in _TYPOLOGY_PREFIX_TO_CATEGORY:
        if typology_code.startswith(prefix):
            return category
    return "Process"


# ─────────────────────────────────────────────────────────────────────────────
# Batch-payload builders
# ─────────────────────────────────────────────────────────────────────────────

def _word_count(sections: list[tuple[str, str]]) -> int:
    return sum(len(text.split()) for _, text in sections)


def _group_by_doc(sections: list[tuple[str, str]]) -> dict[str, list[tuple[str, str]]]:
    by_doc: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for ref, text in sections:
        by_doc[ref.split("/", 1)[0]].append((ref, text))
    return by_doc


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
        "source_doc": source_doc,
        "section_count": len(chunk),
        "word_count": _word_count(chunk),
        "system_prompt": system_prompt,
        "instructions_for_operator": instructions,
        "sections": [{"reference": ref, "text": text} for ref, text in chunk],
    }
    out_path = out_dir / f"{batch_id}.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return out_path


# ─────────────────────────────────────────────────────────────────────────────
# Public: prepare RULE batches (all 9 docs, ~4 sections each)
# ─────────────────────────────────────────────────────────────────────────────

def prepare_rule_batches(sections_per_batch: int = 4) -> list[Path]:
    """Build per-document rule-extraction batches.

    Each batch is single-doc so the loader can inject `source_doc` from
    batch metadata into every CandidateRule.
    """
    rules_dir = settings.extraction_batches_dir / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)

    by_doc = _group_by_doc(get_all_sections())
    if not by_doc:
        logger.warning("No sections found. Did you run scripts/process_all_documents.py?")
        return []

    batches: list[Path] = []
    batch_idx = 0
    for doc_name in sorted(by_doc):
        sections = by_doc[doc_name]
        for start in range(0, len(sections), sections_per_batch):
            batch_idx += 1
            batch_id = f"batch_{batch_idx:04d}"
            chunk = sections[start : start + sections_per_batch]
            instructions = (
                f"Extract every verifiable compliance rule from each section below. "
                f"Follow the system_prompt schema exactly. Write the result as a flat "
                f"JSON array (no markdown) to "
                f"data/extraction_results/rules/{batch_id}.json"
            )
            path = _write_batch(
                out_dir=rules_dir,
                batch_id=batch_id,
                kind="rules",
                source_doc=doc_name,
                system_prompt=RULE_EXTRACTION_SYSTEM,
                instructions=instructions,
                chunk=chunk,
            )
            batches.append(path)

    logger.info(
        f"Prepared {len(batches)} rule batches across {len(by_doc)} document(s) "
        f"in {rules_dir}"
    )
    return batches


# ─────────────────────────────────────────────────────────────────────────────
# Public: prepare CLAUSE batches (MPW/MPG/MPS only, ~2 sections each, filtered)
# ─────────────────────────────────────────────────────────────────────────────

def prepare_clause_batches(sections_per_batch: int = 2) -> list[Path]:
    """Build per-document clause-extraction batches.

    Only sections from MPW_2022 / MPG_2022 / MPS_2017 / MPS_2022, with intro /
    definition / appendix sections filtered out by section_splitter.get_clause_sections().
    """
    clauses_dir = settings.extraction_batches_dir / "clauses"
    clauses_dir.mkdir(parents=True, exist_ok=True)

    by_doc = _group_by_doc(get_clause_sections())
    if not by_doc:
        logger.warning(
            "No clause-eligible sections found. Either no MPW/MPG/MPS documents "
            "have been processed, or every section was filtered out."
        )
        return []

    batches: list[Path] = []
    batch_idx = 0
    for doc_name in sorted(by_doc):
        sections = by_doc[doc_name]
        for start in range(0, len(sections), sections_per_batch):
            batch_idx += 1
            batch_id = f"batch_{batch_idx:04d}"
            chunk = sections[start : start + sections_per_batch]
            instructions = (
                f"Extract complete clause templates with {{parameter}} placeholders. "
                f"Follow the system_prompt schema exactly. Write the result as a flat "
                f"JSON array (no markdown) to "
                f"data/extraction_results/clauses/{batch_id}.json"
            )
            path = _write_batch(
                out_dir=clauses_dir,
                batch_id=batch_id,
                kind="clauses",
                source_doc=doc_name,
                system_prompt=CLAUSE_EXTRACTION_SYSTEM,
                instructions=instructions,
                chunk=chunk,
            )
            batches.append(path)

    logger.info(
        f"Prepared {len(batches)} clause batches across {len(by_doc)} document(s) "
        f"in {clauses_dir}"
    )
    return batches


# ─────────────────────────────────────────────────────────────────────────────
# Manifest writer (data/extraction_batches/manifest.json)
# ─────────────────────────────────────────────────────────────────────────────

def write_manifest(rules_batches: list[Path], clauses_batches: list[Path]) -> Path:
    """Summarise batch inventory + per-doc / per-batch stats."""
    def summarise(paths: list[Path]) -> dict:
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

    settings.extraction_batches_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rules": summarise(rules_batches),
        "clauses": summarise(clauses_batches),
    }
    out = settings.extraction_batches_dir / "manifest.json"
    out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Loader: data/extraction_results/{kind}/*.json → Postgres
# ─────────────────────────────────────────────────────────────────────────────

def load_extraction_results(kind: str = "rules", batch_glob: str = "*.json") -> dict:
    """Load extraction results into Postgres.

    Looks up the corresponding batch metadata file (in data/extraction_batches/{kind}/)
    to inject `source_doc` per rule.
    """
    if kind not in ("rules", "clauses"):
        raise ValueError(f"kind must be 'rules' or 'clauses', got {kind!r}")

    results_dir = settings.extraction_results_dir / kind
    batches_dir = settings.extraction_batches_dir / kind
    results_dir.mkdir(parents=True, exist_ok=True)

    if kind == "rules":
        return _load_rule_results(results_dir, batches_dir, batch_glob)
    return _load_clause_results(results_dir, batches_dir, batch_glob)


def _load_rule_results(results_dir: Path, batches_dir: Path, batch_glob: str) -> dict:
    summary = {
        "files_read": 0,
        "rules_loaded": 0,
        "validation_errors": 0,
        "errors": [],
    }
    candidates: list[CandidateRule] = []

    for result_file in sorted(results_dir.glob(batch_glob)):
        summary["files_read"] += 1
        try:
            data = json.loads(result_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            summary["errors"].append((result_file.name, f"invalid JSON: {e}"))
            continue
        if not isinstance(data, list):
            summary["errors"].append((result_file.name, "expected JSON array at top level"))
            continue

        # Cross-reference batch file for source_doc
        source_doc = "unknown"
        batch_meta = batches_dir / result_file.name
        if batch_meta.exists():
            try:
                source_doc = json.loads(batch_meta.read_text())["source_doc"]
            except Exception:
                pass

        for raw in data:
            raw.setdefault("source_doc", source_doc)
            raw.setdefault("source_chapter", "")
            raw.setdefault(
                "category",
                _category_from_typology(raw.get("typology_code", "")),
            )
            raw.setdefault("valid_from", date.today().isoformat())
            raw.setdefault("extracted_from", result_file.stem)
            raw.setdefault("extraction_confidence", 0.85)
            raw.setdefault("human_status", "pending")
            raw.setdefault("critic_verified", False)
            try:
                candidates.append(CandidateRule(**raw))
            except ValidationError as e:
                summary["validation_errors"] += 1
                msg = e.errors()[0]["msg"] if e.errors() else str(e)
                summary["errors"].append((result_file.name, f"validation: {msg}"))

    if candidates:
        summary["rules_loaded"] = save_candidate_rules(candidates)
    return summary


def _load_clause_results(results_dir: Path, batches_dir: Path, batch_glob: str) -> dict:
    from knowledge_layer.clause_store import save_clause_templates

    summary = {
        "files_read": 0,
        "clauses_loaded": 0,
        "validation_errors": 0,
        "errors": [],
    }
    clauses: list[ClauseTemplate] = []

    for result_file in sorted(results_dir.glob(batch_glob)):
        summary["files_read"] += 1
        try:
            data = json.loads(result_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            summary["errors"].append((result_file.name, f"invalid JSON: {e}"))
            continue
        if not isinstance(data, list):
            summary["errors"].append((result_file.name, "expected JSON array at top level"))
            continue

        for raw in data:
            raw.setdefault("valid_from", date.today().isoformat())
            raw.setdefault("text_telugu", None)
            raw.setdefault("human_verified", False)
            try:
                clauses.append(ClauseTemplate(**raw))
            except ValidationError as e:
                summary["validation_errors"] += 1
                msg = e.errors()[0]["msg"] if e.errors() else str(e)
                summary["errors"].append((result_file.name, f"validation: {msg}"))

    if clauses:
        summary["clauses_loaded"] = save_clause_templates(clauses)
    return summary


# ─────────────────────────────────────────────────────────────────────────────
# Helper for novel rule IDs (operator may use)
# ─────────────────────────────────────────────────────────────────────────────

def stable_rule_id(source_doc: str, short_topic: str, raw_text: str) -> str:
    """Generate a deterministic rule_id from a stable digest of the rule text."""
    digest = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()[:6].upper()
    return f"{source_doc.upper()}-{short_topic.upper()}-{digest}"
