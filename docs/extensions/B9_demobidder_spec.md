# B9 DemoBidder — Design Specification

**Sub-block:** Module 3 Extensions — Ext-7 (design-only)
**Predecessor:** Module 3 core complete (commit `6009edd`)
**Successors:** Ext-1 through Ext-6 (implementation), Ext-8 (seed + run)
**Status:** SPECIFICATION (no data layer changes; no validator code)
**Purpose:** define the design target Ext-1 through Ext-6 build to satisfy

---

## 1. Context

Module 3 core shipped 10 Tier-2 validators (240 BidEvaluationFinding rows) + 4 aggregators (EligibilityMatrix, TenderRanking, CrossBidAnomalyDetector, ComparativeStatementGenerator) over 7 sub-blocks. The corpus exercises the platform on 8 bidder profiles (B1–B8) across 3 synthetic AP Works tenders, producing 3 demo-visible evaluation-committee reports.

The Extensions series (Ext-1 → Ext-8) builds general platform improvements surfaced by Marine Works evaluation reference reading — improvements that apply to ANY procurement evaluation, not just marine projects. These improvements need a design target: a comprehensive bidder profile that exercises every standard evaluation check end-to-end.

**Ext-7 defines that target — B9 — and stops there.** No data inserts, no validator code, no seed-script changes. The spec becomes the contract Ext-1 through Ext-6 build to satisfy; Ext-8 then seeds B9 and runs the full pipeline.

B9 design intent: **"platform performs comprehensive standard evaluation"** — B9 passes every existing Tier-2 validator (already QUALIFIED) AND every new Extension validator (Ext-1 JV/Consortium, Ext-2 compliance docs, Ext-3 dual turnover, Ext-4 ABC variants, Ext-5 solvency variance, Ext-6 counter-signature). After Ext-8 seeds B9 and re-runs the pipeline, B9 becomes the effective L1 on all 3 tenders.

---

## 2. Schema Migration Table

Total new fields: **~25 at BidderProfile level + 4 per similar_works[] entry**. Schema growth: 34 → ~59 fields per BidderProfile. JSONB storage means no migration; `properties.<new_field>` is additive on `kg_nodes`.

**Backfill rules**: each Extension's commit applies safe defaults to B1–B8 preserving existing behavior. New-field introductions for B9 only are explicitly marked.

### Ext-1 JV/Consortium fields (6 new BidderProfile fields)

