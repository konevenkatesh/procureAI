"""
experiments/tender_graph/kg_builder.py

build_kg(doc_id, document) — the single integration entry point that
populates kg_nodes + kg_edges for one tender document.

Consolidates the prior step2/step3/step4 experiment scripts into one
function and aligns with the v2 schema:

  • clause_templates.clause_type   → only DRAFTING_CLAUSE templates are
                                      used (PROCEDURAL_GUIDE etc are
                                      filtered out).
  • rules.rule_type                → only TYPE_1_ACTIONABLE rules
                                      contribute to VIOLATES_RULE /
                                      SATISFIES_RULE edges.
  • rules.defeats                   → an AP-State (or any AP-GO) rule
                                      that "fires" in the document's
                                      context overrides the central-layer
                                      rules listed in its defeats array.
                                      We materialise this by:
                                        – setting `defeated: true` on
                                          the would-be VIOLATES_RULE
                                          edge,
                                        – emitting an OVERRIDES_VIOLATION
                                          edge from the defeater rule
                                          node to the violating clause,
                                        – emitting DEFEATS edges between
                                          rule nodes that we have
                                          already materialised.

Node types used (matches existing conventions):
    TenderDocument, Section, ClauseInstance, RuleNode, ValidationFinding

Edge types used:
    HAS_SECTION, HAS_CLAUSE, CROSS_REFERENCES,
    SATISFIES_RULE, VIOLATES_RULE,
    DEFEATS, OVERRIDES_VIOLATION

Public API:
    build_kg(doc_id, document, ...) -> dict
        - clears any prior kg rows for doc_id
        - returns counts per node_type / edge_type and a defeasibility
          summary
"""
from __future__ import annotations

import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable

# Repo root on sys.path so absolute imports resolve when run as a script
REPO = Path(__file__).resolve().parent.parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from _common import rest_select, rest_insert, rest_delete_doc
from step2_sections import (
    classify_heading_override, classify_sections, _filename_default,
    find_line_range,
)


# ── Configuration ─────────────────────────────────────────────────────

MATCH_THRESHOLD = 0.40        # SequenceMatcher cutoff for clause title vs section heading

# Section-type → substring(s) that must appear in clause_templates.position_section
SECTION_TO_POSITION: dict[str, list[str]] = {
    "NIT":            ["/NIT"],
    "ITB":            ["/ITB"],
    "Datasheet":      ["/Datasheet"],
    "Evaluation":     ["/Evaluation"],
    "Forms":          ["/Forms"],
    "GCC":            ["/GCC"],
    "SCC":            ["/SCC"],
    "Scope":          ["/Scope"],
    "Specifications": ["/Specifications"],
    "BOQ":            ["/BOQ"],
}


# ── Result models ─────────────────────────────────────────────────────

@dataclass
class KGSummary:
    doc_id: str
    nodes_by_type:    dict[str, int] = field(default_factory=dict)
    edges_by_type:    dict[str, int] = field(default_factory=dict)
    defeasibility:    dict           = field(default_factory=dict)
    timing_ms:        dict[str, int] = field(default_factory=dict)

    def __str__(self) -> str:
        lines = [f"KGSummary[{self.doc_id}]"]
        lines.append("  Nodes:")
        for t, n in sorted(self.nodes_by_type.items(), key=lambda x: -x[1]):
            lines.append(f"    {t:22s} {n}")
        lines.append("  Edges:")
        for t, n in sorted(self.edges_by_type.items(), key=lambda x: -x[1]):
            lines.append(f"    {t:22s} {n}")
        lines.append("  Defeasibility:")
        for k, v in self.defeasibility.items():
            lines.append(f"    {k:22s} {v}")
        lines.append("  Timing (ms):")
        for k, v in self.timing_ms.items():
            lines.append(f"    {k:22s} {v}")
        return "\n".join(lines)


# ── Helpers ───────────────────────────────────────────────────────────

