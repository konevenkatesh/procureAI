"""
experiments/tender_graph/step6_queries.py

Step 6 — Run 3 SPARQL queries against vizag_ugss.ttl.

Query 1 — Section overview: every section, its type, and how many clause
          instances pointed at it via ap:inSection.

Query 2 — Cross references: all (from_template, to_template) pairs joined
          via ap:cross_reference, deduplicated.

Query 3 — Hybrid retrieval test: SPARQL identifies all GCC clause
          instances, returns each instance's postgresId, then we hit
          PostgreSQL via Supabase REST to fetch the section's full_text
          for each. Demonstrates the graph-as-index + Postgres-as-store
          pattern.
"""
from __future__ import annotations

import time
from pathlib import Path

from rdflib import Graph, Namespace
from rdflib.namespace import RDF

from _common import DOC_ID, REPO, rest_select


AP = Namespace("https://procureai.in/ns#")
TTL = REPO / "experiments" / "tender_graph" / "vizag_ugss.ttl"


# ── Queries ────────────────────────────────────────────────────────────

QUERY_1 = """
PREFIX ap:  <https://procureai.in/ns#>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>

SELECT ?section ?sectionType ?heading (COUNT(?clause) AS ?nClauses)
WHERE {
    ?section a ap:DocumentSection ;
             ap:sectionType ?sectionType ;
             ap:heading ?heading .
    OPTIONAL { ?clause ap:inSection ?section }
}
GROUP BY ?section ?sectionType ?heading
ORDER BY DESC(?nClauses)
LIMIT 25
"""


QUERY_2 = """
PREFIX ap: <https://procureai.in/ns#>

SELECT DISTINCT ?fromTemplate ?toTemplate (COUNT(*) AS ?nLinks)
WHERE {
    ?fromInstance ap:cross_reference ?toInstance .
    ?fromInstance ap:fromTemplate ?fromTemplate .
    ?toInstance   ap:fromTemplate ?toTemplate .
    FILTER (?fromTemplate != ?toTemplate)
}
GROUP BY ?fromTemplate ?toTemplate
ORDER BY DESC(?nLinks)
LIMIT 15
"""


# Find all GCC clause instances (their section is typed GCC).
# Returns (clause_postgres_id, section_postgres_id) per row so step 6
# can fetch full_text from Postgres in one IN-list call.
QUERY_3 = """
PREFIX ap: <https://procureai.in/ns#>

SELECT ?clausePgId ?sectionPgId ?templateId ?confidence
WHERE {
    ?clause a ap:ClauseInstance ;
            ap:postgresId ?clausePgId ;
            ap:fromTemplate ?templateId ;
            ap:matchConfidence ?confidence ;
            ap:inSection ?section .
    ?section ap:sectionType "GCC" ;
             ap:postgresId ?sectionPgId .
}
ORDER BY DESC(?confidence)
LIMIT 5
"""


# Query 4 — CASCADE VIOLATIONS.
# Find every clause instance that BOTH:
#   • violates at least one rule (graph metadata pre-computed in step 4),
#   • has at least one cross_reference to another clause instance.
# These are the cascade-fix points: changing one violator forces follow-on
# changes in every clause it references.
#
# This query is impossible without the graph because the underlying signal
# spans 3 separate facts (rule.typology, validator.violation, template.xref)
# that flat tables can only join via N+1 sequential lookups in Python.
QUERY_4 = """
PREFIX ap: <https://procureai.in/ns#>

SELECT ?clause ?template ?rule ?related_clause ?related_template
WHERE {
    ?clause a ap:ClauseInstance ;
            ap:fromTemplate ?template ;
            ap:violatesRule ?rule ;
            ap:cross_reference ?related_clause .
    ?related_clause ap:fromTemplate ?related_template .
}
ORDER BY ?clause ?related_clause
"""


# ── Runner ─────────────────────────────────────────────────────────────

