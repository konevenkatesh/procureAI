"""
Rule extraction pipeline (batch-prep + loader).

ARCHITECTURE: There is no LLM SDK call here. Instead:
  1. `prepare_extraction_batches()` writes batch JSON files into
     data/extraction_batches/ — each contains the SYSTEM prompt and a list of
     sections to extract from.
  2. The operator (Claude Code) opens a batch file, performs the extraction
     work in conversation, and writes a JSON result file into
     data/extraction_results/ with the same batch_id.
  3. `load_extraction_results()` reads result files and saves CandidateRules
     into Postgres via knowledge_layer.rule_store.

This separation means: no API keys, no per-section LLM round-trip cost, and
the operator can apply judgment + cross-section consistency that a stateless
SDK call cannot.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Optional

from loguru import logger
from pydantic import ValidationError

from builder.config import settings
from builder.section_splitter import get_all_sections
from knowledge_layer.rule_store import save_candidate_rules
from knowledge_layer.schemas import CandidateRule


# ─────────────────────────────────────────────────────────────────────────────
# The extraction prompt — embedded into every batch file so the operator
# (Claude Code) has the spec inline. Any change here affects future batches
# only — already-prepared batches keep their original prompt.
# ─────────────────────────────────────────────────────────────────────────────

EXTRACTION_SYSTEM_PROMPT = """\
You are an expert in Indian government procurement law (GFR 2017, CVC circulars,
AP state GOs, and central procurement manuals).

For each SECTION provided below, extract ALL verifiable compliance rules.
A verifiable rule = something a reviewer can check YES/NO against a tender document.
Skip definitions, interpretive statements, and non-verifiable guidance.

For every rule, output ONE JSON object with these fields:

{
  "rule_id":              "stable ID — see ID rules below",
  "source_doc":           "doc filename without extension (e.g. 'GFR_2017')",
  "source_chapter":       "chapter / part identifier from the doc",
  "source_clause":        "specific clause/rule number (e.g. 'Rule 170(iii)')",
  "source_url":           null or canonical URL,
  "layer":                "Central | CVC | AP-State | Dept",
  "category":             "Financial | Completeness | Governance | Eligibility | Process",
  "pattern_type":         "P1 | P2 | P3 | P4",
  "natural_language":     "ONE sentence. Plain English. What MUST be true.",
  "verification_method":  "How to check: extract X from doc, compare to Y.",
  "condition_when":       "When this rule applies (e.g. 'TenderType=Works AND EstimatedValue>=2500000')",
  "severity":             "HARD_BLOCK | WARNING | ADVISORY",
  "typology_code":        "code from data/risk_typology.json (e.g. 'EMD-Shortfall')",
  "generates_clause":     true/false,
  "defeats":              [],   // rule_ids this overrides
  "defeated_by":          [],   // rule_ids that override this
  "valid_from":           "YYYY-MM-DD effective date of source",
  "valid_until":          null or "YYYY-MM-DD",
  "extracted_from":       "{section_reference} — copy verbatim from input",
  "extraction_confidence":0.0-1.0,
  "critic_verified":      false,   // gets set true by critic pass
  "critic_note":          null,
  "human_status":         "pending"
}

Pattern types:
  P1 = exact formula or threshold        (EMD = 2%, validity = 90 days)
  P2 = clause must exist                 (Integrity Pact clause must be present)
  P3 = exception/override applies        (Limited tender IF single-source justified)
  P4 = semantic judgment                 (specs must not be unduly restrictive)

ID rules:
  Format: {SOURCE_CODE}-{SHORT_TOPIC}-{NUMBER}
  Examples: GFR-EMD-001, CVC-INT-PACT-003, AP-GOMS79-RT-001
  Make IDs stable: same rule re-extracted later should produce the same ID.

Severity guide:
  HARD_BLOCK = legal/financial mandate, missing this stops publication
  WARNING    = best-practice/protective, missing this is risky
  ADVISORY   = recommendation, missing this is acceptable

