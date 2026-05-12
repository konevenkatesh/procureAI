"""R7.5 — Retrieval+LLM drafter v2 (15-node LangGraph).

Replaces the deterministic 12-node template-mode workflow in langgraph_workflow.py
with a corpus-driven flow that:

  - Retrieves SBDSection content_md per section via pgvector cosine similarity
  - Retrieves TechSpecTemplate exemplars for the BoQ generator
  - Substitutes {{vars}} for TEMPLATE+PLACEHOLDERS sections (NIT, II, III)
  - Drops content_md verbatim for BOILERPLATE sections (IV, V, VII, IX)
  - Calls Gemini Pro for PROJECT-SPECIFIC adaptation (Section VI sub-blocks, VIII PCC)
  - Calls Gemini Flash for BoQ enrichment in 30-row batches (via boq_generator)
  - Falls back to Claude Sonnet 4 on structured-output drift

Backwards-compatible SSE event surface — the frontend live view (M1.3) keeps
working because the new workflow emits the same node_started / section_complete
/ text_chunk / table_row_added / node_complete events. Three additional event
types are introduced for BoQ telemetry:

  - boq_batch_started   { batch_idx, discipline, n_rows }
  - boq_item_complete   { row, batch_idx }
  - llm_call            { model, node, prompt_tokens, completion_tokens, ms }

The 15 nodes are:

  1. analyze_inputs              (read-only — schema validity check)
  2. classify_tender_type        (echo of Step-2 classifications)
  3. retrieve_sbd_sections       (pgvector top-K=3 per section_id)
  4. retrieve_tech_templates     (pgvector top-K=8 per discipline detected)
  5. retrieve_clauses            (rules + reference clauses, additive)
  6. draft_section_I             (NIT  — TEMPLATE+PLACEHOLDERS)
  7. draft_section_II            (ITB  — TEMPLATE+PLACEHOLDERS)
  8. draft_section_III           (Eval — TEMPLATE+PLACEHOLDERS)
  9. draft_section_IV            (Bidding Forms — BOILERPLATE)
 10. draft_section_V             (Eligibility — BOILERPLATE)
 11. draft_section_VI            (Works Reqs — PROJECT-SPECIFIC, Vertex Pro)
 12. draft_BoQ                   (Vertex Flash 30-row batches)
 13. draft_section_VIII          (PCC — PROJECT-SPECIFIC, Vertex Pro)
 14. assemble_document           (citations finalised, section ordering)
 15. render_artifacts            (deferred handoff to renderers.py at publish-time)
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from enum import Enum
from typing import Any, Callable, Iterator, Optional

from .schemas import (
    BoQRow,
    Citations,
    CitationSource,
    GeneralTerms,
    SSEEventFieldUpdate,
    SSEEventNodeComplete,
    SSEEventNodeStarted,
    SSEEventSectionComplete,
    SSEEventSectionStarted,
    SSEEventTableRowAdded,
    SSEEventTextChunk,
    SSEEventWorkflowComplete,
    TenderDraftState,
    now_iso,
)

logger = logging.getLogger(__name__)


# ─── v2 node identifiers (separate from v1 to avoid breaking change) ──


class WorkflowNodeV2(str, Enum):
    ANALYZE_INPUTS         = "analyze_inputs"
    CLASSIFY_TENDER_TYPE   = "classify_tender_type"
    RETRIEVE_SBD_SECTIONS  = "retrieve_sbd_sections"
    RETRIEVE_TECH_TEMPLATES = "retrieve_tech_templates"
    RETRIEVE_CLAUSES       = "retrieve_clauses"
    DRAFT_SECTION_I        = "draft_section_I_NIT"
    DRAFT_SECTION_II       = "draft_section_II_ITB"
    DRAFT_SECTION_III      = "draft_section_III_evaluation"
    DRAFT_SECTION_IV       = "draft_section_IV_bidding_forms"
    DRAFT_SECTION_V        = "draft_section_V_eligibility"
    DRAFT_SECTION_VI       = "draft_section_VI_works_requirements"
    DRAFT_BOQ              = "draft_BoQ"
    DRAFT_SECTION_VIII     = "draft_section_VIII_PCC"
    ASSEMBLE_DOCUMENT      = "assemble_document"
    RENDER_ARTIFACTS       = "render_artifacts"


WORKFLOW_V2_NODES: list[WorkflowNodeV2] = [
    WorkflowNodeV2.ANALYZE_INPUTS,
    WorkflowNodeV2.CLASSIFY_TENDER_TYPE,
    WorkflowNodeV2.RETRIEVE_SBD_SECTIONS,
    WorkflowNodeV2.RETRIEVE_TECH_TEMPLATES,
    WorkflowNodeV2.RETRIEVE_CLAUSES,
    WorkflowNodeV2.DRAFT_SECTION_I,
    WorkflowNodeV2.DRAFT_SECTION_II,
    WorkflowNodeV2.DRAFT_SECTION_III,
    WorkflowNodeV2.DRAFT_SECTION_IV,
    WorkflowNodeV2.DRAFT_SECTION_V,
    WorkflowNodeV2.DRAFT_SECTION_VI,
    WorkflowNodeV2.DRAFT_BOQ,
    WorkflowNodeV2.DRAFT_SECTION_VIII,
    WorkflowNodeV2.ASSEMBLE_DOCUMENT,
    WorkflowNodeV2.RENDER_ARTIFACTS,
]


# Section classification (matches R7.1 measurement)
_SECTION_KIND: dict[str, str] = {
    "I":   "TEMPLATE",            # NIT — high placeholder density
    "II":  "TEMPLATE",            # ITB — measured similarity ~0.27
    "III": "TEMPLATE",            # Evaluation — ~0.07 but structurally template
    "IV":  "BOILERPLATE",         # Bidding Forms
    "V":   "BOILERPLATE",         # Eligibility lists
    "VI":  "PROJECT_SPECIFIC",    # Works Requirements — LLM adapt
    "VII": "BOILERPLATE",         # GCC
    "VIII": "PROJECT_SPECIFIC",   # PCC — LLM adapt
    "IX":  "BOILERPLATE",         # Annexures
}


# ─── pgvector retrieval (psycopg) ─────────────────────────────────────


_REGISTRY_CACHE: Optional[list] = None


def _registry_fallback():
    """Cached TechSpecTemplate REGISTRY for offline / no-DB scenarios."""
    global _REGISTRY_CACHE
    if _REGISTRY_CACHE is None:
        from .tech_spec_templates import all_templates
        _REGISTRY_CACHE = list(all_templates())
    return _REGISTRY_CACHE


def _get_db_conn():
    """Open a psycopg connection to the Supabase pooler. Returns None if unavailable."""
    try:
        import psycopg  # type: ignore
        # Lazy import to avoid hard dep at startup
        sys_path_added = False
        try:
            from builder.config import settings
        except ImportError:
            import sys, pathlib
            repo = pathlib.Path(__file__).resolve().parent.parent.parent.parent
            sys.path.insert(0, str(repo))
            sys_path_added = True
            from builder.config import settings
        if not settings.supabase_url:
            return None
        return psycopg.connect(settings.supabase_url, sslmode="require", connect_timeout=15)
    except Exception as e:
        logger.warning(f"  pgvector DB unavailable: {e}")
        return None


def _embed_query(text: str) -> Optional[list[float]]:
    """Vertex AI 768-dim embedding; returns None on failure.
    R8.3 fix: 12s timeout (down from 60s default) so retrieval fails-soft fast
    when Vertex embedding API is slow. Caller (retrieve_sbd_section /
    retrieve_tech_templates_pgvector) falls through to in-memory registry."""
    try:
        from .vertex_client import embed_text
        return embed_text(text, task_type="RETRIEVAL_QUERY", timeout=12)
    except Exception as e:
        logger.warning(f"  embed_query failed (fast-fail): {e}")
        return None


def _vector_literal(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in v) + "]"


def retrieve_sbd_section(section_id: str, query_text: str, top_k: int = 3) -> list[dict]:
    """pgvector cosine-similarity lookup for SBDSection rows for a given section_id.
    Returns [{node_id, label, properties, distance}, …]; empty list on failure.
    """
    emb = _embed_query(query_text)
    if not emb:
        return []
    conn = _get_db_conn()
    if not conn:
        return []
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT node_id::text, label, properties,
                           embedding <=> %s::vector AS dist
                    FROM kg_nodes
                    WHERE node_type = 'SBDSection'
                      AND properties->>'section_id' = %s
                      AND embedding IS NOT NULL
                    ORDER BY dist ASC
                    LIMIT %s
                    """,
                    (_vector_literal(emb), section_id, top_k),
                )
                rows = cur.fetchall()
                return [{
                    "node_id":    r[0],
                    "label":      r[1],
                    "properties": r[2],
                    "distance":   float(r[3]),
                } for r in rows]
    except Exception as e:
        logger.warning(f"  retrieve_sbd_section({section_id}) failed: {e}")
        return []


