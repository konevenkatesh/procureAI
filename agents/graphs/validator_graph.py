"""
agents/graphs/validator_graph.py

The procureAI v0.2 validator pipeline as a LangGraph StateGraph.

Graph topology (linear, no branches):

    START
      │
      ▼
   document_converter   (file paths → markdown text)
      │
      ▼
   tender_classifier    (text → doc_id, is_ap_tender)
      │  ⚠ classifier produces tender_type/value/duration/funding too,
      │    but they are UNRELIABLE (Fix 2). We propagate ONLY is_ap_tender
      │    into downstream state.
      ▼
   kg_builder           (doc_id + paths → kg_nodes + kg_edges)
      │
      ▼
   validator            (doc_id → 4 SQL queries over kg_*)
      │
      ▼
   report_generator     (validation_result + kg_summary → JSON report)
      │
      ▼
    END

State flows:
    document_path ─► document_converter
                  ◄─ document_paths, document_text
    document_text ─► tender_classifier
                  ◄─ doc_id, is_ap_tender
    doc_id, document_paths, is_ap_tender ─► kg_builder
                                         ◄─ kg_summary
    doc_id ─► validator
            ◄─ validation_result
    validation_result, kg_summary ─► report_generator
                                  ◄─ report

The graph is intentionally a straight line: each node has exactly one
predecessor and one successor. Branching (e.g. "skip kg_builder when KG
already exists for this doc_id") will be added in v0.3 when we add the
kg-cache short-circuit; v0.2 always rebuilds.
"""
from __future__ import annotations

import hashlib
import sys
import time
from pathlib import Path
from typing import Annotated, TypedDict

# Repo root on sys.path so existing modules resolve cleanly when this
# file is invoked as a script.
REPO = Path(__file__).resolve().parent.parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# experiments/tender_graph holds kg_builder.build_kg() — re-exported here
EXP_DIR = REPO / "experiments" / "tender_graph"
if str(EXP_DIR) not in sys.path:
    sys.path.insert(0, str(EXP_DIR))

from langgraph.graph import StateGraph, START, END

from agents.queries.kg_queries import (
    get_violations, get_cascade_violations, get_full_audit_path, get_kg_summary,
)


# ── Graph state ───────────────────────────────────────────────────────

class ValidatorState(TypedDict, total=False):
    """LangGraph state for the validator pipeline.

    `total=False` so each node only writes the keys it owns; the merger
    keeps any keys from prior nodes intact."""
    # Inputs
    document_path:    str | list[str]   # raw input (one file, or list)
    doc_id_override:  str | None        # optional caller-provided doc_id
                                        # (lets tests reuse existing KGs)

    # converter outputs
    document_paths:   list[str]
    document_text:    str

    # classifier outputs
    doc_id:           str
    is_ap_tender:     bool

    # kg_builder output
    kg_summary:       dict

    # validator output
    validation_result: dict

    # report_generator output
    report:           dict

    # diagnostics — every node appends timing here
    timings_ms:       dict[str, int]


# ── Node 1: document_converter ────────────────────────────────────────

def _convert_one(path: Path) -> str:
    """Convert a single file to Markdown text. Already-MD passes through."""
    suf = path.suffix.lower()
    if suf == ".md":
        return path.read_text(encoding="utf-8")
    if suf == ".pdf":
        from builder.document_processor import convert_pdf_to_markdown
        return convert_pdf_to_markdown(str(path), doc_name=path.stem)
    if suf == ".docx":
        from docx import Document
        doc = Document(str(path))
        return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())
    raise ValueError(f"Unsupported document type: {path.suffix}")


def document_converter(state: ValidatorState) -> dict:
    """Convert one or more raw files (PDF/DOCX/MD) to Markdown text.

    Accepts either a single path string or a list of paths in
    `state.document_path`. Outputs `state.document_paths` (always a
    list of normalised Path strings) and `state.document_text` (the
    concatenation, with file headers preserved so doc_id is stable
    across rerun)."""
    t0 = time.perf_counter()
    raw = state.get("document_path")
    if raw is None:
        raise ValueError("ValidatorState.document_path is required")

    paths: list[Path]
    if isinstance(raw, (list, tuple)):
        paths = [Path(p) for p in raw]
    else:
        paths = [Path(raw)]

    # Each file → markdown; concatenate with explicit boundaries so the
    # MD5 below is stable regardless of order.
    paths_sorted = sorted(paths, key=lambda p: p.name)
    chunks: list[str] = []
    for p in paths_sorted:
        if not p.exists():
            raise FileNotFoundError(f"document_converter: {p} not found")
        chunks.append(f"\n\n[FILE: {p.name}]\n\n" + _convert_one(p))
    document_text = "\n\n".join(chunks)

    timings = dict(state.get("timings_ms") or {})
    timings["document_converter"] = int((time.perf_counter() - t0) * 1000)
    return {
        "document_paths": [str(p) for p in paths_sorted],
        "document_text":  document_text,
        "timings_ms":     timings,
    }


