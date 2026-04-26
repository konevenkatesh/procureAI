"""CRUD for SHACL shapes + Apache Jena Fuseki loader/query helpers."""
from __future__ import annotations

from typing import Optional

import requests
from rdflib import Graph
from SPARQLWrapper import JSON, SPARQLWrapper

from builder.config import settings
from knowledge_layer.database import get_session
from knowledge_layer.models import SHACLShapeModel
from knowledge_layer.schemas import SHACLShape


# ─────────────────────────────────────────────────────────────────────────────
# Postgres-side: store shapes alongside metadata
# ─────────────────────────────────────────────────────────────────────────────

def save_shacl_shape(shape: SHACLShape) -> None:
    with get_session() as session:
        row = session.query(SHACLShapeModel).filter_by(shape_id=shape.shape_id).first()
        if row:
            row.turtle_content = shape.turtle_content
            row.test_cases_pass = shape.test_cases_pass
            row.test_cases_fail = shape.test_cases_fail
            row.production_ready = shape.production_ready
        else:
            session.add(SHACLShapeModel(**shape.model_dump()))


def get_production_ready_shapes() -> list[dict]:
    with get_session() as session:
        rows = (
            session.query(SHACLShapeModel)
            .filter_by(production_ready=True)
            .all()
        )
        return [_row_to_dict(r) for r in rows]


def get_shape_by_rule(rule_id: str) -> Optional[dict]:
    with get_session() as session:
        row = session.query(SHACLShapeModel).filter_by(rule_id=rule_id).first()
        return _row_to_dict(row) if row else None


def update_shape_test_results(shape_id: str, passed: int, failed: int) -> None:
    with get_session() as session:
        row = session.query(SHACLShapeModel).filter_by(shape_id=shape_id).first()
        if row:
            row.test_cases_pass = passed
            row.test_cases_fail = failed
            row.production_ready = (passed > 0 and failed == 0)


# ─────────────────────────────────────────────────────────────────────────────
# Fuseki-side: push shapes into the triple store
# ─────────────────────────────────────────────────────────────────────────────

def _fuseki_data_url() -> str:
    return f"{settings.fuseki_url}/{settings.fuseki_dataset}/data"


def _fuseki_query_url() -> str:
    return f"{settings.fuseki_url}/{settings.fuseki_dataset}/sparql"


def upload_turtle(turtle_content: str, graph_name: Optional[str] = None) -> None:
    """POST a Turtle payload into Fuseki.

    Pass `graph_name` to load into a named graph; omit for the default graph.
    """
    url = _fuseki_data_url()
    if graph_name:
        url = f"{url}?graph={graph_name}"
    headers = {"Content-Type": "text/turtle; charset=utf-8"}
    auth = ("admin", settings.fuseki_password)
    resp = requests.post(url, data=turtle_content.encode("utf-8"), headers=headers, auth=auth, timeout=30)
    resp.raise_for_status()


def validate_turtle(turtle_content: str) -> tuple[bool, str]:
    """Parse Turtle content locally with rdflib. Returns (ok, message)."""
    try:
        g = Graph()
        g.parse(data=turtle_content, format="turtle")
        return True, f"OK ({len(g)} triples)"
    except Exception as e:
        return False, str(e)


def sparql_select(query: str) -> list[dict]:
    """Run a SPARQL SELECT against the Fuseki dataset."""
    sparql = SPARQLWrapper(_fuseki_query_url())
    sparql.setQuery(query)
    sparql.setReturnFormat(JSON)
    results = sparql.queryAndConvert()
    return results.get("results", {}).get("bindings", [])


def _row_to_dict(row: SHACLShapeModel) -> dict:
    return {
        "shape_id": row.shape_id,
        "rule_id": row.rule_id,
        "turtle_content": row.turtle_content,
        "test_cases_pass": row.test_cases_pass,
        "test_cases_fail": row.test_cases_fail,
        "production_ready": row.production_ready,
    }
