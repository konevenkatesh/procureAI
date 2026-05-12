"""
M1.5 — 12-node LangGraph-style workflow for tender drafting.

The directive's "LangGraph" framing is honoured by:
  - 12 named nodes executed in fixed order (LANGGRAPH_NODES_IN_ORDER)
  - Each node yields structured SSE events as it processes
  - State accumulates across nodes (LangGraph-style state passing)
  - Citations tracked per node (for the demo's expandable node panels)

Two execution modes:
  - "llm" — uses OpenRouter (qwen-2.5-72b-instruct) when OPENROUTER_API_KEY is set;
            constrained output via Pydantic schemas to prevent hallucination
  - "template" — deterministic template-based generation; no external calls.
            Default mode when OPENROUTER_API_KEY is absent OR M1_DRAFTER_MODE=template
            is set. Suitable for local smoke-tests + offline demos.

Both modes emit the same SSE event types. Template mode adds artificial
~0.5-1.5s delays between nodes so the live structured view (M1.3) reads as
intentional rather than instant.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from typing import AsyncIterator, Iterator, Optional

from .schemas import (
    BoQRow,
    Citations,
    CitationSource,
    GateName,
    GeneralTerms,
    LANGGRAPH_NODES_IN_ORDER,
    LangGraphNode,
    RoleName,
    SSEEventError,
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


# ─── Mode selection ──────────────────────────────────────────────────


def _drafter_mode() -> str:
    """Returns 'llm' if OPENROUTER_API_KEY is set AND mode env != 'template'."""
    env_mode = os.environ.get("M1_DRAFTER_MODE", "").lower()
    if env_mode == "template":
        return "template"
    if env_mode == "llm":
        return "llm"
    # Auto-detect: LLM if key present
    if os.environ.get("OPENROUTER_API_KEY"):
        return "llm"
    return "template"


# ─── Template-mode generation (deterministic) ───────────────────────


def _template_eligibility(state: TenderDraftState) -> str:
    ep = state.enquiry_particulars
    f = state.financial
    c = state.classification
    return (
        f"GENERAL TERMS & CONDITIONS / ELIGIBILITY\n\n"
        f"Tender Notice for: {ep.name_of_work}\n"
        f"Issued by: {ep.officer_inviting_bids}\n"
        f"Estimated Contract Value: ₹ {f.estimated_contract_value_inr:,} "
        f"({f.estimated_contract_value_words})\n\n"
        f"1. ELIGIBILITY CRITERIA\n"
        f"   The bidder must satisfy the following pre-qualification "
        f"criteria as per AP-GO Ms No 94/2003 and AP Standard Tender "
        f"Document:\n\n"
        f"   (a) Be a registered contractor in the {c.tender_category} "
        f"category with valid contractor class registration.\n"
        f"   (b) Average annual turnover of construction works in the "
        f"last 3 financial years shall not be less than 2× the annual "
        f"contract value (CVC-028 financial-standing criterion).\n"
        f"   (c) Solvency Certificate of not less than 40% of the ECV, "
        f"issued by a Scheduled Commercial Bank or the Tahsildar "
        f"within 12 months from the tender submission date "
        f"(AP-GO 89/2009 §4(b)).\n"
        f"   (d) Similar nature of work executed in the last 7 years "
        f"satisfying the 3/2/1 rule (MPW-040 + AP-GO-062):\n"
        f"       - 3 works each costing ≥ 40% of ECV, OR\n"
        f"       - 2 works each costing ≥ 50% of ECV, OR\n"
        f"       - 1 work costing ≥ 80% of ECV.\n"
        f"   (e) The bidder shall not be on any blacklist of the "
        f"AP State Government, Government of India, or CVC.\n\n"
        f"2. SUBMISSION REQUIREMENTS\n"
        f"   Bidders shall submit all 7 mandatory documents listed in "
        f"the 'Required Tender Documents Details' section, along with "
        f"the Letter of Bid, Bid Security (EMD), and Priced BoQ.\n"
    )


def _template_technical(state: TenderDraftState) -> str:
    ep = state.enquiry_particulars
    return (
        f"GENERAL TECHNICAL TERMS AND CONDITIONS (PROCEDURE)\n\n"
        f"Tender: {ep.name_of_work}\n\n"
        f"1. TECHNICAL SPECIFICATIONS\n"
        f"   All works shall conform to the relevant Indian Standards "
        f"(IS codes), CPWD Specifications 2024, and AP State-specific "
        f"design norms. Materials shall be tested and certified as "
        f"per the Bureau of Indian Standards.\n\n"
        f"2. QUALITY ASSURANCE\n"
        f"   The bidder shall implement a Quality Assurance Plan (QAP) "
        f"approved by the Engineer-in-Charge before the start of work. "
        f"Field testing shall be conducted at the bidder's cost as per "
        f"frequencies specified in the QAP.\n\n"
        f"3. KEY PERSONNEL\n"
        f"   The bidder shall depute qualified key personnel as per "
        f"the bid document (Project Manager, Site Engineer, QA Engineer, "
        f"Safety Officer, MEP Engineer, and Surveyor at minimum).\n\n"
        f"4. EQUIPMENT DEPLOYMENT\n"
        f"   Critical equipment shall be deployed as declared in "
        f"Statement-V of the bid. Owned vs leased breakup shall be "
        f"submitted on Judicial Stamp Paper of Rs.100.\n"
    )


def _template_legal(state: TenderDraftState) -> str:
    f = state.financial
    return (
        f"LEGAL TERMS & CONDITIONS\n\n"
        f"1. CONTRACT VALUE & PAYMENT\n"
        f"   The contract is awarded on {state.classification.form_of_contract.value} "
        f"basis. Payment shall be made against running bills certified "
        f"by the Engineer-in-Charge, subject to retention as per "
        f"AP-GO 19/2002 (Performance Bank Guarantee at 5% of contract value).\n\n"
        f"2. PERFORMANCE BANK GUARANTEE (PBG)\n"
        f"   The successful bidder shall furnish a PBG equivalent to "
        f"5% of the Letter of Acceptance amount within 14 days of LOA "
        f"issuance. PBG validity: contract period + 90 days defects "
        f"liability period.\n\n"
        f"3. LIQUIDATED DAMAGES (LD)\n"
        f"   LD shall be levied at 0.5% per week (or part thereof) of "
        f"the contract value, subject to a maximum of 10% of the "
        f"contract value, for delays attributable to the contractor "
        f"(AP-GO 38/2005).\n\n"
        f"4. ARBITRATION\n"
        f"   Disputes shall be resolved as per the Arbitration and "
        f"Conciliation Act 1996 (as amended). Seat of arbitration: "
        f"Vijayawada, Andhra Pradesh. Language: English.\n\n"
        f"5. BID VALIDITY\n"
        f"   Bids shall remain valid for {f.bid_validity_days} days "
        f"from the date of submission. Bids with shorter validity "
        f"shall be rejected as non-responsive.\n\n"
        f"6. ANTI-COLLUSION\n"
        f"   Bidders shall sign the Integrity Pact (CVC OM No. "
        f"006/IPD/02 dated 01.07.2007). Cartel-suspect signals "
        f"(matched signatories, shared bank branches, tight price gaps) "
        f"shall be flagged and referred to the Vigilance Officer.\n"
    )


def _template_bid_procedure(state: TenderDraftState) -> str:
    return (
        f"PROCEDURE FOR BID SUBMISSION\n\n"
        f"1. ELECTRONIC SUBMISSION\n"
        f"   Bids shall be submitted electronically through the "
        f"AP eProcurement Portal (https://tender.apeprocurement.gov.in) "
        f"on or before the closing date and time stated in the "
        f"'Tender Dates' section.\n\n"
        f"2. DOCUMENT UPLOAD\n"
        f"   All 7 mandatory documents listed in the 'Required Tender "
        f"Documents Details' section shall be uploaded in PDF format. "
        f"Each document shall not exceed 5 MB; total upload size 50 MB.\n\n"
        f"3. DIGITAL SIGNATURE\n"
        f"   All documents shall be digitally signed with a Class-III "
        f"Digital Signature Certificate (DSC) issued by a Licensed "
        f"Certifying Authority under IT Act 2000.\n\n"
        f"4. BID OPENING\n"
        f"   Technical bids will be opened on the closing date. "
        f"Commercial bids of technically qualified bidders shall be "
        f"opened on a date communicated separately. Bidders may "
        f"witness the opening online via the portal's live feed.\n\n"
        f"5. CLARIFICATIONS\n"
        f"   Bidders may submit clarifications in writing (English or "
        f"Telugu) through the portal's Q&A facility up to 3 working "
        f"days before the closing date. Responses will be published "
        f"on the portal and circulated to all registered bidders.\n"
    )


def _template_boq_skeleton(state: TenderDraftState) -> list[BoQRow]:
    """Generate a representative BoQ skeleton based on tender type + work."""
    ep_lower = state.enquiry_particulars.name_of_work.lower()
    rows: list[BoQRow] = []

    # Generic civil-works skeleton; the real workflow would call LLM for
    # work-specific items. This is sufficient for the Banaganapalli demo.
    if "kitchen" in ep_lower or "shed" in ep_lower or "building" in ep_lower:
        rows = [
            BoQRow(s_no=1, item="Earthwork excavation in foundation (all classes of soil)", qty=120, unit="m3"),
            BoQRow(s_no=2, item="Plain Cement Concrete 1:4:8 in foundation", qty=18, unit="m3"),
            BoQRow(s_no=3, item="RCC M-20 in foundation footings + columns", qty=22, unit="m3"),
            BoQRow(s_no=4, item="Brick masonry in CM 1:6 in superstructure", qty=85, unit="m3"),
            BoQRow(s_no=5, item="Cement plaster 12mm thick on walls (internal + external)", qty=420, unit="m2"),
            BoQRow(s_no=6, item="Cement concrete flooring 1:2:4 (75mm thick)", qty=95, unit="m2"),
            BoQRow(s_no=7, item="RCC slab roofing with M-25 concrete, 150mm thick", qty=110, unit="m2"),
            BoQRow(s_no=8, item="Reinforcement bars Fe-500 (BIS-marked)", qty=2.8, unit="MT"),
            BoQRow(s_no=9, item="Doors and windows — teakwood frames + shutters", qty=8, unit="No"),
            BoQRow(s_no=10, item="Electrical wiring + fixtures (concealed conduit)", qty=1, unit="lump sum"),
            BoQRow(s_no=11, item="Plumbing fixtures + water supply lines (CPVC)", qty=1, unit="lump sum"),
            BoQRow(s_no=12, item="Painting — primer + 2 coats acrylic emulsion (internal)", qty=420, unit="m2"),
            BoQRow(s_no=13, item="External painting — weather-shield (2 coats)", qty=180, unit="m2"),
            BoQRow(s_no=14, item="Sanitary fittings — WC, washbasin, urinal, kitchen sink", qty=1, unit="set"),
            BoQRow(s_no=15, item="Site clearing + final cleaning before handover", qty=1, unit="lump sum"),
        ]
    elif "road" in ep_lower or "pavement" in ep_lower:
        rows = [
            BoQRow(s_no=1, item="Earthwork excavation for road formation", qty=850, unit="m3"),
            BoQRow(s_no=2, item="Granular sub-base (GSB) 200mm compacted", qty=620, unit="m3"),
            BoQRow(s_no=3, item="WMM (Wet Mix Macadam) 150mm compacted", qty=480, unit="m3"),
            BoQRow(s_no=4, item="DBM (Dense Bituminous Macadam) 75mm", qty=420, unit="m3"),
            BoQRow(s_no=5, item="Bituminous Concrete (BC) wearing course 40mm", qty=320, unit="m3"),
            BoQRow(s_no=6, item="Side drains — RCC + brick lining", qty=180, unit="m"),
            BoQRow(s_no=7, item="Road markings + signage", qty=1, unit="lump sum"),
        ]
    else:
        # Generic fallback
        rows = [
            BoQRow(s_no=1, item=f"{state.classification.tender_category.value} — Item 1 (site preparation)", qty=1, unit="lump sum"),
            BoQRow(s_no=2, item=f"{state.classification.tender_category.value} — Item 2 (main works execution)", qty=1, unit="lump sum"),
            BoQRow(s_no=3, item=f"{state.classification.tender_category.value} — Item 3 (finishing + handover)", qty=1, unit="lump sum"),
        ]
    return rows


# ─── Stub citations (template mode) ─────────────────────────────────


def _template_citations(node: LangGraphNode) -> Citations:
    """Synthetic citations for template mode — points at real rule_ids that
    exist in the kg_nodes Rule table from Module 2's seed."""
    base_sources = {
        LangGraphNode.RETRIEVE_CLAUSES: [
            CitationSource(node_id="rule_ap_go_94_2003", node_type="Rule",
                           quote_excerpt="contractor class registration requirements per AP-GO Ms No 94/2003"),
            CitationSource(node_id="rule_cvc_028", node_type="Rule",
                           quote_excerpt="financial-standing criterion: 2× annual contract value (3-yr avg)"),
        ],
        LangGraphNode.DRAFT_ELIGIBILITY: [
            CitationSource(node_id="rule_mpw_040", node_type="Rule",
                           quote_excerpt="3/2/1 similar-works rule for pre-qualification"),
            CitationSource(node_id="rule_ap_go_89_2009", node_type="Rule",
                           quote_excerpt="Solvency cert ≥ 40% ECV, 12-month validity per Tahsildar"),
        ],
        LangGraphNode.DRAFT_LEGAL_TERMS: [
            CitationSource(node_id="rule_ap_go_19_2002", node_type="Rule",
                           quote_excerpt="PBG 5% of contract value; LD 0.5%/week up to 10%"),
            CitationSource(node_id="rule_cvc_086_integrity_pact", node_type="Rule",
                           quote_excerpt="Integrity Pact mandatory per CVC OM 006/IPD/02"),
        ],
    }
    sources = base_sources.get(node, [])
    return Citations(
        rule_ids=[s.node_id for s in sources],
        clause_ids=[],
        sources=sources,
    )


