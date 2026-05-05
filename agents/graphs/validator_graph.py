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

    # Build the findings list (deduped by typology — multiple rules of
    # the same typology fire on the same evidence; report once per
    # typology with the strongest severity).
    findings: list[dict] = []
    seen_typologies: dict[str, dict] = {}
    sev_rank = {"HARD_BLOCK": 3, "WARNING": 2, "ADVISORY": 1}
    for v in active_violations:
        typ = v.get("typology") or "Unknown"
        existing = seen_typologies.get(typ)
        if existing is not None:
            # Keep the strongest severity; if same, prefer the one with
            # the lowest line_no (earliest in document)
            if (sev_rank.get(v["severity"], 0) > sev_rank.get(existing["severity"], 0)
                or (sev_rank.get(v["severity"], 0) == sev_rank.get(existing["severity"], 0)
                    and (v.get("line_no") or 10**9) < (existing.get("line_no") or 10**9))):
                seen_typologies[typ] = v
            continue
        seen_typologies[typ] = v

    for typ, v in seen_typologies.items():
        ap = vr["audit_paths"].get(v["rule_id"], {})
        findings.append({
            "typology":    typ,
            "rule_id":     v["rule_id"],
            "severity":    v["severity"],
            "attribution": v.get("attribution"),    # "section" | "document"
            "line_no":     v.get("line_no"),
            "section":     ap.get("section") or {
                "section_type":  v.get("section_type"),
                "heading":       v.get("section_heading"),
                "source_file":   v.get("section_source_file"),
                "line_start":    v.get("section_line_start"),
                "line_end":      v.get("section_line_end"),
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

    # KG stats roll-up
    edges = kg.get("edges_by_type", {})
    nodes = kg.get("nodes_by_type", {})
    defe  = kg.get("defeasibility", {}) or {}
    kg_stats = {
        "nodes":              sum(nodes.values()),
        "edges":              sum(edges.values()),
        "sections":           nodes.get("Section", 0),
        "rules_activated":    nodes.get("RuleNode", 0),
        "validation_findings": nodes.get("ValidationFinding", 0),
        "defeaters_fired":    defe.get("active_defeaters", 0),
        "defeats_edges":      edges.get("DEFEATS", 0),
        "violates_edges":     edges.get("VIOLATES_RULE", 0),
        "overrides_edges":    edges.get("OVERRIDES_VIOLATION", 0),
        "attribution_stats":  defe.get("attribution_stats", {}),
    }

    # ── HONEST COVERAGE STATEMENT ──
    # The validator only checks 8 of 42 typologies. This block makes
    # that limitation visible in every report so reviewers do not
    # mistake a PASS as comprehensive validation.
    coverage = _coverage_statement()

    timings = dict(state.get("timings_ms") or {})
    timings["report_generator"] = int((time.perf_counter() - t0) * 1000)

    report = {
        "doc_id":            vr["doc_id"],
        "validation_status": status,
        "hard_block_count":  len(hard_blocks),
        "warning_count":     len(warnings),
        "advisory_count":    len(advisories),
        "findings":          findings,
        "kg_stats":          kg_stats,
        "coverage":          coverage,
        "raw_edge_counts": {
            "violates_total":    len(active_violations),
            "violates_defeated": sum(1 for v in violations if v.get("defeated")),
        },
        "timings_ms":        timings,
    }
    return {"report": report, "timings_ms": timings}


# ── Coverage statement ────────────────────────────────────────────────

# Numbers below are pulled from production rules table (1,223
# TYPE_1_ACTIONABLE rules across 42 distinct typologies, 936 of them
# HARD_BLOCK severity). Update this block whenever a new typology
# checker is wired into the validator pipeline.
#
# The validator runs in TWO independent layers, both of which surface
# findings consumed by this report:
#   1. Legacy regex pipeline — modules/validator/rule_verification_engine.py
#      Fast O(text-length) keyword + percent/days extraction. Cheap but
#      brittle. Misses semantic violations.
#   2. Tier-1 BGE-M3 + LLM pipeline — scripts/tier1_*_check.py
#      Per-typology retrieval (Qdrant top-K) + LLM rerank + L24 evidence
#      guard + L36 grep fallback. Produces ValidationFinding nodes
#      directly into the KG with full audit trail.
# The Tier-1 pipeline is the primary source of truth for the 11
# typologies it covers. The regex pipeline is retained for the typologies
# that haven't been moved to Tier-1 yet (Missing-Anti-Collusion,
# Criteria-Restriction-Narrow) and as a cheap pre-filter.

_VALIDATED_BY_REGEX: tuple[str, ...] = (
    "PBG-Shortfall",
    "EMD-Shortfall",
    "Bid-Validity-Short",
    "Missing-Integrity-Pact",
    "Missing-Anti-Collusion",
    "Missing-PVC-Clause",
    "E-Procurement-Bypass",
    "Judicial-Preview-Bypass",
    "Criteria-Restriction-Narrow",
)

# Tier-1 BGE-M3 + LLM pipeline coverage (as of L38 / typology 11).
# These 11 typologies have dedicated scripts/tier1_*_check.py runners
# and emit ValidationFinding nodes directly into the KG. Eight overlap
# with regex coverage above (regex is the cheap pre-filter); three are
# Tier-1-only (LD, MA, BG-Validity-Gap, Blacklist) — regex has no
# matcher for those.
_VALIDATED_BY_TIER1_PIPELINE: tuple[str, ...] = (
    "PBG-Shortfall",
    "EMD-Shortfall",
    "Bid-Validity-Short",
    "Missing-PVC-Clause",
    "Missing-Integrity-Pact",
    "Missing-LD-Clause",
    "Mobilisation-Advance-Excess",
    "E-Procurement-Bypass",
    "Blacklist-Not-Checked",
    "BG-Validity-Gap",
    "Judicial-Preview-Bypass",
)

# Top typologies NEITHER pipeline covers, with their TYPE_1 rule
# counts. Maintained as static data so the report doesn't pay a
# Supabase round-trip per call. Re-derive when the rules table
# changes shape, or when a new tier1_*_check script lands.
_NOT_VALIDATED: tuple[tuple[str, int], ...] = (
    ("Missing-Mandatory-Field",      596),
    ("Single-Source-Undocumented",    51),
    ("COI-PMC-Works",                 34),
    ("Arbitration-Clause-Violation",  32),
    ("Geographic-Restriction",        29),
    ("Stale-Financial-Year",          29),
    ("Post-Tender-Negotiation",       28),
    ("Bid-Splitting-Pattern",         17),
    ("Limited-Tender-Misuse",         17),
    ("Spec-Tailoring",                14),
    ("Criteria-Restriction-Loose",    13),
    ("MakeInIndia-LCC-Missing",       13),
    ("Cover-Bidding-Signal",          10),
)

# Hard-coded HARD_BLOCK coverage. The Tier-1 pipeline covers more
# HARD_BLOCK rules than regex alone — Blacklist (41) + MA (23) + LD (17)
# add to the regex baseline. Numbers reflect the union of both layers.
_HB_RULES_TOTAL     = 936
_HB_RULES_VALIDATED = 215   # regex 134 + Tier-1-only adds (Blacklist 41 + MA 23 + LD 17)


def _coverage_statement() -> dict:
    """Produce the honest coverage block included on every report.

    Reviewers should read this before reading findings — it states
    precisely which typologies the validator can detect (across both
    regex and Tier-1 BGE-M3 layers), which it cannot, and what fraction
    of HARD_BLOCK rules the system can enforce. As of L38 the union
    covers ~23% of HARD_BLOCK rules; remaining coverage requires
    additional tier1_*_check scripts following the established template
    (see modules/validation/section_router.py + grep_fallback.py)."""
    not_validated_summary = [f"{name} ({n} rules)" for name, n in _NOT_VALIDATED]
    union_validated = set(_VALIDATED_BY_REGEX) | set(_VALIDATED_BY_TIER1_PIPELINE)
    not_validated_summary.append(
        f"…and {42 - len(union_validated) - len(_NOT_VALIDATED)} other typologies"
    )
    pct = round(100.0 * _HB_RULES_VALIDATED / _HB_RULES_TOTAL, 1)
    return {
        "validated_by_regex":           list(_VALIDATED_BY_REGEX),
        "validated_by_tier1_pipeline":  list(_VALIDATED_BY_TIER1_PIPELINE),
        "not_validated":                not_validated_summary,
        "coverage_of_hard_blocks":      f"{_HB_RULES_VALIDATED} of {_HB_RULES_TOTAL} HARD_BLOCK rules checked ({pct}%)",
        "clause_identification":        "Tier-1 pipeline uses BGE-M3 retrieval + LLM rerank with L24 evidence guard; regex pipeline uses keyword + percent/days extraction",
        "rule_satisfaction_verified":   False,
        "section_attribution":          "implemented via global-line lookup; absence-type violations attach to the document",
        "notes": [
            "PASS does NOT mean 'this tender is compliant' — it means "
            "'no rule fired in the typologies we currently check'. "
            f"That is {len(union_validated)} of 42 typologies.",
            "BLOCK status is reliable only for the typologies in "
            "validated_by_regex ∪ validated_by_tier1_pipeline above.",
            "SATISFIES_RULE edges are not produced — we have no "
            "evidence-grounded way to claim a clause satisfies a rule yet.",
        ],
    }


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
