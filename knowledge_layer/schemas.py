"""
Pydantic schemas — the source of truth for the knowledge layer.

LOCKED CONTRACT: Once data exists in Postgres / Qdrant / Fuseki, do NOT modify
these schemas without a migration plan. Every other module reads from here.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────────────────────

class TenderType(str, Enum):
    WORKS = "Works"
    GOODS = "Goods"
    CONSULTANCY = "Consultancy"
    SERVICES = "Services"
    EPC = "EPC"


class Severity(str, Enum):
    HARD_BLOCK = "HARD_BLOCK"
    WARNING = "WARNING"
    ADVISORY = "ADVISORY"


class PatternType(str, Enum):
    P1 = "P1"   # Atomic — formula/threshold       → SHACL shape
    P2 = "P2"   # Structural — clause must exist   → Vector concept
    P3 = "P3"   # Defeasible — exception/override  → Graph chain
    P4 = "P4"   # Semantic — intent judgment       → LLM reasoning template


class RuleStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    MODIFIED = "modified"


class SourceLayer(str, Enum):
    CENTRAL = "Central"        # GFR, MPW, MPG, MPS, DoE, MakeInIndia, MSE
    CVC = "CVC"                # CVC circulars
    AP_STATE = "AP-State"      # GO.Ms.79, Judicial Preview Act, AP codes
    DEPT = "Dept"              # Water Resources, PR, RTGS SOPs


class RuleCategory(str, Enum):
    FINANCIAL = "Financial"
    COMPLETENESS = "Completeness"
    GOVERNANCE = "Governance"
    ELIGIBILITY = "Eligibility"
    PROCESS = "Process"


# ─────────────────────────────────────────────────────────────────────────────
# Rules
# ─────────────────────────────────────────────────────────────────────────────

class Rule(BaseModel):
    """An approved, production rule. Used at runtime by Validator/Drafter."""
    rule_id: str
    source_doc: str
    source_chapter: str
    source_clause: str
    source_url: Optional[str] = None
    layer: SourceLayer
    category: RuleCategory
    pattern_type: PatternType
    natural_language: str
    verification_method: str
    condition_when: str
    severity: Severity
    typology_code: str
    generates_clause: bool = False
    defeats: list[str] = Field(default_factory=list)
    defeated_by: list[str] = Field(default_factory=list)
    shacl_shape_id: Optional[str] = None
    vector_concept_id: Optional[str] = None
    valid_from: str
    valid_until: Optional[str] = None


class CandidateRule(Rule):
    """A rule extracted from a source section, awaiting human review."""
    extracted_from: str                  # "{doc_name}/{section_heading}"
    extraction_confidence: float = Field(ge=0.0, le=1.0)
    critic_verified: bool = False
    critic_note: Optional[str] = None
    human_status: RuleStatus = RuleStatus.PENDING
    human_note: Optional[str] = None     # reviewer's reason / modification note


# ─────────────────────────────────────────────────────────────────────────────
# Clauses
# ─────────────────────────────────────────────────────────────────────────────

class ClauseParameter(BaseModel):
    name: str
    param_type: Literal["currency", "percentage", "days", "text", "boolean", "date", "integer"]
    formula: Optional[str] = None        # e.g. "estimated_value * 0.02"
    cap: Optional[float] = None          # e.g. EMD capped at Rs. 1 crore
    label: str
    example: Optional[str] = None


class ClauseTemplate(BaseModel):
    clause_id: str                       # e.g. "CLAUSE-FIN-WORKS-015"
    title: str
    text_english: str                    # Jinja2 template
    text_telugu: Optional[str] = None    # Jinja2 template (Telugu)
    parameters: list[ClauseParameter] = Field(default_factory=list)
    applicable_tender_types: list[TenderType]
    mandatory: bool
    position_section: str                # e.g. "Volume-I/Section-1/ITB"
    position_order: int
    cross_references: list[str] = Field(default_factory=list)
    rule_ids: list[str] = Field(default_factory=list)
    valid_from: str
    valid_until: Optional[str] = None
    human_verified: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# SHACL shapes (P1 rules)
# ─────────────────────────────────────────────────────────────────────────────

class SHACLShape(BaseModel):
    shape_id: str                        # e.g. "SHAPE-GFR-W-170"
    rule_id: str
    turtle_content: str                  # raw .ttl content
    test_cases_pass: int = 0
    test_cases_fail: int = 0
    production_ready: bool = False       # true once all test cases pass


# ─────────────────────────────────────────────────────────────────────────────
# Test cases (one per rule, 5 cases each)
# ─────────────────────────────────────────────────────────────────────────────

class TestCase(BaseModel):
    test_id: str                         # e.g. "TC-GFR-W-170-001"
    rule_id: str
    document_excerpt: str                # 50-150 word realistic tender text
    expected_result: Literal["PASS", "FAIL"]
    expected_severity: Optional[Severity] = None
    reasoning: str


# ─────────────────────────────────────────────────────────────────────────────
# Vector concepts (P2 rules)
# ─────────────────────────────────────────────────────────────────────────────

class VectorConcept(BaseModel):
    concept_id: str                      # e.g. "CONCEPT-INTEGRITY-PACT"
    rule_ids: list[str]
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    sac_summary: str                     # Semantic Anchor Concept summary
    threshold_trigger: Optional[dict] = None
    applicable_tender_types: list[TenderType]
    similarity_threshold: float = 0.72
    severity: Severity


# ─────────────────────────────────────────────────────────────────────────────
# Risk typology (45 named categories — seed data)
# ─────────────────────────────────────────────────────────────────────────────

class RiskTypology(BaseModel):
    code: str                            # e.g. "EMD-Shortfall"
    name: str
    definition: str
    rule_ids: list[str] = Field(default_factory=list)
    severity: Severity
    category: RuleCategory
    alice_equivalent: Optional[str] = None  # Brazil ALICE typology mapping
