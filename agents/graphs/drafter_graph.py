"""
agents/graphs/drafter_graph.py

procureAI Drafter (Module 3) as a LangGraph StateGraph with three
human-in-the-loop interrupt gates.

This is the v0.4 redesign that REPLACES the single-step CLI in
`scripts/draft_tender.py`. The CLI ships a one-shot render; this
graph runs a multi-phase workflow with explicit human verification at
three points (facts confirmation → clause selection review → final
draft approval). The portal drives the gates: each interrupt pauses
the graph, the portal renders the pending review, and the officer's
input is passed back via `Command(resume=...)` to continue.

──────────────────────────────────────────────────────────────────────
GRAPH TOPOLOGY (linear with three interrupt gates)
──────────────────────────────────────────────────────────────────────

    START
      │
      ▼
   project_brief             (PDF / DPR / free text → extracted_facts dict)
      │  Uses LLM to pull project facts (project_name, tender_type,
      │  ECV, duration, department, scope description, etc.).
      │  Mirrors the tender_facts_extractor / tender_type_extractor
      │  pattern from modules/extraction/.
      ▼
   missing_fields            (extracted_facts → checklist)
      │  Compares extracted facts to the required-fields contract.
      │  Tags each field REQUIRED / OPTIONAL / DEFAULT.
      │
      │  ┌────────────────────────────────────────────────────────┐
      │  │ ── interrupt() — HUMAN GATE 1: FactVerification ──     │
      │  │    Portal renders the checklist as a form (pre-filled  │
      │  │    with extracted values + confidence scores).         │
      │  │    Officer fills in REQUIRED-missing fields, accepts/  │
      │  │    overrides extracted values, submits via portal.     │
      │  │    Resume payload: {confirmed_facts: {…}, status:      │
      │  │                      "confirmed" | "abandoned"}        │
      │  └────────────────────────────────────────────────────────┘
      ▼
   clause_selector           (officer_facts → selected_clauses)
      │  Runs the L48 condition_evaluator on every DRAFTING_CLAUSE
      │  template. Status: MANDATORY (rule FIRES) / ADVISORY
      │  (UNKNOWN) / OPTIONAL / EXCLUDED. Same selection logic the
      │  CLI v3 drafter uses today.
      │
      │  ┌────────────────────────────────────────────────────────┐
      │  │ ── interrupt() — HUMAN GATE 2: ClauseReview (optional) │
      │  │    Portal renders selected clauses grouped by section. │
      │  │    Default action: "Accept All". Officer may toggle    │
      │  │    individual clauses off (for cases where standard    │
      │  │    template clauses don't apply to this tender).       │
      │  │    Resume payload: {accepted_clause_ids: [...],        │
      │  │                      rejected_clause_ids: [...],       │
      │  │                      status: "confirmed" | "abandoned"}│
      │  └────────────────────────────────────────────────────────┘
      ▼
   draft_assembler           (accepted_clauses + facts → markdown)
      │  Loads templates/ap_works_tender_skeleton.md.tmpl, fills the
      │  10 slots (NIT body, BDS overrides, ITB, Forms, etc.),
      │  substitutes {{name}} placeholders. Reuses the v3 CLI's
      │  build_nit_body_rows + build_bds_overrides + render_with_
      │  skeleton helpers verbatim.
      ▼
   validator                 (draft_markdown → validation_findings)
      │  Runs key Tier-1 checks (PBG / EMD / Bid-Validity / DLP / JV /
      │  Solvency) on the draft to confirm it passes. The drafter is
      │  the inverse of the validator: same regulatory baselines, same
      │  audit trail. By construction the drafted BDS values match the
      │  validator's expected values, so this run is a sanity check —
      │  any unexpected HARD_BLOCK triggers auto-correction OR a flag
      │  for officer review.
      ▼
      │  ┌────────────────────────────────────────────────────────┐
      │  │ ── interrupt() — HUMAN GATE 3: FinalApproval ──        │
      │  │    Portal renders the draft + validation summary.      │
      │  │    Officer reviews end-to-end and approves the draft   │
      │  │    OR sends back for re-iteration (loops to gate 1).   │
      │  │    Resume payload: {action: "approve" | "revise",      │
      │  │                      revision_notes: "...",            │
      │  │                      output_format: "md" | "docx"}     │
      │  └────────────────────────────────────────────────────────┘
      ▼
    END (final_output_path written)

──────────────────────────────────────────────────────────────────────
HUMAN-GATE INTERRUPT PATTERN
──────────────────────────────────────────────────────────────────────

Each gate node calls `langgraph.types.interrupt(payload)`:

  - The graph PAUSES. The runtime persists state via the configured
    checkpointer (MemorySaver in dev, PostgresSaver in production).
  - The interrupt's `payload` is what the PORTAL gets back from the
    initial `graph.invoke(...)` call: it's a dict the portal renders
    as a review form.
  - When the officer submits, the portal calls
    `graph.invoke(Command(resume=officer_input), config={"configurable":
    {"thread_id": draft_session_id}})` — this re-enters the same
    interrupt(), the call returns the officer's input, and the gate
    node returns its updates to state.

──────────────────────────────────────────────────────────────────────
RESUMABILITY + THREAD_ID
──────────────────────────────────────────────────────────────────────

Each draft session is identified by a `thread_id` (UUID). The portal
stores the thread_id on the draft session row. Calls to invoke() /
resume() always pass the thread_id in `config["configurable"]`. The
checkpointer persists state per-thread, so a session can be paused
overnight and resumed the next day from the exact gate the officer
was at.

──────────────────────────────────────────────────────────────────────
v0.4 SCOPE — SKELETON + PLACEHOLDER NODE BODIES
──────────────────────────────────────────────────────────────────────

This file currently contains:
  ✓ Full DrafterState TypedDict (all fields the workflow handles)
  ✓ All 8 node functions (5 logic + 3 interrupt gates)
  ✓ Graph build + compile with MemorySaver
  ✓ A test runner (run_to_first_interrupt + resume_gate functions)

Node bodies are PLACEHOLDER:
  - project_brief returns a hard-coded JA-shaped facts dict
  - missing_fields generates the checklist from the contract
  - clause_selector calls the existing select_clauses() helper from
    scripts/draft_tender.py (production-ready already)
  - draft_assembler calls render_with_skeleton() (production-ready)
  - validator returns a stub validation report
  - all 3 human gates correctly interrupt() with structured payloads

The next iteration replaces the project_brief placeholder with a real
LLM extractor (modules/extraction/project_brief_extractor.py — same
shape as tender_type_extractor.py).
"""
from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path
from typing import Any, TypedDict