def main() -> int:
    print("=" * 70)
    print("STEP 6 — SPARQL queries against vizag_ugss.ttl")
    print("=" * 70)

    print(f"\nLoading {TTL.relative_to(REPO)}...")
    t0 = time.perf_counter()
    g = Graph()
    g.parse(TTL.as_posix(), format="turtle")
    parse_ms = int((time.perf_counter() - t0) * 1000)
    print(f"  parsed {len(g)} triples in {parse_ms} ms")

    # ── Query 1 ────────────────────────────────────────────────────────
    print("\n" + "─" * 70)
    print("Query 1 — Section overview (top 25 by clause-instance count)")
    print("─" * 70)
    t0 = time.perf_counter()
    rows1 = list(g.query(QUERY_1))
    q1_ms = int((time.perf_counter() - t0) * 1000)
    print(f"  ran in {q1_ms} ms — {len(rows1)} rows")
    print(f"\n  {'section_type':12s} {'#clauses':>9s}  heading")
    print(f"  {'-'*12} {'-'*9}  {'-'*50}")
    for sec, st, hd, n in rows1:
        st_str = str(st)
        head_short = str(hd).replace("\n", " ")[:60]
        print(f"  {st_str:12s} {int(n):9d}  {head_short}")

    # ── Query 2 ────────────────────────────────────────────────────────
    print("\n" + "─" * 70)
    print("Query 2 — Cross-reference pairs (top 15 by link count)")
    print("─" * 70)
    t0 = time.perf_counter()
    rows2 = list(g.query(QUERY_2))
    q2_ms = int((time.perf_counter() - t0) * 1000)
    print(f"  ran in {q2_ms} ms — {len(rows2)} unique template pairs")
    print(f"\n  {'#links':>6s}  {'from_template':40s} → to_template")
    print(f"  {'-'*6}  {'-'*40}   {'-'*40}")
    for ft, tt, n in rows2:
        print(f"  {int(n):6d}  {str(ft):40s} → {str(tt)}")

    # ── Query 3 — hybrid retrieval ─────────────────────────────────────
    print("\n" + "─" * 70)
    print("Query 3 — Hybrid retrieval: SPARQL → top GCC clauses → Postgres full_text")
    print("─" * 70)
    t0 = time.perf_counter()
    rows3 = list(g.query(QUERY_3))
    q3_sparql_ms = int((time.perf_counter() - t0) * 1000)
    print(f"  SPARQL ran in {q3_sparql_ms} ms — {len(rows3)} rows")

    if not rows3:
        print("  (no GCC clause instances found — nothing to fetch)")
        return 0

    # Fetch full_text for each section_id via Supabase REST
    section_ids = sorted({int(r[1]) for r in rows3})
    ids_quoted = ",".join(str(s) for s in section_ids)
    t0 = time.perf_counter()
    section_rows = rest_select(
        "document_sections",
        params={"select": "id,heading,full_text",
                "id":     f"in.({ids_quoted})",
                "doc_id": f"eq.{DOC_ID}"},
    )
    q3_pg_ms = int((time.perf_counter() - t0) * 1000)
    print(f"  Postgres fetch ran in {q3_pg_ms} ms — {len(section_rows)} sections")
    by_section = {r["id"]: r for r in section_rows}

    print(f"\n  Top 5 GCC clauses (first 200 chars of containing section):")
    for clause_pg, section_pg, tpl, conf in rows3:
        sec = by_section.get(int(section_pg), {})
        head = sec.get("heading", "")[:60]
        snippet = (sec.get("full_text", "") or "")[:200].replace("\n", " ")
        print(f"\n  • {tpl}  (conf={float(conf):.3f})")
        print(f"    section #{int(section_pg)}  {head}")
        print(f"    text:    {snippet}...")

    # Per-result hybrid timing
    n_results = len(rows3)
    sparql_per = q3_sparql_ms / max(n_results, 1)
    pg_per     = q3_pg_ms     / max(n_results, 1)
    print(f"\n  Per-result timing: "
          f"SPARQL {sparql_per:.1f} ms + Postgres {pg_per:.1f} ms = "
          f"{sparql_per + pg_per:.1f} ms total")

    # ── Query 4 — cascade violations ───────────────────────────────────
    print("\n" + "─" * 70)
    print("Query 4 — Cascade violations: a violator that pulls related clauses")
    print("           (impossible with flat storage — needs the graph)")
    print("─" * 70)
    t0 = time.perf_counter()
    rows4 = list(g.query(QUERY_4))
    q4_ms = int((time.perf_counter() - t0) * 1000)
    print(f"  ran in {q4_ms} ms — {len(rows4)} cascade edges")

    if not rows4:
        print("  (no cascade violations — graph contains violatesRule but")
        print("   none of those clauses also have cross_reference edges)")
    else:
        # Group by violator-clause for readability
        from collections import defaultdict
        groups: dict[tuple, list[tuple]] = defaultdict(list)
        for clause, tpl, rule, related_clause, related_tpl in rows4:
            key = (str(clause), str(tpl), str(rule))
            groups[key].append((str(related_clause), str(related_tpl)))

        print(f"\n  {len(groups)} unique (violator, rule) pairs cascade into")
        print(f"  {sum(len(v) for v in groups.values())} related-clause edges:")
        # Show first 8 cascade groups
        for i, ((clause_uri, tpl, rule_uri), kids) in enumerate(list(groups.items())[:8], 1):
            tpl_short  = tpl.split("#")[-1] if "#" in tpl else tpl
            rule_short = rule_uri.split("#")[-1].replace("rule_", "")
            cl_short   = clause_uri.split("#")[-1]
            print(f"\n  {i}. {cl_short}  ({tpl_short})")
            print(f"     violates {rule_short}")
            print(f"     → fixing this forces review of {len(kids)} cross-referenced clause(s):")
            for related_clause, related_tpl in kids[:5]:
                rt_short = related_tpl.split("#")[-1] if "#" in related_tpl else related_tpl
                rc_short = related_clause.split("#")[-1]
                print(f"        • {rc_short:18s} ({rt_short})")
            if len(kids) > 5:
                print(f"        • ... and {len(kids) - 5} more")

    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
