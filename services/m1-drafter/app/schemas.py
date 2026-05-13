"""
Module 1 — Pydantic schemas mirroring frontend/types/m1-drafter.ts.

Anchors the contract between:
  - 7-step initiation form payload (frontend → POST /m1/run)
  - LangGraph workflow node I/O
  - Gate state machine transitions
  - Cloud Run worker → Supabase kg_node persistence
  - SSE event stream (server-emitted)

Schema mirrors AP eGP "Tender Details" page (Banaganapalli sample).
Keep in sync with frontend/types/m1-drafter.ts.
"""
from __future__ import annotations

import datetime as _dt
from enum import Enum
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ─── Enums (StrEnum so JSON serialises to the bare string) ───────────


class TenderCategory(str, Enum):
    WORKS = "WORKS"
    GOODS = "GOODS"
    SERVICES = "SERVICES"


class TenderType(str, Enum):
    OPEN_NCB = "OPEN - NCB"
    OPEN_ICB = "OPEN - ICB"
    LIMITED = "LIMITED"
    SINGLE_TENDER = "SINGLE_TENDER"
    EOI = "EOI"


class BiddingType(str, Enum):
    OPEN = "OPEN"
    LIMITED = "LIMITED"
    EOI = "EOI"
    SINGLE_SOURCE = "SINGLE_SOURCE"


class FormOfContract(str, Enum):
    LS = "L.S"
    ITEM_RATE = "Item Rate"
    PERCENTAGE = "Percentage"
    EPC = "EPC"
    COST_PLUS = "Cost Plus"


class EvaluationType(str, Enum):
    PERCENTAGE = "Percentage"
    ITEM_RATE = "Item Rate"
    LS = "L.S"
    COMPOSITE = "Composite"


class EvaluationCriteria(str, Enum):
    BASED_ON_PRICE = "Based on Price"
    BASED_ON_QCBS = "Based on QCBS"
    TWO_ENVELOPE = "Two-Envelope"


class DisplayRank(str, Enum):
    LOWEST = "Lowest"
    HIGHEST = "Highest"


class CurrencyType(str, Enum):
    INR = "INR"
    USD = "USD"
    EUR = "EUR"


class ConsortiumJV(str, Enum):
    APPLICABLE = "Applicable"
    NOT_APPLICABLE = "Not Applicable"


class DocumentStage(str, Enum):
    COMMON = "COMMON"
    TECHNICAL = "TECHNICAL"
    FINANCIAL = "FINANCIAL"


class DocumentType(str, Enum):
    MANDATORY = "Mandatory"
    OPTIONAL = "Optional"


class FormStage(str, Enum):
    TECHNICAL_STAGE = "Technical Stage"
    COMMERCIAL_STAGE = "Commercial Stage"
    BOTH = "Both"


class FormType(str, Enum):
    SECURE = "Secure"
    STANDARD = "Standard"


class YesNo(str, Enum):
    YES = "Yes"
    NO = "No"


# ─── Gate state machine ──────────────────────────────────────────────


class GateName(str, Enum):
    INITIATION = "INITIATION"
    AI_GENERATION = "AI_GENERATION"
    TECHNICAL = "TECHNICAL"
    FINANCIAL = "FINANCIAL"
    PROCUREMENT = "PROCUREMENT"
    AUTHORITY = "AUTHORITY"
    PUBLISHED = "PUBLISHED"


class RoleName(str, Enum):
    DEALING_OFFICER = "DEALING_OFFICER"
    SENIOR_ENGINEER = "SENIOR_ENGINEER"
    DEPARTMENT_HEAD = "DEPARTMENT_HEAD"
    PROCUREMENT_OFFICER = "PROCUREMENT_OFFICER"
    TENDER_INVITING_AUTHORITY = "TENDER_INVITING_AUTHORITY"


class GateAction(str, Enum):
    APPROVE = "APPROVE"
    REVISE = "REVISE"
    PUBLISH = "PUBLISH"
    SENDBACK = "SENDBACK"
    EDIT = "EDIT"