| field | type | B1–B8 backfill | B9 (JV) | B9 partners (B9.lead / p2 / p3) |
|---|---|---|---|---|
| `bidder_type` | enum SOLE_BIDDER / JV / CONSORTIUM / JV_PARTNER | `SOLE_BIDDER` | `JV` | `JV_PARTNER` |
| `lead_partner_id` | string \| null | `null` | `bid_synth_profile_b9_lead` | `null` (partners themselves don't reference a lead) |
| `partner_ids[]` | array of profile_ids | `[]` | `[bid_synth_profile_b9_lead, b9_p2, b9_p3]` | `[]` |
| `jv_agreement_node_id` | UUID \| null | `null` | `<JV agreement kg_node UUID>` | `null` |
| `jv_agreement_validity_until` | date \| null | `null` | `2027-12-31` | `null` |
| `liability_terms` | enum JOINT_AND_SEVERAL / OTHER \| null | `null` | `JOINT_AND_SEVERAL` | `null` |

### Ext-2 Compliance document fields (~14 new BidderProfile fields, status+value pairs)

| field | type | B1–B8 backfill | B9 value |
|---|---|---|---|
| `company_reg_cert_status` | enum VALID / EXPIRED / MISSING | `VALID` | `VALID` |
| `company_reg_cert_node_id` | UUID \| null | `null` (synthetic-no-doc) | `<UUID of company reg cert kg_node>` |
| `pan_cert_status` | enum | `VALID` | `VALID` |
| `pan_cert_value` | string | existing `pan` field | B9's PAN |
| `gst_cert_status` | enum | `VALID` | `VALID` |
| `gst_cert_value` | string | existing `gstin` field | B9's GSTIN |
| `epf_esi_cert_status` | enum | `VALID` | `VALID` |
| `epf_esi_cert_value` | string \| null | `null` | `EPF/AP/B9JV/2026/001234` |
| `form_12_declaration_status` | enum SIGNED / MISSING / DEFECTIVE | `SIGNED` | `SIGNED` |
| `tender_fee_receipt_status` | enum VALID / MISSING / DEFECTIVE | `VALID` | `VALID` |
| `tender_fee_amount_cr` | numeric \| null | `null` | `0.10` per tender (₹10 lakh) |
| `poa_status` | enum VALID / EXPIRED / MISSING / NOT_REQUIRED | `NOT_REQUIRED` (sole bidder) | `VALID` (JV Form-15 POA) |
| `dsc_status` | enum VALID / EXPIRED / MISSING | `MISSING` (back-dated profiles) | `VALID` |
| `dsc_expiry_date` | date \| null | `null` | `2027-06-30` |

### Ext-3 Dual turnover fields (3 new BidderProfile fields)

| field | type | B1–B8 backfill | B9 (Lead Partner alone) |
|---|---|---|---|
| `construction_turnover_5yr_avg_cr` | numeric | **rename + copy** from existing `average_5yr_turnover_cr` | `260.0` (clears HC's ₹243.4cr PQ floor) |
| `financial_turnover_3yr_avg_cr` | numeric \| null | `null` (NEW for all) | `230.0` |
| `turnover_methodology_note` | text \| null | `null` | `"Construction turnover from Section A of P&L (5yr avg); Financial turnover from audited balance sheets per IT Act Sec 44AB (3yr avg)"` |

### Ext-4 ABC formula fields (2 new BidderProfile fields)

| field | type | B1–B8 backfill | B9 |
|---|---|---|---|
| `abc_formula_M_method` | enum AP_GO_062_M2 / MPW_M3 / OTHER | `AP_GO_062_M2` (existing M=2) | `AP_GO_062_M2` |
| `abc_formula_rule_source` | string | `AP-GO-062` | `AP-GO-062` |

### Ext-5 Solvency variance fields (3 new BidderProfile fields)

| field | type | B1–B8 backfill | B9 |
|---|---|---|---|
| `solvency_cert_validity_window_months` | numeric | `12` (default per AP) | `12` |
| `solvency_cert_source_rule` | enum AP_GO_089_12MO / MPW25_3MO / OTHER | `AP_GO_089_12MO` | `AP_GO_089_12MO` |
| `solvency_methodology_note` | text \| null | `null` | `"12-month window per AP-GO-089 Section 4(b); cert issued by Tahsildar per state default"` |

### Ext-6 Counter-signature fields (4 NEW fields per similar_works[] entry, not BidderProfile-level)

| field | type | B1–B8 backfill (per work) | B9 works |
|---|---|---|---|
| `client_type` | enum GOVT / PSU / PRIVATE | `GOVT` (all existing works are APIIC / APCRDA / AP-HCJ / AP Public Works) | `GOVT` |
| `counter_signature_status` | enum EE_SIGNED / SE_SIGNED / NOT_REQUIRED / MISSING | `EE_SIGNED` | `EE_SIGNED` |
| `tds_certificate_node_id` | UUID \| null | `null` (NOT_REQUIRED for GOVT) | `null` (B9 works are GOVT) |
| `supporting_completion_certificate_node_id` | UUID \| null | `null` | `<UUID per work>` |

### Schema growth summary

| layer | B1–B8 before | B1–B8 after backfill | B9 |
|---|---:|---:|---:|
| BidderProfile data-bearing fields | 34 | ~59 (≈25 new + 0 removed) | ~59 |
| Per similar_works[] entry | 7 fields | 11 fields (+4 Ext-6) | 11 |

---

## 3. B9 JV Entity Full Specification

**B9 is the JV — the bidding entity that submits BidSubmission rows. Its 3 partners are separate BidderProfile nodes with `bidder_type=JV_PARTNER` that JV-bidders reference via `lead_partner_id` and `partner_ids[]`. JV partners do NOT submit bids themselves.**

| field | value |
|---|---|
| `profile_id` | `bid_synth_profile_b9` |
| `company_name` | `M/s Comprehensive Standard Builders JV (Premier Coastal + Northern Engineering + Southern Surveys)` |
| `bidder_type` | `JV` |
| `lead_partner_id` | `bid_synth_profile_b9_lead` |
| `partner_ids` | `[bid_synth_profile_b9_lead, bid_synth_profile_b9_p2, bid_synth_profile_b9_p3]` |
| `jv_agreement_node_id` | `<UUID of JV Agreement document>` |
| `jv_agreement_validity_until` | `2027-12-31` |
| `liability_terms` | `JOINT_AND_SEVERAL` |
| `gstin` | `37AAACJ9999J9Z9` (JV's combined GSTIN) |
| `pan` | `AAACJ9999J` |
| `contractor_class` | `Special` (effective; satisfied via Lead Partner alone per AP-GO-092) |
| `registration_state` | `AndhraPradesh` |
| `registration_authority` | `AP State Government per JV Agreement clause 4.2; Lead Partner per GO Ms No 94/2003` |
| `registration_certificate_no` | `AP/SC/B9JV/2026/0001` |
| `registration_valid_until` | `2027-12-31` (matches JV agreement validity) |
| `primary_business` | `Civil construction (collective: buildings + piling + surveying)` |
| `years_in_business` | `5` (JV inception); 22 / 14 / 11 across 3 partners individually |
| **Ext-3 turnover** | |
| `construction_turnover_5yr_avg_cr` | `260.0` (Lead Partner alone — clears HC's ₹243.4cr) |
| `financial_turnover_3yr_avg_cr` | `230.0` (Lead Partner alone) |
| `turnover_methodology_note` | per Ext-3 spec |
| **legacy fields (Ext-3 rename target)** | |
| `average_5yr_turnover_cr` | `260.0` (kept for backward compat; Ext-3 reads `construction_turnover_5yr_avg_cr`) |
| **ABC** | |
| `max_completed_works_value_cr` | `160.0` (Lead Partner's largest single-year executed civil works) |
| `existing_commitments_cr` | `100.0` (Lead Partner) |
| `abc_M_multiplier` | `2` (legacy field — retained) |
| `abc_formula_M_method` | `AP_GO_062_M2` |
| `abc_formula_rule_source` | `AP-GO-062` |
| **Solvency** | |
| `solvency_cert_source` | `Tahsildar` |
| `solvency_cert_validity_months_ago` | `4` |
| `solvency_cert_validity_window_months` | `12` |
| `solvency_cert_source_rule` | `AP_GO_089_12MO` |
| `solvency_methodology_note` | per Ext-5 spec |
| **Eligibility flags** | |
| `litigation_count` | `0` (clean across all 3 partners) |
| `blacklist_status` | `clean` |
| `equipment_register_completeness` | `full_owned` (collective across partners; verified Statement-V) |
| `key_personnel_count` | `6` (collective across partners) |
| **Module 4 fields** | |
| `email_primary` | `bidder9@example.com` |
| `mobile_primary` | `+91-9000000009` |
| `preferred_notification_channel` | `email` |
| `preferred_language` | `Both` (Telugu + English) |
| `portal_username` | `comprehensive-standard-builders-jv` |
| `portal_credential_hash` | `synth_hash_b9_jv` |
| `portal_credential_status` | `active` |
| `past_blacklist_events` | `[]` |
| `past_tender_participation` | `[2 won + 1 lost + 0 disqualified across JV's combined 2-yr history]` |
| `past_anomaly_flags` | `[]` |
| `authorized_signatory_name` | `Mr. C. Comprehensive` (JV Coordinator — initial 'C.' is unique across all 9 bidders; avoids cartel signature-pattern signal) |
| `authorized_signatory_role` | `JV Coordinator (designated by Form-15 POA)` |
| `communication_address` | `Plot 27, MVP Colony, Visakhapatnam-530017` (unique; not shared with any other bidder) |
| **Ext-2 compliance docs (14 fields)** | per spec, all VALID/SIGNED |
| **Behavior flags** (seed-script meta) | `_premium_pct_delta=-6.0`, `_similar_works_pattern="three_full"`, `_boq_complete=True`, `_emd_bg_anomalous=False`, `_solvency_buffer_mult=1.5`, `_skip_statement_vi=False`, `_boq_line_item_count=312` |

---

## 4. JV Partner Sub-Profiles

3 separate BidderProfile kg_nodes with `bidder_type=JV_PARTNER`. They carry partner-specific resources that the JV collectively claims. They are NOT consumed by BidSubmission-based aggregator queries (no BidSubmission references them as `bidder_profile_id`).

### B9.lead — M/s Premier Coastal Construction Pvt Ltd

| field | value |
|---|---|
| `profile_id` | `bid_synth_profile_b9_lead` |
| `company_name` | `M/s Premier Coastal Construction Pvt Ltd` |
| `bidder_type` | `JV_PARTNER` |
| `contractor_class` | `Special` |
| `construction_turnover_5yr_avg_cr` | `260.0` |
| `financial_turnover_3yr_avg_cr` | `230.0` |
| `max_completed_works_value_cr` | `160.0` |
| `existing_commitments_cr` | `100.0` |
| `equipment_role_in_jv` | `Batching plant + tower crane + excavator (3 critical items)` |
| `personnel_roles_in_jv` | `Project Manager + Site Engineer + QA Engineer (3 of 6 roles)` |
| `similar_works_contributed` | 3 large GOVT works (hospital + edu block + court building) |
| `solvency_cert_source` | `Tahsildar` |
| `solvency_cert_validity_months_ago` | `4` |
| `litigation_count` | `0` |
| `blacklist_status` | `clean` |
| `past_blacklist_events` | `[]` |
| `communication_address` | `Plot 27, MVP Colony, Visakhapatnam-530017` |
| `authorized_signatory_name` | `Mr. C. Comprehensive` (Lead Partner's principal — also JV Coordinator) |

### B9.p2 — M/s Northern Engineering Pvt Ltd

| field | value |
|---|---|
| `profile_id` | `bid_synth_profile_b9_p2` |
| `company_name` | `M/s Northern Engineering Pvt Ltd` |
| `bidder_type` | `JV_PARTNER` |
| `contractor_class` | `Class-I` |
| `construction_turnover_5yr_avg_cr` | `80.0` |
| `financial_turnover_3yr_avg_cr` | `65.0` |
| `equipment_role_in_jv` | `Piling rigs (2 units) + 250 kVA generators (3 units)` |
| `personnel_roles_in_jv` | `Safety Officer + MEP Engineer (2 of 6 roles)` |
| `similar_works_contributed` | 2 piling-specific GOVT works |
| `solvency_cert_source` | `Bank` |
| `solvency_cert_validity_months_ago` | `6` |
| `litigation_count` | `0` |
| `blacklist_status` | `clean` |
| `communication_address` | `2-3-45, Engineers Layout, Hyderabad-500017` |
| `authorized_signatory_name` | `Mr. N. Northern` |

### B9.p3 — M/s Southern Surveys & Services Pvt Ltd

| field | value |
|---|---|
| `profile_id` | `bid_synth_profile_b9_p3` |
| `company_name` | `M/s Southern Surveys & Services Pvt Ltd` |
| `bidder_type` | `JV_PARTNER` |
| `contractor_class` | `Class-I` |
| `construction_turnover_5yr_avg_cr` | `45.0` |
| `financial_turnover_3yr_avg_cr` | `35.0` |
| `equipment_role_in_jv` | `Total-station + GPS surveying equipment + drone-mapping setup` |
| `personnel_roles_in_jv` | `Surveyor + Total-Station Operator (1 of 6 unique roles; supports collective)` |
| `similar_works_contributed` | 1 surveying-specific GOVT contract |
| `communication_address` | `7-1-12, Surveyors Building, Chennai-600017` |
| `authorized_signatory_name` | `Mr. S. Southern` |

**Address diversity check**: 4 distinct addresses across B9 JV + 3 partners (Visakhapatnam / Hyderabad / Chennai / shared Lead-with-JV) — zero overlap with B1–B8 addresses → no false-positive cartel signals.

---

## 5. B9 Bid Submission Shape Per Tender

3 BidSubmissions for B9 (one per tender). Each carries the JV's combined bid + references to lead partner's specific data.

| field | Kurnool | JA | HC |
|---|---:|---:|---:|
| `bidder_profile_id` | `bid_synth_profile_b9` | same | same |
| `tender_id` | `tender_synth_kurnool` | `tender_synth_ja` | `tender_synth_hc` |
| `bid_amount_cr` (premium −6%) | **79.90** | **117.97** | **343.25** |
| `tender_ecv_cr` | 85.00 | 125.50 | 365.16 |
| `signature_date` | 2026-05-10 | 2026-05-10 | 2026-05-10 |
| Ext-1 metadata | `jv_agreement_ref`, `poa_form_15_ref`, `partner_role_declarations` | same | same |

### Premium check vs ranking dynamics (with B9 added → 5 QUALIFIED bidders)

| Premium % | Bid_amount_cr / ECV | Kurnool bid | JA bid | HC bid |
|---|---:|---:|---:|---:|
| B8 −38% | 0.620 | 52.70 | 77.81 | 226.40 |
| B9 −6% | 0.940 | **79.90** | **117.97** | **343.25** |
| B1 −5% | 0.950 | 80.75 | 119.22 | 346.90 |
| B6 −3.10% | 0.969 | 82.36 | 121.61 | 353.84 |
| B7 −3.05% | 0.9695 | 82.41 | 121.67 | 354.02 |

Sorted ascending: **B8 → B9 → B1 → B6 → B7**.

### ALB threshold sanity check (5 QUALIFIED bidders)

Average premium across 5: (−5 − 3.10 − 3.05 − 38 − 6) / 5 = **−11.03%** → average bid = ECV × 0.8897.
ALB threshold = average × 0.80 = **ECV × 0.7118**.

| bidder | bid / ECV | < ALB threshold (0.7118)? |
|---|---:|---|
| B8 | 0.62 | **YES — still ALB** ✓ |
| B9 | 0.94 | no ✓ |
| B1 | 0.95 | no ✓ |
| B6 | 0.969 | no ✓ |
| B7 | 0.9695 | no ✓ |

→ alb_candidates remains `[bid_synth_profile_b8]`; `alb_action_required = True`. B9 doesn't accidentally trigger ALB.

### B9's 10 Statements (composed from JV-collective + Lead Partner data)

| Statement | source of data | B9 value |
|---|---|---|
| **I — Annual Turnover** | Lead Partner alone | avg ₹260cr, 5 FY series around 260 |
| **II — Similar Works** | Combined (3 from Lead + 2 from P2 + 1 from P3) | 3 hospital/edu/court works satisfying 3@40% / 2@50% / 1@80% comfortably |
| **III — Satisfactory Completion** | Mirrors Statement II | 6 listed works, all ≥80% compliance |
| **IV — Bidder Details** | JV identity + partner roster | bidder_type=JV, JV-agreement ref, partner roles |
| **V — Critical Equipment** | Collective across 3 partners | full_owned register (3 Lead + 2 P2 + 1 P3 critical items) |
| **VI — Key Personnel** | Collective across 3 partners | 6/6 roles filled (3 from Lead, 2 from P2, 1 from P3) |
| **VII — Litigation** | Combined: 0 cases across all 3 partners | 0 |
| **VIII — Financial Solvency** | Lead Partner cert | Tahsildar 4mo, declared ₹1.5cr ≥ required ₹1.0cr (Special class) |
| **IX — Work Plan** | JV-collective methodology + milestones | comprehensive 18/24-month delivery plan |
| **X — Bid Capacity** | Lead Partner A=160, N=2, M=2 → ABC=540cr | clears HC's 365.16cr; clears JA's 125.5cr; clears Kurnool's 85cr |

### B9 Supplementary nodes

- **LetterOfBid**: bid_amount per tender per the −6% premium; signing_authority="Mr. C. Comprehensive, JV Coordinator (per Form-15 POA)"; bid_validity_days=90; signature_date=2026-05-10
- **EMD_BG**: bg_issuing_bank=`State Bank of India, Visakhapatnam Main Branch` (distinct branch string from B6/B7's `State Bank of India, Vijayawada Main Branch` → cartel COMMON_BANK_BRANCH signal correctly doesn't fire on B9; contributes to L74 bank-diversity demo); bg_validity_180_days=True; bg_unconditional=True
- **PricedBoQ**: total = bid_amount; line_item_count=312; each_page_signed=True; rates_in_figures_and_words_consistent=True

---

## 6. Per-Extension Contracts

Each Extension implementation must produce a `BidEvaluationFinding` for B9 with `verdict=QUALIFIED` per the contract below.

### Ext-1 — JV/Consortium Validator

**Validator name**: `scripts/bid_jv_consortium_check.py`
**Typology code**: `Bidder-JV-Consortium-Compliance`
**Rule anchors**: AP-GO-094 (JV qualification — verify in rules table; if missing, seed-and-flag per Sub-block 3a Batch 1 precedent) + relevant MPW JV clause

**Validator path**:
- IF `bidder_type == "SOLE_BIDDER"`: SKIP_NOT_APPLICABLE (rule doesn't apply)
- ELIF `bidder_type == "JV"` OR `bidder_type == "CONSORTIUM"`:
  - Verify `jv_agreement_node_id` present AND `jv_agreement_validity_until >= bid_submission_date`
  - Verify `liability_terms == "JOINT_AND_SEVERAL"` (per AP-GO/MPW standard)
  - Verify Lead Partner's `construction_turnover_5yr_avg_cr` ≥ tender PQ floor (Lead-Partner-alone-financial-criterion)
  - Verify `poa_status == "VALID"` (Form-15 POA mandatory)
  - Verify Lead Partner (`bid_synth_profile_<lead_partner_id>`) has `contractor_class == required_class`
  - Verify collective resources: equipment, personnel, similar works (per JV Agreement collective-claim terms)
  - Verify all partners' `blacklist_status == "clean"` (one debarred partner taints whole JV)

**B9's expected verdict**: `QUALIFIED`
- decision_reason: `qualified_jv_lead_partner_financials_met_AND_joint_and_several_AND_poa_valid_AND_collective_resources_complete_per_ap_go_094`

**Citation chain**: BidEvaluationFinding.properties carries `jv_agreement_node_id`, `lead_partner_profile_id`, `lead_partner_construction_turnover_cr`, `partner_blacklist_statuses[]`, `collective_equipment_complete`, `collective_personnel_filled`.

**Special considerations**:
- Lead-Partner-alone-financial-criterion is distinct from Tier-2 bid_turnover_check (which would read JV's BidderProfile.construction_turnover_5yr_avg_cr directly — for B9 JV that's already set to Lead Partner's value, so bid_turnover_check passes too).
- If any partner has past_blacklist_events with current_status="active", JV is INELIGIBLE regardless of Lead Partner cleanliness.

### Ext-2 — Compliance Document Checklist Validator

**Validator name**: `scripts/bid_compliance_documents_check.py`
**Typology code**: `Bidder-Compliance-Documents-Complete`
**Rule anchor**: MPW Section 4.4.1 (Tender Document Submission Requirements) — verify in rules table

**Validator path** (8 mandatory documents):
1. `company_reg_cert_status == "VALID"`
2. `pan_cert_status == "VALID"`
3. `gst_cert_status == "VALID"`
4. `epf_esi_cert_status == "VALID"`
5. `form_12_declaration_status == "SIGNED"` (no exceptions/deviations)
6. `tender_fee_receipt_status == "VALID"` (per tender)
7. IF `bidder_type IN (JV, CONSORTIUM)`: `poa_status == "VALID"` (Form-15 POA mandatory)
8. `dsc_status == "VALID"` (Digital Signature Certificate active) AND `dsc_expiry_date >= submission_date + bid_validity_days`

**Verdict logic**:
- QUALIFIED if all 8 (or 7 for SOLE_BIDDER) are present + valid
- INELIGIBLE-HARD_BLOCK if any required document is MISSING/DEFECTIVE/EXPIRED
- GAP if any status field is null (treat as "not assessed")

**B9's expected verdict**: `QUALIFIED`
- decision_reason: `qualified_all_8_compliance_docs_valid_including_jv_specific_poa_form_15`

### Ext-3 — Dual Turnover Criterion Validator

**Validator name**: `scripts/bid_dual_turnover_check.py` (OR extend `scripts/bid_turnover_check.py` with dual-mode logic)
**Typology code**: `Bidder-Dual-Turnover-Eligibility`
**Rule anchor**: CVC-028 (Construction turnover) + companion financial-turnover rule (verify in rules table; may need seeding)

**Validator path**:
- Read Statement I + Statement VIII (or BidderProfile.construction_turnover_5yr_avg_cr + financial_turnover_3yr_avg_cr)
- Verify `construction_turnover_5yr_avg_cr ≥ tender.construction_floor_cr` (existing PQ floor)
- Verify `financial_turnover_3yr_avg_cr ≥ tender.financial_floor_cr` (NEW; introduced in Ext-3)
- BOTH must pass — single-criterion failure → INELIGIBLE

**B9's expected verdict**: `QUALIFIED`
- decision_reason: `qualified_construction_260cr_above_ja_floor_AND_financial_230cr_above_ja_financial_floor`

**Special considerations**: For JV bidder, Construction = Lead-Partner-alone OR collective (per JV terms). B9 uses Lead-Partner-alone for both criteria.

### Ext-4 — Rule-Source-Aware ABC M Coefficient Validator

**Validator name**: extend `scripts/bid_abc_check.py`
**Typology code**: `Bidder-Capacity-Compliance` (existing typology; extension is on M validation)
**Rule anchor switching**: AP-GO-062 (M=2) OR MPW (M=3) OR OTHER per `abc_formula_M_method`

**Validator path**:
- Read `abc_formula_M_method` from BidderProfile
- IF `AP_GO_062_M2`: require M=2 exact; ABC = A × N × 2 − B
- IF `MPW_M3`: require M=3 exact; ABC = A × N × 3 − B
- IF `OTHER`: GAP_INSUFFICIENT_DATA (manual review required)
- Verify declared computed_abc matches recomputed (within tolerance)
- Verify ABC ≥ ECV

**B9's expected verdict**: `QUALIFIED`
- decision_reason: `qualified_method_AP_GO_062_M2_abc_540cr_above_ecv_hc_365.16cr`

**Special considerations**: Existing B1–B8 are M=2 → all map to AP_GO_062_M2 after backfill; behavior unchanged. B9 same.

### Ext-5 — Solvency Window Variance Validator

**Validator name**: extend `scripts/bid_solvency_check.py`
**Typology code**: `Bidder-Solvency-Compliance` (existing)
**Rule anchor switching**: AP-GO-089 (12mo window) OR MPW25 (3mo window) per `solvency_cert_source_rule`

**Validator path**:
- Read `solvency_cert_source_rule` from BidderProfile
- IF `AP_GO_089_12MO`: window = 12 months
- IF `MPW25_3MO`: window = 3 months
- Verify `solvency_cert_validity_months_ago ≤ window`
- Verify declared_solvency_cr ≥ required_solvency_cr (existing check unchanged)

**B9's expected verdict**: `QUALIFIED`
- decision_reason: `qualified_cert_Tahsildar_age_4mo_within_12mo_window_per_ap_go_089_declared_1.5cr_above_required_1.0cr`

### Ext-6 — Counter-Signature Verification Validator

**Validator name**: extend `scripts/bid_similar_works_check.py`
**Typology code**: `Bidder-Similar-Works-Qualification` (existing typology; extension verifies counter-signature)
**Rule anchor**: MPW-040 (existing) + counter-signature norm (verify in rules; CVC OM Vigilance Aspects covers this)

**Validator path**:
- For each `similar_works[]` entry:
  - IF `client_type == "GOVT"` OR `"PSU"`: require `counter_signature_status IN ("EE_SIGNED", "SE_SIGNED")`
  - IF `client_type == "PRIVATE"`: require `tds_certificate_node_id IS NOT NULL` (TDS cert proves payment + scope)
  - Verify `supporting_completion_certificate_node_id IS NOT NULL` (in all cases)
- A work failing counter-signature check is excluded from the 3/2/1 branch count
- Verdict: re-run MPW-040 3/2/1 logic on the filtered list

**B9's expected verdict**: `QUALIFIED`
- All 3 Lead Partner GOVT works have `counter_signature_status="EE_SIGNED"` + supporting completion cert UUIDs
- decision_reason: `qualified_branch_3at40pct_count_3_threshold_X_all_works_counter_signed_per_govt_norm`

**Special considerations**: Existing B1's works don't have these fields yet; Ext-6 backfills them as `client_type="GOVT"` + `counter_signature_status="EE_SIGNED"` (existing AP works are all GOVT clients per existing data) → no regression on B1 verdict.

---

## 7. Predicted Post-Extensions Evaluation Matrix for B9

After Ext-1 through Ext-6 land + Ext-8 seeds B9 + re-runs the pipeline:

### 16 outcomes per tender

| layer | validator | predicted verdict | severity / consequence |
|---|---|---|---|
| Tier-2 (10 existing) | bid_turnover_check | QUALIFIED | ADVISORY |
| | bid_class_check | QUALIFIED | ADVISORY |
| | bid_solvency_check | QUALIFIED | ADVISORY |
| | bid_blacklist_check | QUALIFIED | ADVISORY |
| | bid_abc_check | QUALIFIED | ADVISORY |
| | bid_similar_works_check | QUALIFIED | ADVISORY |
| | bid_emd_validity_check | QUALIFIED | ADVISORY |
| | bid_equipment_check | QUALIFIED | ADVISORY |
| | bid_personnel_check | QUALIFIED | ADVISORY |
| | bid_litigation_check | QUALIFIED | ADVISORY |
| Extensions (6 new) | bid_jv_consortium_check (Ext-1) | QUALIFIED | ADVISORY |
| | bid_compliance_documents_check (Ext-2) | QUALIFIED | ADVISORY |
| | bid_dual_turnover_check (Ext-3) | QUALIFIED | ADVISORY |
| | bid_abc_check (Ext-4 extension) | QUALIFIED | ADVISORY |
| | bid_solvency_check (Ext-5 extension) | QUALIFIED | ADVISORY |
| | bid_similar_works_check (Ext-6 extension) | QUALIFIED | ADVISORY |

**Aggregate per tender**: 16/16 QUALIFIED → `EligibilityMatrix.aggregate_verdict = QUALIFIED`

### 27-row EligibilityMatrix distribution after Ext-8

| bidder | Kurnool | JA | HC |
|---|---|---|---|
| B1 Premier | QUALIFIED | QUALIFIED | QUALIFIED |
| B2 Marginal | DISQUALIFIED | DISQUALIFIED | DISQUALIFIED |
| B3 Anomalous | DISQUALIFIED | DISQUALIFIED | DISQUALIFIED |
| B4 Borderline-Litigation | FLAGGED | FLAGGED | FLAGGED |
| B5 Incomplete-Documentation | MARK_FOR_DOC | MARK_FOR_DOC | MARK_FOR_DOC |
| B6 Cartel-Pair-A | QUALIFIED | QUALIFIED | QUALIFIED |
| B7 Cartel-Pair-B | QUALIFIED | QUALIFIED | QUALIFIED |
| B8 Abnormally-Low | QUALIFIED | QUALIFIED | QUALIFIED |
| **B9 JV** | **QUALIFIED** | **QUALIFIED** | **QUALIFIED** |

**Distribution**: 15 QUALIFIED + 3 FLAGGED + 3 MARK_FOR_DOC + 6 DISQUALIFIED = **27 EligibilityMatrix rows**.

---

## 8. Effective L1 Dynamics Post-B9-Seed

For each of 3 tenders, TenderRanking produces 5 QUALIFIED bidders sorted ascending by bid_amount_cr.

### Per-tender ranking + effective L1 derivation

| | Kurnool (ECV ₹85cr) | JA (ECV ₹125.5cr) | HC (ECV ₹365.16cr) |
|---|---|---|---|
| **L1** (raw) | B8 ₹52.70cr (−38%) ALB | B8 ₹77.81cr ALB | B8 ₹226.40cr ALB |
| **L2** (raw) | **B9 ₹79.90cr (−6%)** | **B9 ₹117.97cr (−6%)** | **B9 ₹343.25cr (−6%)** |
| **L3** (raw) | B1 ₹80.75cr (−5%) | B1 ₹119.22cr (−5%) | B1 ₹346.90cr (−5%) |
| **L4** (raw) | B6 ₹82.36cr (−3.10%) cartel | B6 ₹121.61cr cartel | B6 ₹353.84cr cartel |
| **L5** (raw) | B7 ₹82.41cr (−3.05%) cartel | B7 ₹121.67cr cartel | B7 ₹354.02cr cartel |

### effective_L1 skip chain per ComparativeStatement Sub-block 7 logic

1. raw L1 (B8) — `bidder in alb_candidates AND alb_action_required` → **skip ALB**
2. raw L2 (B9) — not in either skip list → **effective L1 = B9** ✓

### Result

**B9 wins effective L1 on all 3 tenders.** This is the demo-impressive payoff: "platform performs comprehensive standard evaluation; B9, the well-organized JV with all documents in order, emerges as effective L1 after raw L1 (B8 ALB) is rejected and cartel-pair (B6+B7) is referred."

### Cartel-pair detector check post-B9

5 QUALIFIED bidders → C(5,2) = 10 pairs per tender. B9's signal count with every other bidder:
- vs B1: 1 LOW (if both SBI; but B9=SBI Visakhapatnam ≠ B1=SBI Vijayawada → 0 signals)
- vs B6/B7: 0 signals (different address, different signatory pattern, different bank, premium delta >0.10%)
- vs B8: 0 signals (different address, signatory, premium delta 32%)

→ B9 doesn't trip any cartel-pair flag. The existing B6+B7 CARTEL_SUSPECT finding remains; no new findings from B9.

### ALB_CORROBORATION post-B9

B8's `alb_appearances_count` remains 3-of-3 → cross_tender_consistency=True; severity=HIGH (systemic). Same as before; B9 doesn't change this.

### Post-B9 anomaly findings count

6 BidAnomalyFinding rows (3 CARTEL + 3 ALB) — unchanged from pre-Ext-8 state (B9 introduces no anomalies; B9 IS the standard).

---

## 9. Pre-Ext-8 Prerequisites Checklist

Before Ext-8 runs the seed + full pipeline, verify:

| item | check | how to verify |
|---|---|---|
| Ext-1 through Ext-6 all merged on main | `git log --oneline main` shows 6 commits | manual git history scan |
| Schema backfilled across B1–B8 | New fields populated with safe defaults per per-Extension commit | `SELECT properties->>'bidder_type' FROM kg_nodes WHERE node_type='BidderProfile'` returns `SOLE_BIDDER` for all 8 |
| JSONB schema growth without migration | `kg_nodes.properties` is JSONB; additive | already confirmed (no schema column added) |
| `JV_PARTNER` node_type accepted (subtype of BidderProfile) | Wait — JV_PARTNER is `bidder_type` value, not `node_type`. `node_type` stays `BidderProfile`. | confirm via probe insert with `node_type=BidderProfile` + `properties.bidder_type=JV_PARTNER` |
| EligibilityMatrix aggregator excludes JV_PARTNER entities | Aggregator filters by `bid_submission_id` → JV partners have no BidSubmission → naturally excluded | verify post-Ext-8 by counting EligibilityMatrix rows: should be 9 × 3 = 27 (NOT 12 × 3 = 36 if partners leaked) |
| run_cross_bid_anomaly_detector handles 5 QUALIFIED bidders per tender | C(5,2) = 10 pairs per tender; existing logic enumerates pairs via `itertools.combinations` | confirm by running on B9-included corpus + verifying CARTEL_SUSPECT count stays at 3 (one per tender, B6+B7 only) |
| run_tender_ranking handles 5 QUALIFIED bidders | ranking[] length becomes 5; effective L1 computation in Sub-block 7 already handles arbitrary length | confirm via TenderRanking output post-Ext-8 |
| ComparativeStatement regeneration overwrites 3 .md + .docx files | `_delete_prior_comparative_statements` + file overwrite | run + verify file timestamps update |
| Sentinel test: no upstream-table drift during Ext-8 batch seed | Sentinel snapshot pre/post unchanged on Tier-1 ValidationFinding count (154) | run Ext-8, confirm `count(ValidationFinding)=154` post-run |

---

## 10. Out of Scope (Explicitly Deferred)

Items NOT included in B9 spec or Ext-1 through Ext-8; deferred to future series:

- **CONSORTIUM variant** — only JV in B9. Consortium-specific rules (e.g., joint billing across non-allied firms, lead-partner-rotation) queued for future iteration.
- **Blacklist-history-edge-case bidder** — e.g., B10 = previously debarred but rehabilitated; tests the rehabilitation-acceptance path. Queued.
- **Multi-JV competition** — only one JV (B9) on each tender. Demo of multiple JVs bidding simultaneously queued.
- **Marine / Highway / Building project-type discrimination** — Marine Works reference document inspired Extensions, but the Extensions are NOT marine-specific. Project-type discrimination is queued (similar disposition to Phase 1 sewer clauses — generalizable but not in current scope).
- **Subcontractor cross-bid signal** (Ext-6 originally proposed for COMMON_SUBCONTRACTOR) — requires Statement-V subcontractor list extension, which the synthetic data doesn't carry. Queued.
- **PDF rendering for ComparativeStatement** — L75 follow-up; parallel option to Extensions.
- **Module 4 (Communicator)** — separate sub-block sequence; B9's Module 4 fields are populated (Sub-block 1.2 L67) but Module 4's email/SMS/portal-notification logic isn't built yet.
- **Tier-2 tender-criterion node extraction** — prerequisite for scaling beyond synthetic 8 bidders to real corpus. Queued; out of Extensions scope.

---

## Spec status

| element | status |
|---|---|
| Document scope | DESIGN-ONLY |
| Data layer changes | NONE (B9 isn't seeded by this sub-block) |
| Validator code changes | NONE |
| Successor sub-blocks | Ext-1 → Ext-6 implement; Ext-8 seeds B9 + runs pipeline |
| Schema mutation | NONE during Ext-7 (JSONB additive when Extensions land) |
| Sentinel preservation | 154 / 240 / 45 / 24 / 3 / 6 / 3 unchanged |
| Output artifact | this Markdown spec only |