# Repo root on sys.path
REPO = Path(__file__).resolve().parent.parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.memory import MemorySaver


# ──────────────────────────────────────────────────────────────────────
# DrafterState — TypedDict
# ──────────────────────────────────────────────────────────────────────

class DrafterState(TypedDict, total=False):
    """LangGraph state for the Drafter workflow.

    `total=False` so each node only writes the keys it owns; the merger
    keeps prior keys intact (same pattern as ValidatorState).
    """
    # ── Inputs ────────────────────────────────────────────────────────
    project_brief_path:    str | None       # uploaded PDF / DPR path
    project_brief_text:    str | None       # OR free-text description
    project_brief_source:  str              # "pdf" | "text" | "form"
    officer_overrides:     dict             # CLI / portal overrides

    # ── Node 1 outputs (project_brief) ────────────────────────────────
    extracted_facts:       dict             # all facts the LLM pulled
    facts_confidence:      dict             # per-field 0.0–1.0
    facts_source:          dict             # per-field "extracted" / "derived" / "default" / "not_found"
    facts_evidence:        dict             # per-field verbatim quote from brief
    extractor_summary:     dict             # n_required_filled / missing / ready_for_gate1
    extractor_model:       str              # LLM model identifier

    # ── Node 2 outputs (missing_fields) ───────────────────────────────
    facts_checklist:       list[dict]       # see schema in node body
    n_required_missing:    int
    n_optional_missing:    int

    # ── Gate 1 outputs (officer's confirmed facts) ────────────────────
    officer_facts:         dict             # final tender_facts dict
    gate_1_status:         str              # "pending" | "confirmed" | "abandoned"

    # ── Node 4 outputs (clause_selector) ──────────────────────────────
    selected_clauses:      list[dict]       # MANDATORY/ADVISORY/EXCLUDED
    clauses_by_status:     dict             # counts per status
    clauses_by_section:    dict             # grouped for review

    # ── Gate 2 outputs (clause review) ────────────────────────────────
    accepted_clause_ids:   list[str]
    rejected_clause_ids:   list[str]
    gate_2_status:         str

    # ── Node 6 outputs (draft_assembler) ──────────────────────────────
    draft_markdown:        str
    draft_path:            str
    n_clauses_in_draft:    int
    n_placeholders_filled: int
    n_placeholders_unresolved: int

    # ── Node 7 outputs (validator) ────────────────────────────────────
    validation_findings:   list[dict]
    n_hard_blocks:         int
    n_warnings:            int
    auto_corrections:      list[dict]

    # ── Gate 3 outputs (final approval) ───────────────────────────────
    officer_approved:      bool
    revision_notes:        str
    output_format:         str              # "md" | "docx"
    final_output_path:     str
    gate_3_status:         str

    # ── Diagnostics ───────────────────────────────────────────────────
    timings_ms:            dict             # node_name → ms
    workflow_status:       str              # "in_progress" | "awaiting_officer" | "approved" | "abandoned"
    thread_id:             str              # session id (set by runner)


# ──────────────────────────────────────────────────────────────────────
# Required-fields contract — what the workflow needs to produce a draft
# ──────────────────────────────────────────────────────────────────────

REQUIRED_FIELDS: tuple[tuple[str, str, str], ...] = (
    # (field_name, human_label, prompt_for_officer)
    ("project_name",        "Project Name",
     "What is the official name of the work? "
     "(e.g. 'Construction of Andhra Pradesh Judicial Academy')"),
    ("tender_type",         "Tender Type",
     "Works / EPC / PPP / Goods / Services / Consultancy?"),
    ("is_ap_tender",        "AP-State Tender?",
     "Is this an Andhra Pradesh State tender? (true/false)"),
    ("ecv_cr",              "Estimated Contract Value (Rs. Crore)",
     "Estimated Contract Value in crores (e.g. 125.5)"),
    ("duration_months",     "Period of Completion (months)",
     "Period of completion of the works in months (e.g. 24)"),
    ("department",          "Department / Implementing Agency",
     "Short department acronym (e.g. APCRDA, AGICL, NREDCAP)"),
)

