/**
 * Module 1 — Drafter type contract.
 *
 * Anchors all M1 surfaces:
 *  - 7-step initiation form (Dealing Officer)
 *  - LangGraph SSE event stream (12 nodes)
 *  - 4-gate review UIs (TECHNICAL / FINANCIAL / PROCUREMENT / AUTHORITY)
 *  - Cloud Run worker payload contract
 *  - TenderDraft / GateTransition / DraftVersionSnapshot kg_node properties
 *
 * Schema mirrors the AP eGP "Tender Details" page (Banaganapalli sample).
 * Pydantic equivalents live in services/m1-drafter/app/schemas.py.
 */

// ─── Enums ──────────────────────────────────────────────────────────

export type TenderCategory = "WORKS" | "GOODS" | "SERVICES";

export type TenderType =
  | "OPEN - NCB"    // National Competitive Bidding (default for AP)
  | "OPEN - ICB"    // International Competitive Bidding
  | "LIMITED"
  | "SINGLE_TENDER"
  | "EOI";          // Expression of Interest

export type BiddingType = "OPEN" | "LIMITED" | "EOI" | "SINGLE_SOURCE";

export type FormOfContract =
  | "L.S"           // Lump Sum (Banaganapalli sample default)
  | "Item Rate"
  | "Percentage"
  | "EPC"
  | "Cost Plus";

export type EvaluationType = "Percentage" | "Item Rate" | "L.S" | "Composite";
export type EvaluationCriteria = "Based on Price" | "Based on QCBS" | "Two-Envelope";
export type DisplayRank = "Lowest" | "Highest";

export type CurrencyType = "INR" | "USD" | "EUR";
export type ConsortiumJV = "Applicable" | "Not Applicable";

export type DocumentStage = "COMMON" | "TECHNICAL" | "FINANCIAL";
export type DocumentType = "Mandatory" | "Optional";

export type FormStage = "Technical Stage" | "Commercial Stage" | "Both";
export type FormType = "Secure" | "Standard";
export type YesNo = "Yes" | "No";

// ─── Gate state machine ─────────────────────────────────────────────

export type GateName =
  | "INITIATION"       // Dealing Officer fills 7-step form
  | "AI_GENERATION"    // LangGraph runs; fields populate via SSE
  | "TECHNICAL"        // Senior Engineer reviews
  | "FINANCIAL"        // Department Head reviews
  | "PROCUREMENT"      // Procurement Officer reviews
  | "AUTHORITY"        // Tender Inviting Authority — read-only; publish/sendback
  | "PUBLISHED";       // Final; PDF/DOCX/XLSX rendered; tender_id assigned

export type RoleName =
  | "DEALING_OFFICER"
  | "SENIOR_ENGINEER"
  | "DEPARTMENT_HEAD"
  | "PROCUREMENT_OFFICER"
  | "TENDER_INVITING_AUTHORITY";

export type GateAction = "APPROVE" | "REVISE" | "PUBLISH" | "SENDBACK" | "EDIT";

/**
 * Gate transition rules (locked per directive):
 *   INITIATION   → AI_GENERATION  (Dealing Officer triggers Generate)
 *   AI_GENERATION → TECHNICAL     (workflow_complete event)
 *   TECHNICAL    → FINANCIAL      (approve) | INITIATION (revise → back to Dealing Officer)
 *   FINANCIAL    → PROCUREMENT    (approve) | INITIATION (revise)
 *   PROCUREMENT  → AUTHORITY      (approve) | INITIATION (revise)
 *   AUTHORITY    → PUBLISHED      (publish) | <any prior gate> (sendback)
 */
export const GATE_REVIEWER_ROLE: Record<GateName, RoleName | null> = {
  INITIATION:    "DEALING_OFFICER",
  AI_GENERATION: null,                       // system, no human
  TECHNICAL:     "SENIOR_ENGINEER",
  FINANCIAL:     "DEPARTMENT_HEAD",
  PROCUREMENT:   "PROCUREMENT_OFFICER",
  AUTHORITY:     "TENDER_INVITING_AUTHORITY",
  PUBLISHED:     null,                       // terminal
};

/**
 * Editable field scope per gate (dot-paths into TenderDraftState).
 * Locked per directive M1.0 design decisions. Wildcard "*" means all
 * fields editable (INITIATION only). Empty list means read-only.
 */
