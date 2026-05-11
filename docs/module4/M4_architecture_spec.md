# Module 4 — Communicator: Architecture Design Specification (M4.1)

**Sub-block:** Module 4 — M4.1 (design-only)
**Predecessor:** Module 3 Extensions arc complete (commit `e364bec` Ext-8 B9 seed) + L75 PDF renderer (`87ee7a9`)
**Successors:** M4.2 (3 drafter pilots) → M4.3 (DOCX rendering + audit log) → future M4.x (remaining 6 communication types, Telugu, SMS, portal API)
**Status:** SPECIFICATION (no data layer changes; no drafter code)
**Purpose:** define the contract M4.2+ build to satisfy

---

## 1. Context

Module 3 produces machine-grade structured findings: 351 BidEvaluationFinding rows + 27 EligibilityMatrix aggregations + 3 TenderRanking rankings + 6 BidAnomalyFinding signals + 3 ComparativeStatement reports (DOCX + PDF). These findings carry the full citation chain (rule_id → decision_reason → evidence) but they are **not yet communications**. The recipients — bidders, committee members, internal officers, vigilance — never see a `BidEvaluationFinding` UUID. They see (and act on) **plain-language letters, notifications, and referrals** drafted from those findings.

**Module 4 sits between Module 3 (evaluation) and external recipients** (bidders via portal/email/SMS; internal officers via portal queues; vigilance/CVC via referral chains). It converts structured findings into letters/notifications, attaches drilldown chains (audit_id + source_finding_node_ids[]) for traceability, routes through the correct channel per recipient preferences, and writes an audit log durable enough to defend each communication's provenance under RTI / committee scrutiny.

M4.1 (this document) is design-only. M4.2 builds 3 drafter pilots (DISQUALIFICATION + AWARD + ALB_JUSTIFICATION) — the same Sub-block 3a → 3b pattern from Module 3. M4.3 adds DOCX rendering + audit log query helper. Future M4.x sub-blocks add the remaining 6 communication types, Telugu via Sarvam-M, SMS gateway, and portal API integration.

---

## 2. Schema: Communication kg_node

Single new `kg_nodes.node_type = "Communication"`. JSONB additive schema — no DDL. Carries 14 properties:

| field | type | required | notes |
|---|---|---|---|
| `communication_type` | enum (see §3) | yes | DISQUALIFICATION / AWARD / ALB_JUSTIFICATION / FLAGGED / DOC_REVIEW / REGRET / CARTEL_REVIEW / BID_ACK / INTERNAL_ROUTING |
| `recipient_bidder_profile_id` | string \| null | conditional | null only for INTERNAL_ROUTING (internal recipients addressed by role, not by bidder_profile_id) |
| `recipient_email` | string \| null | conditional | populated from BidderProfile.email_primary for external recipients; null for internal routing |
| `recipient_role` | enum INTERNAL roles \| null | conditional | DEPARTMENT_HEAD / VIGILANCE_OFFICER / COMMITTEE_CHAIR; null for external recipients |
| `tender_id` | string | yes | scoping anchor for per-tender drilldown |
| `sender_role` | enum | yes | SYSTEM (automated) / CLERK / DEALING_OFFICER / DEPARTMENT_HEAD; for M4.2 always SYSTEM |
| `channel` | enum EMAIL / SMS / PORTAL / POST | yes | from BidderProfile.preferred_notification_channel for external; PORTAL for internal |
| `language` | enum EN / TE / EN+TE | yes | EN for M4.2; TE deferred to future M4.x via Sarvam-M |
| `status` | enum DRAFT / READY_FOR_REVIEW / APPROVED / SENT / FAILED / WITHDRAWN | yes | M4.2 emits DRAFT; M4.4+ adds approval workflow |
| `audit_id` | string (SHA256 hex 16-char) | yes | hash of sorted source_finding_node_ids[] + communication_type + recipient_bidder_profile_id; deterministic across re-runs |
| `source_finding_node_ids` | array of kg_node UUIDs | yes | drilldown chain: every claim in the letter cites one of these |
| `content_en` | text | yes | composed Markdown body in English |
| `content_te` | text \| null | optional | composed Telugu body; null for M4.2 (deferred) |
| `artifact_path_md` | string | yes (post-M4.2) | filesystem path to rendered Markdown |
| `artifact_path_docx` | string \| null | yes (post-M4.3) | filesystem path to rendered DOCX |
| `artifact_path_pdf` | string \| null | optional | filesystem path to rendered PDF (deferred future M4.x) |
| `extracted_by` | string | yes | "module4:draft_<type>_v1" |
| `defeated` | bool | yes | false; reserved for future override workflows |