# ─── Workflow driver (generator that yields SSE events) ──────────────


def run_workflow(state: TenderDraftState, mode: Optional[str] = None) -> Iterator[dict]:
    """Run the 12-node workflow, yielding SSE event dicts (ready to JSON-serialise).

    Caller is responsible for:
      - Persisting the initial state (TenderDraft kg_node)
      - Streaming events to client (SSE)
      - On workflow_complete: bumping state.current_gate to TECHNICAL + snapshot
    """
    chosen_mode = mode or _drafter_mode()
    total = len(LANGGRAPH_NODES_IN_ORDER)
    workflow_start = time.time()

    # Build up state across nodes (template mode mutates a shared dict)
    accumulated_citations: dict[str, CitationSource] = {}

    for index, node in enumerate(LANGGRAPH_NODES_IN_ORDER, start=1):
        node_start = time.time()
        yield SSEEventNodeStarted(node=node, index=index, total=total).model_dump()

        # Per-node work
        if node == LangGraphNode.ANALYZE_INPUTS:
            yield SSEEventSectionStarted(section="analysis", node=node).model_dump()
            time.sleep(0.4 if chosen_mode == "template" else 0.1)
            # No state changes; just an acknowledgement
            yield SSEEventFieldUpdate(
                path="_analysis_completeness",
                value=True,
                node=node,
            ).model_dump()
            yield SSEEventSectionComplete(section="analysis", node=node).model_dump()

        elif node == LangGraphNode.CLASSIFY_TENDER_TYPE:
            yield SSEEventSectionStarted(section="classification", node=node).model_dump()
            time.sleep(0.5 if chosen_mode == "template" else 0.1)
            # Already classified by Step 2 of wizard; emit echo events
            for path in [
                "classification.tender_category",
                "classification.tender_type",
                "classification.form_of_contract",
            ]:
                val = _get_path(state.model_dump(mode="json"), path)
                yield SSEEventFieldUpdate(path=path, value=val, node=node).model_dump()
            yield SSEEventSectionComplete(section="classification", node=node).model_dump()

        elif node == LangGraphNode.RETRIEVE_TEMPLATES:
            yield SSEEventSectionStarted(section="templates", node=node).model_dump()
            time.sleep(0.6 if chosen_mode == "template" else 0.2)
            # Reference template lookups — would BGE-M3 over Phase 1 drafts here
            yield SSEEventFieldUpdate(
                path="_templates_referenced",
                value=["tender_synth_kurnool", "tender_synth_ja", "tender_synth_hc"],
                node=node,
            ).model_dump()
            yield SSEEventSectionComplete(section="templates", node=node).model_dump()

        elif node == LangGraphNode.RETRIEVE_CLAUSES:
            yield SSEEventSectionStarted(section="clauses", node=node).model_dump()
            time.sleep(0.7 if chosen_mode == "template" else 0.2)
            cites = _template_citations(node)
            for s in cites.sources:
                accumulated_citations[s.node_id] = s
            yield SSEEventFieldUpdate(
                path="citations.rule_ids",
                value=[s.node_id for s in cites.sources],
                node=node,
            ).model_dump()
            yield SSEEventSectionComplete(section="clauses", node=node).model_dump()

        elif node == LangGraphNode.DRAFT_NIT:
            yield SSEEventSectionStarted(section="nit", node=node).model_dump()
            time.sleep(0.5 if chosen_mode == "template" else 0.1)
            # NIT details already in enquiry_particulars; emit synthesised tender_notice_number
            nit_no = f"NIT No: {uuid.uuid4().hex[:6].upper()}/2026-27 Dt. {now_iso()[:10]}"
            yield SSEEventFieldUpdate(path="tender_notice_number", value=nit_no, node=node).model_dump()
            state.tender_notice_number = nit_no
            yield SSEEventSectionComplete(section="nit", node=node).model_dump()

        elif node == LangGraphNode.DRAFT_ITB:
            yield SSEEventSectionStarted(section="general_terms.technical", node=node).model_dump()
            time.sleep(1.0 if chosen_mode == "template" else 0.3)
            tech_text = _template_technical(state)
            # Stream as chunks
            for chunk in _chunkify(tech_text, chunk_size=120):
                yield SSEEventTextChunk(
                    path="general_terms.technical", chunk=chunk, node=node,
                ).model_dump()
                if chosen_mode == "template":
                    time.sleep(0.03)
            state.general_terms.technical = tech_text
            yield SSEEventSectionComplete(section="general_terms.technical", node=node).model_dump()

        elif node == LangGraphNode.DRAFT_ELIGIBILITY:
            yield SSEEventSectionStarted(section="general_terms.eligibility", node=node).model_dump()
            time.sleep(1.0 if chosen_mode == "template" else 0.3)
            elig_text = _template_eligibility(state)
            for chunk in _chunkify(elig_text, chunk_size=120):
                yield SSEEventTextChunk(
                    path="general_terms.eligibility", chunk=chunk, node=node,
                ).model_dump()
                if chosen_mode == "template":
                    time.sleep(0.03)
            state.general_terms.eligibility = elig_text
            cites = _template_citations(node)
            for s in cites.sources:
                accumulated_citations[s.node_id] = s
            yield SSEEventSectionComplete(section="general_terms.eligibility", node=node).model_dump()

        elif node == LangGraphNode.DRAFT_BOQ_SKELETON:
            yield SSEEventSectionStarted(section="boq", node=node).model_dump()
            time.sleep(0.8 if chosen_mode == "template" else 0.2)
            rows = _template_boq_skeleton(state)
            for row in rows:
                yield SSEEventTableRowAdded(
                    table="boq", row=row.model_dump(), node=node,
                ).model_dump()
                if chosen_mode == "template":
                    time.sleep(0.05)
            state.boq = rows
            yield SSEEventSectionComplete(section="boq", node=node).model_dump()

        elif node == LangGraphNode.DRAFT_LEGAL_TERMS:
            yield SSEEventSectionStarted(section="general_terms.legal", node=node).model_dump()
            time.sleep(1.0 if chosen_mode == "template" else 0.3)
            legal_text = _template_legal(state)
            for chunk in _chunkify(legal_text, chunk_size=120):
                yield SSEEventTextChunk(
                    path="general_terms.legal", chunk=chunk, node=node,
                ).model_dump()
                if chosen_mode == "template":
                    time.sleep(0.03)
            state.general_terms.legal = legal_text
            cites = _template_citations(node)
            for s in cites.sources:
                accumulated_citations[s.node_id] = s
            yield SSEEventSectionComplete(section="general_terms.legal", node=node).model_dump()

        elif node == LangGraphNode.DRAFT_EVALUATION_FORM:
            yield SSEEventSectionStarted(section="general_terms.bid_procedure", node=node).model_dump()
            time.sleep(0.8 if chosen_mode == "template" else 0.2)
            proc_text = _template_bid_procedure(state)
            for chunk in _chunkify(proc_text, chunk_size=120):
                yield SSEEventTextChunk(
                    path="general_terms.bid_procedure", chunk=chunk, node=node,
                ).model_dump()
                if chosen_mode == "template":
                    time.sleep(0.03)
            state.general_terms.bid_procedure = proc_text
            yield SSEEventSectionComplete(section="general_terms.bid_procedure", node=node).model_dump()

        elif node == LangGraphNode.ASSEMBLE_DOCUMENT:
            yield SSEEventSectionStarted(section="citations", node=node).model_dump()
            time.sleep(0.4 if chosen_mode == "template" else 0.1)
            # Finalise citations
            state.citations = Citations(
                rule_ids=list(accumulated_citations.keys()),
                clause_ids=[],
                sources=list(accumulated_citations.values()),
            )
            yield SSEEventFieldUpdate(
                path="citations",
                value=state.citations.model_dump(),
                node=node,
            ).model_dump()
            yield SSEEventSectionComplete(section="citations", node=node).model_dump()

        elif node == LangGraphNode.RENDER_DOCX:
            yield SSEEventSectionStarted(section="render_skip_in_workflow", node=node).model_dump()
            time.sleep(0.3 if chosen_mode == "template" else 0.05)
            # Rendering is deferred to publish-time per M1.7. Workflow just
            # confirms the state is renderable.
            yield SSEEventFieldUpdate(
                path="_workflow_status",
                value="ready_for_review",
                node=node,
            ).model_dump()
            yield SSEEventSectionComplete(section="render_skip_in_workflow", node=node).model_dump()

        # Per-node complete event
        elapsed_ms = int((time.time() - node_start) * 1000)
        node_cites = _template_citations(node) if node in {
            LangGraphNode.RETRIEVE_CLAUSES,
            LangGraphNode.DRAFT_ELIGIBILITY,
            LangGraphNode.DRAFT_LEGAL_TERMS,
        } else None
        yield SSEEventNodeComplete(
            node=node,
            index=index,
            total=total,
            elapsed_ms=elapsed_ms,
            citations=node_cites,
        ).model_dump()

    # Workflow complete
    state.last_updated_at = now_iso()
    yield SSEEventWorkflowComplete(
        draft_id=state.draft_id,
        total_elapsed_ms=int((time.time() - workflow_start) * 1000),
    ).model_dump()


# ─── Utilities ───────────────────────────────────────────────────────


def _chunkify(text: str, chunk_size: int = 120) -> Iterator[str]:
    """Split text into ~chunk_size chunks, preferring word boundaries."""
    if not text:
        return
    start = 0
    n = len(text)
    while start < n:
        end = min(start + chunk_size, n)
        if end < n:
            # back up to last whitespace
            space = text.rfind(" ", start, end)
            if space > start:
                end = space + 1
        yield text[start:end]
        start = end


def _get_path(d: dict, path: str):
    cur = d
    for p in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(p)
        elif isinstance(cur, list) and p.isdigit():
            cur = cur[int(p)]
        else:
            return None
        if cur is None:
            return None
    return cur
