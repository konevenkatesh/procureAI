"""M1 drafter R7.3 — TechSpecTemplate registry.

70+ discipline-specific Pydantic schemas + LLM prompts + retrieval queries.
Each TechSpec template defines:
  - Pydantic BoQItemOutput class for structured output enforcement
  - retrieval_query: BGE-M3 query template (uses item context)
  - llm_prompt: Vertex AI prompt template (uses {item_name}, {qty}, {project_context})
  - validation_rules: post-generation quality checks
  - expected_output_tokens: rough budget signal

Registry is keyed by f"{discipline}/{sub_discipline}".
"""
from .base import (
    BoQItemOutput,
    TechSpecTemplate,
    REGISTRY,
    get_template,
    all_templates,
)

__all__ = [
    "BoQItemOutput",
    "TechSpecTemplate",
    "REGISTRY",
    "get_template",
    "all_templates",
]
