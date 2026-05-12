# Module 1 (Drafter) — Data Model + State Machine

**Status:** locked per M1.0 directive (2026-05-12).
**Source of truth:** `frontend/types/m1-drafter.ts` + `services/m1-drafter/app/schemas.py`.
**Supabase schema:** JSONB-additive; no DDL — three new `node_type` values plus existing `kg_nodes` infrastructure.

---

## 1. New kg_node types

### 1.1 `TenderDraft`

The master tender artifact. One row per draft. `properties` holds the full `TenderDraftState` (see `frontend/types/m1-drafter.ts`).

| Column | Value |
|---|---|
| `node_id` | UUID (Supabase-generated) |
| `doc_id` | `m1_draft_<draft_uuid>` (drafter-scoped; isolates queries) |
| `node_type` | `"TenderDraft"` |
| `label` | `f"M1 Draft: {name_of_work[:50]} ({current_gate})"` — refreshed on each gate transition |
| `properties` | Full `TenderDraftState` JSONB |
| `source_ref` | `"module1:drafter_v1"` |
| `created_at` | (Supabase auto) |

### 1.2 `GateTransition`

Append-only audit row per `APPROVE / REVISE / PUBLISH / SENDBACK / EDIT` action.

| Column | Value |
|---|---|
| `doc_id` | `m1_draft_<draft_uuid>` (matches parent TenderDraft) |
| `node_type` | `"GateTransition"` |
| `label` | `f"{reviewer_role}: {action} {from_gate}→{to_gate}"` |
| `properties` | `GateTransitionProps` JSONB (transition_id, from_gate, to_gate, reviewer_role, action, comments, edits[]) |
| `source_ref` | `"module1:gates_v1"` |

### 1.3 `DraftVersionSnapshot`

Immutable snapshot of the full `TenderDraftState` at each gate boundary. One row per (draft_id, version).

| Column | Value |
|---|---|
| `doc_id` | `m1_draft_<draft_uuid>` |
| `node_type` | `"DraftVersionSnapshot"` |
| `label` | `f"v{version} after {previous_gate}"` |
| `properties` | `DraftVersionSnapshotProps` JSONB (snapshot_id, version, payload, created_by_role) |
| `source_ref` | `"module1:snapshots_v1"` |

**Versioning convention:**
- `version=1` — after `analyze_inputs` (post-7-step form submission, pre-AI generation)
- `version=2` — after `workflow_complete` (post-AI generation; entering TECHNICAL gate)
- `version=3` — after TECHNICAL approve (entering FINANCIAL gate)
- `version=4` — after FINANCIAL approve (entering PROCUREMENT gate)
- `version=5` — after PROCUREMENT approve (entering AUTHORITY gate)
- `version=6` — after AUTHORITY publish (PUBLISHED; final artifact set rendered)
- Send-back creates a new version bump rather than reusing.

---

## 2. Gate state machine

```
INITIATION (Dealing Officer)
    │ form submitted, "Generate" clicked
    ▼
AI_GENERATION (system — LangGraph 12 nodes; SSE stream live)
    │ workflow_complete
    ▼
TECHNICAL (Senior Engineer)
    │ approve ─────────────────────────────────┐
    │ revise → INITIATION                       │
    ▼                                            ▼
FINANCIAL (Department Head)                 [send-back from
    │ approve                                  AUTHORITY only]
    │ revise → INITIATION
    ▼
PROCUREMENT (Procurement Officer)
    │ approve
    │ revise → INITIATION
    ▼
AUTHORITY (Tender Inviting Authority)
    │ publish → PUBLISHED (final; tender_id assigned; PDF/DOCX/XLSX rendered)
    │ sendback → <any prior gate>
    ▼
PUBLISHED (terminal)
```

### 2.1 Send-back semantics

- `TECHNICAL / FINANCIAL / PROCUREMENT` reviewers can only **revise** — routes back to `INITIATION` (Dealing Officer re-triggers Generate).
- `AUTHORITY` is the only role allowed to **send back to any prior gate** via `M1GateActionRequest.send_back_to`. Used for "Authority sees issue → wants specific gate to re-review without restarting AI generation."

### 2.2 Edit scope per gate

Codified in `GATE_EDIT_SCOPE` (TypeScript) / `GATE_EDIT_SCOPE` (Python). Dot-paths into `TenderDraftState`. Enforced server-side by `gates.validate_edits()` (built in M1.6).

