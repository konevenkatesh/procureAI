"""
Telugu translation pipeline for clause templates (batch-prep + loader).
"""
from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from builder.config import settings
from knowledge_layer.clause_store import get_clauses_missing_telugu, update_clause_telugu


TELUGU_SYSTEM_PROMPT = """\
You are translating Indian government procurement clauses from English to formal Telugu.

Style requirements:
  - Official government Telugu register — precise, formal, NOT colloquial.
  - Match the tone of AP Government Orders (GOs) and APPWD Code.
  - Keep all {{parameter_name}} placeholders EXACTLY as-is — do not translate or modify them.
  - Keep clause numbers, rule references (e.g. "GFR Rule 170(iii)"), and English abbreviations
    (EMD, PBG, BG, NIT, OEM, MSE, GeM) in English.
  - Keep currency symbols (Rs., ₹) and numerical values unchanged.
  - Where the English clause uses "shall" / "must", use the appropriate Telugu mandatory voice.

Output FORMAT: a single JSON object keyed by clause_id, value is the Telugu translation string.

{
  "CLAUSE-FIN-WORKS-015": "...formal Telugu translation here, with {{parameters}} preserved...",
  "CLAUSE-GOV-WORKS-008": "...another translation..."
}

Output JSON ONLY. No markdown, no commentary, no transliteration aids.
"""


def prepare_telugu_batches(clauses_per_batch: int = 40) -> list[Path]:
    """Build Telugu translation batches for clauses missing text_telugu."""
    settings.telugu_batches_dir.mkdir(parents=True, exist_ok=True)

    clauses = get_clauses_missing_telugu()
    if not clauses:
        logger.info("All clauses already have Telugu translations.")
        return []

    created: list[Path] = []
    for batch_idx, start in enumerate(range(0, len(clauses), clauses_per_batch), 1):
        chunk = clauses[start : start + clauses_per_batch]
        batch_id = f"telugu_batch_{batch_idx:04d}"
        payload = {
            "batch_id": batch_id,
            "system_prompt": TELUGU_SYSTEM_PROMPT,
            "instructions_for_operator": (
                f"Translate every clause's text_english into formal government Telugu. "
                f"Write the result to data/telugu_results/{batch_id}.json."
            ),
            "clause_count": len(chunk),
            "clauses": [
                {
                    "clause_id": c["clause_id"],
                    "title": c["title"],
                    "text_english": c["text_english"],
                }
                for c in chunk
            ],
        }
        out_path = settings.telugu_batches_dir / f"{batch_id}.json"
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        created.append(out_path)

    logger.info(f"Prepared {len(created)} Telugu batches.")
    return created


def load_telugu_results(batch_glob: str = "*.json") -> dict:
    """Read Telugu result files and update clause_templates.text_telugu."""
    settings.telugu_results_dir.mkdir(parents=True, exist_ok=True)

    summary = {"files_read": 0, "clauses_updated": 0, "errors": []}

    for result_file in sorted(settings.telugu_results_dir.glob(batch_glob)):
        summary["files_read"] += 1
        try:
            data = json.loads(result_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            summary["errors"].append((result_file.name, f"invalid JSON: {e}"))
            continue

        for clause_id, telugu_text in data.items():
            if not isinstance(telugu_text, str) or not telugu_text.strip():
                summary["errors"].append((result_file.name, f"{clause_id}: empty/non-string"))
                continue
            try:
                update_clause_telugu(clause_id, telugu_text)
                summary["clauses_updated"] += 1
            except Exception as e:
                summary["errors"].append((result_file.name, f"{clause_id}: {e}"))

    return summary
