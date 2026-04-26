"""CRUD operations for rules."""
from __future__ import annotations

from typing import Optional

from knowledge_layer.database import get_session
from knowledge_layer.models import RuleModel
from knowledge_layer.schemas import CandidateRule, RuleStatus


def save_candidate_rules(candidates: list[CandidateRule]) -> int:
    """Insert candidate rules. Returns number saved (skips duplicates by rule_id)."""
    saved = 0
    with get_session() as session:
        existing_ids = {r[0] for r in session.query(RuleModel.rule_id).all()}
        for c in candidates:
            if c.rule_id in existing_ids:
                continue
            row = RuleModel(
                rule_id=c.rule_id,
                source_doc=c.source_doc,
                source_chapter=c.source_chapter,
                source_clause=c.source_clause,
                source_url=c.source_url,
                layer=c.layer.value,
                category=c.category.value,
                pattern_type=c.pattern_type.value,
                natural_language=c.natural_language,
                verification_method=c.verification_method,
                condition_when=c.condition_when,
                severity=c.severity.value,
                typology_code=c.typology_code,
                generates_clause=c.generates_clause,
                defeats=c.defeats,
                defeated_by=c.defeated_by,
                shacl_shape_id=c.shacl_shape_id,
                vector_concept_id=c.vector_concept_id,
                valid_from=c.valid_from,
                valid_until=c.valid_until,
                extracted_from=c.extracted_from,
                extraction_confidence=c.extraction_confidence,
                critic_verified=c.critic_verified,
                critic_note=c.critic_note,
                human_status=c.human_status.value,
            )
            session.add(row)
            saved += 1
    return saved


def get_pending_rules(limit: int = 20) -> list[dict]:
    """Return rules awaiting human review, oldest first."""
    with get_session() as session:
        rows = (
            session.query(RuleModel)
            .filter(RuleModel.human_status == RuleStatus.PENDING.value)
            .order_by(RuleModel.created_at.asc())
            .limit(limit)
            .all()
        )
        return [_row_to_dict(r) for r in rows]


def get_approved_rules(pattern_type: Optional[str] = None) -> list[dict]:
    """Return approved (or modified-and-approved) rules. Optionally filter by P1/P2/P3/P4."""
    with get_session() as session:
        q = session.query(RuleModel).filter(
            RuleModel.human_status.in_([RuleStatus.APPROVED.value, RuleStatus.MODIFIED.value])
        )
        if pattern_type:
            q = q.filter(RuleModel.pattern_type == pattern_type)
        return [_row_to_dict(r) for r in q.all()]


def update_rule_status(
    rule_id: str,
    status: str,
    note: Optional[str] = None,
    modified_text: Optional[str] = None,
    modified_severity: Optional[str] = None,
) -> None:
    """Update human review outcome."""
    with get_session() as session:
        row = session.query(RuleModel).filter_by(rule_id=rule_id).first()
        if not row:
            raise ValueError(f"Rule {rule_id} not found")
        row.human_status = status
        if note:
            row.human_note = note
        if modified_text:
            row.natural_language = modified_text
        if modified_severity:
            row.severity = modified_severity


def attach_shacl_shape(rule_id: str, shape_id: str) -> None:
    with get_session() as session:
        row = session.query(RuleModel).filter_by(rule_id=rule_id).first()
        if row:
            row.shacl_shape_id = shape_id


def attach_vector_concept(rule_id: str, concept_id: str) -> None:
    with get_session() as session:
        row = session.query(RuleModel).filter_by(rule_id=rule_id).first()
        if row:
            row.vector_concept_id = concept_id


def _row_to_dict(row: RuleModel) -> dict:
    return {
        "rule_id": row.rule_id,
        "source_doc": row.source_doc,
        "source_chapter": row.source_chapter,
        "source_clause": row.source_clause,
        "source_url": row.source_url,
        "layer": row.layer,
        "category": row.category,
        "pattern_type": row.pattern_type,
        "natural_language": row.natural_language,
        "rule_text": row.natural_language,           # alias for review CLI
        "verification_method": row.verification_method,
        "condition_when": row.condition_when,
        "severity": row.severity,
        "typology_code": row.typology_code,
        "generates_clause": row.generates_clause,
        "defeats": row.defeats or [],
        "defeated_by": row.defeated_by or [],
        "shacl_shape_id": row.shacl_shape_id,
        "vector_concept_id": row.vector_concept_id,
        "valid_from": row.valid_from,
        "valid_until": row.valid_until,
        "extracted_from": row.extracted_from,
        "extraction_confidence": row.extraction_confidence,
        "critic_verified": row.critic_verified,
        "critic_note": row.critic_note,
        "human_status": row.human_status,
        "human_note": row.human_note,
    }