export const GATE_EDIT_SCOPE: Record<GateName, string[]> = {
  INITIATION:    ["*"],
  AI_GENERATION: [],                         // read-only while LangGraph runs
  TECHNICAL: [
    "boq",
    "general_terms.technical",
    "general_terms.eligibility",             // technical eligibility criteria
    "enquiry_particulars.name_of_work",
    "financial.period_of_completion_months",
    "documents",                              // Senior Engineer can adjust mandatory docs list
  ],
  FINANCIAL: [
    "financial.estimated_contract_value_inr",
    "financial.estimated_contract_value_words",
    "financial.bid_security_percent",
    "financial.bid_security_inr",
    "financial.transaction_fee_inr",
    "financial.bid_validity_days",
    "classification.form_of_contract",
  ],
  PROCUREMENT: [
    "evaluation.evaluation_type",
    "evaluation.evaluation_criteria",
    "evaluation.display_rank",
    "classification.bid_call_numbers",
    "enquiry_forms",                          // stages config (Tech/Commercial)
  ],
  AUTHORITY:  [],                            // read-only; can publish or send-back
  PUBLISHED:  [],
};

// ─── Sub-schemas ────────────────────────────────────────────────────

export interface EnquiryParticulars {
  department_name: string;                   // "PRED"
  circle_division: string;                   // "PRED-Executive Engineer, PR PIU division, Kurnool"
  officer_inviting_bids: string;             // "Executive Engineer, PR PIU division, Kurnool"
  bid_opening_authority: string;             // "E E" (typically same as Officer Inviting Bids)
  address: string;                           // "Nunepalli MPDO Office Compound"
  contact_details: string;                   // phone
  email: string;
  name_of_project: string;                   // "DMF"
  name_of_work: string;                      // "Providing Kitchen Shed..."
}

export interface Classification {
  tender_category: TenderCategory;           // "WORKS"
  type_of_work: string;                      // "Civil Works"
  tender_type: TenderType;                   // "OPEN - NCB"
  bidding_type: BiddingType;                 // "OPEN"
  form_of_contract: FormOfContract;          // "L.S"
  consortium_joint_venture: ConsortiumJV;    // "Not Applicable"
  bid_call_numbers: number;                  // 1 (default)
}

export interface Financial {
  estimated_contract_value_inr: number;             // 1597185
  estimated_contract_value_words: string;           // "Fifteen Lakh Ninety Seven Thousand..."
  period_of_completion_months: number;              // 6
  bid_validity_days: number;                        // 90
  bid_security_percent: number;                     // 1.0 (typically 1%)
  bid_security_inr: number;                         // 15972 (computed)
  bid_security_in_favour_of: string;                // "Online payment" or "Officer Name"
  mode_of_payment: string;                          // "Online Payment, Challan Generation, BG"
  currency_type: CurrencyType;                      // "INR"
  default_currency: string;                         // "Indian Rupee - INR"
  transaction_fee_inr: number;                      // 566 (per APTS norm)
  transaction_fee_payable_to: string;               // "APTS payable at Vijayawada"
  transaction_fee_go_reference: string;             // "G.O.Ms No 4, Dtd 17.02.2015 IT&C Dept"
}

export interface Geography {
  state: string;       // "ANDHRA PRADESH"
  district: string;    // "NANDYAL"
  mandal: string;      // "BANAGANAPALLE"
  assembly: string;    // "Banaganapalli"
  parliament: string;  // "Nandyal"
}

export interface Evaluation {
  evaluation_type: EvaluationType;          // "Percentage"
  evaluation_criteria: EvaluationCriteria;  // "Based on Price"
  display_rank: DisplayRank;                // "Lowest"
}

export interface TenderDocument {
  s_no: number;
  document_name: string;                    // "Registration Certificate"
  stage: DocumentStage;                     // "COMMON"
  document_type: DocumentType;              // "Mandatory"
}

export interface TenderDates {
  start_date: string;                       // ISO 8601 with time, e.g. "2026-05-12T18:20:00+05:30"
  end_date: string;                         // "2026-05-28T11:00:00+05:30"
  closing_date: string;                     // "2026-05-28T11:30:00+05:30"
}

export interface EnquiryForm {
  stage: FormStage;                         // "Commercial Stage"
  form_name: string;                        // "Percentage Wise Rate"
  type_of_form: FormType;                   // "Secure"
  supporting_document_required: YesNo;
  supporting_document_description: string;  // "N/A" if none
}

export interface GeneralTerms {
  eligibility: string;                      // ITB eligibility + PQ criteria; populated by draft_eligibility node
  technical: string;                        // technical terms; populated by draft_ITB / draft_legal_terms
  legal: string;                            // legal/arbitration boilerplate
  bid_procedure: string;                    // procedure for bid submission
}

export interface BoQRow {
  s_no: number;
  item: string;
  qty: number;
  unit: string;                             // "m3", "m2", "lump sum", "RM"
  rate?: number | null;                     // optional — populated by bidders at bid time; skeleton may omit
  amount?: number | null;
}

export interface Citations {
  rule_ids: string[];                       // Rule kg_node UUIDs referenced during generation
  clause_ids: string[];                     // Clause kg_node UUIDs referenced
  sources: Array<{ node_id: string; node_type: string; quote_excerpt: string }>;
}

// ─── Master state object ────────────────────────────────────────────