def _normalise_heading(text: str) -> str:
    """Normalise heading text for similarity scoring."""
    t = (text or "").lower()
    t = re.sub(r"\\\(([^)]*)\\\)", r"\1", t)
    t = re.sub(r"<a\s+id=\"[^\"]*\">\s*</a>", "", t)
    t = re.sub(r"\s*\(part\s+\d+\)\s*$", "", t)
    t = re.sub(r"[*_#`|\\]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _batch_insert(table: str, rows: list[dict], *, batch: int = 100,
                   max_retries: int = 3) -> list[dict]:
    """Insert rows in chunks; return all inserted records (with autogen ids).

    Retries each chunk up to `max_retries` times on transient
    Connection / Read errors with exponential backoff. PostgREST 4xx /
    5xx HTTPErrors are NOT retried — they indicate logical errors that
    won't fix themselves."""
    import time as _time
    import requests as _req

    out: list[dict] = []
    for i in range(0, len(rows), batch):
        chunk = rows[i:i + batch]
        for attempt in range(1, max_retries + 1):
            try:
                out.extend(rest_insert(table, chunk))
                break
            except (_req.ConnectionError, _req.Timeout) as e:
                if attempt == max_retries:
                    raise
                wait = 0.6 * (2 ** (attempt - 1))   # 0.6, 1.2, 2.4 s
                _time.sleep(wait)
                # try again
    return out


def _clear_kg(doc_id: str) -> tuple[int, int]:
    """Idempotent reset: delete edges first (FK), then nodes."""
    n_e = rest_delete_doc("kg_edges", doc_id)
    n_n = rest_delete_doc("kg_nodes", doc_id)
    return n_n, n_e


# ── Phase 1: section split + classify ─────────────────────────────────

def _split_and_classify(source_files: list[Path]) -> list[dict]:
    """Split each source file via builder.section_splitter, then classify
    section_type via the heading-content-primary `classify_sections`
    walker (filename hint = starter default; sub-clause sections inherit
    the latched parent type). Returns a list of section dicts (no DB
    writes yet)."""
    from builder.section_splitter import split_into_sections

    out: list[dict] = []
    for path in source_files:
        text = path.read_text(encoding="utf-8")
        per_file_rows: list[dict] = []
        for ref, body in split_into_sections(text, path.stem):
            heading = ref.split("/", 1)[1] if "/" in ref else ref
            line_start, line_end = find_line_range(text, body)
            per_file_rows.append({
                "section_type": None,        # filled in by classify_sections below
                "heading":      heading,
                "line_start":   line_start,
                "line_end":     line_end,
                "word_count":   len(body.split()),
                "full_text":    body,
                "source_file":  path.name,
            })
        types = classify_sections(per_file_rows, path.name, file_text=text)
        for row, t in zip(per_file_rows, types):
            row["section_type"] = t
        out.extend(per_file_rows)
    return out


# ── Phase 2: clause-template matching ─────────────────────────────────

def _match_clauses(
    sections: list[dict],
    section_node_ids: list[str],
    drafting_templates_by_position: dict[str, list[dict]],
    drafting_templates_all: list[dict],
) -> list[dict]:
    """Two-pass matching: narrow by section_type, then SequenceMatcher.

    Returns a list of clause-instance dicts ready to insert as kg_nodes.
    Each carries a `_section_node_id` attr so HAS_CLAUSE edges can be
    emitted later."""
    instances: list[dict] = []
    for sec, sec_node_id in zip(sections, section_node_ids):
        substrs = SECTION_TO_POSITION.get(sec["section_type"], [])
        if substrs:
            candidates: list[dict] = []
            for pos_key, lst in drafting_templates_by_position.items():
                if any(s in pos_key for s in substrs):
                    candidates.extend(lst)
        else:
            candidates = drafting_templates_all

        heading_norm = _normalise_heading(sec["heading"])
        for tpl in candidates:
            title_norm = _normalise_heading(tpl["title"])
            ratio = SequenceMatcher(None, heading_norm, title_norm).ratio()
            if ratio < MATCH_THRESHOLD:
                continue
            instances.append({
                "_section_node_id": sec_node_id,
                "_template":        tpl,
                "_match_confidence": round(ratio, 4),
                "_section_type":    sec["section_type"],
            })
    return instances


# ── Phase 3: defeasibility resolution ─────────────────────────────────

