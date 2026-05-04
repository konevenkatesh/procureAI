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

import os
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

# DISABLED — see L14 / L21 in LESSONS_LEARNED.md.
# The regex validator (RuleVerificationEngine) used to run inside build_kg
# at phase 7, materialising ValidationFinding nodes with tier=null and
# VIOLATES_RULE edges. Tier 1 BGE-M3 + LLM (scripts/tier1_pbg_check.py)
# is the replacement: it produces tier=1 findings with verbatim evidence
# and full audit trails. The regex validator polluted the database four
# separate times across this project (every rebuild) — it has been
# superseded, not augmented. Set this flag to True only if you need to
# diff the regex output against Tier 1 for debugging.
RUN_REGEX_VALIDATOR = False

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


# Qdrant collection used for section-vector retrieval. Shared across all
# tender docs; payloads carry a `doc_id` filter. v0.4 schema fields:
#     doc_id            : string         (filter for cross-doc isolation)
#     section_id        : kg_node UUID   (Section node_id — primary join key)
#     section_type      : string         (NIT|ITB|GCC|SCC|...)
#     heading           : string
#     source_file       : string
#     line_start_local  : int            (1-indexed in source MD file)
#     line_end_local    : int
#     word_count        : int
QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
QDRANT_COLLECTION = "tender_sections"
BGE_M3_DIM = 1024


def _qdrant_request(method: str, path: str, body: dict | None = None) -> dict:
    import requests as _req
    url = f"{QDRANT_URL.rstrip('/')}{path}"
    fn = getattr(_req, method.lower())
    r = fn(url, json=body, timeout=60) if body is not None else fn(url, timeout=60)
    r.raise_for_status()
    return r.json() if r.text else {}


def _ensure_qdrant_collection():
    """Create `tender_sections` (1024-dim, cosine) if it doesn't exist.
    No-op when the collection is already present."""
    try:
        _qdrant_request("GET", f"/collections/{QDRANT_COLLECTION}")
        return
    except Exception:
        pass
    _qdrant_request("PUT", f"/collections/{QDRANT_COLLECTION}", {
        "vectors": {"size": BGE_M3_DIM, "distance": "Cosine"},
    })
    # Indexed payload field for fast filter-by-doc_id
    try:
        _qdrant_request("PUT", f"/collections/{QDRANT_COLLECTION}/index", {
            "field_name": "doc_id",
            "field_schema": "keyword",
        })
    except Exception:
        pass   # already exists


def _qdrant_clear_doc(doc_id: str) -> int:
    """Delete every Qdrant point whose payload doc_id == this doc_id.
    Returns deleted count from the response (often 0 — Qdrant doesn't
    always echo). Idempotent: safe to call when nothing is indexed."""
    try:
        resp = _qdrant_request(
            "POST", f"/collections/{QDRANT_COLLECTION}/points/delete",
            {"filter": {"must": [{"key": "doc_id", "match": {"value": doc_id}}]}},
        )
        return resp.get("result", {}).get("operation_id", 0) and 1 or 0
    except Exception:
        return 0


def _qdrant_count_doc(doc_id: str) -> int:
    """Return exact point count for this doc_id."""
    try:
        resp = _qdrant_request(
            "POST", f"/collections/{QDRANT_COLLECTION}/points/count",
            {"filter": {"must": [{"key": "doc_id", "match": {"value": doc_id}}]},
             "exact": True},
        )
        return int(resp["result"]["count"])
    except Exception:
        return 0