Plus the standard `kg_nodes` columns: `node_id` (UUID), `doc_id` (= tender_id for tender-scoped communications), `node_type` = "Communication", `label` (human-readable summary), `source_ref` (= same as extracted_by), `created_at` (auto).

---

## 3. Nine Communication Types

Each type has a template + source-finding drilldown chain + expected emission count from current corpus.

### 3.1 DISQUALIFICATION (HARD_BLOCK rejection letter)

**When emitted**: per EligibilityMatrix row with `aggregate_verdict = DISQUALIFIED`.
**Source findings**: EligibilityMatrix row + all BidEvaluationFinding rows with `evaluation_consequence = HARD_BLOCK` for that (bidder, tender).
**Drilldown chain**: ComparativeStatement → EligibilityMatrix → BidEvaluationFinding (HARD_BLOCK subset) → rule_id + decision_reason.
**Template body**: "Your bid for [tender] is INELIGIBLE on N grounds: 1. [rule_id] [decision_reason] 2. … N. … Per [rule_layer], any HARD_BLOCK finding renders the bid INELIGIBLE. You may appeal within 7 days by submitting a written representation citing the specific finding node_id."
**Predicted current corpus**: **6 letters** (B2 × 3 tenders + B3 × 3 tenders = all 6 DISQUALIFIED rows).

### 3.2 AWARD (effective L1 award notification)

**When emitted**: per TenderRanking row, addressed to the `effective_l1_bidder_id` named in the corresponding ComparativeStatement.
**Source findings**: ComparativeStatement (with `effective_l1_bidder_id`) + the bidder's 13 QUALIFIED BidEvaluationFinding rows + TenderRanking.
**Drilldown chain**: ComparativeStatement → TenderRanking → BidEvaluationFinding (13 QUALIFIED) → BidSubmission/BidderProfile.
**Template body**: "We are pleased to inform you that your bid (₹X.XX cr at Y% premium) has been adjudged the effective L1 for tender [name]. Subject to LoA issuance per APCRDA contract terms, please report within 14 days with required securities. Effective L1 derivation: [rationale referencing audit_id + ranking + ALB skip + cartel review]."
**Predicted current corpus**: **3 letters** (B9 × 3 tenders — effective L1 on all 3).

### 3.3 ALB_JUSTIFICATION (CVC-required justification request)

**When emitted**: per BidAnomalyFinding where `anomaly_class = ALB_CORROBORATION`.
**Source findings**: BidAnomalyFinding (with cross_tender_consistency + signals) + the raw L1 BidSubmission + TenderRanking ALB threshold computation.
**Drilldown chain**: BidAnomalyFinding → BidSubmission → BidderProfile → past_anomaly_flags + past_tender_participation.
**Template body**: "Per CVC OM Vigilance Aspects (citation), your bid of ₹X.XX cr is below ALB threshold of ₹Y.YY cr (Z% under estimated value). You are required to submit within 7 days: (i) detailed cost analysis demonstrating viability at the bid amount; (ii) audited financial statements showing capacity to absorb pricing risk; (iii) bank guarantee equal to the shortfall as additional security. Cross-tender consistency: [N of 3 tenders showed this pattern; recurrence is a HIGH-severity signal per CVC norms]."
**Predicted current corpus**: **3 letters** (B8 × 3 tenders — ALB candidate on all 3).

### 3.4 CARTEL_REVIEW (Vigilance referral, NOT a bidder-facing letter)