| Gate | Editable fields |
|---|---|
| `INITIATION` | `*` (everything) |
| `AI_GENERATION` | (none — workflow running) |
| `TECHNICAL` | `boq`, `general_terms.technical`, `general_terms.eligibility`, `enquiry_particulars.name_of_work`, `financial.period_of_completion_months`, `documents` |
| `FINANCIAL` | `financial.estimated_contract_value_inr`, `financial.estimated_contract_value_words`, `financial.bid_security_percent`, `financial.bid_security_inr`, `financial.transaction_fee_inr`, `financial.bid_validity_days`, `classification.form_of_contract` |
| `PROCUREMENT` | `evaluation.evaluation_type`, `evaluation.evaluation_criteria`, `evaluation.display_rank`, `classification.bid_call_numbers`, `enquiry_forms` |
| `AUTHORITY` | (none — read-only with publish/sendback actions) |
| `PUBLISHED` | (none — terminal) |

Any attempt to PATCH a field outside the current gate's scope → HTTP 403 + audit-log entry.

---

## 3. Role → Gate mapping (RBAC for demo)

| Role | Gate where active | Action verbs |
|---|---|---|
| `DEALING_OFFICER` | `INITIATION` (and revisions returning to it) | edit form, click Generate, re-trigger after revise |
| `SENIOR_ENGINEER` | `TECHNICAL` | approve, revise (with comments), edit scoped fields |
| `DEPARTMENT_HEAD` | `FINANCIAL` | approve, revise, edit scoped fields |
| `PROCUREMENT_OFFICER` | `PROCUREMENT` | approve, revise, edit scoped fields |
| `TENDER_INVITING_AUTHORITY` | `AUTHORITY` | publish, sendback (with target gate), read-only view |

**Demo posture:** role switching via `<RoleSwitcher />` dropdown in nav (localStorage-persisted). Production posture (deferred): Keycloak SSO + JWT with role claims; same `RoleName` enum.

---

## 4. eGP-format field reference (Banaganapalli sample)

Drawn directly from the AP eGP "Tender Details" page for Tender ID `933192`. This is the canonical smoke-test target for M1.8.

```yaml
draft_id: m1_draft_<uuid>
tender_id: 933192                                              # assigned at PUBLISHED
tender_notice_number: "NIT No: 52/2026-27 Dt. 27-04-2026"

enquiry_particulars:
  department_name: "PRED"
  circle_division: "PRED-Executive Engineer, PR PIU division, Kurnool"
  officer_inviting_bids: "Executive Engineer, PR PIU division, Kurnool"
  bid_opening_authority: "E E"
  address: "Nunepalli MPDO Office Compound"
  contact_details: "7780743028"
  email: "eepiuknl@yahoo.com"
  name_of_project: "DMF"
  name_of_work: "Providing Kitchen Shed and additional facilities to Shadikhana at Banaganapalli"

classification:
  tender_category: "WORKS"
  type_of_work: "Civil Works"
  tender_type: "OPEN - NCB"
  bidding_type: "OPEN"
  form_of_contract: "L.S"
  consortium_joint_venture: "Not Applicable"
  bid_call_numbers: 1

financial:
  estimated_contract_value_inr: 1597185
  estimated_contract_value_words: "Fifteen Lakh Ninety Seven Thousand One Hundred and Eighty Five Rupees"
  period_of_completion_months: 6
  bid_validity_days: 90
  bid_security_percent: 1.0
  bid_security_inr: 15972
  bid_security_in_favour_of: "Online payment"
  mode_of_payment: "Online Payment, Challan Generation, BG"
  currency_type: "INR"
  default_currency: "Indian Rupee - INR"
  transaction_fee_inr: 566
  transaction_fee_payable_to: "APTS payable at Vijayawada"
  transaction_fee_go_reference: "G.O.Ms No 4, Dtd 17.02.2015 IT&C Dept"

geography:
  state: "ANDHRA PRADESH"
  district: "NANDYAL"
  mandal: "BANAGANAPALLE"
  assembly: "Banaganapalli"
  parliament: "Nandyal"

evaluation:
  evaluation_type: "Percentage"
  evaluation_criteria: "Based on Price"
  display_rank: "Lowest"

documents:                                                     # all 7 mandatory
  - {s_no: 1, document_name: "Registration Certificate", stage: COMMON, document_type: Mandatory}
  - {s_no: 2, document_name: "EMD Using net Banking/RTGS/NEFT The Bidders Should be Pay EMDS from their Registered bank accounts and the Unsuccessful Bidders", stage: COMMON, document_type: Mandatory}
  - {s_no: 3, document_name: "GST Registration Certificate", stage: COMMON, document_type: Mandatory}
  - {s_no: 4, document_name: "Declaration and Critical Equipment Owned or Leased on Judicial Stamp paper of RS 100", stage: COMMON, document_type: Mandatory}
  - {s_no: 5, document_name: "Saral 2025-2026 Submitted to IT Dept with PAN Card", stage: COMMON, document_type: Mandatory}
  - {s_no: 6, document_name: "Key Personal as per Bid Document", stage: COMMON, document_type: Mandatory}
  - {s_no: 7, document_name: "Any other Documents Required as per Tender Schedule", stage: COMMON, document_type: Mandatory}

dates:
  start_date: "2026-05-12T18:20:00+05:30"
  end_date: "2026-05-28T11:00:00+05:30"
  closing_date: "2026-05-28T11:30:00+05:30"

enquiry_forms:
  - {stage: "Commercial Stage", form_name: "Percentage Wise Rate", type_of_form: "Secure", supporting_document_required: "No", supporting_document_description: "N/A"}

# AI-generated fields (populated by LangGraph 12-node workflow during AI_GENERATION)
general_terms:
  eligibility: "(populated by draft_eligibility node)"
  technical:   "(populated by draft_ITB / draft_legal_terms nodes)"
  legal:       "(populated by draft_legal_terms node)"
  bid_procedure: "(populated by draft_eligibility node — Procedure for Bid Submission)"

boq: "(populated by draft_BoQ_skeleton node)"

citations:
  rule_ids:    "(populated as nodes execute)"
  clause_ids:  "(populated as nodes execute)"
  sources:     "(quote_excerpt per cited node)"

current_gate: TECHNICAL                                        # after AI_GENERATION completes
current_assignee_role: SENIOR_ENGINEER
version: 2                                                      # post-AI generation
```