Output FORMAT: a single JSON object with one key per section reference, mapping
to a list of rule objects. Example:

{
  "GFR_2017/Chapter 6 Rule 170": [ {...rule1...}, {...rule2...} ],
  "GFR_2017/Chapter 6 Rule 171": [ {...rule3...} ]
}

If a section has NO verifiable rules, emit an empty list `[]` for that section.
Output JSON ONLY. No markdown, no commentary.
"""


# ─────────────────────────────────────────────────────────────────────────────
# Batch preparation
# ─────────────────────────────────────────────────────────────────────────────

def prepare_extraction_batches(
    sections_per_batch: int = 15,
    only_doc: Optional[str] = None,
) -> list[Path]:
    """Split all sections into batch files for the operator to process.

    Args:
        sections_per_batch: roughly how many sections per batch file.
        only_doc: if given, only build batches for sections from this doc.

    Returns: list of batch file paths created.
    """
    settings.extraction_batches_dir.mkdir(parents=True, exist_ok=True)

    sections = get_all_sections()
    if only_doc:
        sections = [(ref, txt) for ref, txt in sections if ref.startswith(f"{only_doc}/")]

    if not sections:
        logger.warning("No sections found. Did you run process_all_documents.py?")
        return []

    created: list[Path] = []
    for batch_idx, start in enumerate(range(0, len(sections), sections_per_batch), 1):
        chunk = sections[start : start + sections_per_batch]
        batch_id = f"batch_{batch_idx:04d}"
        payload = {
            "batch_id": batch_id,
            "system_prompt": EXTRACTION_SYSTEM_PROMPT,
            "instructions_for_operator": (
                "Read every section below. For each, extract verifiable rules per the "
                "system_prompt schema. Write results to "
                f"data/extraction_results/{batch_id}.json — same shape as the example "
                "in the system_prompt."
            ),
            "section_count": len(chunk),
            "sections": [
                {"reference": ref, "text": txt} for ref, txt in chunk
            ],
        }
        out_path = settings.extraction_batches_dir / f"{batch_id}.json"
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        created.append(out_path)

    logger.info(f"Prepared {len(created)} extraction batches in {settings.extraction_batches_dir}")
    return created


# ─────────────────────────────────────────────────────────────────────────────
# Result loading
# ─────────────────────────────────────────────────────────────────────────────

def load_extraction_results(batch_glob: str = "*.json") -> dict:
    """Read extraction result files and save valid CandidateRules to Postgres."""
    settings.extraction_results_dir.mkdir(parents=True, exist_ok=True)

    summary = {"files_read": 0, "rules_loaded": 0, "validation_errors": 0, "errors": []}
    candidates: list[CandidateRule] = []

    for result_file in sorted(settings.extraction_results_dir.glob(batch_glob)):
        summary["files_read"] += 1
        try:
            data = json.loads(result_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            summary["errors"].append((result_file.name, f"invalid JSON: {e}"))
            continue

        for section_ref, rules in data.items():
            if not isinstance(rules, list):
                summary["errors"].append((result_file.name, f"{section_ref}: not a list"))
                continue
            for raw in rules:
                # Inject defaults if operator omitted them
                raw.setdefault("extracted_from", section_ref)
                raw.setdefault("human_status", "pending")
                raw.setdefault("critic_verified", False)
                raw.setdefault("valid_from", date.today().isoformat())
                try:
                    candidates.append(CandidateRule(**raw))
                except ValidationError as e:
                    summary["validation_errors"] += 1
                    summary["errors"].append((result_file.name, f"validation: {e.errors()[0]['msg']}"))

    if candidates:
        summary["rules_loaded"] = save_candidate_rules(candidates)
    return summary


def stable_rule_id(source_doc: str, short_topic: str, raw_text: str) -> str:
    """Generate a deterministic rule_id. Operator may use this for novel rules."""
    digest = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()[:6].upper()
    return f"{source_doc.upper()}-{short_topic.upper()}-{digest}"