**When emitted**: per BidAnomalyFinding where `anomaly_class = CARTEL_SUSPECT`. Recipient is internal (Vigilance Officer / CVC), NOT the suspect bidders.
**Source findings**: BidAnomalyFinding (with signal_count + severity + bidder pair) + both bidders' BidSubmissions + BidderProfile fields that trigger signals (signatory, EMD bank-branch, address, premium-delta).
**Drilldown chain**: BidAnomalyFinding → 2 BidSubmissions + 2 BidderProfiles → past_anomaly_flags + EMD_BG.bg_issuing_bank.
**Template body**: "Internal vigilance referral. Pair [B6 + B7] shows N collusion signals on tender [name]: COMMON_BANK_BRANCH (SBI Vijayawada Main Branch), MATCHED_SIGNATORY (initial+surname pattern), SHARED_ADDRESS (same building), TIGHT_PRICE_GAP (premium delta 0.05%). Severity HIGH, confidence HIGH. Recommended action: defer L1 award decision; refer to CVC anti-collusion protocol per [rule_id]. Cross-tender consistency: 3 of 3 tenders show identical pair pattern (systemic)."
**Predicted current corpus**: **3 referrals** (B6+B7 pair × 3 tenders — but each is a single referral naming both bidders, NOT 6).

### 3.5 FLAGGED (committee-review notification)

**When emitted**: per EligibilityMatrix row with `aggregate_verdict = FLAGGED_FOR_COMMITTEE_REVIEW`. Goes to internal committee, not bidder.
**Source findings**: EligibilityMatrix + the WARNING-severity BidEvaluationFinding(s) that triggered the FLAGGED aggregate.
**Drilldown chain**: ComparativeStatement → EligibilityMatrix → BidEvaluationFinding (WARNING subset).
**Template body**: "Committee review requested for bidder [B4] on tender [name]. N WARNING-severity findings: [rule_id + decision_reason for each]. Per [rule_layer], WARNING does not block eligibility but invites committee discretion. Recommended scope: review the specific findings and decide retention or exclusion."
**Predicted current corpus**: **3 notifications** (B4 × 3 tenders).

### 3.6 DOC_REVIEW (documentation re-submission request, bidder-facing)

**When emitted**: per EligibilityMatrix row with `aggregate_verdict = MARK_FOR_DOCUMENTATION_REVIEW`.
**Source findings**: EligibilityMatrix + the GAP_INSUFFICIENT_DATA BidEvaluationFinding(s).
**Drilldown chain**: ComparativeStatement → EligibilityMatrix → BidEvaluationFinding (GAP subset).
**Template body**: "Per review of your bid for tender [name], N criteria require additional documentation: [criterion / decision_reason for each]. Please submit within 7 days the following: [statement reference, e.g. Statement-VI Key Personnel form]. Failure to submit within 7 days will result in your bid being moved to INELIGIBLE."
**Predicted current corpus**: **3 letters** (B5 × 3 tenders).

### 3.7 REGRET (non-L1 QUALIFIED notification, bidder-facing)

**When emitted**: per QUALIFIED bidder who is NOT the effective_l1 AND not under any anomaly review.
**Source findings**: ComparativeStatement (with ranking + effective_l1) + bidder's 13 QUALIFIED BidEvaluationFinding rows + TenderRanking entry showing their rank position.
**Drilldown chain**: ComparativeStatement → TenderRanking → bidder's ranking entry → all 13 BidEvaluationFinding (QUALIFIED).
**Template body**: "We thank you for your bid for tender [name]. Your bid was QUALIFIED on all 13 evaluation criteria but did not emerge as L1. The contract has been awarded to [effective L1 bidder name] at ₹X.XX cr. Your bid amount was ₹Y.YY cr (Lk position). We encourage your participation in future tenders."
**Predicted current corpus**: **3 letters** (B1 × 3 tenders — only QUALIFIED-and-not-anomaly bidder besides B9). B6+B7 receive CARTEL_REVIEW instead (committee defers REGRET until vigilance review completes). B8 receives ALB_JUSTIFICATION instead.

### 3.8 BID_ACK (bid receipt acknowledgement, bidder-facing)

**When emitted**: per BidSubmission, immediately on tender deadline close.
**Source findings**: BidSubmission row + LetterOfBid + EMD_BG + PricedBoQ supplementary nodes.
**Drilldown chain**: BidSubmission → 3 supplementary nodes (LetterOfBid + EMD_BG + PricedBoQ).
**Template body**: "Your bid for tender [name] (NIT [nit_no]) has been received on [submission_date]. Receipts: (i) Letter of Bid (signed by [authorized_signatory_name]); (ii) EMD BG of ₹X.XX cr from [bg_issuing_bank], valid until [bg_expiry_date]; (iii) Priced BoQ with N line items. Evaluation will be completed per AP Procurement timeline; expect outcome notification within X days."
**Predicted current corpus**: **27 acknowledgements** (one per BidSubmission). Deferred to future M4.x — focus M4.2 on post-evaluation communications.

