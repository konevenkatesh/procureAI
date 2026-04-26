"""
SQLAlchemy ORM models. These mirror the Pydantic schemas in schemas.py.

Naming convention: every model is suffixed with `Model` to keep imports
unambiguous (Rule = Pydantic schema, RuleModel = DB row).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Rules
# ─────────────────────────────────────────────────────────────────────────────

class RuleModel(Base):
    __tablename__ = "rules"

    rule_id = Column(String(64), primary_key=True)
    source_doc = Column(String(128), nullable=False, index=True)
    source_chapter = Column(String(128))
    source_clause = Column(String(128))
    source_url = Column(String(512))
    layer = Column(String(32), nullable=False, index=True)
    category = Column(String(32), nullable=False, index=True)
    pattern_type = Column(String(2), nullable=False, index=True)

    natural_language = Column(Text, nullable=False)
    verification_method = Column(Text, nullable=False)
    condition_when = Column(Text, nullable=False)
    severity = Column(String(16), nullable=False, index=True)
    typology_code = Column(String(64), nullable=False, index=True)
    generates_clause = Column(Boolean, default=False)

    defeats = Column(JSON, default=list)              # list[str] of rule_ids
    defeated_by = Column(JSON, default=list)
    shacl_shape_id = Column(String(64))
    vector_concept_id = Column(String(64))

    valid_from = Column(String(16), nullable=False)
    valid_until = Column(String(16))

    # candidate-only fields (populated during extraction)
    extracted_from = Column(String(256))
    extraction_confidence = Column(Float)
    critic_verified = Column(Boolean, default=False)
    critic_note = Column(Text)
    human_status = Column(String(16), default="pending", index=True)
    human_note = Column(Text)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ─────────────────────────────────────────────────────────────────────────────
# Clause templates
# ─────────────────────────────────────────────────────────────────────────────

class ClauseTemplateModel(Base):
    __tablename__ = "clause_templates"

    clause_id = Column(String(64), primary_key=True)
    title = Column(String(256), nullable=False)
    text_english = Column(Text, nullable=False)
    text_telugu = Column(Text)

    parameters = Column(JSON, default=list)           # list[ClauseParameter dicts]
    applicable_tender_types = Column(JSON, default=list)
    mandatory = Column(Boolean, default=True)
    position_section = Column(String(128))
    position_order = Column(Integer)

    cross_references = Column(JSON, default=list)
    rule_ids = Column(JSON, default=list)

    valid_from = Column(String(16), nullable=False)
    valid_until = Column(String(16))
    human_verified = Column(Boolean, default=False, index=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ─────────────────────────────────────────────────────────────────────────────
# SHACL shapes
# ─────────────────────────────────────────────────────────────────────────────

class SHACLShapeModel(Base):
    __tablename__ = "shacl_shapes"

    shape_id = Column(String(64), primary_key=True)
    rule_id = Column(String(64), ForeignKey("rules.rule_id"), nullable=False, index=True)
    turtle_content = Column(Text, nullable=False)
    test_cases_pass = Column(Integer, default=0)
    test_cases_fail = Column(Integer, default=0)
    production_ready = Column(Boolean, default=False, index=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ─────────────────────────────────────────────────────────────────────────────
# Test cases
# ─────────────────────────────────────────────────────────────────────────────

class TestCaseModel(Base):
    __tablename__ = "test_cases"

    test_id = Column(String(64), primary_key=True)
    rule_id = Column(String(64), ForeignKey("rules.rule_id"), nullable=False, index=True)
    document_excerpt = Column(Text, nullable=False)
    expected_result = Column(String(8), nullable=False)        # PASS | FAIL
    expected_severity = Column(String(16))
    reasoning = Column(Text, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow)


# ─────────────────────────────────────────────────────────────────────────────
# Vector concepts (kept in Postgres as the source-of-truth; Qdrant has the embedding)
# ─────────────────────────────────────────────────────────────────────────────

class VectorConceptModel(Base):
    __tablename__ = "vector_concepts"

    concept_id = Column(String(64), primary_key=True)
    rule_ids = Column(JSON, default=list)
    canonical_name = Column(String(256), nullable=False)
    aliases = Column(JSON, default=list)
    sac_summary = Column(Text, nullable=False)
    threshold_trigger = Column(JSON)
    applicable_tender_types = Column(JSON, default=list)
    similarity_threshold = Column(Float, default=0.72)
    severity = Column(String(16), nullable=False)

    qdrant_point_id = Column(String(64))                       # mirror of Qdrant point ID
    created_at = Column(DateTime, default=datetime.utcnow)


# ─────────────────────────────────────────────────────────────────────────────
# Risk typology (45 named categories)
# ─────────────────────────────────────────────────────────────────────────────

class RiskTypologyModel(Base):
    __tablename__ = "risk_typology"

    code = Column(String(64), primary_key=True)
    name = Column(String(128), nullable=False)
    definition = Column(Text, nullable=False)
    rule_ids = Column(JSON, default=list)
    severity = Column(String(16), nullable=False)
    category = Column(String(32), nullable=False)
    alice_equivalent = Column(String(128))