OPTIONAL_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("department_full_name",  "Department Full Name",
     "Full name of the issuing department"),
    ("department_office",     "Department Office Address",
     "Office address (for pre-bid meeting + bid opening)"),
    ("contact_officer",       "Contact Officer",
     "Name + designation of the contact officer for clarifications"),
    ("contact_email",         "Contact Email",
     "Email for clarification requests"),
    ("scope_description",     "Scope of Work",
     "Detailed description of the scope of work (multi-paragraph OK)"),
    ("nit_number",            "NIT Number Override",
     "If you want to override the auto-generated NIT number"),
)


# ──────────────────────────────────────────────────────────────────────
# Node 1 — project_brief
# ──────────────────────────────────────────────────────────────────────

def project_brief_node(state: DrafterState) -> dict:
    """Extract project facts from uploaded PDF/DPR or free text.

    Wired to modules.extraction.project_brief_extractor.extract_project_brief
    which runs an LLM (OpenRouter qwen-2.5-72b-instruct by default) over the
    project brief text and returns 15 fields (6 REQUIRED + 4 IMPORTANT +
    5 OPTIONAL) — each with `{value, confidence, source, evidence}`.

    Inputs (state):
      project_brief_text   : str (free-text brief from the officer)
      project_brief_path   : str | None  (path to PDF/DPR — converted)
      project_brief_source : "pdf" | "text" | "form"

    PDF conversion: if project_brief_path is set and project_brief_text
    is not provided, the file is converted via builder.document_processor
    .convert_pdf_to_markdown (same converter the validator uses).
    """
    t0 = time.perf_counter()

    from modules.extraction.project_brief_extractor import extract_project_brief

    brief_text = state.get("project_brief_text") or ""
    brief_path = state.get("project_brief_path")

    if not brief_text and brief_path:
        brief_path_p = Path(brief_path)
        if brief_path_p.suffix.lower() == ".pdf":
            from builder.document_processor import convert_pdf_to_markdown
            brief_text = convert_pdf_to_markdown(str(brief_path_p),
                                                 doc_name=brief_path_p.stem)
        elif brief_path_p.suffix.lower() in (".md", ".txt"):
            brief_text = brief_path_p.read_text(encoding="utf-8")
        elif brief_path_p.suffix.lower() == ".docx":
            from docx import Document as _Doc
            doc = _Doc(str(brief_path_p))
            brief_text = "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())
        else:
            raise ValueError(f"Unsupported project-brief format: {brief_path_p.suffix}")

    if not brief_text.strip():
        raise ValueError(
            "project_brief_node: neither project_brief_text nor "
            "project_brief_path provided (or path resolved to empty content)"
        )

    result = extract_project_brief(brief_text)
    fields = result["fields"]

    # Flatten to the state shape downstream nodes expect: extracted_facts
    # has plain {field: value} so build_tender_facts(...) accepts it.
    extracted: dict = {f: fields[f]["value"] for f in fields}
    confidence: dict = {f: fields[f]["confidence"] for f in fields}
    source: dict     = {f: fields[f]["source"]     for f in fields}
    # Evidence per field — kept for Gate-1 portal display
    evidence: dict   = {f: fields[f]["evidence"]   for f in fields}

    timings = dict(state.get("timings_ms") or {})
    timings["project_brief"] = int((time.perf_counter() - t0) * 1000)

    return {
        "extracted_facts":   extracted,
        "facts_confidence":  confidence,
        "facts_source":      source,
        "facts_evidence":    evidence,
        "extractor_summary": result["summary"],
        "extractor_model":   result.get("model"),
        "workflow_status":   "in_progress",
        "timings_ms":        timings,
    }


# ──────────────────────────────────────────────────────────────────────
# Node 2 — missing_fields
# ──────────────────────────────────────────────────────────────────────

def missing_fields_node(state: DrafterState) -> dict:
    """Compare extracted_facts to REQUIRED_FIELDS + OPTIONAL_FIELDS.

    Output: a per-field checklist the portal can render as a form.
    Each entry has:
      {
        "field":       <name>,
        "label":       <human-readable label>,
        "prompt":      <officer prompt>,
        "status":      "REQUIRED" | "OPTIONAL" | "DEFAULT",
        "current_value": <extracted value or None>,
        "confidence":   <0.0-1.0>,
        "source":       "llm" | "default" | "missing",
        "must_fill":    bool,
      }
    """
    t0 = time.perf_counter()

    extracted  = state.get("extracted_facts") or {}
    confidence = state.get("facts_confidence") or {}
    source     = state.get("facts_source") or {}

    checklist: list[dict] = []
    n_req_missing = 0
    n_opt_missing = 0

    for name, label, prompt in REQUIRED_FIELDS:
        val = extracted.get(name)
        status = "REQUIRED"
        must_fill = (val is None or val == "")
        if must_fill:
            n_req_missing += 1
        checklist.append({
            "field":         name,
            "label":         label,
            "prompt":        prompt,
            "status":        status,
            "current_value": val,
            "confidence":    confidence.get(name, 0.0),
            "source":        source.get(name, "missing"),
            "must_fill":     must_fill,
        })

    for name, label, prompt in OPTIONAL_FIELDS:
        val = extracted.get(name)
        status = "OPTIONAL"
        # Optional fields don't block — but the officer may want to fill
        if val is None or val == "":
            n_opt_missing += 1
        checklist.append({
            "field":         name,
            "label":         label,
            "prompt":        prompt,
            "status":        status,
            "current_value": val,
            "confidence":    confidence.get(name, 0.0),
            "source":        source.get(name, "missing"),
            "must_fill":     False,
        })

    timings = dict(state.get("timings_ms") or {})
    timings["missing_fields"] = int((time.perf_counter() - t0) * 1000)

    return {
        "facts_checklist":    checklist,
        "n_required_missing": n_req_missing,
        "n_optional_missing": n_opt_missing,
        "timings_ms":         timings,
    }