### 3.9 INTERNAL_ROUTING (Clerk → Dealing Officer → Department Head, internal workflow)

**When emitted**: triggered by EligibilityMatrix completion per tender. Routes the evaluation summary to the next internal role for review.
**Source findings**: ComparativeStatement (per tender) + all EligibilityMatrix rows for that tender.
**Drilldown chain**: ComparativeStatement → all 9 EligibilityMatrix rows for the tender.
**Template body**: "Tender [name] evaluation complete. Distribution: N QUALIFIED, M DISQUALIFIED, etc. Effective L1: [name]. Anomaly summary: K cartel-suspect pairs, P ALB candidates. Forwarded to [next role] for review and approval. Drilldown: ComparativeStatement [node_id]."
**Predicted current corpus**: **3 routings** (one per tender). Deferred — internal routing workflow not yet built.

---

## 4. Bilingual Output Strategy

### 4.1 English primary (default for M4.2)

All M4.2 drafters emit English-only Communication kg_nodes. `content_en` populated, `content_te` = null. `language = "EN"`. This covers the demo + initial production posture for English-fluent committee members and internal officers.

### 4.2 Telugu via Sarvam-M API (production target, future M4.x)

For bidder-facing communications (DISQUALIFICATION / ALB_JUSTIFICATION / DOC_REVIEW / AWARD / REGRET / BID_ACK), Telugu is mandated by AP State language policy for bidders who select `preferred_language = "Telugu"` or `"Both"` on portal registration. B9 is the only synthetic bidder with `preferred_language = "Both"`.

**Implementation plan (deferred):**
1. After `content_en` composed, call Sarvam-M `/translate` endpoint with `source_language=en`, `target_language=te`, `mode=formal`.
2. Pseudonymise recipient PII (company name, signatory name, contact details, NIT no) BEFORE the external API call — replace with placeholders `<COMPANY>`, `<SIGNATORY>`, `<NIT>`. Substitute back after translation.
3. Store both `content_en` + `content_te` on the Communication kg_node.
4. Render bilingual DOCX/PDF artifacts (English first column, Telugu second column, side-by-side per paragraph) when `language = "EN+TE"`.

**Demo-only fallback (no Sarvam-M API key required):** template-based Telugu translation for the 9 fixed templates. Translator: hand-translated paragraph templates with `{placeholder}` substitution. Acceptable for demo; not production-quality for arbitrary content. Deferred to M4.x.

### 4.3 DPDP compliance — pseudonymisation before external translation

Per DPDP Act 2023 §7 (purpose limitation) and §8 (collection limitation), bidder PII (signatory names, registered company name with proprietorship link, communication address, mobile, email) must not be shipped to external translation APIs without explicit consent. Sarvam-M's API hosts data on Sarvam servers (Indian, in-country, but distinct from AP State infrastructure). DPDP compliance posture:

1. **Pseudonymise before**: replace all PII tokens with `<TOKEN_N>` placeholders.
2. **Submit pseudonymised text to Sarvam-M** for translation.
3. **De-pseudonymise after**: substitute original PII back into the Telugu output.
4. **Log every external API call** with `audit_id` + outbound payload hash + Sarvam-M response hash.
5. **Cache aggressively**: identical pseudonymised templates (e.g. the 9 fixed Communication templates) need translation only ONCE; future emissions reuse cached Telugu paragraphs.

This pattern keeps PII inside AP infrastructure; only template phraseology (already public) crosses the API boundary. Sentinel logging is required for vigilance/RTI defensibility.

---

## 5. Channel Routing Logic

Channel selected from `BidderProfile.preferred_notification_channel`:

| preferred_channel | Communication.channel | Behaviour |
|---|---|---|
| `email` | EMAIL | render to email body; ship via SMTP (deferred); store recipient_email |
| `sms` | SMS | render to 160-char summary + URL link to portal artifact; ship via SMS gateway (deferred) |
| `portal` | PORTAL | post to bidder's portal notification queue (deferred) |
| (null / unset) | EMAIL | default fallback; only if email_primary present |
| `post` | POST | render to PDF for printing + courier dispatch (deferred — printing layer); store recipient_postal_address |