---

## 5. Sentinel discipline for M1

| Sentinel | Value | Behaviour during M1 |
|---|---|---|
| `ValidationFinding` | 154 | **frozen** — no M1 work touches this |
| `BidEvaluationFinding` | 351 | **frozen** |
| `BIDDER_VIOLATES_RULE` | 49 | **frozen** |
| `EligibilityMatrix` | 27 | **frozen** |
| `TenderRanking` | 3 | **frozen** |
| `BidAnomalyFinding` | 6 | **frozen** |
| `ComparativeStatement` | 3 | **frozen** |
| `Communication` | 78+ | **additive** (per M4 demo Q&A — unchanged by M1) |
| `TenderDraft` | NEW | **additive** — count grows per draft (smoke test target: +1 for Banaganapalli) |
| `GateTransition` | NEW | **additive** — +4 per draft (TECHNICAL approve / FIN approve / PROC approve / AUTHORITY publish) |
| `DraftVersionSnapshot` | NEW | **additive** — +5 per draft (one per gate boundary) |

The 7 hard sentinels (`154 / 351 / 49 / 27 / 3 / 6 / 3`) must remain unchanged after every M1.x sub-block. Additive growth on the 4 new kg_node types is expected.

---

## 6. Open design decisions deferred

| Item | Phase |
|---|---|
| Production RBAC (Keycloak SSO + JWT roles) | Phase 2 |
| Multi-officer collaboration on same draft (CRDT or operational transform) | Phase 2 |
| Tender amendment / corrigendum workflow | Phase 2 |
| eGP-portal API submission (apeprocurement.gov.in) | Phase 2 |
| `key_personnel` as first-class sub-schema (currently embedded in `documents[6]`) | Phase 2 |
| Email/SMS notification on gate transitions | Phase 2 |
| Telugu output for M1 — **OUT OF SCOPE** (M1 is English-only per directive) | n/a |

---

## 7. Cross-references

- TypeScript types: `frontend/types/m1-drafter.ts`
- Pydantic schemas: `services/m1-drafter/app/schemas.py`
- Gate state machine + RBAC enforcement (M1.6): `services/m1-drafter/app/gates.py`
- LangGraph workflow (M1.5): `services/m1-drafter/app/langgraph_workflow.py`
- SSE endpoint (M1.5): `services/m1-drafter/app/main.py::stream_draft`
- Wizard UI (M1.2): `frontend/app/module1/new-draft/page.tsx` + `frontend/app/module1/new-draft/steps/`
- Live structured view (M1.3): `frontend/components/m1/EGPLiveView.tsx` + `frontend/hooks/useSSEDraftStream.ts`
- Gate review UI (M1.4): `frontend/app/module1/draft/[draft_id]/review/page.tsx`
- Artifact rendering (M1.7): `services/m1-drafter/app/renderers.py`
