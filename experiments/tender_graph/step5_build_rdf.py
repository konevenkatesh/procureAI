"""
experiments/tender_graph/step5_build_rdf.py

Step 5 — Build the RDF graph from PostgreSQL state.

Reads document_sections, clause_instances, clause_relationships rows
filtered by doc_id and constructs an rdflib.Graph using the ap: namespace
already established by the rest of procureAI (https://procureai.in/ns#).

The graph is intentionally light — it stores REFERENCES (postgresId,
fromTemplate id, etc.) rather than the full section text, so the graph
remains small and the LLM-/SPARQL-side reasoning can hit Postgres for
heavy text content (the "hybrid" pattern Step 6 demonstrates).

Serialised to: experiments/tender_graph/vizag_ugss.ttl
"""
from __future__ import annotations

import time
from pathlib import Path

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, XSD

from _common import DOC_ID, REPO, rest_select


AP = Namespace("https://procureai.in/ns#")
OUT_TTL = REPO / "experiments" / "tender_graph" / "vizag_ugss.ttl"


# Whitelist of relationship types that map to a clean ap: predicate.
# Rule-bound types come out of step 4 as "violatesRule:MPW-029" / etc;
# we keep the rule_id as object-URI annotation so it's queryable.
_BARE_REL_TYPES = {"cross_reference"}
_RULE_PREFIXES  = ("violatesRule:", "satisfiesRule:")


def _section_uri(pg_id: int) -> URIRef:
    return URIRef(AP[f"section_{pg_id}"])


def _clause_uri(pg_id: int) -> URIRef:
    return URIRef(AP[f"clause_{pg_id}"])


def _rule_uri(rule_id: str) -> URIRef:
    return URIRef(AP[f"rule_{rule_id}"])


def main() -> int:
    print("=" * 70)
    print(f"STEP 5 — Build RDF graph for {DOC_ID}")
    print("=" * 70)

    t_step = time.perf_counter()

    # 1. Read all 3 tables
    sections = rest_select(
        "document_sections",
        params={"select": "id,section_type,heading,line_start,line_end,word_count",
                "doc_id": f"eq.{DOC_ID}", "order": "id.asc"},
    )
    instances = rest_select(
        "clause_instances",
        params={"select": "id,clause_template_id,match_confidence,section_id",
                "doc_id": f"eq.{DOC_ID}", "order": "id.asc"},
    )
    relationships = rest_select(
        "clause_relationships",
        params={"select": "id,from_instance_id,to_instance_id,relationship_type",
                "doc_id": f"eq.{DOC_ID}", "order": "id.asc"},
    )
    print(f"Loaded: {len(sections)} sections, {len(instances)} instances, "
          f"{len(relationships)} relationships")

    # 2. Build graph
    g = Graph()
    g.bind("ap", AP)
    g.bind("xsd", XSD)
    g.bind("rdf", RDF)

    DOC_LIT = Literal(DOC_ID)

    # Sections
    for s in sections:
        uri = _section_uri(s["id"])
        g.add((uri, RDF.type, AP.DocumentSection))
        g.add((uri, AP.sectionType,  Literal(s["section_type"] or "")))
        g.add((uri, AP.heading,      Literal(s["heading"] or "")))
        if s.get("line_start") is not None:
            g.add((uri, AP.lineStart, Literal(int(s["line_start"]), datatype=XSD.integer)))
        if s.get("line_end") is not None:
            g.add((uri, AP.lineEnd,   Literal(int(s["line_end"]),   datatype=XSD.integer)))
        if s.get("word_count") is not None:
            g.add((uri, AP.wordCount, Literal(int(s["word_count"]), datatype=XSD.integer)))
        g.add((uri, AP.postgresId, Literal(int(s["id"]), datatype=XSD.integer)))
        g.add((uri, AP.docId,      DOC_LIT))

    # Clause instances
    for inst in instances:
        uri = _clause_uri(inst["id"])
        g.add((uri, RDF.type, AP.ClauseInstance))
        g.add((uri, AP.fromTemplate,    Literal(inst["clause_template_id"])))
        g.add((uri, AP.matchConfidence, Literal(float(inst["match_confidence"] or 0.0),
                                                  datatype=XSD.decimal)))
        g.add((uri, AP.postgresId,      Literal(int(inst["id"]), datatype=XSD.integer)))
        g.add((uri, AP.inSection,       _section_uri(inst["section_id"])))
        g.add((uri, AP.docId,           DOC_LIT))

    # Relationships
    for rel in relationships:
        from_uri = _clause_uri(rel["from_instance_id"])
        to_uri   = _clause_uri(rel["to_instance_id"])
        rt       = rel["relationship_type"]
        if rt in _BARE_REL_TYPES:
            pred = URIRef(AP[rt])
            g.add((from_uri, pred, to_uri))
        elif rt.startswith(_RULE_PREFIXES):
            kind, rid = rt.split(":", 1)
            pred = URIRef(AP[kind])              # violatesRule / satisfiesRule
            # Edge between clause instance and the rule itself, not to_uri
            # (which is the same instance — see step 4 self-loop comment).
            g.add((from_uri, pred, _rule_uri(rid)))
        else:
            # Unknown relationship type — skip rather than mint a bad URI
            continue

    # 3. Serialise to Turtle
    print(f"\nTotal RDF triples: {len(g)}")
    OUT_TTL.parent.mkdir(parents=True, exist_ok=True)
    g.serialize(destination=OUT_TTL.as_posix(), format="turtle")
    print(f"Wrote {OUT_TTL.relative_to(REPO)} ({OUT_TTL.stat().st_size:,} bytes)")

    # 4. First 30 lines preview
    print("\n--- First 30 lines of vizag_ugss.ttl ---")
    text = OUT_TTL.read_text(encoding="utf-8")
    for i, line in enumerate(text.splitlines()[:30], 1):
        print(f"{i:3d}  {line}")

    elapsed = int((time.perf_counter() - t_step) * 1000)
    print(f"\nStep 5 wall time: {elapsed} ms ({elapsed/1000:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