def _ingest_sections_to_qdrant(
    doc_id: str,
    sections: list[dict],
    section_node_ids: list[str],
) -> tuple[int, dict]:
    """Embed each section's full_text via BGE-M3 and upsert to Qdrant
    with the v0.4 payload schema. Returns (n_points_after, timing_dict).
    """
    import time as _time
    timings: dict[str, int] = {}

    if not sections:
        return 0, {"embed_ms": 0, "upsert_ms": 0}

    t0 = _time.perf_counter()
    from sentence_transformers import SentenceTransformer
    cached = getattr(_ingest_sections_to_qdrant, "_model", None)
    if cached is None:
        model = SentenceTransformer("BAAI/bge-m3")
        model.max_seq_length = 1024
        _ingest_sections_to_qdrant._model = model
    else:
        model = cached
    timings["model_load_ms"] = int((_time.perf_counter() - t0) * 1000)

    # Idempotent: clear any prior points for this doc before re-ingesting
    _ensure_qdrant_collection()
    _qdrant_clear_doc(doc_id)

    # Embed in small batches (BGE-M3 is memory-hungry on long sections)
    t0 = _time.perf_counter()
    texts = [s.get("full_text") or "" for s in sections]
    vectors = model.encode(
        texts,
        normalize_embeddings=True,   # so Qdrant cosine = dot product
        show_progress_bar=False,
        batch_size=4,
    ).tolist()
    timings["embed_ms"] = int((_time.perf_counter() - t0) * 1000)

    # Build points. Qdrant point IDs are deterministic UUID5 of
    # (doc_id, section_node_id) so re-ingestion is a true upsert and
    # never proliferates duplicates.
    import uuid
    NS = uuid.uuid5(uuid.NAMESPACE_URL, "procureai/tender_sections/v0.4")
    points = []
    for sec, node_id, vec in zip(sections, section_node_ids, vectors):
        point_id = str(uuid.uuid5(NS, f"{doc_id}:{node_id}"))
        points.append({
            "id":     point_id,
            "vector": vec,
            "payload": {
                "doc_id":           doc_id,
                "section_id":       node_id,                       # kg_node UUID
                "section_type":     sec.get("section_type"),
                "heading":          sec.get("heading"),
                "source_file":      sec.get("source_file"),
                "line_start_local": sec.get("line_start_local"),
                "line_end_local":   sec.get("line_end_local"),
                "word_count":       sec.get("word_count"),
            },
        })

    t0 = _time.perf_counter()
    # Qdrant accepts up to thousands of points per upsert; keep batches
    # at 100 to bound memory + latency for retries.
    BATCH = 100
    for i in range(0, len(points), BATCH):
        _qdrant_request(
            "PUT", f"/collections/{QDRANT_COLLECTION}/points?wait=true",
            {"points": points[i:i + BATCH]},
        )
    timings["upsert_ms"] = int((_time.perf_counter() - t0) * 1000)

    n_after = _qdrant_count_doc(doc_id)
    return n_after, timings


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


def _snapshot_findings(doc_id: str) -> tuple[list[dict], list[dict]]:
    """Snapshot ValidationFinding nodes + VIOLATES_RULE edges before
    `_clear_kg` wipes the doc's KG (per L32).

    Why: typology scripts own the lifecycle of ValidationFindings and
    VIOLATES_RULE edges. kg_builder rebuilding the structural KG
    (TenderDocument + Sections) should NOT silently delete typology
    findings — that loses audit trail and forces every downstream
    typology check to re-run on every doc rebuild. The fix is
    snapshot-before-clear + restore-after-rebuild, with structural
    references (`from_node_id` → Section, `to_node_id` → RuleNode)
    re-resolved against the freshly-built nodes during restore.

    The cascade-delete on `kg_edges.from_node_id`/`to_node_id` FKs
    means a simple "DELETE WHERE node_type != 'ValidationFinding'"
    wouldn't work — deleting the structural Section/RuleNode nodes
    cascades to the edges anyway. We have to copy the rows out of
    the DB before the clear and re-insert after the rebuild.
    """
    findings = rest_select(
        "kg_nodes",
        params={"doc_id": f"eq.{doc_id}", "node_type": "eq.ValidationFinding"},
    )
    edges = rest_select(
        "kg_edges",
        params={"doc_id": f"eq.{doc_id}", "edge_type": "eq.VIOLATES_RULE"},
    )
    return list(findings or []), list(edges or [])