For M4.2 pilots: all communications use `channel = EMAIL` (synthetic bidders all have `email_primary` populated). Multi-channel routing is a future M4.x sub-block.

**Internal recipients (CARTEL_REVIEW + FLAGGED + INTERNAL_ROUTING)** always use `channel = PORTAL` — they post to the internal officer's portal queue, not email (internal email distribution is a future sub-block).

---

## 6. Audit Log Integration

Every Communication kg_node carries:
- **audit_id**: 16-char hex SHA256 of `f"{communication_type}|{recipient_bidder_profile_id}|{tender_id}|" + sorted(source_finding_node_ids).join(',')`. Deterministic — identical inputs produce identical audit_id across re-runs. This enables idempotent re-emission and audit replay.
- **source_finding_node_ids[]**: array of kg_node UUIDs that ground every claim in `content_en`. Reviewer can query each node_id to verify the underlying finding.

### 6.1 Forward drilldown (Communication → findings)

Standard audit-defense query: "Given Communication X, what findings support each claim?"

```sql
-- Pseudocode against kg_nodes.properties JSONB
SELECT properties->>'source_finding_node_ids' FROM kg_nodes WHERE node_id = '<comm_node_id>';
-- Then for each finding_id, fetch:
SELECT node_id, node_type, properties FROM kg_nodes WHERE node_id IN (<finding_ids>);
```

### 6.2 Reverse drilldown (finding → all Communications citing it)

RTI-friendly query: "What communications were generated from BidEvaluationFinding Y?"

Implementation: `scripts/m4_drafters/query_communication_audit_trail.py` — given a finding_node_id, fetches all Communication kg_nodes whose `properties->>'source_finding_node_ids'` JSONB array contains the supplied UUID.

```python
SELECT node_id, label, properties->>'communication_type', properties->>'audit_id'
FROM kg_nodes
WHERE node_type = 'Communication'
  AND properties->'source_finding_node_ids' ? '<finding_uuid>';
```

(Supabase REST: `properties->source_finding_node_ids=cs.[<uuid>]` — array contains operator.)

This query is built in M4.3.

### 6.3 Determinism + idempotency

Identical evaluation state → identical audit_ids → same source_finding_node_ids → same content (template + finding citations). Re-running a drafter overwrites the prior Communication row (matched by audit_id), no duplicates. Pattern: `_delete_prior_communications(communication_type=X)` clears prior rows for the drafter before re-emitting.

---

## 7. Role-Based Sender Attribution

`Communication.sender_role` enum:

| sender_role | when set | M4.2 status |
|---|---|---|
| `SYSTEM` | drafters in M4.2 auto-emit; no human attribution | M4.2 default |
| `CLERK` | future M4.x — manual draft entry by Clerk-grade officer | deferred |
| `DEALING_OFFICER` | future M4.x — Dealing Officer prepares/reviews | deferred |
| `DEPARTMENT_HEAD` | future M4.x — final approval signature | deferred |

M4.2 only uses SYSTEM. The role-based workflow (Clerk → Dealing Officer → Department Head approval ladder) requires:
- A workflow engine kg_node type or status transitions on Communication
- Review queue queries per role
- Approval-event log
- Signature-block templating per signing role
- All deferred to M4.4+.

---

## 8. DPDP Compliance Posture

Beyond the §4.3 pseudonymisation-before-translation rule, the broader DPDP posture for Module 4:

1. **Purpose limitation**: Communications are generated FOR the procurement evaluation purpose declared at tender publication. No use for unrelated purposes (marketing, etc.).
2. **Storage limitation**: Communication kg_nodes retained for 7 years per CVC vigilance retention norms; auto-archive thereafter.
3. **Right to access**: bidders can query `source_finding_node_ids[]` via portal and view supporting findings (reverse drilldown helper).
4. **Right to correction**: if a bidder disputes a DISQUALIFICATION, the appeal triggers a `defeated=True` flag on the Communication + creation of a corrected Communication referencing the original via `defeats` array. Sentinel-preservation rule applies.
5. **Right to erasure**: ON appeal-and-overturn, the original Communication is NOT deleted (audit defensibility) but marked `status=WITHDRAWN`.
6. **Data minimisation**: only the finding node_ids and rule citations are embedded; no PII beyond what the bidder already supplied via Statement-IV (Bidder Details).
7. **Cross-border**: no cross-border data transfers. Sarvam-M is India-hosted; verified before integration.
8. **Notice**: bidders consent to electronic communications at portal registration; opt-out for non-statutory communications (REGRET letters can be suppressed if bidder preference indicates).