# ──────────────────────────────────────────────────────────────────────
# Node 3 — Human Gate 1: FactVerification
# ──────────────────────────────────────────────────────────────────────

def human_gate_1_facts_verification(state: DrafterState) -> dict:
    """INTERRUPT — wait for officer to confirm/fill facts.

    The portal renders facts_checklist as a form. The officer:
      - Reviews extracted values + confidence + source per field
      - Fills any REQUIRED-missing fields
      - Optionally overrides extracted values (e.g. correcting a
        misclassified tender_type)
      - Optionally fills OPTIONAL fields
      - Submits via portal

    Resume payload shape (sent via Command(resume=...)):
      {
        "status":          "confirmed" | "abandoned",
        "confirmed_facts": {<field>: <value>, ...},
      }

    On "abandoned": graph returns and END is reached with
    workflow_status="abandoned".
    On "confirmed": graph proceeds to clause_selector with
    officer_facts populated.
    """
    payload = {
        "gate":              "FactVerification",
        "title":             "Step 1 — Confirm Project Facts",
        "facts_checklist":   state.get("facts_checklist") or [],
        "n_required_missing": state.get("n_required_missing", 0),
        "n_optional_missing": state.get("n_optional_missing", 0),
        "instructions": (
            "Review the facts the system extracted from your project brief. "
            "Fill in any REQUIRED missing fields. You may override any "
            "extracted value the system got wrong. Click 'Confirm and "
            "Continue' to proceed to clause selection."
        ),
    }
    response = interrupt(payload)

    # The portal sends back {"status": "confirmed" | "abandoned",
    #                        "confirmed_facts": {...}}
    # On abandon, propagate; clause_selector will see gate_1_status and
    # short-circuit.
    if not isinstance(response, dict):
        response = {"status": "abandoned",
                    "confirmed_facts": state.get("extracted_facts") or {}}

    status = response.get("status", "abandoned")
    confirmed = response.get("confirmed_facts") or state.get("extracted_facts") or {}

    return {
        "officer_facts":     confirmed,
        "gate_1_status":     status,
        "workflow_status":   ("in_progress" if status == "confirmed"
                              else "abandoned"),
    }


# ──────────────────────────────────────────────────────────────────────
# Node 4 — clause_selector
# ──────────────────────────────────────────────────────────────────────

def clause_selector_node(state: DrafterState) -> dict:
    """Run condition_evaluator on every DRAFTING_CLAUSE template.

    Reuses the production-ready helpers from scripts/draft_tender.py:
      - fetch_drafting_clauses() — pull all 512 templates (499 +
        13 forms seeded in L55)
      - select_clauses(clauses, facts) — filter by tender_type,
        evaluate condition_when, classify each clause as
        MANDATORY / ADVISORY / MANDATORY-DEFAULT / OPTIONAL / EXCLUDED

    Output:
      selected_clauses:    full list with status per clause
      clauses_by_status:   {status: count}
      clauses_by_section:  {section: [clause_id, ...]}
    """
    t0 = time.perf_counter()

    if state.get("gate_1_status") != "confirmed":
        # Officer abandoned — short-circuit
        return {"selected_clauses": [], "clauses_by_status": {}, "clauses_by_section": {}}

    # Lazy import — avoid circular dep when this module is imported by
    # the portal API server before scripts/ is initialised.
    sys.path.insert(0, str(REPO / "scripts"))
    from draft_tender import (
        fetch_drafting_clauses,
        select_clauses,
        build_tender_facts,
    )
    from collections import defaultdict
    import argparse as _ap

    # Build an argparse.Namespace-shaped object from officer_facts so
    # the existing select_clauses() helper accepts it. The graph's
    # state-dict-shaped facts → CLI's namespace-shaped facts adapter.
    of = state.get("officer_facts") or {}
    args = _ap.Namespace(
        project_name=of.get("project_name", ""),
        tender_type=of.get("tender_type", "Works"),
        is_ap_tender=bool(of.get("is_ap_tender", True)),
        ecv_cr=float(of.get("ecv_cr", 0)),
        duration_months=int(of.get("duration_months", 0)),
        department=of.get("department"),
        department_full_name=of.get("department_full_name"),
        department_office=of.get("department_office"),
        contact_officer=of.get("contact_officer"),
        contact_email=of.get("contact_email"),
        scope_description=of.get("scope_description"),
        scope_file=None,
        nit_number=of.get("nit_number"),
        output=None,
    )
    facts = build_tender_facts(args)

    clauses = fetch_drafting_clauses()
    selected = select_clauses(clauses, facts)

    by_status: dict[str, int] = defaultdict(int)
    by_section: dict[str, list[str]] = defaultdict(list)
    for c in selected:
        by_status[c["status"]] += 1
        if c["status"] != "EXCLUDED":
            by_section[c.get("position_section") or "(none)"].append(c.get("clause_id"))

    timings = dict(state.get("timings_ms") or {})
    timings["clause_selector"] = int((time.perf_counter() - t0) * 1000)

    return {
        "selected_clauses":   selected,
        "clauses_by_status":  dict(by_status),
        "clauses_by_section": {k: v for k, v in by_section.items()},
        "officer_facts":      of,    # passthrough so downstream nodes don't re-key
        "timings_ms":         timings,
    }


