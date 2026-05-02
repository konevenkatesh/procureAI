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
    ClauseInstance (so the caller knows WHICH clause holds the violation)
    and target RuleNode (so the caller knows WHICH rule fired).

    Returns list of dicts with keys:
        rule_id, typology, severity, defeated,
        clause_node_id, clause_template_id, clause_title,
        clause_section_type, clause_match_confidence,
        rule_node_id, rule_layer, rule_pattern_type
    """
    edges = _rest("kg_edges", params={
        "select":    "edge_id,from_node_id,to_node_id,properties",
        "doc_id":    f"eq.{doc_id}",
        "edge_type": "eq.VIOLATES_RULE",
    })
    if not edges:
        return []

    clause_ids = {e["from_node_id"] for e in edges}
    rule_ids   = {e["to_node_id"]   for e in edges}

    clause_nodes = _fetch_nodes(clause_ids)
    rule_nodes   = _fetch_nodes(rule_ids)

    out: list[dict] = []
    for e in edges:
        cl = clause_nodes.get(e["from_node_id"], {})
        ru = rule_nodes.get(e["to_node_id"],   {})
        cp = cl.get("properties", {}) or {}
        rp = ru.get("properties", {}) or {}
        out.append({
            "rule_id":                   e["properties"].get("rule_id"),
            "typology":                  e["properties"].get("typology"),
            "severity":                  e["properties"].get("severity"),
            "defeated":                  bool(e["properties"].get("defeated")),
            "clause_node_id":            e["from_node_id"],
            "clause_template_id":        cp.get("template_id"),
            "clause_title":              cl.get("label"),
            "clause_section_type":       cp.get("section_type"),
            "clause_match_confidence":   cp.get("match_confidence"),
            "rule_node_id":              e["to_node_id"],
            "rule_layer":                rp.get("layer"),
            "rule_pattern_type":         rp.get("pattern_type"),
        })
    return out


# ── 2. Cascade violations: violator → cross-referenced clauses ───────

def get_cascade_violations(doc_id: str) -> list[dict]:
    """For every VIOLATES_RULE edge, find clauses that the violating
    clause cross-references. These are the "fixing this forces fixing
    these too" relationships — the cascade.

    Returns list of dicts with keys:
        violator_clause_node_id, violator_template_id, violator_rule_id,
        related_clause_node_id, related_template_id, related_clause_title
    """
    vio = _rest("kg_edges", params={
        "select":    "from_node_id,properties",
        "doc_id":    f"eq.{doc_id}",
        "edge_type": "eq.VIOLATES_RULE",
    })
    if not vio:
        return []
    violator_ids = {e["from_node_id"] for e in vio}

    xref = _rest("kg_edges", params={
        "select":    "from_node_id,to_node_id",
        "doc_id":    f"eq.{doc_id}",
        "edge_type": "eq.CROSS_REFERENCES",
    })
    xref_by_violator: dict[str, list[str]] = defaultdict(list)
    for e in xref:
        if e["from_node_id"] in violator_ids:
            xref_by_violator[e["from_node_id"]].append(e["to_node_id"])
    if not xref_by_violator:
        return []

    all_clause_ids = violator_ids | {tid for tids in xref_by_violator.values() for tid in tids}
    clause_nodes = _fetch_nodes(all_clause_ids)

    # Collapse: for each (violator, rule, related), one row.
    out: list[dict] = []
    for v_edge in vio:
        v_id   = v_edge["from_node_id"]
        v_node = clause_nodes.get(v_id, {})
        v_tpl  = (v_node.get("properties") or {}).get("template_id")
        v_rule = v_edge["properties"].get("rule_id")
        for r_id in xref_by_violator.get(v_id, []):
            r_node = clause_nodes.get(r_id, {})
            r_tpl  = (r_node.get("properties") or {}).get("template_id")
            out.append({
                "violator_clause_node_id":  v_id,
                "violator_template_id":     v_tpl,
                "violator_rule_id":         v_rule,
                "related_clause_node_id":   r_id,
                "related_template_id":      r_tpl,
                "related_clause_title":     r_node.get("label"),
            })
    return out


# ── 3. Audit path for a single rule violation ────────────────────────

def get_full_audit_path(doc_id: str, rule_id: str) -> dict:
    """Trace the chain that links a rule violation back to its evidence:

        TenderDocument
            └── HAS_SECTION → Section (section_type, heading, line_start..line_end)
                    └── HAS_CLAUSE → ClauseInstance (template_id, confidence)
                            └── VIOLATES_RULE → RuleNode (rule_id, severity, typology)
                                    │
                                    └── (DEFEATS …) ← if any active defeater overrides

    Returns a single dict per (doc_id, rule_id). When the rule has
    multiple violating clauses, the FIRST is reported here (most
    severe-confidence first); call `get_violations` to see all.
    """
    vios = [v for v in get_violations(doc_id) if v["rule_id"] == rule_id]
    if not vios:
        return {"doc_id": doc_id, "rule_id": rule_id, "found": False}

    # Pick the highest-severity, highest-confidence violator
    sev_rank = {"HARD_BLOCK": 3, "WARNING": 2, "ADVISORY": 1}
    vios.sort(key=lambda v: (
        -sev_rank.get(v["severity"] or "", 0),
        -(float(v["clause_match_confidence"] or 0)),
    ))
    v = vios[0]

    # Find the section that contains this clause via HAS_CLAUSE edge
    has_clause = _rest("kg_edges", params={
        "select":    "from_node_id,to_node_id",
        "doc_id":    f"eq.{doc_id}",
        "edge_type": "eq.HAS_CLAUSE",
        "to_node_id": f"eq.{v['clause_node_id']}",
    })
    section_node_id = has_clause[0]["from_node_id"] if has_clause else None
    section = _fetch_nodes({section_node_id}).get(section_node_id) if section_node_id else None
    sp = (section or {}).get("properties", {}) or {}

    # Find any defeating rules for this rule_id (DEFEATS edges into it)
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
        "doc_id":           doc_id,
        "rule_id":          rule_id,
        "found":            True,
        "severity":         v["severity"],
        "typology":         v["typology"],
        "defeated":         v["defeated"],
        "section": {
            "node_id":      section_node_id,
            "section_type": sp.get("section_type"),
            "heading":      sp.get("heading"),
            "line_start":   sp.get("line_start"),
            "line_end":     sp.get("line_end"),
            "word_count":   sp.get("word_count"),
            "source_file":  sp.get("source_file"),
        },
        "clause": {
            "node_id":          v["clause_node_id"],
            "template_id":      v["clause_template_id"],
            "title":            v["clause_title"],
            "match_confidence": v["clause_match_confidence"],
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