def _is_defeater_active(rule: dict, *, is_ap_tender: bool, layer: str | None) -> bool:
    """Decide whether a rule's `defeats` list applies in this document's
    context.

    The 27 wired rules are AP-State / AP-GO rules whose defeats supersede
    the central MPW/MPG/MPS/GFR rules in AP-tender contexts. The
    contract therefore reduces to: defeater fires iff the document is an
    AP tender AND the defeater's layer is AP-State (or its rule_id is
    prefixed AP-GO-).

    For other layer combinations (which we do not see today but the
    schema permits) we treat the defeater as inactive — that is the
    conservative choice and surfaces both rules to the reviewer."""
    rid = rule.get("rule_id", "")
    rule_layer = (rule.get("layer") or layer or "").lower()
    if not rule.get("defeats"):
        return False
    if rid.startswith("AP-GO-") or rule_layer == "ap-state":
        return is_ap_tender
    return False


def _compute_defeated_set(
    rules_by_id: dict[str, dict],
    *,
    is_ap_tender: bool,
) -> tuple[set[str], dict[str, set[str]]]:
    """Return:
      defeated_ids  — set of rule_ids that are defeated by some active defeater
      pairs         — defeater_id → {defeated_rule_ids it took out}
    """
    defeated: set[str] = set()
    pairs: dict[str, set[str]] = defaultdict(set)
    for rid, r in rules_by_id.items():
        if not _is_defeater_active(r, is_ap_tender=is_ap_tender, layer=r.get("layer")):
            continue
        for victim in (r.get("defeats") or []):
            defeated.add(victim)
            pairs[rid].add(victim)
    return defeated, pairs


# ── Phase 4: validator → set of violated rule_ids ─────────────────────

def _run_validator(document_text: str, document_name: str) -> set[str]:
    """Run the regex/cascade validator and return the set of rule_ids
    whose findings appeared. We use rule_ids (not just typology) because
    a typology can map to many rules and we want fine-grained edges."""
    from modules.validator.rule_verification_engine import RuleVerificationEngine
    eng = RuleVerificationEngine()
    report = eng.verify(document_text, document_name=document_name)
    rule_ids: set[str] = set()
    for f in (report.hard_blocks + report.warnings + report.advisories):
        rule_ids.add(f.rule_id)
        for trig in (f.triggered_rule_ids or []):
            rule_ids.add(trig)
    return rule_ids


def _validation_findings_for_kg(document_text: str, document_name: str) -> list[dict]:
    """Same call as _run_validator but returns full finding records to
    materialise as ValidationFinding nodes."""
    from modules.validator.rule_verification_engine import RuleVerificationEngine
    eng = RuleVerificationEngine()
    report = eng.verify(document_text, document_name=document_name)
    out: list[dict] = []
    for f in (report.hard_blocks + report.warnings + report.advisories):
        out.append({
            "rule_id":       f.rule_id,
            "typology_code": f.typology_code,
            "severity":      f.severity,
            "evidence":      (f.evidence_text or "")[:280],
            "source_clause": f.source_clause or "",
            "defeated_by":   list(f.defeated_by or []),
        })
    return out


# ── Phase 5: classify the doc to decide context (is_ap_tender etc.) ──

def _classify(document_text: str, *,
              estimated_value_override: float | None = None) -> dict:
    """Run TenderClassifier; return only the fields kg_builder needs."""
    from engines.classifier import TenderClassifier
    cls = TenderClassifier().classify(document_text)
    ev = estimated_value_override or cls.estimated_value or 0
    return {
        "primary_type":    cls.primary_type,
        "is_ap_tender":    bool(cls.is_ap_tender),
        "estimated_value": float(ev),
        "duration_months": int(cls.duration_months or 12),
        "funding_source":  cls.funding_source,
    }


# ── Public API ────────────────────────────────────────────────────────

