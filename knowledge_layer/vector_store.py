"""Qdrant vector store helpers for P2 clause concepts (BGE-M3 embeddings)."""
from __future__ import annotations

from typing import Optional

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from builder.config import settings
from knowledge_layer.database import get_session
from knowledge_layer.models import VectorConceptModel
from knowledge_layer.schemas import VectorConcept


def get_client() -> QdrantClient:
    return QdrantClient(url=settings.qdrant_url)


def ensure_collection() -> None:
    """Create the clause-concepts collection if it doesn't exist."""
    client = get_client()
    existing = {c.name for c in client.get_collections().collections}
    if settings.qdrant_collection in existing:
        return
    client.create_collection(
        collection_name=settings.qdrant_collection,
        vectors_config=qmodels.VectorParams(
            size=settings.bge_m3_dim,
            distance=qmodels.Distance.COSINE,
        ),
    )


def upsert_concept(concept: VectorConcept, embedding: list[float]) -> None:
    """Save concept metadata to Postgres + push embedding to Qdrant."""
    if len(embedding) != settings.bge_m3_dim:
        raise ValueError(
            f"Embedding dim {len(embedding)} != expected {settings.bge_m3_dim}"
        )

    client = get_client()
    point_id = _stable_point_id(concept.concept_id)

    client.upsert(
        collection_name=settings.qdrant_collection,
        points=[
            qmodels.PointStruct(
                id=point_id,
                vector=embedding,
                payload={
                    "concept_id": concept.concept_id,
                    "canonical_name": concept.canonical_name,
                    "aliases": concept.aliases,
                    "rule_ids": concept.rule_ids,
                    "applicable_tender_types": [t.value for t in concept.applicable_tender_types],
                    "severity": concept.severity.value,
                    "similarity_threshold": concept.similarity_threshold,
                },
            )
        ],
    )

    with get_session() as session:
        row = session.query(VectorConceptModel).filter_by(concept_id=concept.concept_id).first()
        payload = {
            "concept_id": concept.concept_id,
            "rule_ids": concept.rule_ids,
            "canonical_name": concept.canonical_name,
            "aliases": concept.aliases,
            "sac_summary": concept.sac_summary,
            "threshold_trigger": concept.threshold_trigger,
            "applicable_tender_types": [t.value for t in concept.applicable_tender_types],
            "similarity_threshold": concept.similarity_threshold,
            "severity": concept.severity.value,
            "qdrant_point_id": str(point_id),
        }
        if row:
            for k, v in payload.items():
                setattr(row, k, v)
        else:
            session.add(VectorConceptModel(**payload))


def search_concepts(query_embedding: list[float], top_k: int = 5, score_threshold: Optional[float] = None) -> list[dict]:
    client = get_client()
    results = client.search(
        collection_name=settings.qdrant_collection,
        query_vector=query_embedding,
        limit=top_k,
        score_threshold=score_threshold,
    )
    return [{"score": r.score, **(r.payload or {})} for r in results]


def collection_count() -> int:
    client = get_client()
    try:
        info = client.get_collection(settings.qdrant_collection)
        return info.points_count or 0
    except Exception:
        return 0


def _stable_point_id(concept_id: str) -> int:
    """Hash concept_id to a stable Qdrant int point ID."""
    import hashlib
    h = hashlib.sha256(concept_id.encode("utf-8")).hexdigest()
    # Use first 15 hex chars → fits in int64
    return int(h[:15], 16)