def _restore_findings(
    doc_id: str,
    new_doc_node_id: str,
    findings_snapshot: list[dict],
    edges_snapshot: list[dict],
) -> tuple[int, int]:
    """Re-insert preserved ValidationFinding nodes + VIOLATES_RULE
    edges after the structural rebuild, re-resolving structural
    references to the freshly-built nodes (per L32).

    Reference rewriting:
      * ValidationFinding.properties.section_node_id    → kept as-is
        (stale UUID; the original Section is gone, but the audit
        trail in the JSONB still records section_heading +
        source_file + line_start_local for human review).
      * VIOLATES_RULE.from_node_id                      → re-pointed
        to the new TenderDocument node (the original Section UUID is
        gone; FK requires a live target). The audit-trail
        attribution lives in finding.properties.section_heading and
        the JSONB `section_node_id` echo, which is sufficient for a
        reviewer.
      * VIOLATES_RULE.to_node_id                        → re-resolved
        via get_or_create RuleNode(doc_id, rule_id). The rule_id
        comes from the edge's properties.rule_id (always populated
        by the typology scripts).

    Findings/edges are re-inserted with their ORIGINAL `node_id` /
    `edge_id` so external audit references (UI deep-links, prior
    reports) keep resolving. Idempotent: re-running build_kg multiple
    times preserves the same UUIDs.
    """
    if not findings_snapshot and not edges_snapshot:
        return 0, 0

    # Re-insert ValidationFinding rows verbatim (with their original
    # node_id, properties, label, source_ref). PostgREST 'upsert' via
    # Prefer: resolution=merge-duplicates handles the case where a
    # second build_kg invocation with clear_existing=True snapshots
    # and re-inserts the same row.
    n_findings_restored = 0
    if findings_snapshot:
        rows = [{
            "node_id":    f["node_id"],
            "doc_id":     f["doc_id"],
            "node_type":  f["node_type"],
            "label":      f.get("label"),
            "properties": f.get("properties") or {},
            "source_ref": f.get("source_ref"),
        } for f in findings_snapshot]
        rest_insert("kg_nodes", rows)
        n_findings_restored = len(rows)

    # Re-resolve RuleNode for each preserved edge. RuleNodes are
    # per-doc, so they were also wiped by _clear_kg; re-create on
    # demand using the same get_or_create semantics that typology
    # scripts use.
    n_edges_restored = 0
    rule_node_id_cache: dict[str, str] = {}
    edge_rows: list[dict] = []
    for e in edges_snapshot:
        ep = e.get("properties") or {}
        rule_id = ep.get("rule_id")
        if not rule_id:
            # Unrecoverable — no rule_id means we can't re-target the
            # edge. Skip; the original ValidationFinding is still
            # restored and carries its rule_id in the finding props.
            continue
        if rule_id not in rule_node_id_cache:
            rule_node_id_cache[rule_id] = _get_or_create_rule_node_during_restore(
                doc_id, rule_id,
            )
        new_to = rule_node_id_cache[rule_id]
        edge_rows.append({
            "edge_id":      e["edge_id"],
            "doc_id":       e["doc_id"],
            "from_node_id": new_doc_node_id,   # re-point to fresh TenderDocument
            "to_node_id":   new_to,             # re-point to fresh RuleNode
            "edge_type":    e["edge_type"],
            "weight":       e.get("weight", 1.0),
            "properties":   ep,
        })
    if edge_rows:
        rest_insert("kg_edges", edge_rows)
        n_edges_restored = len(edge_rows)

    return n_findings_restored, n_edges_restored


def _get_or_create_rule_node_during_restore(doc_id: str, rule_id: str) -> str:
    """Mirror of the get_or_create_rule_node helper in tier1
    typology scripts, scoped to the kg_builder restore path.

    Looks up RuleNode(doc_id, rule_id) — if missing, fetches the rule
    row from the rules table and inserts a fresh RuleNode kg_node.
    Returns the RuleNode's node_id for use as VIOLATES_RULE.to_node_id.
    """
    existing = rest_select("kg_nodes", params={
        "doc_id":                f"eq.{doc_id}",
        "node_type":             "eq.RuleNode",
        "properties->>rule_id":  f"eq.{rule_id}",
    })
    if existing:
        return existing[0]["node_id"]
    rule_rows = rest_select("rules", params={
        "rule_id": f"eq.{rule_id}",
    })
    r = rule_rows[0] if rule_rows else {}
    inserted = rest_insert("kg_nodes", [{
        "doc_id":    doc_id,
        "node_type": "RuleNode",
        "label":     f"{rule_id}: {(r.get('natural_language') or '')[:90]}",
        "properties": {
            "rule_id":         rule_id,
            "layer":           r.get("layer"),
            "severity":        r.get("severity"),
            "rule_type":       r.get("rule_type"),
            "pattern_type":    r.get("pattern_type"),
            "typology_code":   r.get("typology_code"),
            "defeats":         r.get("defeats") or [],
        },
        "source_ref": f"rules:{rule_id}",
    }])
    return inserted[0]["node_id"]