def build_kg(
    doc_id: str,
    document: str | Path | list[str] | list[Path],
    *,
    document_name: str | None = None,
    estimated_value_override: float | None = None,
    clear_existing: bool = True,
) -> KGSummary:
    """Populate kg_nodes and kg_edges for one document.

    Args:
        doc_id:
            Stable identifier; same value goes onto every kg_node.doc_id
            and kg_edge.doc_id row.
        document:
            Either a single processed-Markdown path, a single text
            blob, or a list of paths (multi-volume tender).
        document_name:
            Display label; stored on the TenderDocument node.
        estimated_value_override:
            If supplied, used to override the classifier's value for
            cascade parameters and TenderDocument properties.
        clear_existing:
            If True (default), all kg_nodes/kg_edges for this doc_id
            are deleted first so the function is fully idempotent.

    Returns:
        KGSummary with counts and timings.
    """
    summary = KGSummary(doc_id=doc_id)
    t_total = time.perf_counter()

    # ── 1. Resolve input to (source_files, full_text)
    if isinstance(document, (list, tuple)):
        source_files = [Path(p) for p in document]
        full_text = "\n\n".join(p.read_text(encoding="utf-8") for p in source_files)
    elif isinstance(document, Path) or (isinstance(document, str) and Path(document).exists()):
        source_files = [Path(document)]
        full_text = source_files[0].read_text(encoding="utf-8")
    else:
        # Raw text input — no source_files, only the blob
        source_files = []
        full_text = str(document)

    document_name = document_name or (
        source_files[0].stem if source_files else f"doc:{doc_id}"
    )

    # ── 2. Idempotent reset
    if clear_existing:
        n_n, n_e = _clear_kg(doc_id)
        summary.timing_ms["clear_prior_rows"] = 0  # delete returns count, not ms
        summary.defeasibility.setdefault("cleared_nodes", n_n)
        summary.defeasibility.setdefault("cleared_edges", n_e)

    # ── 3. Classify document
    t0 = time.perf_counter()
    ctx = _classify(full_text, estimated_value_override=estimated_value_override)
    summary.timing_ms["classify"] = int((time.perf_counter() - t0) * 1000)

    # ── 4. Insert TenderDocument node.
    # Classifier-derived properties are flagged with `*_classified`
    # suffix and an `*_reliable: false` boolean so downstream agents
    # know not to read them until a label-aware extractor exists.
    # Only `is_ap_tender` is trusted — its AP-keyword detection works
    # correctly across all docs we've seen so far.
    t0 = time.perf_counter()
    doc_node = _batch_insert("kg_nodes", [{
        "doc_id":     doc_id,
        "node_type":  "TenderDocument",
        "label":      document_name,
        "properties": {
            "doc_id":          doc_id,
            # ── Reliable ──
            "is_ap_tender":    ctx["is_ap_tender"],
            "layer":           "AP-State" if ctx["is_ap_tender"] else "Central",
            # ── Unreliable (Fix 2): classifier output, do not consume
            #    until a label-aware extractor replaces it ──
            "tender_type_classified":         ctx["primary_type"],
            "tender_type_reliable":           False,
            "estimated_value_classified":     ctx["estimated_value"],
            "estimated_value_cr_classified":  round(ctx["estimated_value"] / 1_00_00_000, 2),
            "estimated_value_reliable":       False,
            "duration_months_classified":     ctx["duration_months"],
            "duration_reliable":              False,
            "funding_source_classified":      ctx["funding_source"],
            "funding_source_reliable":        False,
        },
        "source_ref": "manual",
    }])[0]
    doc_node_id = doc_node["node_id"]

    # ── 5. Section split + classify + insert as kg_nodes
    sections = _split_and_classify(source_files) if source_files else []
    section_rows = [{
        "doc_id":     doc_id,
        "node_type":  "Section",
        "label":      f"{s['section_type']}: {s['heading'][:80]}",
        "properties": {
            "section_type": s["section_type"],
            "heading":      s["heading"],
            "line_start":   s["line_start"],
            "line_end":     s["line_end"],
            "word_count":   s["word_count"],
            "source_file":  s["source_file"],
        },
        "source_ref": f"section:{i}",
    } for i, s in enumerate(sections)]
    inserted_sections = _batch_insert("kg_nodes", section_rows)
    section_node_ids = [r["node_id"] for r in inserted_sections]
    summary.timing_ms["section_split_insert"] = int((time.perf_counter() - t0) * 1000)

    # ── 6. HAS_SECTION edges (Doc → Section)
    t0 = time.perf_counter()
    has_section_edges = [{
        "doc_id":       doc_id,
        "from_node_id": doc_node_id,
        "to_node_id":   sec_id,
        "edge_type":    "HAS_SECTION",
        "weight":       1.0,
        "properties":   {"section_type": s["section_type"]},
    } for s, sec_id in zip(sections, section_node_ids)]
    _batch_insert("kg_edges", has_section_edges)
    summary.timing_ms["has_section_edges"] = int((time.perf_counter() - t0) * 1000)

    # ── 7. Fetch DRAFTING_CLAUSE templates only
    t0 = time.perf_counter()
    drafting = rest_select("clause_templates", params={
        "select":      "clause_id,title,position_section,mandatory,"
                       "cross_references,rule_ids,applicable_tender_types,clause_type",
        "clause_type": "eq.DRAFTING_CLAUSE",
        "order":       "clause_id.asc",
    })
    summary.defeasibility["drafting_clause_templates"] = len(drafting)
    by_position: dict[str, list[dict]] = defaultdict(list)
    for t in drafting:
        by_position[t["position_section"] or ""].append(t)
    summary.timing_ms["fetch_templates"] = int((time.perf_counter() - t0) * 1000)

    # ── 8. Match → ClauseInstance nodes + HAS_CLAUSE edges
    t0 = time.perf_counter()
    matches = _match_clauses(sections, section_node_ids, by_position, drafting)
    clause_rows = [{
        "doc_id":     doc_id,
        "node_type":  "ClauseInstance",
        "label":      m["_template"]["title"],
        "properties": {
            "template_id":             m["_template"]["clause_id"],
            "match_confidence":        m["_match_confidence"],
            "section_type":            m["_section_type"],
            "position_section":        m["_template"].get("position_section"),
            "mandatory":               m["_template"].get("mandatory"),
            "applicable_tender_types": m["_template"].get("applicable_tender_types") or [],
            "rule_ids":                m["_template"].get("rule_ids") or [],
            "cross_references":        m["_template"].get("cross_references") or [],
        },
        "source_ref": f"template:{m['_template']['clause_id']}",
        "_section_node_id": m["_section_node_id"],   # stripped before insert
    } for m in matches]
    # Strip private keys that aren't real columns
    clean_clause_rows = [{k: v for k, v in r.items() if not k.startswith("_")}
                          for r in clause_rows]
    inserted_clauses = _batch_insert("kg_nodes", clean_clause_rows)
    clause_node_ids = [r["node_id"] for r in inserted_clauses]

    # HAS_CLAUSE edges from each clause's owning Section.
    # Iterate `matches` (the original records) rather than `clause_rows`
    # because the row dicts had their private `_match_confidence` field
    # stripped before insert.
    has_clause_edges = [{
        "doc_id":       doc_id,
        "from_node_id": match["_section_node_id"],
        "to_node_id":   cid,
        "edge_type":    "HAS_CLAUSE",
        "weight":       match["_match_confidence"],
        "properties":   {"confidence": str(match["_match_confidence"])},
    } for match, cid in zip(matches, clause_node_ids)]
    _batch_insert("kg_edges", has_clause_edges)
    summary.timing_ms["clause_match_insert"] = int((time.perf_counter() - t0) * 1000)

    # ── 9. Distinct rule_ids referenced + their metadata + RuleNode insert
    t0 = time.perf_counter()
    referenced_rule_ids: set[str] = set()
    for m in matches:
        for rid in (m["_template"].get("rule_ids") or []):
            referenced_rule_ids.add(rid)
    # Also pull defeaters that aren't directly referenced — we still want
    # to materialise them as RuleNodes so their DEFEATS / OVERRIDES_VIOLATION
    # edges have valid targets.
    rules_by_id = _fetch_rules(referenced_rule_ids)

    # Now expand the rule set with DEFEATERS whose victims are in our
    # referenced set. Two complementary lookups:
    #   reverse: each referenced rule has a `defeated_by` array → pull those
    #   forward: scan ALL wired defeaters in the catalog (rules where
    #            `defeats` is non-empty) and keep any whose defeats list
    #            intersects our referenced rule_ids.
    # The forward path is necessary because `defeated_by` is not always
    # populated as the inverse of `defeats` — only `defeats` is wired
    # for the 27 active defeaters today.
    extra_defeaters: set[str] = set()
    for r in rules_by_id.values():
        for did in (r.get("defeated_by") or []):
            if did not in rules_by_id:
                extra_defeaters.add(did)
    # Forward scan
    wired_defeaters = _fetch_wired_defeaters()
    for d in wired_defeaters:
        victims = set(d.get("defeats") or [])
        if victims & referenced_rule_ids and d["rule_id"] not in rules_by_id:
            extra_defeaters.add(d["rule_id"])
    if extra_defeaters:
        rules_by_id.update(_fetch_rules(extra_defeaters))

    # Drop non-TYPE_1 rules from edge participation (they aren't actionable).
    actionable: dict[str, dict] = {
        rid: r for rid, r in rules_by_id.items()
        if r.get("rule_type") == "TYPE_1_ACTIONABLE"
    }
    summary.defeasibility["distinct_rules_referenced"] = len(rules_by_id)
    summary.defeasibility["actionable_TYPE_1"]         = len(actionable)

    # Insert RuleNode for every rule we'll touch (actionable or defeater)
    rule_node_id: dict[str, str] = {}
    rule_node_rows = [{
        "doc_id":     doc_id,
        "node_type":  "RuleNode",
        "label":      f"{rid}: {(r.get('natural_language') or '')[:90]}",
        "properties": {
            "rule_id":         rid,
            "layer":           r.get("layer"),
            "severity":        r.get("severity"),
            "rule_type":       r.get("rule_type"),
            "pattern_type":    r.get("pattern_type"),
            "typology_code":   r.get("typology_code"),
            "verification_method": r.get("verification_method"),
            "defeats":         r.get("defeats") or [],
        },
        "source_ref": f"rules:{rid}",
    } for rid, r in rules_by_id.items()]
    inserted_rules = _batch_insert("kg_nodes", rule_node_rows)
    for row, ins in zip(rule_node_rows, inserted_rules):
        rule_node_id[row["properties"]["rule_id"]] = ins["node_id"]
    summary.timing_ms["rule_node_insert"] = int((time.perf_counter() - t0) * 1000)

    # ── 10. Defeasibility — which rule_ids are defeated by an active defeater
    t0 = time.perf_counter()
    defeated_ids, defeater_pairs = _compute_defeated_set(
        rules_by_id, is_ap_tender=ctx["is_ap_tender"],
    )
    active_defeaters = list(defeater_pairs.keys())
    summary.defeasibility["active_defeaters"]     = len(active_defeaters)
    summary.defeasibility["defeated_rule_ids"]    = len(defeated_ids)
    summary.defeasibility["active_defeater_ids"]  = sorted(active_defeaters)
    summary.timing_ms["defeasibility_resolve"] = int((time.perf_counter() - t0) * 1000)

    # ── 11. DEFEATS edges between RuleNodes (only when both ends materialised)
    t0 = time.perf_counter()
    defeats_edges: list[dict] = []
    for defeater, victims in defeater_pairs.items():
        from_id = rule_node_id.get(defeater)
        if not from_id:
            continue
        for victim in victims:
            to_id = rule_node_id.get(victim)
            if not to_id:
                continue
            defeats_edges.append({
                "doc_id":       doc_id,
                "from_node_id": from_id,
                "to_node_id":   to_id,
                "edge_type":    "DEFEATS",
                "weight":       1.0,
                "properties":   {"context": "AP-tender" if ctx["is_ap_tender"] else "Central"},
            })
    if defeats_edges:
        _batch_insert("kg_edges", defeats_edges)
    summary.timing_ms["defeats_edges"] = int((time.perf_counter() - t0) * 1000)

    # ── 12. Run the validator → violated rule_ids
    t0 = time.perf_counter()
    violated_rule_ids = _run_validator(full_text, document_name=document_name)
    findings = _validation_findings_for_kg(full_text, document_name=document_name)
    summary.defeasibility["validator_violations"] = len(violated_rule_ids)
    summary.timing_ms["validator"] = int((time.perf_counter() - t0) * 1000)

    # ── 13. ValidationFinding nodes
    t0 = time.perf_counter()
    finding_rows = [{
        "doc_id":     doc_id,
        "node_type":  "ValidationFinding",
        "label":      f"{f['typology_code'] or 'Unknown'}: {(f['evidence'] or '')[:60]}",
        "properties": {
            "rule_id":       f["rule_id"],
            "typology_code": f["typology_code"],
            "severity":      f["severity"],
            "evidence":      f["evidence"],
            "source_clause": f["source_clause"],
            "defeated":      f["rule_id"] in defeated_ids,
            "status":        "OPEN",
        },
        "source_ref": "validation_finding",
    } for f in findings]
    inserted_findings = _batch_insert("kg_nodes", finding_rows)
    finding_node_id_by_idx = {i: row["node_id"] for i, row in enumerate(inserted_findings)}
    summary.timing_ms["finding_nodes"] = int((time.perf_counter() - t0) * 1000)

    # ── 14. SATISFIES_RULE / VIOLATES_RULE edges per ClauseInstance
    t0 = time.perf_counter()
    sat_edges: list[dict] = []
    vio_edges: list[dict] = []
    overrides_edges: list[dict] = []

    # Defeater rule_id → list of clause_instance node_ids whose violation
    # it overrode (used to generate OVERRIDES_VIOLATION edges below)
    overridden_by: dict[str, list[tuple[str, str]]] = defaultdict(list)

    for clause_node, match in zip(inserted_clauses, matches):
        clause_id = clause_node["node_id"]
        for rid in (match["_template"].get("rule_ids") or []):
            if rid not in actionable:
                # Skip TYPE_2 / TYPE_3 / unclassified rules — they don't
                # take part in PASS/FAIL adjudication.
                continue
            r = actionable[rid]
            target_rule_node = rule_node_id.get(rid)
            if not target_rule_node:
                continue
            if rid in violated_rule_ids:
                is_defeated = rid in defeated_ids
                vio_edges.append({
                    "doc_id":       doc_id,
                    "from_node_id": clause_id,
                    "to_node_id":   target_rule_node,
                    "edge_type":    "VIOLATES_RULE",
                    "weight":       1.0,
                    "properties": {
                        "rule_id":   rid,
                        "severity":  r.get("severity"),
                        "typology":  r.get("typology_code"),
                        "defeated":  is_defeated,
                    },
                })
                if is_defeated:
                    # find the active defeater(s) that took it out
                    for defeater_rid, victims in defeater_pairs.items():
                        if rid in victims:
                            overridden_by[defeater_rid].append(
                                (clause_id, rid),
                            )
            else:
                sat_edges.append({
                    "doc_id":       doc_id,
                    "from_node_id": clause_id,
                    "to_node_id":   target_rule_node,
                    "edge_type":    "SATISFIES_RULE",
                    "weight":       1.0,
                    "properties":   {"rule_id": rid, "severity": r.get("severity")},
                })

    # OVERRIDES_VIOLATION edges: defeater RuleNode → ClauseInstance whose
    # would-be violation it overrode.
    for defeater_rid, pairs in overridden_by.items():
        from_id = rule_node_id.get(defeater_rid)
        if not from_id:
            continue
        for clause_id, victim_rid in pairs:
            overrides_edges.append({
                "doc_id":       doc_id,
                "from_node_id": from_id,
                "to_node_id":   clause_id,
                "edge_type":    "OVERRIDES_VIOLATION",
                "weight":       1.0,
                "properties": {
                    "defeater_rule":  defeater_rid,
                    "defeated_rule":  victim_rid,
                    "context":        "AP-tender" if ctx["is_ap_tender"] else "Central",
                },
            })

    if sat_edges:        _batch_insert("kg_edges", sat_edges)
    if vio_edges:        _batch_insert("kg_edges", vio_edges)
    if overrides_edges:  _batch_insert("kg_edges", overrides_edges)
    summary.defeasibility["violations_overridden"] = sum(
        1 for e in vio_edges if e["properties"]["defeated"]
    )
    summary.timing_ms["rule_edges"] = int((time.perf_counter() - t0) * 1000)

    # ── 15. CROSS_REFERENCES edges between materialised ClauseInstances
    t0 = time.perf_counter()
    template_to_clause_nodes: dict[str, list[str]] = defaultdict(list)
    for clause_node, match in zip(inserted_clauses, matches):
        template_to_clause_nodes[match["_template"]["clause_id"]].append(
            clause_node["node_id"]
        )

    xref_edges: list[dict] = []
    for clause_node, match in zip(inserted_clauses, matches):
        from_id = clause_node["node_id"]
        for xref_template in (match["_template"].get("cross_references") or []):
            for to_id in template_to_clause_nodes.get(xref_template, []):
                if to_id == from_id:
                    continue
                xref_edges.append({
                    "doc_id":       doc_id,
                    "from_node_id": from_id,
                    "to_node_id":   to_id,
                    "edge_type":    "CROSS_REFERENCES",
                    "weight":       1.0,
                    "properties":   {"via_template": xref_template},
                })
    if xref_edges:
        _batch_insert("kg_edges", xref_edges)
    summary.timing_ms["xref_edges"] = int((time.perf_counter() - t0) * 1000)

    # ── 16. Final counts (re-read to be authoritative)
    summary.nodes_by_type = _count_by(table="kg_nodes",
                                       group_col="node_type", doc_id=doc_id)
    summary.edges_by_type = _count_by(table="kg_edges",
                                       group_col="edge_type", doc_id=doc_id)
    summary.timing_ms["total"] = int((time.perf_counter() - t_total) * 1000)
    return summary