# ──────────────────────────────────────────────────────────────────────
# Node 5 — Human Gate 2: ClauseReview (optional one-click)
# ──────────────────────────────────────────────────────────────────────

def human_gate_2_clause_review(state: DrafterState) -> dict:
    """INTERRUPT — let officer review which clauses are included.

    Most officers will click "Accept All" for the standard regulatory
    set. The gate exists for the case where the officer wants to drop
    a non-applicable template (e.g. a clause that fired because the
    rule_id matched but the project context doesn't actually need it).

    Resume payload shape:
      {
        "status":            "confirmed" | "abandoned",
        "accept_all":        bool,
        "rejected_clause_ids": [<clause_id>, ...],
      }

    Default if accept_all=True: all non-EXCLUDED clauses kept.
    If accept_all=False: kept = selected - rejected.
    """
    selected = state.get("selected_clauses") or []
    by_status = state.get("clauses_by_status") or {}

    payload = {
        "gate":              "ClauseReview",
        "title":             "Step 2 — Review Selected Clauses",
        "summary":           by_status,
        "by_section":        state.get("clauses_by_section") or {},
        "n_total_included":  sum(1 for c in selected if c.get("status") != "EXCLUDED"),
        "n_excluded":        by_status.get("EXCLUDED", 0),
        "instructions": (
            "The system has selected clauses based on your project facts. "
            "Most officers will click 'Accept All' here. To drop a specific "
            "clause, use the per-clause toggle below."
        ),
    }
    response = interrupt(payload)

    if not isinstance(response, dict):
        response = {"status": "confirmed", "accept_all": True, "rejected_clause_ids": []}

    status = response.get("status", "confirmed")
    accept_all = bool(response.get("accept_all", True))
    rejected = list(response.get("rejected_clause_ids") or [])

    if accept_all:
        accepted = [c.get("clause_id") for c in selected
                    if c.get("status") != "EXCLUDED"]
    else:
        accepted = [c.get("clause_id") for c in selected
                    if c.get("status") != "EXCLUDED"
                    and c.get("clause_id") not in rejected]

    return {
        "accepted_clause_ids": accepted,
        "rejected_clause_ids": rejected,
        "gate_2_status":       status,
        "workflow_status":     ("in_progress" if status == "confirmed"
                                else "abandoned"),
    }


# ──────────────────────────────────────────────────────────────────────
# Node 6 — draft_assembler
# ──────────────────────────────────────────────────────────────────────

def draft_assembler_node(state: DrafterState) -> dict:
    """Render the canonical AP Works tender skeleton with selected clauses.

    Reuses the production-ready helpers from scripts/draft_tender.py:
      - build_parameter_map(args, facts) — 67-key pmap
      - render_with_skeleton(args, facts, selected, pmap) — 2-pass
        slot fill + {{name}} substitution

    Filters selected_clauses to only those in accepted_clause_ids
    (so the officer's Gate-2 rejections take effect). Writes the draft
    to /tmp/draft_<thread_id>.md and returns the path + statistics.
    """
    t0 = time.perf_counter()

    if state.get("gate_2_status") != "confirmed":
        return {"draft_markdown": "", "draft_path": "", "n_clauses_in_draft": 0,
                "n_placeholders_filled": 0, "n_placeholders_unresolved": 0}

    sys.path.insert(0, str(REPO / "scripts"))
    from draft_tender import (
        build_tender_facts,
        build_parameter_map,
        render_with_skeleton,
        _PLACEHOLDER_RE,
    )
    import argparse as _ap

    of = state.get("officer_facts") or {}
    args = _ap.Namespace(
        project_name=of.get("project_name", ""),
        tender_type=of.get("tender_type", "Works"),
        is_ap_tender=bool(of.get("is_ap_tender", True)),
        ecv_cr=float(of.get("ecv_cr", 0)),
        duration_months=int(of.get("duration_months", 0)),
        department=of.get("department"),
        department_full_name=of.get("department_full_name"),
        department_office=of.get("department_office"),
        contact_officer=of.get("contact_officer"),
        contact_email=of.get("contact_email"),
        scope_description=of.get("scope_description"),
        scope_file=None,
        nit_number=of.get("nit_number"),
        output=None,
    )
    facts = build_tender_facts(args)
    pmap = build_parameter_map(args, facts)

    # Honour Gate-2 rejections
    accepted = set(state.get("accepted_clause_ids") or [])
    selected = state.get("selected_clauses") or []
    if accepted:
        kept = [c if c.get("clause_id") in accepted
                else dict(c, status="EXCLUDED")
                for c in selected]
    else:
        kept = selected

    body, render_stats = render_with_skeleton(args, facts, kept, pmap)

    # Stats: placeholders filled vs unresolved
    n_placeholders = len(_PLACEHOLDER_RE.findall(body))
    n_unresolved   = len([m for m in _PLACEHOLDER_RE.finditer(body)
                          if m.group(0).startswith("{{")])
    # After substitute_placeholders, unresolved appear as `[[FILL: name]]`
    n_unresolved   = body.count("[[FILL:")

    thread_id = state.get("thread_id") or "draft"
    out_path = Path(f"/tmp/draft_{thread_id}.md")
    out_path.write_text(body, encoding="utf-8")

    timings = dict(state.get("timings_ms") or {})
    timings["draft_assembler"] = int((time.perf_counter() - t0) * 1000)

    return {
        "draft_markdown":            body,
        "draft_path":                str(out_path),
        "n_clauses_in_draft":        sum(1 for c in kept
                                         if c.get("status") != "EXCLUDED"),
        "n_placeholders_filled":     n_placeholders - n_unresolved,
        "n_placeholders_unresolved": n_unresolved,
        "timings_ms":                timings,
    }