def _clear_kg(doc_id: str) -> tuple[int, int]:
    """Idempotent reset: delete edges first (FK), then nodes.

    NOTE (L32): callers that want to preserve typology findings
    across rebuilds must call `_snapshot_findings(doc_id)` BEFORE
    `_clear_kg(doc_id)` and `_restore_findings(...)` AFTER the
    structural rebuild has created the new TenderDocument node.
    `build_kg` does this automatically when `clear_existing=True`.
    """
    n_e = rest_delete_doc("kg_edges", doc_id)
    n_n = rest_delete_doc("kg_nodes", doc_id)
    return n_n, n_e


# ── Phase 1: section split + classify ─────────────────────────────────

def _split_and_classify(source_files: list[Path]) -> list[dict]:
    """Split each source file via builder.section_splitter, then classify
    section_type via the heading-content-primary `classify_sections`
    walker.

    Returns sections with line_start/line_end in GLOBAL (concatenated
    full_text) coordinates. The validator runs on full_text and reports
    line numbers in that coordinate system, so section attribution must
    use the same coordinates.

    Concatenation contract — kept in sync with `build_kg`:
        full_text = "\\n\\n".join(file.read_text() for file in source_files)
    so each file boundary inserts ONE extra blank line between files
    (the "\\n\\n" join sequence). file N starts at global line:
        sum(line_count(file_i) for i < N) + N    (for the inserted blank lines)
    """
    from builder.section_splitter import split_into_sections

    out: list[dict] = []
    global_offset = 0     # line offset for the current file in full_text

    for file_idx, path in enumerate(source_files):
        text = path.read_text(encoding="utf-8")
        per_file_rows: list[dict] = []
        for ref, body in split_into_sections(text, path.stem):
            heading = ref.split("/", 1)[1] if "/" in ref else ref
            ls_local, le_local = find_line_range(text, body)
            per_file_rows.append({
                "section_type":     None,    # filled in by classify_sections below
                "heading":          heading,
                # Global coordinates (used by validator-line attribution)
                "line_start":       global_offset + ls_local,
                "line_end":         global_offset + le_local,
                # Local coordinates kept for diagnostics / human reading
                "line_start_local": ls_local,
                "line_end_local":   le_local,
                "word_count":       len(body.split()),
                "full_text":        body,
                "source_file":      path.name,
            })
        types = classify_sections(per_file_rows, path.name, file_text=text)
        for row, t in zip(per_file_rows, types):
            row["section_type"] = t
        out.extend(per_file_rows)
        # Advance global_offset: this file's line count + 1 blank line
        # inserted by "\n\n".join().
        n_lines_this_file = text.count("\n") + 1
        global_offset += n_lines_this_file + 1

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

def _run_validator_with_lines(
    document_text: str, document_name: str
) -> tuple[dict[str, int | None], list[dict]]:
    """Run the regex validator and return:
        rule_id_to_line: {rule_id → line_no | None}    — None ⇒ doc-level
        findings:        full finding records for ValidationFinding nodes

    A `rule_id` maps to a line number when the matcher could pinpoint
    where in the document the violation lives (e.g. a numeric shortfall
    check found "2.5%" at line 1451). It maps to None for absence-type
    violations (e.g. "no integrity pact clause anywhere"). kg_builder
    uses the line_no to look up the Section node that contains it."""
    from modules.validator.rule_verification_engine import RuleVerificationEngine
    eng = RuleVerificationEngine()
    report = eng.verify(document_text, document_name=document_name)
    rule_id_to_line: dict[str, int | None] = {}
    findings: list[dict] = []
    for f in (report.hard_blocks + report.warnings + report.advisories):
        line_no = getattr(f, "line_no", None)
        # Primary rule_id and all triggered rule_ids inherit the same line
        rule_id_to_line[f.rule_id] = line_no
        for trig in (f.triggered_rule_ids or []):
            rule_id_to_line[trig] = line_no
        findings.append({
            "rule_id":       f.rule_id,
            "typology_code": f.typology_code,
            "severity":      f.severity,
            "evidence":      (f.evidence_text or "")[:280],
            "source_clause": f.source_clause or "",
            "defeated_by":   list(f.defeated_by or []),
            "line_no":       line_no,
        })
    return rule_id_to_line, findings


