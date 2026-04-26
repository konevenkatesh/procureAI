"""
P2 (structural) rules → Vector Concepts in Qdrant via BGE-M3 embeddings.

Pipeline:
  1. For each approved P2 rule, build a VectorConcept (canonical_name, aliases,
     SAC summary). The operator (Claude Code) writes these in a batch the same
     way as clause/SHACL generation — see prepare_concept_batches() / load_concept_results().
  2. Embed each concept's `sac_summary` with BGE-M3 (local, no API).
  3. Upsert into Qdrant + record in Postgres.
"""
from __future__ import annotations

import json
from pathlib import Path

from loguru import logger
from pydantic import ValidationError
from tqdm import tqdm

from builder.config import settings
from knowledge_layer.rule_store import attach_vector_concept, get_approved_rules
from knowledge_layer.schemas import VectorConcept
from knowledge_layer.vector_store import ensure_collection, upsert_concept


CONCEPT_SYSTEM_PROMPT = """\
For each P2 (structural) procurement rule below, produce a Vector Concept used
to detect whether the rule's required clause is present in a tender document.

A "concept" represents the SEMANTIC meaning of the required clause. Aliases
are alternate phrasings the same clause might appear as.

Output ONE JSON object keyed by rule_id, each value is a concept object:

{
  "concept_id":             "CONCEPT-{SHORT-NAME}",
  "rule_ids":               ["GFR-INT-PACT-001"],
  "canonical_name":         "Integrity Pact",
  "aliases":                ["IP", "Integrity Agreement", "Anti-corruption pact"],
  "sac_summary":            "150-300 word Semantic Anchor Concept summary that fully describes what this clause covers, including its purpose, who it binds, and what it requires. This summary is what gets embedded with BGE-M3.",
  "threshold_trigger":      {"value_band": ">=10000000"} OR null,
  "applicable_tender_types":["Works", "Goods", "Consultancy"],
  "similarity_threshold":   0.72,
  "severity":               "HARD_BLOCK | WARNING | ADVISORY"
}

Output JSON ONLY. No markdown.
"""


# ─────────────────────────────────────────────────────────────────────────────
# Batch prep + load (parallel to other generators)
# ─────────────────────────────────────────────────────────────────────────────

def prepare_concept_batches(rules_per_batch: int = 20) -> list[Path]:
    """Build concept-generation batches from approved P2 rules."""
    batches_dir = settings.data_dir / "concept_batches"
    batches_dir.mkdir(parents=True, exist_ok=True)

    rules = get_approved_rules(pattern_type="P2")
    if not rules:
        logger.warning("No approved P2 rules found.")
        return []

    created: list[Path] = []
    for batch_idx, start in enumerate(range(0, len(rules), rules_per_batch), 1):
        chunk = rules[start : start + rules_per_batch]
        batch_id = f"concept_batch_{batch_idx:04d}"
        payload = {
            "batch_id": batch_id,
            "system_prompt": CONCEPT_SYSTEM_PROMPT,
            "instructions_for_operator": (
                f"Build Vector Concepts for the rules below. Write the result "
                f"to data/concept_results/{batch_id}.json."
            ),
            "rule_count": len(chunk),
            "rules": chunk,
        }
        out_path = batches_dir / f"{batch_id}.json"
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        created.append(out_path)

    logger.info(f"Prepared {len(created)} concept batches.")
    return created


def load_concept_results_and_embed(batch_glob: str = "*.json") -> dict:
    """Read concept JSON results, embed with BGE-M3, push to Qdrant + Postgres."""
    results_dir = settings.data_dir / "concept_results"
    results_dir.mkdir(parents=True, exist_ok=True)

    summary = {"files_read": 0, "concepts_loaded": 0, "validation_errors": 0, "errors": []}
    concepts: list[VectorConcept] = []

    for result_file in sorted(results_dir.glob(batch_glob)):
        summary["files_read"] += 1
        try:
            data = json.loads(result_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            summary["errors"].append((result_file.name, f"invalid JSON: {e}"))
            continue

        for rule_id, raw in data.items():
            raw.setdefault("rule_ids", [rule_id])
            try:
                concepts.append(VectorConcept(**raw))
            except ValidationError as e:
                summary["validation_errors"] += 1
                summary["errors"].append((result_file.name, f"validation: {e.errors()[0]['msg']}"))

    if not concepts:
        return summary

    # Embed + upsert
    ensure_collection()
    embedder = _load_embedder()

    for concept in tqdm(concepts, desc="Embedding + upserting"):
        try:
            vec = embedder.encode(concept.sac_summary, normalize_embeddings=True).tolist()
            if len(vec) != settings.bge_m3_dim:
                raise RuntimeError(f"Got dim {len(vec)}, expected {settings.bge_m3_dim}")
            upsert_concept(concept, vec)
            for rid in concept.rule_ids:
                attach_vector_concept(rid, concept.concept_id)
            summary["concepts_loaded"] += 1
        except Exception as e:
            summary["errors"].append((concept.concept_id, str(e)))

    return summary


# ─────────────────────────────────────────────────────────────────────────────
# Embedder loader (cached singleton)
# ─────────────────────────────────────────────────────────────────────────────

_embedder = None


def _load_embedder():
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer
        logger.info(f"Loading {settings.bge_m3_model} (one-time, ~2GB)...")
        _embedder = SentenceTransformer(settings.bge_m3_model)
    return _embedder