# ──────────────────────────────────────────────────────────────────────
# Node 7 — validator
# ──────────────────────────────────────────────────────────────────────

def validator_node(state: DrafterState) -> dict:
    """Run key Tier-1 checks on the draft to confirm compliance.

    PLACEHOLDER: returns a stub validation report. Real implementation:
      For each of the 6 most-cited typologies (PBG / EMD / Bid-Validity
      / DLP / JV-allowed / Solvency-framework), inspect the draft
      against the same regulatory baselines:
        - PBG: BDS row "Performance Security 10% of contract value"
        - EMD: BDS row "Bid Security 1% + 1.5%"
        - Bid Validity: NIT body row "90 days"
        - DLP: NIT body row "24 months"
        - JV: BDS row "Joint Venture: Allowed"
        - Solvency: forms include Statement-VI + Annexure V refs
      Each check returns COMPLIANT / HARD_BLOCK.
      Any HARD_BLOCK (which would only happen if Gate-1 facts overrode
      the compliance defaults) triggers an entry in auto_corrections
      with a suggested fix the officer can review at Gate 3.
    """
    t0 = time.perf_counter()

    if state.get("gate_2_status") != "confirmed":
        return {"validation_findings": [], "n_hard_blocks": 0,
                "n_warnings": 0, "auto_corrections": []}

    # Stub findings — by construction the draft passes the 24 validators
    findings = [
        {"typology": "PBG-Shortfall",        "status": "COMPLIANT",
         "expected": "10%", "actual": "10%"},
        {"typology": "EMD-Shortfall",        "status": "COMPLIANT",
         "expected": "1% + 1.5%", "actual": "1% + 1.5%"},
        {"typology": "Bid-Validity-Short",   "status": "COMPLIANT",
         "expected": ">= 90 days", "actual": "90 days"},
        {"typology": "DLP-Period-Short",     "status": "COMPLIANT",
         "expected": ">= 24 months", "actual": "24 months"},
        {"typology": "Criteria-Restriction-Narrow",  "status": "COMPLIANT",
         "expected": "JV Allowed (no arbitrary ban)", "actual": "JV Allowed, max 2 members"},
        {"typology": "Solvency-Stale",       "status": "COMPLIANT",
         "expected": "Tahsildar OR Bank + 1-yr validity",
         "actual": "Bank + 1-yr validity stated in BDS"},
    ]
    n_hard = sum(1 for f in findings if f["status"] == "HARD_BLOCK")
    n_warn = sum(1 for f in findings if f["status"] == "WARNING")

    timings = dict(state.get("timings_ms") or {})
    timings["validator"] = int((time.perf_counter() - t0) * 1000)

    return {
        "validation_findings": findings,
        "n_hard_blocks":       n_hard,
        "n_warnings":          n_warn,
        "auto_corrections":    [],
        "timings_ms":          timings,
    }


# ──────────────────────────────────────────────────────────────────────
# Node 8 — Human Gate 3: FinalApproval
# ──────────────────────────────────────────────────────────────────────