/**
 * TenderDraftState — full eGP-format payload.
 * Persisted in kg_nodes WHERE node_type='TenderDraft', JSONB column `properties`.
 * Snapshots at each gate transition in DraftVersionSnapshot.
 */
export interface TenderDraftState {
  // Identification
  draft_id: string;
  tender_id?: string | null;                          // assigned at PUBLISHED gate (eGP-system-generated)
  tender_notice_number?: string | null;               // "NIT No: 52/2026-27 Dt. 27-04-2026"

  // 7-step form payload
  enquiry_particulars: EnquiryParticulars;
  classification: Classification;
  financial: Financial;
  geography: Geography;
  evaluation: Evaluation;
  documents: TenderDocument[];
  dates: TenderDates;
  enquiry_forms: EnquiryForm[];

  // AI-generated body
  general_terms: GeneralTerms;
  boq: BoQRow[];
  citations: Citations;

  // Workflow state
  current_gate: GateName;
  current_assignee_role: RoleName | null;
  version: number;                                    // monotonic; bumps at each gate transition

  // Audit metadata
  created_by: string;                                 // synthetic role/officer for demo
  created_at: string;                                 // ISO 8601
  last_updated_at: string;
}

// ─── Gate transition + snapshot kg_node properties ──────────────────

export interface GateTransitionEdit {
  path: string;                                       // dot-path into TenderDraftState
  old_value: unknown;
  new_value: unknown;
}

export interface GateTransitionProps {
  transition_id: string;                              // UUID
  draft_id: string;
  from_gate: GateName;
  to_gate: GateName;
  reviewer_role: RoleName;
  reviewer_id: string;                                // synthetic for demo
  action: GateAction;
  comments: string;
  edits: GateTransitionEdit[];
  timestamp: string;
}

export interface DraftVersionSnapshotProps {
  snapshot_id: string;
  draft_id: string;
  version: number;
  payload: TenderDraftState;                          // immutable snapshot
  created_by_role: RoleName;
  created_at: string;
}

// ─── SSE event schema (LangGraph → frontend stream) ─────────────────

/** 12 LangGraph node identifiers in execution order. */
export type LangGraphNode =
  | "analyze_inputs"
  | "classify_tender_type"
  | "retrieve_templates"
  | "retrieve_clauses"
  | "draft_NIT"
  | "draft_ITB"
  | "draft_eligibility"
  | "draft_BoQ_skeleton"
  | "draft_legal_terms"
  | "draft_evaluation_form"
  | "assemble_document"
  | "render_DOCX";

export const LANGGRAPH_NODES_IN_ORDER: LangGraphNode[] = [
  "analyze_inputs",
  "classify_tender_type",
  "retrieve_templates",
  "retrieve_clauses",
  "draft_NIT",
  "draft_ITB",
  "draft_eligibility",
  "draft_BoQ_skeleton",
  "draft_legal_terms",
  "draft_evaluation_form",
  "assemble_document",
  "render_DOCX",
];

export type SSEEvent =
  | { type: "node_started";     node: LangGraphNode; index: number; total: number }
  | { type: "section_started";  section: string;     node: LangGraphNode }
  | { type: "field_update";     path: string;        value: unknown;       node: LangGraphNode }
  | { type: "text_chunk";       path: string;        chunk: string;        node: LangGraphNode }
  | { type: "table_row_added";  table: "documents" | "boq" | "enquiry_forms"; row: unknown; node: LangGraphNode }
  | { type: "section_complete"; section: string;     node: LangGraphNode }
  | { type: "node_complete";    node: LangGraphNode; index: number; total: number; elapsed_ms: number; citations?: Citations }
  | { type: "workflow_complete"; draft_id: string;   total_elapsed_ms: number }
  | { type: "error";            node: LangGraphNode | "system"; message: string };

// ─── Worker API contracts ───────────────────────────────────────────

/** POST /m1/run body (Dealing Officer initiates draft from 7-step form). */
export interface M1RunRequest {
  tender_id?: null;                                   // null at INITIATION; assigned only at PUBLISHED
  params: {
    draft_id?: string;                                // optional: client-generated UUID; server creates if absent
    initiator_role: "DEALING_OFFICER";
    initiator_id: string;                             // synthetic
    initial_payload: Partial<TenderDraftState>;       // 7-step form fields (everything except general_terms + boq + citations)
  };
}

/** Response from POST /m1/run (or any /<module>/run endpoint). */
export interface M1RunResponse {
  job_id: string;
  status: "QUEUED" | "COMPLETED_INLINE";
  poll_url: string;
  draft_id?: string;
  stream_url?: string;                                // SSE endpoint for live updates
}

/** Gate action request body. */
export interface M1GateActionRequest {
  draft_id: string;
  actor_role: RoleName;
  actor_id: string;
  comments?: string;
  edits?: GateTransitionEdit[];
  send_back_to?: GateName;                            // AUTHORITY only
}