# ── Phase 5: detect AP-tender context (the only signal we still
#    derive from the document text without an LLM) ──────────────────

# AP-tender keyword list: lifted verbatim from engines/classifier.py
# AP_KEYWORDS. Detection is intentionally simple — case-insensitive
# substring matches against the concatenated document text — because
# all 6 docs in our corpus carry one or more of these tokens
# unambiguously, and the regex classifier's other outputs (tender_type,
# estimated_value, duration_months, funding_source) have all been
# replaced by LLM extractors. tender_facts_extractor and
# tender_type_extractor are run as mandatory phases below; their
# outputs land on the same TenderDocument node.
_AP_KEYWORDS = (
    "apeprocurement.gov.in", "go ms", "ap state", "andhra pradesh",
    "apss", "reverse tendering", "ap pwd", "apcrda", "agicl",
    "amaravati", "vizag", "vijayawada", "tirupati", "kakinada",
    "judicial preview", "telugu", "tahsildar",
)


def _detect_ap_tender(document_text: str) -> bool:
    """Cheap AP-tender detector — case-insensitive substring match
    against the AP keyword list. Returns True iff any keyword appears."""
    haystack = document_text.lower()
    return any(kw in haystack for kw in _AP_KEYWORDS)


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

    # ── 2. Idempotent reset.
    # L32: snapshot ValidationFinding nodes + VIOLATES_RULE edges
    # BEFORE the clear; restore them AFTER the structural rebuild
    # creates the new TenderDocument. Typology scripts own the
    # lifecycle of those rows; kg_builder must not silently delete
    # findings on rebuild.
    findings_snapshot: list[dict] = []
    edges_snapshot:    list[dict] = []
    if clear_existing:
        findings_snapshot, edges_snapshot = _snapshot_findings(doc_id)
        n_n, n_e = _clear_kg(doc_id)
        summary.timing_ms["clear_prior_rows"] = 0  # delete returns count, not ms
        summary.defeasibility.setdefault("cleared_nodes", n_n)
        summary.defeasibility.setdefault("cleared_edges", n_e)
        summary.defeasibility["preserved_findings_pending_restore"] = len(findings_snapshot)
        summary.defeasibility["preserved_edges_pending_restore"]    = len(edges_snapshot)

    # ── 3. Detect AP-tender context (only field still derived from
    #       the raw text without an LLM). The other regex classifier
    #       outputs (tender_type, estimated_value, duration_months,
    #       funding_source) have been removed — LLM extractors below
    #       (Phase 5b) are the single source of truth.
    t0 = time.perf_counter()
    is_ap_tender = _detect_ap_tender(full_text)
    summary.timing_ms["classify"] = int((time.perf_counter() - t0) * 1000)

    # ── 4. Insert TenderDocument node. Only the two fields that
    #       don't require LLM extraction are written here:
    #         • doc_id (echo)
    #         • is_ap_tender + layer (substring match on AP keywords)
    #       Phase 5b runs tender_type_extractor and
    #       tender_facts_extractor, both of which patch the same
    #       node with their LLM-extracted fields.
    t0 = time.perf_counter()
    doc_node = _batch_insert("kg_nodes", [{
        "doc_id":     doc_id,
        "node_type":  "TenderDocument",
        "label":      document_name,
        "properties": {
            "doc_id":          doc_id,
            "is_ap_tender":    is_ap_tender,
            "layer":           "AP-State" if is_ap_tender else "Central",
        },
        "source_ref": "manual",
    }])[0]
    doc_node_id = doc_node["node_id"]

    # ── 4b. L32 restore: re-insert preserved ValidationFindings +
    # VIOLATES_RULE edges. Edges have their from_node_id re-pointed
    # to the new TenderDocument node and their to_node_id re-resolved
    # to a freshly-created RuleNode (per rule_id). Original UUIDs are
    # preserved so external audit references keep resolving.
    n_f_restored, n_e_restored = _restore_findings(
        doc_id, doc_node_id, findings_snapshot, edges_snapshot,
    )
    summary.defeasibility["restored_findings"] = n_f_restored
    summary.defeasibility["restored_edges"]    = n_e_restored

    # ── 5. Section split + classify + insert as kg_nodes
    sections = _split_and_classify(source_files) if source_files else []
    section_rows = [{
        "doc_id":     doc_id,
        "node_type":  "Section",
        "label":      f"{s['section_type']}: {s['heading'][:80]}",
        "properties": {
            "section_type":     s["section_type"],
            "heading":          s["heading"],
            # Global coords (concatenated full_text) — used by validator
            # line-attribution lookup.
            "line_start":       s["line_start"],
            "line_end":         s["line_end"],
            # Local coords (within this single source file) — what a
            # reviewer would see when opening the file in an editor.
            "line_start_local": s.get("line_start_local"),
            "line_end_local":   s.get("line_end_local"),
            "word_count":       s["word_count"],
            "source_file":      s["source_file"],
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

    # ── 6b. BGE-M3 ingest of section vectors → Qdrant.
    # Same `sections` list (full_text in memory) feeds the embedder.
    # Payload schema is the v0.4 contract used by Tier-1 retrieval:
    #     doc_id, section_id (kg_node UUID), section_type, heading,
    #     source_file, line_start_local, line_end_local, word_count.
    # Idempotent: prior points for this doc_id are deleted before
    # upsert, and point IDs are deterministic UUID5(doc_id, node_id)
    # so re-runs upsert in place rather than appending duplicates.
    t0 = time.perf_counter()
    n_qdrant, qdrant_timings = _ingest_sections_to_qdrant(
        doc_id, sections, section_node_ids,
    )
    summary.timing_ms["qdrant_ingest"] = int((time.perf_counter() - t0) * 1000)
    summary.defeasibility["qdrant_points_after_ingest"] = n_qdrant
    summary.defeasibility["qdrant_timings_ms"] = qdrant_timings

    # ── 6c. MANDATORY LLM extraction of tender facts.
    # tender_type_extractor + tender_facts_extractor patch the
    # TenderDocument node with the authoritative LLM-derived fields:
    #   tender_type, estimated_value_cr, integrity_pact_required
    # (each with *_reliable / *_confidence / *_evidence siblings).
    # No document enters the system without these — the regex-classifier
    # fallback that used to live in `_classify` has been removed.
    #
    # Placement note: these MUST run after Phase 5 (Section insertion)
    # because both extractors read Section nodes from kg_nodes to build
    # their NIT-text prompt. Failures are captured into the summary but
    # do NOT abort the build — Tier-1 typology checks degrade to
    # ADVISORY when the facts are null (UNKNOWN-fire path), which keeps
    # the pipeline live for downstream review while we follow up on
    # the extraction gap.
    t0 = time.perf_counter()
    extraction_results: dict[str, dict] = {}
    extraction_errors: dict[str, str] = {}
    try:
        from modules.extraction.tender_type_extractor import run as run_tender_type
        extraction_results["tender_type"] = run_tender_type(doc_id, commit=True)
    except Exception as exc:    # noqa: BLE001 — surface every error path
        extraction_errors["tender_type"] = repr(exc)
    try:
        from modules.extraction.tender_facts_extractor import run as run_tender_facts
        # L33: wider NIT window. The narrow tender_type-extractor
        # defaults (n_sections=1, max_chars=800) miss `estimated_value_cr`
        # on docs where the cost line sits in the second NIT section
        # (JA, HC, Vijayawada all return null at the narrow defaults
        # and reliable=True at n_sections=3, max_chars=6000).
        extraction_results["tender_facts"] = run_tender_facts(
            doc_id, commit=True, n_sections=3, max_chars=6000,
        )
    except Exception as exc:    # noqa: BLE001
        extraction_errors["tender_facts"] = repr(exc)
    summary.timing_ms["llm_extraction"]      = int((time.perf_counter() - t0) * 1000)
    summary.defeasibility["llm_extraction_errors"] = extraction_errors
    summary.defeasibility["llm_extraction_ran"]    = list(extraction_results.keys())

    # ── 7. Regex validator pass + RuleNode/DEFEATS/ValidationFinding/
    #       VIOLATES_RULE materialisation (phases 7–12).
    #
    # Gated behind RUN_REGEX_VALIDATOR (see top of file). Disabled by
    # default — Tier 1 BGE-M3 + LLM is the replacement that produces
    # tier=1 findings with verbatim evidence. The regex pass below
    # was found to produce wrong attributions and tier=null pollution
    # on every rebuild (L14 / L21).
    if not RUN_REGEX_VALIDATOR:
        summary.defeasibility["validator_violations"] = 0
        summary.defeasibility["validator_skipped"]   = True
        summary.timing_ms["validator"] = 0
        # Final counts (re-read to be authoritative even when the
        # validator phases are skipped).
        summary.nodes_by_type = _count_by(table="kg_nodes",
                                           group_col="node_type", doc_id=doc_id)
        summary.edges_by_type = _count_by(table="kg_edges",
                                           group_col="edge_type", doc_id=doc_id)
        summary.timing_ms["total"] = int((time.perf_counter() - t_total) * 1000)
        return summary

    # ── 7. Run the validator FIRST so we know which rules to materialise.
    # Honest scope: we no longer match clause_templates to sections —
    # that path produced 0.40-confidence noise that was misattributing
    # violations to wrong sections. v0.3 will replace it with BGE-M3
    # cross-encoder. For now, the KG only materialises rules that the
    # validator actually has something to say about.
    t0 = time.perf_counter()
    rule_id_to_line, findings = _run_validator_with_lines(
        full_text, document_name=document_name,
    )
    violated_rule_ids: set[str] = set(rule_id_to_line.keys())
    summary.defeasibility["validator_violations"] = len(violated_rule_ids)
    summary.timing_ms["validator"] = int((time.perf_counter() - t0) * 1000)

    # ── 8. Pull the rule rows we need: violated rules + their defeaters.
    t0 = time.perf_counter()
    rules_by_id = _fetch_rules(violated_rule_ids)

    # Forward scan: find every wired defeater whose `defeats` array
    # touches one of our violated rules. Materialise both ends so
    # DEFEATS edges have valid targets.
    wired_defeaters = _fetch_wired_defeaters()
    extra_defeaters: set[str] = set()
    for d in wired_defeaters:
        victims = set(d.get("defeats") or [])
        if victims & violated_rule_ids and d["rule_id"] not in rules_by_id:
            extra_defeaters.add(d["rule_id"])
    if extra_defeaters:
        rules_by_id.update(_fetch_rules(extra_defeaters))

    actionable: dict[str, dict] = {
        rid: r for rid, r in rules_by_id.items()
        if r.get("rule_type") == "TYPE_1_ACTIONABLE"
    }
    summary.defeasibility["distinct_rules_referenced"] = len(rules_by_id)
    summary.defeasibility["actionable_TYPE_1"]         = len(actionable)

    # Insert RuleNode for every rule we'll touch
    rule_node_id: dict[str, str] = {}
    rule_node_rows = [{
        "doc_id":     doc_id,
        "node_type":  "RuleNode",
        "label":      f"{rid}: {(r.get('natural_language') or '')[:90]}",
        "properties": {
            "rule_id":             rid,
            "layer":               r.get("layer"),
            "severity":            r.get("severity"),
            "rule_type":           r.get("rule_type"),
            "pattern_type":        r.get("pattern_type"),
            "typology_code":       r.get("typology_code"),
            "verification_method": r.get("verification_method"),
            "defeats":             r.get("defeats") or [],
        },
        "source_ref": f"rules:{rid}",
    } for rid, r in rules_by_id.items()]
    inserted_rules = _batch_insert("kg_nodes", rule_node_rows)
    for row, ins in zip(rule_node_rows, inserted_rules):
        rule_node_id[row["properties"]["rule_id"]] = ins["node_id"]
    summary.timing_ms["rule_node_insert"] = int((time.perf_counter() - t0) * 1000)

    # ── 9. Defeasibility — which rule_ids are defeated by an active defeater
    t0 = time.perf_counter()
    defeated_ids, defeater_pairs = _compute_defeated_set(
        rules_by_id, is_ap_tender=ctx["is_ap_tender"],
    )
    summary.defeasibility["active_defeaters"]    = len(defeater_pairs)
    summary.defeasibility["defeated_rule_ids"]   = len(defeated_ids)
    summary.defeasibility["active_defeater_ids"] = sorted(defeater_pairs.keys())
    summary.timing_ms["defeasibility_resolve"] = int((time.perf_counter() - t0) * 1000)

    # ── 10. DEFEATS edges (RuleNode → RuleNode)
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

    # ── 11. ValidationFinding nodes (one per finding, for diagnostics).
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
            "line_no":       f["line_no"],
        },
        "source_ref": "validation_finding",
    } for f in findings]
    if finding_rows:
        _batch_insert("kg_nodes", finding_rows)
    summary.timing_ms["finding_nodes"] = int((time.perf_counter() - t0) * 1000)

    # ── 12. VIOLATES_RULE edges — Section/Document → RuleNode
    #
    # Honest attribution: each violation is attached to the Section node
    # whose [line_start, line_end] contains the violating evidence's
    # line_no. For absence-type violations (line_no=None), the violation
    # is doc-level — we attach it to the TenderDocument node.
    #
    # NO SATISFIES_RULE edges are created. The previous logic that
    # emitted "this clause satisfies this rule" whenever a 0.40-confidence
    # template match referenced a rule_id in its declared metadata was
    # decoration, not verification. Until BGE-M3 cross-encoder lands,
    # the only honest claim is "we know this rule was violated and where".
    t0 = time.perf_counter()

    def _section_for_line(line_no: int | None) -> str | None:
        """Find the Section node whose line range contains line_no.
        Returns the Section's node_id, or None if no section covers it."""
        if line_no is None:
            return None
        for s, sec_id in zip(sections, section_node_ids):
            if int(s["line_start"]) <= line_no <= int(s["line_end"]):
                return sec_id
        return None

    vio_edges: list[dict] = []
    overrides_edges: list[dict] = []
    attribution_stats = {"section": 0, "document": 0, "skipped_no_target": 0}

    for rid, line_no in rule_id_to_line.items():
        target_rule_node = rule_node_id.get(rid)
        if not target_rule_node:
            attribution_stats["skipped_no_target"] += 1
            continue
        r = actionable.get(rid)
        if not r:
            attribution_stats["skipped_no_target"] += 1
            continue

        section_node = _section_for_line(line_no)
        from_node = section_node if section_node else doc_node_id
        attribution = "section" if section_node else "document"
        attribution_stats[attribution] += 1

        is_defeated = rid in defeated_ids
        vio_edges.append({
            "doc_id":       doc_id,
            "from_node_id": from_node,
            "to_node_id":   target_rule_node,
            "edge_type":    "VIOLATES_RULE",
            "weight":       1.0,
            "properties": {
                "rule_id":      rid,
                "severity":     r.get("severity"),
                "typology":     r.get("typology_code"),
                "defeated":     is_defeated,
                "attribution":  attribution,        # "section" or "document"
                "line_no":      line_no,            # None for doc-level
            },
        })

        # OVERRIDES_VIOLATION: defeater RuleNode → Section/Document where
        # the would-be violation lives.
        if is_defeated:
            for defeater_rid, victims in defeater_pairs.items():
                if rid not in victims:
                    continue
                defeater_node = rule_node_id.get(defeater_rid)
                if not defeater_node:
                    continue
                overrides_edges.append({
                    "doc_id":       doc_id,
                    "from_node_id": defeater_node,
                    "to_node_id":   from_node,
                    "edge_type":    "OVERRIDES_VIOLATION",
                    "weight":       1.0,
                    "properties": {
                        "defeater_rule":  defeater_rid,
                        "defeated_rule":  rid,
                        "context":        "AP-tender" if ctx["is_ap_tender"] else "Central",
                        "attribution":    attribution,
                    },
                })

    if vio_edges:       _batch_insert("kg_edges", vio_edges)
    if overrides_edges: _batch_insert("kg_edges", overrides_edges)
    summary.defeasibility["violations_overridden"] = sum(
        1 for e in vio_edges if e["properties"]["defeated"]
    )
    summary.defeasibility["attribution_stats"] = attribution_stats
    summary.timing_ms["rule_edges"] = int((time.perf_counter() - t0) * 1000)

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