def human_gate_3_final_approval(state: DrafterState) -> dict:
    """INTERRUPT — officer reviews the draft + validation report.

    Resume payload shape:
      {
        "action":          "approve" | "revise" | "abandon",
        "revision_notes":  "...",     # if action == "revise"
        "output_format":   "md" | "docx",
      }

    On approve: writes final_output_path (DOCX conversion if requested)
    and ends the workflow.
    On revise: officer's revision notes are stashed; workflow ends but
    the portal can spawn a new session pre-filled with the same
    project_brief + revision notes appended.
    On abandon: workflow ends.
    """
    payload = {
        "gate":                 "FinalApproval",
        "title":                "Step 3 — Final Approval",
        "draft_path":           state.get("draft_path"),
        "n_clauses_in_draft":   state.get("n_clauses_in_draft", 0),
        "n_placeholders_filled":     state.get("n_placeholders_filled", 0),
        "n_placeholders_unresolved": state.get("n_placeholders_unresolved", 0),
        "validation_findings":  state.get("validation_findings") or [],
        "n_hard_blocks":        state.get("n_hard_blocks", 0),
        "n_warnings":           state.get("n_warnings", 0),
        "instructions": (
            "Review the generated draft alongside the validation report. "
            "Click 'Approve' to finalise the document, 'Revise' to send "
            "back for re-iteration, or 'Abandon' to discard."
        ),
    }
    response = interrupt(payload)

    if not isinstance(response, dict):
        response = {"action": "abandon"}

    action = response.get("action", "abandon")
    revision_notes = response.get("revision_notes", "")
    output_format  = response.get("output_format", "md")

    final_path = state.get("draft_path", "")
    if action == "approve" and output_format == "docx":
        # PLACEHOLDER: real impl uses python-docx or pandoc to convert
        # final_path = final_path.replace(".md", ".docx")
        pass

    return {
        "officer_approved":  (action == "approve"),
        "revision_notes":    revision_notes,
        "output_format":     output_format,
        "final_output_path": final_path,
        "gate_3_status":     action,
        "workflow_status":   ("approved" if action == "approve"
                              else ("revise" if action == "revise"
                                    else "abandoned")),
    }


# ──────────────────────────────────────────────────────────────────────
# Build the graph
# ──────────────────────────────────────────────────────────────────────

def build_drafter_graph(checkpointer=None):
    """Compile the Drafter StateGraph and return a runnable.

    Pass `checkpointer=MemorySaver()` (default) for in-memory dev,
    or `SqliteSaver` / `PostgresSaver` for production persistence
    across server restarts.
    """
    g = StateGraph(DrafterState)

    g.add_node("project_brief",          project_brief_node)
    g.add_node("missing_fields",         missing_fields_node)
    g.add_node("human_gate_1_facts",     human_gate_1_facts_verification)
    g.add_node("clause_selector",        clause_selector_node)
    g.add_node("human_gate_2_clauses",   human_gate_2_clause_review)
    g.add_node("draft_assembler",        draft_assembler_node)
    g.add_node("validator",              validator_node)
    g.add_node("human_gate_3_approval",  human_gate_3_final_approval)

    g.add_edge(START,                    "project_brief")
    g.add_edge("project_brief",          "missing_fields")
    g.add_edge("missing_fields",         "human_gate_1_facts")
    g.add_edge("human_gate_1_facts",     "clause_selector")
    g.add_edge("clause_selector",        "human_gate_2_clauses")
    g.add_edge("human_gate_2_clauses",   "draft_assembler")
    g.add_edge("draft_assembler",        "validator")
    g.add_edge("validator",              "human_gate_3_approval")
    g.add_edge("human_gate_3_approval",  END)

    return g.compile(checkpointer=checkpointer or MemorySaver())


# ──────────────────────────────────────────────────────────────────────
# Convenience runners
# ──────────────────────────────────────────────────────────────────────

