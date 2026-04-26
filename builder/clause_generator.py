"""
Clause-template generation pipeline (batch-prep + loader).

Same pattern as rule_extractor: writes batch files for the operator (Claude
Code) to fill in, then loads the JSON results into Postgres.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from loguru import logger
from pydantic import ValidationError

from builder.config import settings
from knowledge_layer.clause_store import save_clause_templates
from knowledge_layer.rule_store import get_approved_rules
from knowledge_layer.schemas import ClauseTemplate


CLAUSE_SYSTEM_PROMPT = """\
You are an expert drafter of Indian government tender documents.
Write in the formal register of AP government procurement.

For each RULE provided below (where generates_clause=true), generate a
parameterised Jinja2 clause template. Use {{parameter_name}} placeholders for
every variable value (amounts, percentages, dates, names).

For each rule output ONE JSON object:

{
  "clause_id":               "CLAUSE-{CATEGORY-SHORT}-{TENDER-SHORT}-{NNN}",
  "title":                   "Short clause title",
  "text_english":            "Full formal clause body with {{parameters}}",
  "text_telugu":             null,                     // filled by translator phase
  "parameters": [
    {
      "name":      "parameter_name",
      "param_type":"currency|percentage|days|text|boolean|date|integer",
      "formula":   "estimated_value * 0.02 OR null",
      "cap":       null OR numeric cap,
      "label":     "Human-readable label for the form field",
      "example":   "Rs. 2,00,000"
    }
  ],
  "applicable_tender_types": ["Works"],            // subset of TenderType enum
  "mandatory":               true,
  "position_section":        "Volume-I/Section-1/ITB",   // where in tender doc
  "position_order":          15,                          // sort order within section
  "cross_references":        [],                          // other clause_ids
  "rule_ids":                ["GFR-EMD-001"],             // rules this clause implements
  "valid_from":              "YYYY-MM-DD",
  "valid_until":             null,
  "human_verified":          false
}

Output FORMAT: a single JSON object keyed by rule_id, each value is a list of
clause objects. (Most rules → 1 clause, some → multiple variants.)

{
  "GFR-EMD-001": [ {...clause...} ],
  "AP-GOMS79-RT-001": [ {...clauseA...}, {...clauseB...} ]
}

Output JSON ONLY. No markdown, no commentary.
"""


def prepare_clause_batches(rules_per_batch: int = 25) -> list[Path]:
    """Build clause-generation batches from approved rules where generates_clause=true."""
    settings.clause_batches_dir.mkdir(parents=True, exist_ok=True)

    rules = [r for r in get_approved_rules() if r.get("generates_clause")]
    if not rules:
        logger.warning("No approved rules with generates_clause=true found.")
        return []

    created: list[Path] = []
    for batch_idx, start in enumerate(range(0, len(rules), rules_per_batch), 1):
        chunk = rules[start : start + rules_per_batch]
        batch_id = f"clause_batch_{batch_idx:04d}"
        payload = {
            "batch_id": batch_id,
            "system_prompt": CLAUSE_SYSTEM_PROMPT,
            "instructions_for_operator": (
                f"Generate Jinja2 clause templates for the rules below. "
                f"Write the result to data/clause_results/{batch_id}.json."
            ),
            "rule_count": len(chunk),
            "rules": chunk,
        }
        out_path = settings.clause_batches_dir / f"{batch_id}.json"
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        created.append(out_path)

    logger.info(f"Prepared {len(created)} clause batches.")
    return created


def load_clause_results(batch_glob: str = "*.json") -> dict:
    """Read clause result files and save ClauseTemplates to Postgres."""
    settings.clause_results_dir.mkdir(parents=True, exist_ok=True)

    summary = {"files_read": 0, "clauses_loaded": 0, "validation_errors": 0, "errors": []}
    clauses: list[ClauseTemplate] = []

    for result_file in sorted(settings.clause_results_dir.glob(batch_glob)):
        summary["files_read"] += 1
        try:
            data = json.loads(result_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            summary["errors"].append((result_file.name, f"invalid JSON: {e}"))
            continue

        for rule_id, clause_list in data.items():
            if not isinstance(clause_list, list):
                continue
            for raw in clause_list:
                raw.setdefault("valid_from", date.today().isoformat())
                raw.setdefault("rule_ids", [rule_id])
                raw.setdefault("human_verified", False)
                try:
                    clauses.append(ClauseTemplate(**raw))
                except ValidationError as e:
                    summary["validation_errors"] += 1
                    summary["errors"].append((result_file.name, f"validation: {e.errors()[0]['msg']}"))

    if clauses:
        summary["clauses_loaded"] = save_clause_templates(clauses)
    return summary
