"""CRUD operations for clause templates."""
from __future__ import annotations

from typing import Optional

from knowledge_layer.database import get_session
from knowledge_layer.models import ClauseTemplateModel
from knowledge_layer.schemas import ClauseTemplate


def save_clause_templates(clauses: list[ClauseTemplate]) -> int:
    """Insert or update clause templates by clause_id."""
    saved = 0
    with get_session() as session:
        for c in clauses:
            row = session.query(ClauseTemplateModel).filter_by(clause_id=c.clause_id).first()
            payload = {
                "clause_id": c.clause_id,
                "title": c.title,
                "text_english": c.text_english,
                "text_telugu": c.text_telugu,
                "parameters": [p.model_dump() for p in c.parameters],
                "applicable_tender_types": [t.value for t in c.applicable_tender_types],
                "mandatory": c.mandatory,
                "position_section": c.position_section,
                "position_order": c.position_order,
                "cross_references": c.cross_references,
                "rule_ids": c.rule_ids,
                "valid_from": c.valid_from,
                "valid_until": c.valid_until,
                "human_verified": c.human_verified,
            }
            if row:
                for k, v in payload.items():
                    setattr(row, k, v)
            else:
                session.add(ClauseTemplateModel(**payload))
            saved += 1
    return saved


def update_clause_telugu(clause_id: str, telugu_text: str) -> None:
    with get_session() as session:
        row = session.query(ClauseTemplateModel).filter_by(clause_id=clause_id).first()
        if row:
            row.text_telugu = telugu_text


def get_clauses_missing_telugu() -> list[dict]:
    with get_session() as session:
        rows = (
            session.query(ClauseTemplateModel)
            .filter(ClauseTemplateModel.text_telugu.is_(None))
            .all()
        )
        return [_row_to_dict(r) for r in rows]


def get_all_clauses(human_verified: Optional[bool] = None) -> list[dict]:
    with get_session() as session:
        q = session.query(ClauseTemplateModel)
        if human_verified is not None:
            q = q.filter_by(human_verified=human_verified)
        return [_row_to_dict(r) for r in q.all()]


def _row_to_dict(row: ClauseTemplateModel) -> dict:
    return {
        "clause_id": row.clause_id,
        "title": row.title,
        "text_english": row.text_english,
        "text_telugu": row.text_telugu,
        "parameters": row.parameters or [],
        "applicable_tender_types": row.applicable_tender_types or [],
        "mandatory": row.mandatory,
        "position_section": row.position_section,
        "position_order": row.position_order,
        "cross_references": row.cross_references or [],
        "rule_ids": row.rule_ids or [],
        "valid_from": row.valid_from,
        "valid_until": row.valid_until,
        "human_verified": row.human_verified,
    }
