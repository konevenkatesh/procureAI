"""
agents/queries/kg_queries.py

Pure-SQL accessors over the kg_nodes / kg_edges tables.

Used by the validator LangGraph node — every query reads ONLY from the
graph; no document re-scan, no validator re-run. The graph is the source
of truth for everything the validator decides.

Each function returns plain Python dicts/lists so the caller can JSON-
serialise without re-modelling.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from builder.config import settings
import requests


# ── REST helpers (anon key; RLS off on these tables) ──────────────────

def _rest(table: str, *, params: dict | None = None,
          page_size: int = 1000) -> list[dict]:
    """GET /rest/v1/{table}, paginating with Range header."""
    headers = {
        "apikey":        settings.supabase_anon_key,
        "Authorization": f"Bearer {settings.supabase_anon_key}",
        "Range-Unit":    "items",
    }
    url = f"{settings.supabase_rest_url}/rest/v1/{table}"
    out: list[dict] = []
    offset = 0
    while True:
        h = {**headers, "Range": f"{offset}-{offset + page_size - 1}"}
        r = requests.get(url, params=params or {}, headers=h, timeout=30)
        r.raise_for_status()
        batch = r.json()
        out.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
    return out


# ── 1. Violations on a doc ────────────────────────────────────────────

def get_violations(doc_id: str) -> list[dict]:
    """Every VIOLATES_RULE edge for this doc_id, joined to its source
    node (Section or TenderDocument depending on attribution) and to
    the target RuleNode.

    Per the v0.3-clean redesign, violations attach directly to the
    Section that contains the violating evidence (line_no inside its
    line range), or to the TenderDocument when the violation is
    absence-type (no specific line — e.g. "no integrity pact clause
    anywhere"). This replaces the earlier ClauseInstance-attribution
    scheme, which produced 0.40-confidence template-title matches that
    landed violations on unrelated sections.

    Returns list of dicts with keys:
        rule_id, typology, severity, defeated,
        attribution                 # "section" | "document"
        line_no                     # int | None — None for doc-level
        source_node_id, source_node_type
        section_type, section_heading, section_source_file
        section_line_start, section_line_end             # global coords
        section_line_start_local, section_line_end_local # per-file coords
        rule_node_id, rule_layer, rule_pattern_type
    """
    edges = _rest("kg_edges", params={
        "select":    "edge_id,from_node_id,to_node_id,properties",
        "doc_id":    f"eq.{doc_id}",
        "edge_type": "eq.VIOLATES_RULE",
    })
    if not edges:
        return []

    src_ids  = {e["from_node_id"] for e in edges}
    rule_ids = {e["to_node_id"]   for e in edges}

    src_nodes  = _fetch_nodes(src_ids)
    rule_nodes = _fetch_nodes(rule_ids)

    out: list[dict] = []
    for e in edges:
        src = src_nodes.get(e["from_node_id"], {})
        ru  = rule_nodes.get(e["to_node_id"], {})
        sp  = src.get("properties", {}) or {}
        rp  = ru.get("properties", {}) or {}
        ep  = e["properties"] or {}
        out.append({
            "rule_id":                ep.get("rule_id"),
            "typology":               ep.get("typology"),
            "severity":               ep.get("severity"),
            "defeated":               bool(ep.get("defeated")),
            "attribution":            ep.get("attribution"),    # "section" | "document"
            "line_no":                ep.get("line_no"),
            "source_node_id":         e["from_node_id"],
            "source_node_type":       src.get("node_type"),
            "section_type":           sp.get("section_type"),
            "section_heading":        sp.get("heading"),
            "section_source_file":    sp.get("source_file"),
            "section_line_start":     sp.get("line_start"),
            "section_line_end":       sp.get("line_end"),
            "section_line_start_local": sp.get("line_start_local"),
            "section_line_end_local":   sp.get("line_end_local"),
            "rule_node_id":           e["to_node_id"],
            "rule_layer":             rp.get("layer"),
            "rule_pattern_type":      rp.get("pattern_type"),
        })
    return out


# ── 2. Cascade violations: violator → cross-referenced clauses ───────

def get_cascade_violations(doc_id: str) -> list[dict]:
    """Cascade-violation query.

    NOTE — v0.3: this query historically traversed ClauseInstance
    cross-references from violator clauses. Since the v0.3-clean
    redesign no longer creates ClauseInstance / HAS_CLAUSE / per-doc
    CROSS_REFERENCES edges (clause-template matching at 0.40
    SequenceMatcher confidence was producing false attribution), the
    cascade view is currently empty. It will be reintroduced in v0.4
    when BGE-M3 cross-encoder lands and we have trustworthy clause-level
    relationships.

    Returns []. The function is retained for API stability so callers
    don't need to special-case its absence."""
    return []


# ── 3. Audit path for a single rule violation ────────────────────────

def get_full_audit_path(doc_id: str, rule_id: str) -> dict:
    """Trace the chain linking a rule violation back to its evidence:

        TenderDocument
          └── HAS_SECTION → Section (section_type, heading, line_start, line_end, source_file)
                                            │
                                            └── VIOLATES_RULE → RuleNode
                                                  │
                                                  └── (DEFEATS …) ← active defeaters

    For absence-type violations (line_no=None) the source is the
    TenderDocument itself; the section block carries None values and
    the audit_path callout describes the violation as doc-level.

    Returns a single dict per (doc_id, rule_id). When multiple
    violation edges exist for the same rule_id (one per matched
    pattern), we pick the most-severe one with the lowest line_no.
    """
    vios = [v for v in get_violations(doc_id) if v["rule_id"] == rule_id]
    if not vios:
        return {"doc_id": doc_id, "rule_id": rule_id, "found": False}

    sev_rank = {"HARD_BLOCK": 3, "WARNING": 2, "ADVISORY": 1}
    # Strongest first: severity desc, then earliest line (None last)
    vios.sort(key=lambda v: (
        -sev_rank.get(v["severity"] or "", 0),
        v["line_no"] if v["line_no"] is not None else 10**9,
    ))
    v = vios[0]

    # Defeating rules (DEFEATS edges incoming to this rule_node)
    defeats = _rest("kg_edges", params={
        "select":    "from_node_id,properties",
        "doc_id":    f"eq.{doc_id}",
        "edge_type": "eq.DEFEATS",
        "to_node_id": f"eq.{v['rule_node_id']}",
    })
    defeaters: list[dict] = []
    if defeats:
        defeater_node_ids = {d["from_node_id"] for d in defeats}
        defeater_nodes = _fetch_nodes(defeater_node_ids)
        for did in defeater_node_ids:
            dp = (defeater_nodes.get(did) or {}).get("properties", {}) or {}
            defeaters.append({
                "rule_id":  dp.get("rule_id"),
                "layer":    dp.get("layer"),
                "severity": dp.get("severity"),
            })

    return {
        "doc_id":      doc_id,
        "rule_id":     rule_id,
        "found":       True,
        "severity":    v["severity"],
        "typology":    v["typology"],
        "defeated":    v["defeated"],
        "attribution": v["attribution"],   # "section" | "document"
        "line_no":     v["line_no"],
        "section": {
            "node_id":          v["source_node_id"] if v["attribution"] == "section" else None,
            "section_type":     v["section_type"],
            "heading":          v["section_heading"],
            "source_file":      v["section_source_file"],
            "line_start":       v["section_line_start"],
            "line_end":         v["section_line_end"],
            "line_start_local": v["section_line_start_local"],
            "line_end_local":   v["section_line_end_local"],
        },
        "rule": {
            "node_id":      v["rule_node_id"],
            "layer":        v["rule_layer"],
            "pattern_type": v["rule_pattern_type"],
        },
        "defeaters": defeaters,
    }


# ── 4. KG summary stats ───────────────────────────────────────────────

def get_kg_summary(doc_id: str) -> dict:
    """Roll-up node + edge counts for a doc_id, plus defeasibility
    summary (active defeaters, defeated-rule count, overrides)."""
    from collections import Counter
    nodes = _rest("kg_nodes", params={
        "select": "node_type,properties", "doc_id": f"eq.{doc_id}",
    })
    edges = _rest("kg_edges", params={
        "select": "edge_type,properties", "doc_id": f"eq.{doc_id}",
    })

    nodes_by_type = dict(Counter(n["node_type"] for n in nodes))
    edges_by_type = dict(Counter(e["edge_type"] for e in edges))

    # Tender document
    td = next((n for n in nodes if n["node_type"] == "TenderDocument"), None)
    td_props = (td or {}).get("properties", {}) or {}

    # Defeasibility roll-up
    defeats_edges = [e for e in edges if e["edge_type"] == "DEFEATS"]
    overrides_edges = [e for e in edges if e["edge_type"] == "OVERRIDES_VIOLATION"]
    vio_edges = [e for e in edges if e["edge_type"] == "VIOLATES_RULE"]
    n_defeated_violations = sum(
        1 for e in vio_edges if (e.get("properties") or {}).get("defeated") is True
    )

    return {
        "doc_id":                 doc_id,
        "is_ap_tender":           td_props.get("is_ap_tender"),
        "layer":                  td_props.get("layer"),
        "nodes_by_type":          nodes_by_type,
        "edges_by_type":          edges_by_type,
        "clauses_matched":        nodes_by_type.get("ClauseInstance", 0),
        "rules_activated":        nodes_by_type.get("RuleNode", 0),
        "sections":               nodes_by_type.get("Section", 0),
        "validation_findings":    nodes_by_type.get("ValidationFinding", 0),
        "defeats_edges":          len(defeats_edges),
        "overrides_edges":        len(overrides_edges),
        "violations_total":       len(vio_edges),
        "violations_defeated":    n_defeated_violations,
        "violations_active":      len(vio_edges) - n_defeated_violations,
    }


# ── private helpers ──────────────────────────────────────────────────

def _fetch_nodes(node_ids) -> dict[str, dict]:
    """Pull rows from kg_nodes for a set of UUIDs. Chunks the IN-list."""
    ids = sorted({i for i in node_ids if i})
    if not ids:
        return {}
    out: dict[str, dict] = {}
    CHUNK = 50
    for i in range(0, len(ids), CHUNK):
        chunk = ids[i:i + CHUNK]
        ids_quoted = ",".join(f'"{x}"' for x in chunk)
        rows = _rest("kg_nodes", params={
            "select":  "node_id,node_type,label,properties",
            "node_id": f"in.({ids_quoted})",
        })
        for r in rows:
            out[r["node_id"]] = r
    return out