# ── Helpers used above ────────────────────────────────────────────────

def _fetch_wired_defeaters() -> list[dict]:
    """Return every rule whose `defeats` array is non-empty (the 27 wired
    defeaters today). Cached on the function attribute so repeated
    build_kg() calls in the same process pay the round-trip once."""
    cache = getattr(_fetch_wired_defeaters, "_cache", None)
    if cache is not None:
        return cache
    # PostgREST has no clean "JSON array non-empty" filter, so fetch
    # every rule's defeats column and filter client-side. Trivial cost
    # (~1.4k rows, ~few hundred KB) and only paid once per process via
    # the cache wrapping.
    rows = rest_select("rules", params={
        "select":  "rule_id,layer,severity,rule_type,typology_code,defeats,defeated_by",
    })
    rows = [r for r in rows if r.get("defeats")]
    _fetch_wired_defeaters._cache = rows  # type: ignore[attr-defined]
    return rows


def _fetch_rules(rule_ids: Iterable[str]) -> dict[str, dict]:
    """Fetch full rule rows for the given ids (chunked for URL length)."""
    rule_ids = sorted(set(rule_ids))
    if not rule_ids:
        return {}
    out: dict[str, dict] = {}
    CHUNK = 60
    for i in range(0, len(rule_ids), CHUNK):
        ids = rule_ids[i:i + CHUNK]
        ids_quoted = ",".join(f'"{x}"' for x in ids)
        rows = rest_select("rules", params={
            "select":   "rule_id,natural_language,layer,severity,rule_type,"
                         "pattern_type,typology_code,verification_method,"
                         "defeats,defeated_by",
            "rule_id":  f"in.({ids_quoted})",
        })
        for r in rows:
            out[r["rule_id"]] = r
    return out


def _count_by(*, table: str, group_col: str, doc_id: str) -> dict[str, int]:
    """Return {group_col_value: count} for rows matching doc_id."""
    rows = rest_select(table, params={
        "select": group_col,
        "doc_id": f"eq.{doc_id}",
    })
    return dict(Counter(r[group_col] for r in rows))


# ── CLI runner — verifies the function on Vizag UGSS ──────────────────

def _cli() -> int:
    from _common import DOC_ID, DOC_NAME, SOURCE_FILES

    print("=" * 72)
    print(f"build_kg() — building KG for {DOC_ID}")
    print(f"             {DOC_NAME}")
    print("=" * 72)
    summary = build_kg(
        doc_id=DOC_ID,
        document=SOURCE_FILES,
        document_name=DOC_NAME,
        clear_existing=True,
    )
    print()
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