# Mapping locked per directive M1.0 design decision.
GATE_REVIEWER_ROLE: dict[GateName, Optional[RoleName]] = {
    GateName.INITIATION:    RoleName.DEALING_OFFICER,
    GateName.AI_GENERATION: None,
    GateName.TECHNICAL:     RoleName.SENIOR_ENGINEER,
    GateName.FINANCIAL:     RoleName.DEPARTMENT_HEAD,
    GateName.PROCUREMENT:   RoleName.PROCUREMENT_OFFICER,
    GateName.AUTHORITY:     RoleName.TENDER_INVITING_AUTHORITY,
    GateName.PUBLISHED:     None,
}


# Editable scope per gate. "*" = all fields editable; [] = read-only.
# Dot-path notation into TenderDraftState.
GATE_EDIT_SCOPE: dict[GateName, list[str]] = {
    GateName.INITIATION: ["*"],
    GateName.AI_GENERATION: [],
    GateName.TECHNICAL: [
        "boq",
        "general_terms.technical",
        "general_terms.eligibility",
        "enquiry_particulars.name_of_work",
        "financial.period_of_completion_months",
        "documents",
    ],
    GateName.FINANCIAL: [
        "financial.estimated_contract_value_inr",
        "financial.estimated_contract_value_words",
        "financial.bid_security_percent",
        "financial.bid_security_inr",
        "financial.transaction_fee_inr",
        "financial.bid_validity_days",
        "classification.form_of_contract",
    ],
    GateName.PROCUREMENT: [
        "evaluation.evaluation_type",
        "evaluation.evaluation_criteria",
        "evaluation.display_rank",
        "classification.bid_call_numbers",
        "enquiry_forms",
    ],
    GateName.AUTHORITY: [],
    GateName.PUBLISHED: [],
}


# ─── Sub-schemas ─────────────────────────────────────────────────────


class EnquiryParticulars(BaseModel):
    model_config = ConfigDict(extra="forbid")
    department_name: str
    circle_division: str
    officer_inviting_bids: str
    bid_opening_authority: str
    address: str
    contact_details: str
    email: str
    name_of_project: str
    name_of_work: str


class Classification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tender_category: TenderCategory
    type_of_work: str
    tender_type: TenderType
    bidding_type: BiddingType
    form_of_contract: FormOfContract
    consortium_joint_venture: ConsortiumJV
    bid_call_numbers: int = 1


class Financial(BaseModel):
    model_config = ConfigDict(extra="forbid")
    estimated_contract_value_inr: int = Field(..., ge=1)
    estimated_contract_value_words: str
    period_of_completion_months: int = Field(..., ge=1)
    bid_validity_days: int = Field(default=90, ge=30, le=365)
    bid_security_percent: float = Field(default=1.0, ge=0.0, le=10.0)
    bid_security_inr: int = Field(..., ge=0)
    bid_security_in_favour_of: str
    mode_of_payment: str
    currency_type: CurrencyType = CurrencyType.INR
    default_currency: str = "Indian Rupee - INR"
    transaction_fee_inr: int = Field(default=566, ge=0)
    transaction_fee_payable_to: str = "APTS payable at Vijayawada"
    transaction_fee_go_reference: str = "G.O.Ms No 4, Dtd 17.02.2015 IT&C Dept"

    @field_validator("bid_security_inr")
    @classmethod
    def _bid_security_consistent(cls, v, info):
        """Sanity check: bid_security_inr should match ECV × bid_security_percent / 100
        within ₹100 rounding tolerance."""
        # Cross-field validation deferred; soft-check only since gate FINANCIAL
        # may legitimately tweak this. Hard validation lives in
        # gates.validate_edits() before persistence.
        return v