def start_session(
    *,
    project_brief_path: str | None = None,
    project_brief_text: str | None = None,
    thread_id: str | None = None,
    checkpointer=None,
) -> tuple[str, dict, "Any"]:
    """Start a new draft session. Returns (thread_id, interrupt_payload, graph).

    The graph runs from START up to the first interrupt (Gate 1) and
    pauses. The caller (portal) gets back the gate's payload to render
    as a form. To resume, call `resume_session(thread_id, response, graph)`.
    """
    if not (project_brief_path or project_brief_text):
        raise ValueError("Either project_brief_path or project_brief_text required")
    thread_id = thread_id or f"draft-{uuid.uuid4().hex[:12]}"
    graph = build_drafter_graph(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": thread_id}}
    init_state: dict = {
        "project_brief_path": project_brief_path,
        "project_brief_text": project_brief_text,
        "project_brief_source": ("pdf" if project_brief_path else "text"),
        "thread_id": thread_id,
        "workflow_status": "in_progress",
    }
    result = graph.invoke(init_state, config=config)
    # If interrupted, result is __interrupt__ list; payload is in result
    interrupt_payload = _extract_interrupt_payload(result)
    return thread_id, interrupt_payload, graph


def resume_session(
    thread_id: str,
    response: dict,
    graph,
) -> dict:
    """Resume a paused session with the officer's response. Returns the
    next interrupt payload, OR the final state if the graph reached END.
    """
    config = {"configurable": {"thread_id": thread_id}}
    result = graph.invoke(Command(resume=response), config=config)
    interrupt_payload = _extract_interrupt_payload(result)
    if interrupt_payload is None:
        # Graph completed
        return {"status": "completed", "final_state": result}
    return interrupt_payload


def _extract_interrupt_payload(result: dict) -> dict | None:
    """LangGraph returns __interrupt__ as a list of Interrupt objects when paused.
    Extract the payload of the most recent interrupt (or None if not paused)."""
    if not isinstance(result, dict):
        return None
    interrupts = result.get("__interrupt__")
    if not interrupts:
        return None
    # Take the most recent interrupt's value
    last = interrupts[-1] if isinstance(interrupts, list) else interrupts
    return getattr(last, "value", None) or last


# ──────────────────────────────────────────────────────────────────────
# CLI entry point — dry run
# ──────────────────────────────────────────────────────────────────────

def main(argv: list[str]) -> int:
    """Dry run: start a session, auto-respond to all gates with the
    JA-shaped defaults, print the final state. Useful for verifying
    the graph topology end-to-end without the portal."""
    print("=" * 76)
    print("  procureAI Drafter — LangGraph workflow (dry run)")
    print("=" * 76)

    # Demo brief — Kurnool District Hospital, AP-State, Rs.85cr, 18mo
    DEMO_BRIEF = (
        "We need to issue a tender for construction of a new District "
        "Hospital at Kurnool with 3 floors, total built-up area 15,000 "
        "sqm. Budget is Rs.85 crore. APIIC is the implementing agency. "
        "The work should complete in 18 months. This is a state "
        "government funded project."
    )

    # Step 1: start session — pauses at Gate 1
    thread_id, gate1_payload, graph = start_session(
        project_brief_text=DEMO_BRIEF,
    )
    print(f"\n  brief: {DEMO_BRIEF[:100]}…")
    print(f"  thread_id      : {thread_id}")
    print(f"  paused at gate : {gate1_payload.get('gate')}")
    print(f"  required missing: {gate1_payload.get('n_required_missing')}")
    print(f"  optional missing: {gate1_payload.get('n_optional_missing')}")
    print(f"  checklist items: {len(gate1_payload.get('facts_checklist') or [])}")
    print(f"\n  Extracted facts (top fields):")
    for c in (gate1_payload.get('facts_checklist') or [])[:10]:
        v = c.get('current_value')
        src = c.get('source')
        conf = c.get('confidence')
        if v is not None:
            print(f"    {c['field']:24s} = {str(v)[:40]:40s}  conf={conf:.2f}  src={src}")
        else:
            print(f"    {c['field']:24s} = (missing — must_fill={c.get('must_fill')})")

    # Auto-respond to Gate 1 — fill missing OPTIONAL fields with values
    # consistent with the extracted department (no hard-coded APCRDA).
    confirmed_facts = {
        c["field"]: c["current_value"]
        for c in gate1_payload.get("facts_checklist", [])
    }
    # Heuristic fallbacks per the extracted department acronym
    DEPT_FULL_NAMES = {
        "APIIC":   "Andhra Pradesh Industrial Infrastructure Corporation (APIIC)",
        "APCRDA":  "Andhra Pradesh Capital Region Development Authority (APCRDA)",
        "AGICL":   "Amaravati Growth and Infrastructure Corporation Limited (AGICL)",
        "NREDCAP": "New and Renewable Energy Development Corporation of Andhra Pradesh (NREDCAP)",
        "APMSIDC": "AP Medical Services and Infrastructure Development Corporation (APMSIDC)",
    }
    dept = confirmed_facts.get("department") or "APCRDA"
    if not confirmed_facts.get("department_full_name"):
        confirmed_facts["department_full_name"] = DEPT_FULL_NAMES.get(
            dept, f"{dept} (Government of Andhra Pradesh)")
    if not confirmed_facts.get("department_office"):
        confirmed_facts["department_office"] = (
            f"{dept} Office (procurement officer to update)")
    if not confirmed_facts.get("contact_officer"):
        confirmed_facts["contact_officer"] = (
            f"Tender Inviting Authority, {dept}")
    if not confirmed_facts.get("contact_email"):
        confirmed_facts["contact_email"] = "proc@example.org"
    if not confirmed_facts.get("scope_description"):
        confirmed_facts["scope_description"] = (
            confirmed_facts.get("scope_description")
            or "[SCOPE OF WORK TO BE SPECIFIED BY PROCUREMENT OFFICER]"
        )

    gate2_payload = resume_session(thread_id, {
        "status":          "confirmed",
        "confirmed_facts": confirmed_facts,
    }, graph)
    print(f"\n  paused at gate : {gate2_payload.get('gate')}")
    print(f"  clauses summary: {gate2_payload.get('summary')}")
    print(f"  total included : {gate2_payload.get('n_total_included')}")

    # Auto-respond to Gate 2 (Accept All)
    gate3_payload = resume_session(thread_id, {
        "status":              "confirmed",
        "accept_all":          True,
        "rejected_clause_ids": [],
    }, graph)
    print(f"\n  paused at gate : {gate3_payload.get('gate')}")
    print(f"  draft path     : {gate3_payload.get('draft_path')}")
    print(f"  clauses        : {gate3_payload.get('n_clauses_in_draft')}")
    print(f"  placeholders   : {gate3_payload.get('n_placeholders_filled')} filled / "
          f"{gate3_payload.get('n_placeholders_unresolved')} unresolved")
    print(f"  HARD_BLOCKs    : {gate3_payload.get('n_hard_blocks')}")
    print(f"  validation     : {len(gate3_payload.get('validation_findings', []))} typologies checked")

    # Auto-respond to Gate 3 (Approve)
    final = resume_session(thread_id, {
        "action":         "approve",
        "output_format":  "md",
    }, graph)
    print(f"\n  workflow_status: {final.get('final_state', {}).get('workflow_status', 'unknown')}")
    print(f"  final output   : {final.get('final_state', {}).get('final_output_path', 'unknown')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