# ── Node 2: tender_classifier ─────────────────────────────────────────

def tender_classifier(state: ValidatorState) -> dict:
    """Classify the document and emit a stable doc_id.

    ⚠ Only `is_ap_tender` is trusted (Fix 2). The classifier also
    produces tender_type / estimated_value / duration_months /
    funding_source but these are KNOWN UNRELIABLE; we do NOT propagate
    them into state. The kg_builder still records them on the
    TenderDocument node with `*_classified` suffix and reliable=false
    flags so downstream nodes that read from kg_nodes see the warning.

    `doc_id` strategy:
        1. If `state.doc_id_override` is provided, use it verbatim
           (lets tests reuse an existing KG without rebuild).
        2. Otherwise: MD5 of document_text, first 12 chars, prefixed
           with "doc_" so it's recognisable as a content-addressed id.
    """
    t0 = time.perf_counter()

    text = state.get("document_text") or ""
    if not text:
        raise ValueError("tender_classifier: state.document_text is empty")

    from engines.classifier import TenderClassifier
    cls = TenderClassifier().classify(text)

    override = state.get("doc_id_override")
    if override:
        doc_id = override
    else:
        doc_id = "doc_" + hashlib.md5(text.encode("utf-8")).hexdigest()[:12]

    timings = dict(state.get("timings_ms") or {})
    timings["tender_classifier"] = int((time.perf_counter() - t0) * 1000)
    return {
        "doc_id":       doc_id,
        "is_ap_tender": bool(cls.is_ap_tender),
        "timings_ms":   timings,
    }


# ── Node 3: kg_builder (wraps build_kg) ───────────────────────────────

def kg_builder_node(state: ValidatorState) -> dict:
    """Wrap experiments/tender_graph/kg_builder.build_kg().

    Builds a fresh KG (clear-and-repopulate) for the supplied doc_id.
    The graph contract is idempotent: rerunning yields the same result
    in steady state. v0.3 will short-circuit when the KG is already
    fresh; v0.2 always rebuilds."""
    t0 = time.perf_counter()

    from kg_builder import build_kg     # experiments/tender_graph/kg_builder.py

    doc_id = state["doc_id"]
    paths  = [Path(p) for p in state["document_paths"]]

    summary = build_kg(
        doc_id=doc_id,
        document=paths,
        document_name=Path(paths[0]).stem if len(paths) == 1 else "Multi-volume",
        clear_existing=True,
    )
    # KGSummary is a dataclass; expose as a dict for state portability.
    kg_summary = {
        "doc_id":        summary.doc_id,
        "nodes_by_type": summary.nodes_by_type,
        "edges_by_type": summary.edges_by_type,
        "defeasibility": summary.defeasibility,
        "timing_ms":     summary.timing_ms,
    }
    timings = dict(state.get("timings_ms") or {})
    timings["kg_builder"] = int((time.perf_counter() - t0) * 1000)
    return {"kg_summary": kg_summary, "timings_ms": timings}


# ── Node 4: validator ─────────────────────────────────────────────────

def validator(state: ValidatorState) -> dict:
    """Read-only KG queries. Does NOT re-scan the document.

    Runs exactly the four query functions from kg_queries.py:
        get_violations(doc_id)
        get_cascade_violations(doc_id)
        get_full_audit_path(doc_id, rule_id) for each unique violated rule
        get_kg_summary(doc_id)
    """
    t0 = time.perf_counter()
    doc_id = state["doc_id"]

    violations = get_violations(doc_id)
    cascade    = get_cascade_violations(doc_id)
    summary    = get_kg_summary(doc_id)

    # Audit path for each UNIQUE violated rule_id (a rule may have
    # multiple violating clauses; we report the strongest per rule).
    unique_rule_ids = sorted({v["rule_id"] for v in violations if v["rule_id"]})
    audit_paths = {rid: get_full_audit_path(doc_id, rid) for rid in unique_rule_ids}

    timings = dict(state.get("timings_ms") or {})
    timings["validator"] = int((time.perf_counter() - t0) * 1000)
    return {
        "validation_result": {
            "doc_id":      doc_id,
            "violations":  violations,
            "cascade":     cascade,
            "audit_paths": audit_paths,
            "kg_summary":  summary,
        },
        "timings_ms": timings,
    }


# ── Node 5: report_generator ──────────────────────────────────────────