---

## 9. Predicted Communication Outputs (current corpus, post-Ext-8)

The actual evaluation state determines the predicted communication batch. Verified against EligibilityMatrix + TenderRanking + ComparativeStatement + BidAnomalyFinding queries:

| Type | Recipients × Tenders | Count | M4.2 covered? |
|---|---|---:|---|
| DISQUALIFICATION | B2 × 3 + B3 × 3 | **6** | ✓ M4.2 pilot |
| AWARD | B9 × 3 (effective L1) | **3** | ✓ M4.2 pilot |
| ALB_JUSTIFICATION | B8 × 3 | **3** | ✓ M4.2 pilot |
| CARTEL_REVIEW | (B6+B7) × 3 (internal vigilance) | **3** | future M4.x |
| FLAGGED | B4 × 3 (internal committee) | **3** | future M4.x |
| DOC_REVIEW | B5 × 3 | **3** | future M4.x |
| REGRET | B1 × 3 | **3** | future M4.x |
| BID_ACK | 27 BidSubmissions | **27** | future M4.x (pre-evaluation) |
| INTERNAL_ROUTING | 3 tenders | **3** | future M4.x |
| **M4.2 pilot total** | | **12** | DISQUAL+AWARD+ALB |
| **Full corpus total** | | **54** | when all 9 types built |

M4.2 pilot batch ships 3 of 9 types → 12 of 54 communications. M4.x sub-blocks fill in the remaining 6 types incrementally per directive priority.

---

## 10. Out of Scope (Explicitly Deferred)

Items NOT in M4.1 or M4.2/M4.3 scope; deferred to future Module 4 sub-blocks or future modules:

- **Actual sending** — no SMTP, no SMS gateway, no portal API integration. M4.2 emits DRAFT Communication kg_nodes + Markdown artifacts. M4.4+ adds approval workflow + sending.
- **Telugu translation via Sarvam-M API** — design integrated above; implementation requires Sarvam-M API key + DPDP pseudonymisation logic + cache layer.
- **Approval workflow (Clerk → Dealing Officer → Department Head)** — multi-role status transitions; deferred to M4.5+.
- **6 remaining communication types** — CARTEL_REVIEW, FLAGGED, DOC_REVIEW, REGRET, BID_ACK, INTERNAL_ROUTING. Built incrementally in M4.x sub-blocks.
- **Portal API integration** — apeprocurement.gov.in (or successor portal) API for bidder notification queue + bid_ack receipt + status update. Out of synthetic corpus; needs live portal credential.
- **SMS gateway integration** — requires SMS provider (TextLocal / Karix / etc.) + per-SMS cost budget + DLT registration.
- **Email delivery infrastructure** — SMTP server config + bounce handling + delivery receipt logging.
- **PDF rendering for Communications** — possible via L75 reportlab pattern; deferred to M4.4+ (M4.3 ships only DOCX since communication artifacts are typically circulated via DOCX for committee markup).
- **Bilingual side-by-side rendering** — DOCX/PDF that shows English + Telugu columns; requires Sarvam-M live integration.
- **Communication delivery state tracking** — SENT / FAILED / RETRY workflow; deferred to M4.4+.

---

## Spec status

| element | status |
|---|---|
| Document scope | DESIGN-ONLY |
| Data layer changes | NONE during M4.1 (Communication kg_nodes are seeded in M4.2) |
| Drafter code changes | NONE during M4.1 |
| Successor sub-blocks | M4.2 (3 drafters) → M4.3 (DOCX + audit) → M4.x (Telugu, sending, remaining 6 types) |
| Schema mutation | NONE (kg_nodes.properties is JSONB; Communication type is additive) |
| Sentinel preservation | 154 / 351 / 49 / 27 / 3 / 6 / 3 unchanged during M4.1 |
| Output artifact | this Markdown spec only |