def retrieve_tech_templates_pgvector(
    discipline: str, query_text: str, top_k: int = 8,
) -> list:
    """Returns top-K TechSpecTemplate objects (Pydantic) by cosine similarity.
    Falls back to in-memory registry filter on DB or embed failure."""
    emb = _embed_query(query_text)
    conn = _get_db_conn() if emb else None
    if not conn:
        # In-memory fallback by discipline tag
        from .boq_generator import retrieve_templates_by_discipline
        return retrieve_templates_by_discipline(discipline, top_k=top_k,
                                                registry=_registry_fallback())
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT properties, embedding <=> %s::vector AS dist
                    FROM kg_nodes
                    WHERE node_type = 'TechSpecTemplate'
                      AND embedding IS NOT NULL
                    ORDER BY dist ASC
                    LIMIT %s
                    """,
                    (_vector_literal(emb), top_k),
                )
                rows = cur.fetchall()
        # Hydrate into TechSpecTemplate Pydantic objects
        from .tech_spec_templates.base import TechSpecTemplate
        out = []
        for props, _dist in rows:
            try:
                out.append(TechSpecTemplate.model_validate(props))
            except Exception:
                continue
        if out:
            return out
        # Empty result → registry fallback (likely an embedding mismatch)
        from .boq_generator import retrieve_templates_by_discipline
        return retrieve_templates_by_discipline(discipline, top_k=top_k,
                                                registry=_registry_fallback())
    except Exception as e:
        logger.warning(f"  retrieve_tech_templates_pgvector failed: {e}")
        from .boq_generator import retrieve_templates_by_discipline
        return retrieve_templates_by_discipline(discipline, top_k=top_k,
                                                registry=_registry_fallback())


# ─── Placeholder substitution ─────────────────────────────────────────


def _build_placeholder_map(state: TenderDraftState) -> dict[str, str]:
    """Map of {{var}} → value for TEMPLATE / TEMPLATE+PLACEHOLDERS sections."""
    ep = state.enquiry_particulars
    f = state.financial
    c = state.classification
    g = state.geography
    d = state.dates
    return {
        "name_of_work":           ep.name_of_work,
        "name_of_project":        ep.name_of_project,
        "department_name":        ep.department_name,
        "officer_inviting_bids":  ep.officer_inviting_bids,
        "bid_opening_authority":  ep.bid_opening_authority,
        "address":                ep.address,
        "contact_details":        ep.contact_details,
        "email":                  ep.email,
        "tender_category":        c.tender_category.value,
        "type_of_work":           c.type_of_work,
        "tender_type":            c.tender_type.value,
        "bidding_type":           c.bidding_type.value,
        "form_of_contract":       c.form_of_contract.value,
        "ecv_inr":                f"{f.estimated_contract_value_inr:,}",
        "ecv_words":              f.estimated_contract_value_words,
        "period_months":          str(f.period_of_completion_months),
        "bid_validity_days":      str(f.bid_validity_days),
        "bid_security_percent":   f"{f.bid_security_percent:.2f}",
        "bid_security_inr":       f"{f.bid_security_inr:,}",
        "state":                  g.state,
        "district":               g.district,
        "mandal":                 g.mandal,
        "start_date":             d.start_date,
        "end_date":                d.end_date,
        "closing_date":           d.closing_date,
        "draft_id":               state.draft_id,
        "tender_notice_number":   state.tender_notice_number or "TBD",
    }


_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def substitute_placeholders(text: str, vars_map: dict[str, str]) -> str:
    def repl(m):
        key = m.group(1)
        return vars_map.get(key, m.group(0))     # leave unknown {{vars}} untouched
    return _PLACEHOLDER_RE.sub(repl, text)


# ─── Per-section drafting helpers ─────────────────────────────────────


def _draft_template_section(
    section_id: str,
    state: TenderDraftState,
    query_text: str,
) -> tuple[str, list[dict]]:
    """Retrieve top SBDSection candidate and substitute placeholders."""
    candidates = retrieve_sbd_section(section_id, query_text, top_k=1)
    if not candidates:
        # No corpus row available — emit a stub
        return (
            f"[Section {section_id} corpus row unavailable — placeholder]\n",
            [],
        )
    top = candidates[0]
    content_md = (top["properties"] or {}).get("content_md", "") or top["label"]
    filled = substitute_placeholders(content_md, _build_placeholder_map(state))
    cite = [{
        "node_id":    top["node_id"],
        "node_type":  "SBDSection",
        "section_id": section_id,
        "distance":   top["distance"],
    }]
    return filled, cite


def _draft_boilerplate_section(
    section_id: str,
    state: TenderDraftState,
    query_text: str,
) -> tuple[str, list[dict]]:
    """Drop content_md verbatim with minimal placeholder swap."""
    return _draft_template_section(section_id, state, query_text)


def _draft_project_specific_section(
    section_id: str,
    state: TenderDraftState,
    query_text: str,
    *,
    max_output_tokens: int = 4096,
) -> tuple[str, list[dict], dict]:
    """Vertex AI Pro adaptation — section VI sub-blocks and VIII PCC."""
    candidates = retrieve_sbd_section(section_id, query_text, top_k=3)
    if not candidates:
        return (
            f"[Section {section_id} — corpus retrieval empty; LLM-only generation skipped offline]\n",
            [],
            {},
        )

    # Prompt: pass top-3 SBDSection content as exemplars, ask Pro to compose adaptation
    exemplars = []
    for i, c in enumerate(candidates, 1):
        body = (c["properties"] or {}).get("content_md", "") or c["label"]
        exemplars.append(f"--- EXEMPLAR {i} (cosine_dist={c['distance']:.3f}) ---\n{body[:6000]}\n")

    vars_map = _build_placeholder_map(state)
    project_summary = (
        f"PROJECT: {vars_map['name_of_project']}\n"
        f"WORK SCOPE: {vars_map['name_of_work']}\n"
        f"CATEGORY: {vars_map['tender_category']}, TYPE: {vars_map['tender_type']}\n"
        f"ECV: ₹{vars_map['ecv_inr']} ({vars_map['ecv_words']}), "
        f"PERIOD: {vars_map['period_months']} months\n"
        f"FORM: {vars_map['form_of_contract']}, BID VALIDITY: {vars_map['bid_validity_days']} days\n"
        f"STATE: {vars_map['state']} / {vars_map['district']} / {vars_map['mandal']}\n"
    )

    prompt = (
        f"You are adapting Section {section_id} of an Andhra Pradesh capital-project SBD "
        f"for the project below.\n\n"
        f"{project_summary}\n\n"
        f"Use the {len(exemplars)} exemplars from comparable AP capital tenders below as your reference. "
        f"Produce a Section {section_id} body that:\n"
        f"  - Matches the structure and tone of the exemplars\n"
        f"  - Replaces project-specific particulars with the values above\n"
        f"  - Cites AP-GO numbers / MPW clauses / CVC OMs by name+number where exemplars do\n"
        f"  - Keeps any boilerplate paragraphs verbatim\n\n"
        f"{chr(10).join(exemplars)}\n\n"
        f"Return ONLY the markdown body of Section {section_id}, no commentary."
    )

    try:
        from .vertex_client import gemini_pro
        resp = gemini_pro(prompt, max_output_tokens=max_output_tokens, temperature=0.2)
        text = (resp.get("text") or "").strip() or f"[Section {section_id} — empty Pro response]\n"
        cites = [{
            "node_id":    c["node_id"],
            "node_type":  "SBDSection",
            "section_id": section_id,
            "distance":   c["distance"],
        } for c in candidates]
        usage = {
            "model": "gemini-2.5-pro",
            "prompt_tokens":     resp.get("prompt_tokens"),
            "completion_tokens": resp.get("completion_tokens"),
            "thought_tokens":    resp.get("thought_tokens"),
        }
        return text, cites, usage
    except Exception as e:
        logger.warning(f"  Pro adaptation for Section {section_id} failed: {e}; falling back to verbatim")
        # Fallback to top exemplar with placeholder substitution
        top = candidates[0]
        body = (top["properties"] or {}).get("content_md", "") or top["label"]
        return substitute_placeholders(body, vars_map), [{
            "node_id":    top["node_id"],
            "node_type":  "SBDSection",
            "section_id": section_id,
            "distance":   top["distance"],
        }], {"model": "fallback_verbatim"}


# ─── BoQ drafting helper ──────────────────────────────────────────────


def _discipline_hint_for_state(state: TenderDraftState) -> str:
    """Heuristic mapping from tender_category + work_type to a discipline tag
    used by the BoQ template retriever."""
    cat = state.classification.tender_category.value
    work = (state.classification.type_of_work or "").lower()
    if any(k in work for k in ("mep", "electrical", "hvac", "fire")):
        return "MEP"
    if any(k in work for k in ("road", "bridge", "pavement", "civil", "rcc")):
        return "Civil"
    return cat or "Civil"


def _draft_BoQ_node(
    state: TenderDraftState,
    boq_skeleton: Optional[list] = None,
    *,
    on_row: Optional[Callable] = None,
    on_batch_start: Optional[Callable] = None,
) -> tuple[list, dict]:
    """Run the BoQ generator. If no skeleton supplied, emit empty BoQ + warning."""
    from .boq_generator import (
        BoQSkeletonRow, ProjectContext, generate_boq_specs,
    )

    project_ctx = ProjectContext(
        project_name=state.enquiry_particulars.name_of_project,
        discipline_hint=_discipline_hint_for_state(state),
        tender_category=state.classification.tender_category.value,
        state=state.geography.state,
    )

    if not boq_skeleton:
        return [], {"reason": "no_skeleton_supplied", "n_rows": 0}

    # Map any officer-supplied dicts → BoQSkeletonRow
    rows: list[BoQSkeletonRow] = []
    for r in boq_skeleton:
        if isinstance(r, BoQSkeletonRow):
            rows.append(r)
        elif isinstance(r, dict):
            rows.append(BoQSkeletonRow(
                s_no=int(r.get("s_no") or len(rows) + 1),
                item_name=str(r.get("item_name") or r.get("item") or ""),
                qty=float(r.get("qty") or 1.0),
                unit=str(r.get("unit") or "lump sum"),
                raw_row_hint=str(r.get("raw_row_hint") or ""),
            ))
    if not rows:
        return [], {"reason": "skeleton_empty_after_normalisation", "n_rows": 0}

    enriched: list = []
    batch_count = {"n": 0}

    def _on_batch_start(idx, disc, n):
        batch_count["n"] += 1
        if on_batch_start:
            on_batch_start(idx, disc, n)

    def _on_row(row):
        enriched.append(row)
        if on_row:
            on_row(row)

    # Pipe template retrieval through the pgvector path
    def _retriever(disc, top_k=8):
        return retrieve_tech_templates_pgvector(disc, disc, top_k=top_k)

    for _row in generate_boq_specs(
        rows, project_ctx,
        batch_size=15,                  # 15 rows × ~300 tokens fits 12288 output cap
        template_retriever=_retriever,
        on_batch_start=_on_batch_start,
        on_row_complete=_on_row,
    ):
        pass  # rows are collected via _on_row above

    return enriched, {
        "n_rows":   len(enriched),
        "n_batches": batch_count["n"],
        "discipline_hint": project_ctx.discipline_hint,
    }


# ─── SSE event helpers (the new event types) ──────────────────────────


def _ev_boq_batch_started(batch_idx: int, discipline: str, n_rows: int, node: WorkflowNodeV2) -> dict:
    return {
        "type":       "boq_batch_started",
        "batch_idx":  batch_idx,
        "discipline": discipline,
        "n_rows":     n_rows,
        "node":       node.value,
    }


def _ev_boq_item_complete(row, batch_idx: int, node: WorkflowNodeV2) -> dict:
    return {
        "type":      "boq_item_complete",
        "batch_idx": batch_idx,
        "row":       (row.model_dump() if hasattr(row, "model_dump") else dict(row)),
        "node":      node.value,
    }


def _ev_llm_call(model: str, node: WorkflowNodeV2, usage: dict, elapsed_ms: int) -> dict:
    return {
        "type":   "llm_call",
        "model":  model,
        "node":   node.value,
        "prompt_tokens":     usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "thought_tokens":    usage.get("thought_tokens"),
        "elapsed_ms":        elapsed_ms,
    }


def _chunkify(text: str, chunk_size: int = 160) -> Iterator[str]:
    if not text:
        return
    start, n = 0, len(text)
    while start < n:
        end = min(start + chunk_size, n)
        if end < n:
            space = text.rfind(" ", start, end)
            if space > start:
                end = space + 1
        yield text[start:end]
        start = end


# ─── The 15-node workflow driver ──────────────────────────────────────


def run_workflow_v2(
    state: TenderDraftState,
    *,
    boq_skeleton: Optional[list] = None,
    dry_run: bool = False,
) -> Iterator[dict]:
    """Yield SSE event dicts as the 15-node workflow advances.

    Caller:
      - Persists initial TenderDraft kg_node (gate=AI_GENERATION)
      - Streams these events to client
      - On workflow_complete: bump gate→TECHNICAL, snapshot v2

    boq_skeleton: optional list of skeleton rows. When None, the draft_BoQ
      node short-circuits with a 'no_skeleton_supplied' note instead of
      emitting empty rows — the officer can upload a skeleton later via
      a re-run path (workflow_v2_partial).
    dry_run: if True, skip all Vertex AI calls; emit stub text + empty BoQ.
      Used by R7.6 smoke (cost-aware) and by the unit tests.
    """
    total = len(WORKFLOW_V2_NODES)
    workflow_start = time.time()
    accumulated_citations: dict[str, CitationSource] = {}
    section_texts: dict[str, str] = {}      # section_id → drafted body
    tech_templates_cache: list = []
    boq_telemetry: dict = {}

    for index, node in enumerate(WORKFLOW_V2_NODES, start=1):
        node_start = time.time()
        # node_started uses LangGraphNode (old enum) for back-compat with the
        # SSE schema; downstream just reads `.value`. We pass the v2 enum value
        # as a string-like type via a duck-typed dict.
        yield {
            "type":  "node_started",
            "node":  node.value,
            "index": index,
            "total": total,
        }

        try:
            if node == WorkflowNodeV2.ANALYZE_INPUTS:
                yield _section_started("analysis", node)
                # Validate that the state has enough to draft
                missing = []
                if not state.enquiry_particulars.name_of_work:
                    missing.append("name_of_work")
                if state.financial.estimated_contract_value_inr <= 0:
                    missing.append("estimated_contract_value_inr")
                yield _field_update("_analysis_completeness", {"ok": not missing, "missing": missing}, node)
                yield _section_complete("analysis", node)

            elif node == WorkflowNodeV2.CLASSIFY_TENDER_TYPE:
                yield _section_started("classification", node)
                payload = state.model_dump(mode="json")
                for p in ["classification.tender_category", "classification.tender_type",
                          "classification.form_of_contract"]:
                    yield _field_update(p, _get_path(payload, p), node)
                yield _section_complete("classification", node)

            elif node == WorkflowNodeV2.RETRIEVE_SBD_SECTIONS:
                yield _section_started("retrieval.sbd_sections", node)
                # Don't actually fetch yet — per-section retrieval happens in
                # the draft_section_X nodes. We just announce intent so the
                # frontend can show a progress chip.
                yield _field_update("_sbd_retrieval_planned",
                                    list(_SECTION_KIND.keys()), node)
                yield _section_complete("retrieval.sbd_sections", node)

            elif node == WorkflowNodeV2.RETRIEVE_TECH_TEMPLATES:
                yield _section_started("retrieval.tech_templates", node)
                if dry_run or not boq_skeleton:
                    yield _field_update("_tech_template_retrieval", "skipped (no skeleton)", node)
                else:
                    disc = _discipline_hint_for_state(state)
                    tech_templates_cache = retrieve_tech_templates_pgvector(
                        disc, f"{disc} {state.classification.type_of_work}", top_k=8,
                    )
                    yield _field_update("_tech_template_count", len(tech_templates_cache), node)
                yield _section_complete("retrieval.tech_templates", node)

            elif node == WorkflowNodeV2.RETRIEVE_CLAUSES:
                yield _section_started("retrieval.clauses", node)
                # Static citation seed; the real corpus-clause retrieval lives
                # in Module 2 and is brought in by Section V eligibility text.
                seed = [
                    CitationSource(node_id="rule_ap_go_94_2003", node_type="Rule",
                                   quote_excerpt="contractor class registration per AP-GO Ms No 94/2003"),
                    CitationSource(node_id="rule_cvc_028", node_type="Rule",
                                   quote_excerpt="financial-standing criterion: 2× annual contract value"),
                    CitationSource(node_id="rule_mpw_040", node_type="Rule",
                                   quote_excerpt="3/2/1 similar-works rule for pre-qualification"),
                ]
                for s in seed:
                    accumulated_citations[s.node_id] = s
                yield _field_update("citations.rule_ids", [s.node_id for s in seed], node)
                yield _section_complete("retrieval.clauses", node)

            elif node in (
                WorkflowNodeV2.DRAFT_SECTION_I,
                WorkflowNodeV2.DRAFT_SECTION_II,
                WorkflowNodeV2.DRAFT_SECTION_III,
                WorkflowNodeV2.DRAFT_SECTION_IV,
                WorkflowNodeV2.DRAFT_SECTION_V,
                WorkflowNodeV2.DRAFT_SECTION_VI,
                WorkflowNodeV2.DRAFT_SECTION_VIII,
            ):
                section_id = _section_id_for_node(node)
                kind = _SECTION_KIND.get(section_id, "TEMPLATE")
                section_key = f"section_{section_id}"
                yield _section_started(section_key, node)

                if dry_run:
                    body = f"[dry_run] Section {section_id} ({kind}) — generation skipped.\n"
                    cites_meta: list[dict] = []
                    usage: dict = {}
                else:
                    query_text = _query_for_section(section_id, state)
                    if kind == "PROJECT_SPECIFIC":
                        body, cites_meta, usage = _draft_project_specific_section(
                            section_id, state, query_text, max_output_tokens=4096,
                        )
                        if usage.get("model", "").startswith("gemini"):
                            yield _ev_llm_call(
                                usage["model"], node, usage,
                                int((time.time() - node_start) * 1000),
                            )
                    elif kind == "TEMPLATE":
                        body, cites_meta = _draft_template_section(section_id, state, query_text)
                        usage = {}
                    else:  # BOILERPLATE
                        body, cites_meta = _draft_boilerplate_section(section_id, state, query_text)
                        usage = {}

                section_texts[section_id] = body
                # Stream as chunks so the live view updates progressively
                for chunk in _chunkify(body, chunk_size=200):
                    yield _text_chunk(f"sections.{section_id}", chunk, node)

                # Record SBDSection citations
                for c in cites_meta:
                    cid = c.get("node_id")
                    if cid and cid not in accumulated_citations:
                        accumulated_citations[cid] = CitationSource(
                            node_id=cid,
                            node_type=c.get("node_type", "SBDSection"),
                            quote_excerpt=f"Section {c.get('section_id', section_id)} retrieved (dist={c.get('distance', 0):.3f})",
                        )
                yield _section_complete(section_key, node)

                # Mirror critical section content onto state.general_terms.*
                _mirror_to_general_terms(state, section_id, body)

            elif node == WorkflowNodeV2.DRAFT_BOQ:
                yield _section_started("boq", node)
                if dry_run or not boq_skeleton:
                    if not boq_skeleton:
                        yield _field_update("_boq_telemetry",
                                           {"reason": "no_skeleton_supplied", "n_rows": 0}, node)
                    yield _field_update("boq", [], node)
                    yield _section_complete("boq", node)
                else:
                    # Inline the BoQ batching loop so events yield progressively
                    # (cf. earlier deque-based approach which blocked until all
                    # batches completed — invisible to SSE consumers + smoke
                    # wall-clock guards).
                    from .boq_generator import (
                        BoQSkeletonRow, ProjectContext, classify_discipline,
                        run_batches_parallel,
                    )
                    max_concurrent = int(os.environ.get("M1_BOQ_MAX_CONCURRENT", "10"))
                    project_ctx = ProjectContext(
                        project_name=state.enquiry_particulars.name_of_project,
                        discipline_hint=_discipline_hint_for_state(state),
                        tender_category=state.classification.tender_category.value,
                        state=state.geography.state,
                    )
                    # Normalise skeleton input
                    norm_rows: list[BoQSkeletonRow] = []
                    for r in boq_skeleton:
                        if isinstance(r, BoQSkeletonRow):
                            norm_rows.append(r)
                        elif isinstance(r, dict):
                            norm_rows.append(BoQSkeletonRow(
                                s_no=int(r.get("s_no") or len(norm_rows) + 1),
                                item_name=str(r.get("item_name") or r.get("item") or ""),
                                qty=float(r.get("qty") or 1.0),
                                unit=str(r.get("unit") or "lump sum"),
                                raw_row_hint=str(r.get("raw_row_hint") or ""),
                            ))
                    # Bucket by discipline
                    buckets: dict[str, list[BoQSkeletonRow]] = {}
                    for r in norm_rows:
                        d = classify_discipline(r.item_name, r.raw_row_hint)
                        buckets.setdefault(d, []).append(r)

                    BATCH_SIZE = 15
                    enriched_all = []
                    batch_idx = 0
                    n_batches_total = sum(
                        (len(rows) + BATCH_SIZE - 1) // BATCH_SIZE
                        for rows in buckets.values()
                    )
                    yield _field_update("_boq_batch_plan", {
                        "n_batches":       n_batches_total,
                        "n_rows":          len(norm_rows),
                        "max_concurrent":  max_concurrent,
                        "buckets":         {d: len(r) for d, r in buckets.items()},
                    }, node)

                    # R8.6 — pre-build the (batch_idx, discipline, rows, exemplars) tuples
                    # for the parallel runner. Exemplars are retrieved once per discipline.
                    pending_batches = []
                    exemplars_by_disc: dict[str, list] = {}
                    for discipline, rows in buckets.items():
                        retrieval_disc = discipline if discipline != "Unknown" else "Civil"
                        exemplars_by_disc[discipline] = retrieve_tech_templates_pgvector(
                            retrieval_disc, retrieval_disc, top_k=8,
                        )
                        for start in range(0, len(rows), BATCH_SIZE):
                            batch = rows[start:start + BATCH_SIZE]
                            batch_idx += 1
                            pending_batches.append(
                                (batch_idx, discipline, batch, exemplars_by_disc[discipline])
                            )
                            # Emit the batch_started up-front so the SSE consumer
                            # sees N batches kicked off concurrently.
                            yield _ev_boq_batch_started(batch_idx, discipline, len(batch), node)

                    # R8.6 — drive the runner; results stream as each batch finishes.
                    runner = run_batches_parallel(
                        pending_batches, project_ctx,
                        max_concurrent=max_concurrent,
                    )
                    for (bidx, disc, enriched, usage) in runner:
                        yield _ev_llm_call(
                            usage.get("model", "gemini-2.5-flash"),
                            node, usage, usage.get("elapsed_ms", 0),
                        )
                        for er in enriched:
                            enriched_all.append(er)
                            yield _ev_boq_item_complete(er, bidx, node)
                            yield {
                                "type":  "table_row_added",
                                "table": "boq",
                                "row":   BoQRow(
                                    s_no=er.sno, item=er.short_desc, qty=er.est_qty,
                                    unit=er.uom, rate=(er.rate_inr or None), amount=None,
                                ).model_dump(),
                                "node":  node.value,
                            }

                    # Persist BoQ on state (downgraded to BoQRow shape)
                    state.boq = [
                        BoQRow(
                            s_no=r.sno, item=r.short_desc, qty=r.est_qty,
                            unit=r.uom, rate=(r.rate_inr or None), amount=None,
                        ) for r in enriched_all
                    ]
                    boq_telemetry = {
                        "n_rows":     len(enriched_all),
                        "n_batches":  batch_idx,
                        "buckets":    {d: len(r) for d, r in buckets.items()},
                    }
                    yield _field_update("_boq_telemetry", boq_telemetry, node)
                    yield _section_complete("boq", node)

            elif node == WorkflowNodeV2.ASSEMBLE_DOCUMENT:
                yield _section_started("assembly", node)
                state.citations = Citations(
                    rule_ids=[k for k, v in accumulated_citations.items() if v.node_type == "Rule"],
                    clause_ids=[k for k, v in accumulated_citations.items() if v.node_type == "Clause"],
                    sources=list(accumulated_citations.values()),
                )
                yield _field_update("citations", state.citations.model_dump(), node)
                # Section order for downstream renderers
                ordered = [s for s in ("I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX")
                           if s in section_texts]
                yield _field_update("_section_order", ordered, node)
                yield _section_complete("assembly", node)

            elif node == WorkflowNodeV2.RENDER_ARTIFACTS:
                yield _section_started("render_handoff", node)
                # Actual rendering deferred to publish-time (renderers.py). This
                # node only marks the workflow as ready_for_review.
                yield _field_update("_workflow_status", "ready_for_review", node)
                yield _field_update("_boq_summary", {
                    "n_items":    len(state.boq),
                    "telemetry":  boq_telemetry,
                }, node)
                yield _section_complete("render_handoff", node)

        except Exception as e:
            logger.error(f"  node {node.value} crashed: {e}", exc_info=True)
            yield {
                "type":    "error",
                "node":    node.value,
                "message": f"{type(e).__name__}: {e}",
            }
            # Continue to next node; partial workflow output is better than none

        elapsed_ms = int((time.time() - node_start) * 1000)
        yield {
            "type":       "node_complete",
            "node":       node.value,
            "index":      index,
            "total":      total,
            "elapsed_ms": elapsed_ms,
        }

    state.last_updated_at = now_iso()
    yield {
        "type":              "workflow_complete",
        "draft_id":          state.draft_id,
        "total_elapsed_ms":  int((time.time() - workflow_start) * 1000),
    }


# ─── Event constructors (back-compat shapes) ──────────────────────────


def _section_started(section: str, node: WorkflowNodeV2) -> dict:
    return {"type": "section_started", "section": section, "node": node.value}


def _section_complete(section: str, node: WorkflowNodeV2) -> dict:
    return {"type": "section_complete", "section": section, "node": node.value}


def _field_update(path: str, value: Any, node: WorkflowNodeV2) -> dict:
    return {"type": "field_update", "path": path, "value": value, "node": node.value}


def _text_chunk(path: str, chunk: str, node: WorkflowNodeV2) -> dict:
    return {"type": "text_chunk", "path": path, "chunk": chunk, "node": node.value}


# ─── Lookups ──────────────────────────────────────────────────────────


_NODE_TO_SECTION: dict[WorkflowNodeV2, str] = {
    WorkflowNodeV2.DRAFT_SECTION_I:    "I",
    WorkflowNodeV2.DRAFT_SECTION_II:   "II",
    WorkflowNodeV2.DRAFT_SECTION_III:  "III",
    WorkflowNodeV2.DRAFT_SECTION_IV:   "IV",
    WorkflowNodeV2.DRAFT_SECTION_V:    "V",
    WorkflowNodeV2.DRAFT_SECTION_VI:   "VI",
    WorkflowNodeV2.DRAFT_SECTION_VIII: "VIII",
}


def _section_id_for_node(node: WorkflowNodeV2) -> str:
    return _NODE_TO_SECTION[node]


def _query_for_section(section_id: str, state: TenderDraftState) -> str:
    """Build retrieval query text for the given section."""
    ep = state.enquiry_particulars
    c = state.classification
    common = f"{ep.name_of_work} {c.tender_category.value} {c.type_of_work}"
    if section_id == "I":
        return f"Invitation for Bids NIT notice for {common}"
    if section_id == "II":
        return f"Instructions to Bidders eligibility submission {common}"
    if section_id == "III":
        return f"Evaluation and Qualification Criteria {common}"
    if section_id == "IV":
        return f"Bidding Forms templates {common}"
    if section_id == "V":
        return f"Eligible Countries source of funds {common}"
    if section_id == "VI":
        return f"Works Requirements technical specifications scope {common}"
    if section_id == "VII":
        return f"General Conditions of Contract GCC {common}"
    if section_id == "VIII":
        return f"Particular Conditions of Contract PCC overrides {common}"
    if section_id == "IX":
        return f"Annexures contract forms {common}"
    return common


def _get_path(d: dict, path: str):
    cur = d
    for p in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(p)
        else:
            return None
        if cur is None:
            return None
    return cur


def _mirror_to_general_terms(state: TenderDraftState, section_id: str, body: str) -> None:
    """Stash drafted section body onto state.general_terms.* slots used by v1
    consumers and the existing DOCX renderer. Idempotent."""
    if section_id == "II":
        # ITB ≈ bid_procedure for the existing v1 renderer
        state.general_terms.bid_procedure = body
    elif section_id == "III":
        state.general_terms.eligibility = body  # evaluation overlaps with eligibility
    elif section_id == "V":
        # Override with the more specific eligibility once Section V is drafted
        state.general_terms.eligibility = body
    elif section_id == "VI":
        state.general_terms.technical = body
    elif section_id == "VIII":
        state.general_terms.legal = body  # PCC legal/contractual variations


# ─── Smoke test ───────────────────────────────────────────────────────


if __name__ == "__main__":  # pragma: no cover
    from .schemas import (
        EnquiryParticulars, Classification, Financial, Geography,
        Evaluation, TenderDates, TenderCategory, TenderType, BiddingType,
        FormOfContract, EvaluationType, EvaluationCriteria, DisplayRank,
        ConsortiumJV, GateName,
    )
    ts = now_iso()
    smoke_state = TenderDraftState(
        draft_id="smoke_r75",
        enquiry_particulars=EnquiryParticulars(
            department_name="APCRDA",
            circle_division="Amaravati",
            officer_inviting_bids="EE/APCRDA",
            bid_opening_authority="CE/APCRDA",
            address="Amaravati AP",
            contact_details="0000000000",
            email="eo@apcrda.gov.in",
            name_of_project="LPS Zone-11 Test",
            name_of_work="Civil works for sewerage network",
        ),
        classification=Classification(
            tender_category=TenderCategory.WORKS,
            type_of_work="Civil Works",
            tender_type=TenderType.OPEN_NCB,
            bidding_type=BiddingType.OPEN,
            form_of_contract=FormOfContract.ITEM_RATE,
            consortium_joint_venture=ConsortiumJV.NOT_APPLICABLE,
        ),
        financial=Financial(
            estimated_contract_value_inr=500_000_000,
            estimated_contract_value_words="Rupees Fifty Crore Only",
            period_of_completion_months=18,
            bid_security_inr=5_000_000,
            bid_security_in_favour_of="EO/APCRDA",
            mode_of_payment="DD/BG",
        ),
        geography=Geography(state="Andhra Pradesh", district="Krishna",
                           mandal="Vijayawada Rural", assembly="—", parliament="—"),
        evaluation=Evaluation(
            evaluation_type=EvaluationType.ITEM_RATE,
            evaluation_criteria=EvaluationCriteria.BASED_ON_PRICE,
            display_rank=DisplayRank.LOWEST,
        ),
        dates=TenderDates(start_date=ts, end_date=ts, closing_date=ts),
        current_gate=GateName.AI_GENERATION,
        created_by="DEALING_OFFICER:smoke",
        created_at=ts, last_updated_at=ts,
    )

    print("=== workflow_v2 dry-run smoke ===")
    for ev in run_workflow_v2(smoke_state, dry_run=True):
        t = ev.get("type")
        if t in ("node_started", "section_complete", "node_complete", "workflow_complete"):
            print(f"  {t:20s}  {ev.get('node', '')}  {ev.get('section', '')}")