class Geography(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: str
    district: str
    mandal: str
    assembly: str
    parliament: str


class Evaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evaluation_type: EvaluationType
    evaluation_criteria: EvaluationCriteria
    display_rank: DisplayRank


class TenderDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    s_no: int = Field(..., ge=1)
    document_name: str
    stage: DocumentStage = DocumentStage.COMMON
    document_type: DocumentType = DocumentType.MANDATORY


class TenderDates(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_date: str        # ISO 8601 with TZ
    end_date: str
    closing_date: str


class EnquiryForm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: FormStage
    form_name: str
    type_of_form: FormType = FormType.SECURE
    supporting_document_required: YesNo = YesNo.NO
    supporting_document_description: str = "N/A"


class GeneralTerms(BaseModel):
    model_config = ConfigDict(extra="forbid")
    eligibility: str = ""
    technical: str = ""
    legal: str = ""
    bid_procedure: str = ""


class BoQRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    s_no: int = Field(..., ge=1)
    item: str
    qty: float = Field(..., gt=0)
    unit: str
    rate: Optional[float] = None
    amount: Optional[float] = None


class CitationSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_id: str
    node_type: str
    quote_excerpt: str


class Citations(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule_ids: list[str] = Field(default_factory=list)
    clause_ids: list[str] = Field(default_factory=list)
    sources: list[CitationSource] = Field(default_factory=list)


# ─── Master state object ─────────────────────────────────────────────


class TenderDraftState(BaseModel):
    """Full eGP-format payload persisted on kg_nodes (node_type='TenderDraft')."""
    model_config = ConfigDict(extra="forbid")

    draft_id: str
    tender_id: Optional[str] = None
    tender_notice_number: Optional[str] = None

    enquiry_particulars: EnquiryParticulars
    classification: Classification
    financial: Financial
    geography: Geography
    evaluation: Evaluation
    documents: list[TenderDocument] = Field(default_factory=list)
    dates: TenderDates
    enquiry_forms: list[EnquiryForm] = Field(default_factory=list)

    general_terms: GeneralTerms = Field(default_factory=GeneralTerms)
    boq: list[BoQRow] = Field(default_factory=list)
    citations: Citations = Field(default_factory=Citations)

    current_gate: GateName = GateName.INITIATION
    current_assignee_role: Optional[RoleName] = None
    version: int = 1

    created_by: str
    created_at: str
    last_updated_at: str


# ─── Gate transition + snapshot kg_node payloads ─────────────────────


class GateTransitionEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str                                          # dot-path into TenderDraftState
    old_value: Any
    new_value: Any


class GateTransitionProps(BaseModel):
    """Properties for a GateTransition kg_node."""
    model_config = ConfigDict(extra="forbid")
    transition_id: str
    draft_id: str
    from_gate: GateName
    to_gate: GateName
    reviewer_role: RoleName
    reviewer_id: str
    action: GateAction
    comments: str = ""
    edits: list[GateTransitionEdit] = Field(default_factory=list)
    timestamp: str


class DraftVersionSnapshotProps(BaseModel):
    """Properties for a DraftVersionSnapshot kg_node — immutable payload at gate boundary."""
    model_config = ConfigDict(extra="forbid")
    snapshot_id: str
    draft_id: str
    version: int
    payload: TenderDraftState
    created_by_role: RoleName
    created_at: str


# ─── SSE event schema ────────────────────────────────────────────────


class LangGraphNode(str, Enum):
    ANALYZE_INPUTS = "analyze_inputs"
    CLASSIFY_TENDER_TYPE = "classify_tender_type"
    RETRIEVE_TEMPLATES = "retrieve_templates"
    RETRIEVE_CLAUSES = "retrieve_clauses"
    DRAFT_NIT = "draft_NIT"
    DRAFT_ITB = "draft_ITB"
    DRAFT_ELIGIBILITY = "draft_eligibility"
    DRAFT_BOQ_SKELETON = "draft_BoQ_skeleton"
    DRAFT_LEGAL_TERMS = "draft_legal_terms"
    DRAFT_EVALUATION_FORM = "draft_evaluation_form"
    ASSEMBLE_DOCUMENT = "assemble_document"
    RENDER_DOCX = "render_DOCX"


LANGGRAPH_NODES_IN_ORDER: list[LangGraphNode] = [
    LangGraphNode.ANALYZE_INPUTS,
    LangGraphNode.CLASSIFY_TENDER_TYPE,
    LangGraphNode.RETRIEVE_TEMPLATES,
    LangGraphNode.RETRIEVE_CLAUSES,
    LangGraphNode.DRAFT_NIT,
    LangGraphNode.DRAFT_ITB,
    LangGraphNode.DRAFT_ELIGIBILITY,
    LangGraphNode.DRAFT_BOQ_SKELETON,
    LangGraphNode.DRAFT_LEGAL_TERMS,
    LangGraphNode.DRAFT_EVALUATION_FORM,
    LangGraphNode.ASSEMBLE_DOCUMENT,
    LangGraphNode.RENDER_DOCX,
]


class SSEEventNodeStarted(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["node_started"] = "node_started"
    node: LangGraphNode
    index: int
    total: int


class SSEEventSectionStarted(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["section_started"] = "section_started"
    section: str
    node: LangGraphNode


class SSEEventFieldUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["field_update"] = "field_update"
    path: str
    value: Any
    node: LangGraphNode


class SSEEventTextChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["text_chunk"] = "text_chunk"
    path: str
    chunk: str
    node: LangGraphNode


class SSEEventTableRowAdded(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["table_row_added"] = "table_row_added"
    table: Literal["documents", "boq", "enquiry_forms"]
    row: Any
    node: LangGraphNode


class SSEEventSectionComplete(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["section_complete"] = "section_complete"
    section: str
    node: LangGraphNode


class SSEEventNodeComplete(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["node_complete"] = "node_complete"
    node: LangGraphNode
    index: int
    total: int
    elapsed_ms: int
    citations: Optional[Citations] = None


class SSEEventWorkflowComplete(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["workflow_complete"] = "workflow_complete"
    draft_id: str
    total_elapsed_ms: int


class SSEEventError(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["error"] = "error"
    node: str  # LangGraphNode value OR "system"
    message: str


SSEEvent = Union[
    SSEEventNodeStarted,
    SSEEventSectionStarted,
    SSEEventFieldUpdate,
    SSEEventTextChunk,
    SSEEventTableRowAdded,
    SSEEventSectionComplete,
    SSEEventNodeComplete,
    SSEEventWorkflowComplete,
    SSEEventError,
]


# ─── Worker API request/response schemas ─────────────────────────────


class InitialPayload(BaseModel):
    """The 7-step form data submitted to /m1/run."""
    model_config = ConfigDict(extra="forbid")

    enquiry_particulars: EnquiryParticulars
    classification: Classification
    financial: Financial
    geography: Geography
    evaluation: Evaluation
    documents: list[TenderDocument] = Field(default_factory=list)
    dates: TenderDates
    enquiry_forms: list[EnquiryForm] = Field(default_factory=list)


class M1RunParams(BaseModel):
    """params field of POST /m1/run body.

    R9.4 fix: extra="allow" (was "forbid") so optional fields like
    boq_skeleton, boq_skeleton_filename, and future workflow knobs flow
    through without breaking the Pydantic gate. The worker code reads
    these via params.get(...) — they don't need to be on the validated
    M1RunParams model.
    """
    model_config = ConfigDict(extra="allow")

    draft_id: Optional[str] = None                    # if absent, server creates
    initiator_role: Literal["DEALING_OFFICER"] = "DEALING_OFFICER"
    initiator_id: str
    initial_payload: InitialPayload


class M1GateActionRequest(BaseModel):
    """Body for POST /m1/draft/{id}/approve | /revise | /publish | /edit."""
    model_config = ConfigDict(extra="forbid")

    draft_id: str
    actor_role: RoleName
    actor_id: str
    comments: str = ""
    edits: list[GateTransitionEdit] = Field(default_factory=list)
    send_back_to: Optional[GateName] = None           # AUTHORITY only


# ─── Utility ─────────────────────────────────────────────────────────


def now_iso() -> str:
    """ISO 8601 UTC timestamp with timezone."""
    return _dt.datetime.now(tz=_dt.timezone.utc).isoformat(timespec="seconds")