def report_generator(state: ValidatorState) -> dict:
    """Produce the final structured JSON report."""
    t0 = time.perf_counter()

    vr = state["validation_result"]
    kg = state["kg_summary"]
    violations = vr["violations"]

    # Filter out violations the validator marked as defeated.
    active_violations = [v for v in violations if not v.get("defeated")]

    # Build the findings list FIRST (deduped) so all counters below
    # reflect the same set of facts the report shows.
    findings: list[dict] = []
    seen_keys: set[tuple] = set()
    for v in active_violations:
        # Dedupe on (rule_id, clause_template_id, clause_section_type) so
        # the same finding emitted across N matched-clause realisations
        # of the same template doesn't show up N times in the report.
        key = (v["rule_id"], v["clause_template_id"], v["clause_section_type"])
        if key in seen_keys:
            continue
        seen_keys.add(key)
        ap = vr["audit_paths"].get(v["rule_id"], {})
        findings.append({
            "typology":   v["typology"],
            "rule_id":    v["rule_id"],
            "severity":   v["severity"],
            "clause": {
                "template_id":      v["clause_template_id"],
                "title":            v["clause_title"],
                "match_confidence": v["clause_match_confidence"],
            },
            "section": ap.get("section") or {
                "section_type": v["clause_section_type"],
            },
            "audit_path": {
                "rule":       ap.get("rule"),
                "defeaters":  ap.get("defeaters") or [],
            },
        })

    hard_blocks = [f for f in findings if f["severity"] == "HARD_BLOCK"]
    warnings    = [f for f in findings if f["severity"] == "WARNING"]
    advisories  = [f for f in findings if f["severity"] == "ADVISORY"]
    status = "BLOCK" if hard_blocks else "PASS"

    # Raw edge counts are kept too — useful for diagnostics that want
    # to know e.g. "the same violation matched 5 different clause
    # realisations" before dedup.
    raw_hard_blocks_edges = sum(
        1 for v in active_violations if v["severity"] == "HARD_BLOCK"
    )

    # KG stats roll-up
    edges = kg.get("edges_by_type", {})
    nodes = kg.get("nodes_by_type", {})
    defe  = kg.get("defeasibility", {}) or {}
    kg_stats = {
        "nodes":              sum(nodes.values()),
        "edges":              sum(edges.values()),
        "clauses_matched":    nodes.get("ClauseInstance", 0),
        "rules_activated":    nodes.get("RuleNode", 0),
        "sections":           nodes.get("Section", 0),
        "defeaters_fired":    defe.get("active_defeaters", 0),
        "defeats_edges":      edges.get("DEFEATS", 0),
        "violates_edges":     edges.get("VIOLATES_RULE", 0),
        "satisfies_edges":    edges.get("SATISFIES_RULE", 0),
        "cross_ref_edges":    edges.get("CROSS_REFERENCES", 0),
        "overrides_edges":    edges.get("OVERRIDES_VIOLATION", 0),
    }

    timings = dict(state.get("timings_ms") or {})
    timings["report_generator"] = int((time.perf_counter() - t0) * 1000)

    report = {
        "doc_id":            vr["doc_id"],
        "validation_status": status,
        "hard_block_count":  len(hard_blocks),
        "warning_count":     len(warnings),
        "advisory_count":    len(advisories),
        "cascade_count":     len(vr.get("cascade", [])),
        "findings":          findings,
        "kg_stats":          kg_stats,
        "raw_edge_counts": {
            "violates_total":      len(active_violations),
            "violates_hard_block": raw_hard_blocks_edges,
            "violates_defeated":   sum(1 for v in violations if v.get("defeated")),
        },
        "timings_ms":        timings,
    }
    return {"report": report, "timings_ms": timings}


# ── Build the graph ───────────────────────────────────────────────────

def build_validator_graph():
    """Compile the validator StateGraph and return a runnable."""
    g = StateGraph(ValidatorState)

    g.add_node("document_converter", document_converter)
    g.add_node("tender_classifier",  tender_classifier)
    g.add_node("kg_builder",         kg_builder_node)
    g.add_node("validator",          validator)
    g.add_node("report_generator",   report_generator)

    g.add_edge(START,                "document_converter")
    g.add_edge("document_converter", "tender_classifier")
    g.add_edge("tender_classifier",  "kg_builder")
    g.add_edge("kg_builder",         "validator")
    g.add_edge("validator",          "report_generator")
    g.add_edge("report_generator",   END)

    return g.compile()


# ── CLI entry point ───────────────────────────────────────────────────

def run(document_path: str | list[str], *, doc_id_override: str | None = None) -> dict:
    """Convenience wrapper. Returns the final report dict."""
    graph = build_validator_graph()
    final_state = graph.invoke({
        "document_path":   document_path,
        "doc_id_override": doc_id_override,
    })
    return final_state["report"]


if __name__ == "__main__":
    import json
    if len(sys.argv) < 2:
        print("Usage: python -m agents.graphs.validator_graph <path> [doc_id_override]")
        sys.exit(2)
    path = sys.argv[1]
    override = sys.argv[2] if len(sys.argv) > 2 else None
    report = run(path, doc_id_override=override)
    print(json.dumps(report, indent=2, default=str))
