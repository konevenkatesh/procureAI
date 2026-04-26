"""
SHACL shape generation pipeline for P1 (atomic) rules — batch-prep + loader.

The operator (Claude Code) writes SHACL Turtle for each rule.  Loader validates
the Turtle locally with rdflib before saving to Postgres + writing a .ttl file
into ontology/shacl_shapes/.
"""
from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from builder.config import settings
from knowledge_layer.rule_store import attach_shacl_shape, get_approved_rules
from knowledge_layer.schemas import SHACLShape
from knowledge_layer.shacl_store import save_shacl_shape, validate_turtle


SHACL_SYSTEM_PROMPT = """\
Convert each P1 (atomic) procurement rule below to valid SHACL Turtle syntax.

Required prefixes (declare every time):
  @prefix ap:   <https://procurement.ap.gov.in/ontology#> .
  @prefix sh:   <http://www.w3.org/ns/shacl#> .
  @prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
  @prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
  @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

Requirements:
  - Use sh:NodeShape with sh:targetClass against ap:WorksTender / ap:GoodsTender /
    ap:ConsultancyTender / ap:ServicesTender / ap:EPCTender (subset of TenderType).
  - Use sh:property with sh:path for atomic field constraints.
  - Use sh:sparql for any constraint that requires calculation across fields.
  - sh:message MUST quote the rule_id and source_clause verbatim, e.g.
        sh:message "GFR-EMD-001 (GFR Rule 170(iii)): EMD must be >= 2% of estimated value."
  - sh:severity:
        sh:Violation  → for HARD_BLOCK
        sh:Warning    → for WARNING
        sh:Info       → for ADVISORY
  - Shape ID format: SHAPE-{rule_id}, e.g. SHAPE-GFR-EMD-001 → ap:Shape-GFR-EMD-001

Output FORMAT: a single JSON object keyed by rule_id, each value is the full
Turtle text for that shape (string). Example:

{
  "GFR-EMD-001": "@prefix ap: <...> .\\nap:Shape-GFR-EMD-001 a sh:NodeShape ;\\n  sh:targetClass ap:WorksTender ;\\n  ...",
  "AP-GOMS79-RT-001": "@prefix ap: <...> .\\n..."
}

Output JSON ONLY. No markdown, no commentary. Make sure newlines are properly
escaped as \\n inside the JSON string values.
"""


def prepare_shacl_batches(rules_per_batch: int = 12) -> list[Path]:
    """Build SHACL-generation batches for approved P1 rules."""
    settings.shacl_batches_dir.mkdir(parents=True, exist_ok=True)

    rules = get_approved_rules(pattern_type="P1")
    if not rules:
        logger.warning("No approved P1 rules found.")
        return []

    created: list[Path] = []
    for batch_idx, start in enumerate(range(0, len(rules), rules_per_batch), 1):
        chunk = rules[start : start + rules_per_batch]
        batch_id = f"shacl_batch_{batch_idx:04d}"
        payload = {
            "batch_id": batch_id,
            "system_prompt": SHACL_SYSTEM_PROMPT,
            "instructions_for_operator": (
                f"Generate SHACL Turtle for each rule. Write the result to "
                f"data/shacl_results/{batch_id}.json."
            ),
            "rule_count": len(chunk),
            "rules": chunk,
        }
        out_path = settings.shacl_batches_dir / f"{batch_id}.json"
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        created.append(out_path)

    logger.info(f"Prepared {len(created)} SHACL batches.")
    return created


def load_shacl_results(batch_glob: str = "*.json") -> dict:
    """Read SHACL result files, validate Turtle, and save to DB + .ttl files."""
    settings.shacl_results_dir.mkdir(parents=True, exist_ok=True)
    settings.shacl_shapes_dir.mkdir(parents=True, exist_ok=True)

    summary = {"files_read": 0, "shapes_loaded": 0, "invalid_turtle": 0, "errors": []}

    for result_file in sorted(settings.shacl_results_dir.glob(batch_glob)):
        summary["files_read"] += 1
        try:
            data = json.loads(result_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            summary["errors"].append((result_file.name, f"invalid JSON: {e}"))
            continue

        for rule_id, turtle in data.items():
            if not isinstance(turtle, str) or not turtle.strip():
                summary["errors"].append((result_file.name, f"{rule_id}: empty turtle"))
                continue

            ok, msg = validate_turtle(turtle)
            if not ok:
                summary["invalid_turtle"] += 1
                summary["errors"].append((result_file.name, f"{rule_id}: {msg}"))
                continue

            shape_id = f"SHAPE-{rule_id}"
            shape = SHACLShape(
                shape_id=shape_id,
                rule_id=rule_id,
                turtle_content=turtle,
            )
            try:
                save_shacl_shape(shape)
                attach_shacl_shape(rule_id, shape_id)
                # Also dump to a .ttl file for ops/debugging
                ttl_path = settings.shacl_shapes_dir / f"{shape_id}.ttl"
                ttl_path.write_text(turtle, encoding="utf-8")
                summary["shapes_loaded"] += 1
            except Exception as e:
                summary["errors"].append((result_file.name, f"{rule_id}: {e}"))

    return summary
