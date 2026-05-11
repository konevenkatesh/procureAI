"""Module 3 Sub-block 1.1 + 1.2 — Synthetic bid data generator.

Bootstraps the data needed to develop and verify Tier-2 Bid Evaluator
validators (Sub-blocks 3-6) AND Module 4 communicator forward-compat.
NO actual Tier-2 validator runs in this script — pure data-layer seeding.

== Scope (Sub-block 1.2 — extended to 8 profiles) ==
  3 tenders (Kurnool, JA, HC — using stable synthetic tender_id
  references).

  8 bidder profiles per tender = 24 BidSubmission total:
    B1 — Clean Contractor                (Special, 250cr, clean → QUALIFIED)
    B2 — Marginal Class/Solvency         (Class-I, 80cr, stale → DISQUALIFIED)
    B3 — Anomalous                       (Special, 30cr, M=3, debarred → DISQUALIFIED)
    B4 — Borderline-Litigation           (clean except 1 litigation → FLAGGED)
    B5 — Incomplete-Documentation        (Statement-VI suppressed → MARK_FOR_DOC)
    B6 — Cartel-Pair-A                   (10/10 QUALIFIED; cartel signal vs B7)
    B7 — Cartel-Pair-B                   (10/10 QUALIFIED; cartel signal vs B6)
    B8 — Abnormally-Low                  (10/10 QUALIFIED; premium_pct=-38% ALB)

  10 Statement rows per bid in `fact_sheets` (B5 skips Statement-VI → 237 total).

  3 supplementary kg_nodes per bid (LetterOfBid + EMD BG + PricedBoQ) = 72.

  BidderProfile carries 13 Module 4 forward-compat fields per profile:
  email_primary, mobile_primary, preferred_notification_channel,
  preferred_language, portal_username, portal_credential_hash (synthetic
  placeholder ONLY — never real password material), portal_credential_status,
  past_blacklist_events[], past_tender_participation[], past_anomaly_flags[],
  authorized_signatory_name, authorized_signatory_role, communication_address.

== Data shape ==
  Path A + C (per diagnose):
    kg_nodes:
      • 3 BidderProfile nodes        (B1, B2, B3 — shared across tenders)
      • 9 BidSubmission nodes        (one per bidder × tender combo)
      • 27 supplementary nodes       (LetterOfBid + EMD + PricedBoQ per bid)
    fact_sheets:
      • 90 rows (10 Statements × 9 bids)
    kg_edges:
      • 9 SUBMITTED_BY               (BidSubmission → BidderProfile)
      • 9 BIDS_FOR_TENDER            (BidSubmission → tender stable id;
                                       link to TenderDocument node deferred
                                       until corpus / drafter doc_ids are
                                       resolved per-tender)

  Total new rows: 39 kg_nodes + 90 fact_sheets + 18 kg_edges = 147.

== Idempotent ==
  Re-runs safely: deletes prior rows by doc_id prefix `bid_synth_` (which
  covers all BidderProfile / BidSubmission / supplementary nodes + their
  fact_sheets + edges) before re-inserting. Deterministic seeds per
  bidder profile mean re-runs produce byte-identical content.

== Audit annotation ==
  Every fact carries `_designed_to_trip` in extracted_facts noting which
  Tier-2 validator outcome the value is engineered to exercise. Demo-
  defensibility: "why does B2 fail on HC?" answer is recorded inline.

Run:
    set -a && . ./.env && set +a
    /opt/homebrew/bin/python3.11 scripts/seed_synthetic_bids.py
"""
from __future__ import annotations
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import requests
from builder.config import settings


REST = settings.supabase_rest_url
H = {"apikey":        settings.supabase_anon_key,
     "Authorization": f"Bearer {settings.supabase_anon_key}",
     "Content-Type":  "application/json"}


# ── Tender catalogue ──────────────────────────────────────────────────

TENDERS = {
    "kurnool": dict(
        tender_id="tender_synth_kurnool",
        project_name="Construction of a new District Hospital at Kurnool",
        ecv_cr=85.0,
        duration_months=18,
        nit_no="100/PROC/APIIC/1/2026",
        required_class="Special",   # ECV >10cr → Special class needed
        pq_turnover_floor_cr=121.7,   # 2× annual contract value (CONSTRUCTION 5yr)
        pq_similar_works_threshold_pct=80,
        # Ext-3: financial 3yr PQ floor at 30% × ECV per CVC-028 minimum
        financial_pq_floor_cr=25.5,
    ),
    "ja": dict(
        tender_id="tender_synth_ja",
        project_name="Construction of Andhra Pradesh Judicial Academy",
        ecv_cr=125.5,
        duration_months=24,
        nit_no="130/MAU61-USI0HB(BG)/7/2026",
        required_class="Special",
        pq_turnover_floor_cr=83.7,    # 2× (125.5/3) (CONSTRUCTION 5yr)
        pq_similar_works_threshold_pct=80,
        # Ext-3:
        financial_pq_floor_cr=37.65,
    ),
    "hc": dict(
        tender_id="tender_synth_hc",
        project_name="Construction of the new Andhra Pradesh High Court complex",
        ecv_cr=365.16,
        duration_months=24,
        nit_no="HC/APCRDA/2026/PROC/001",
        required_class="Special",
        pq_turnover_floor_cr=243.4,   # 2× (365.16/3) (CONSTRUCTION 5yr)
        pq_similar_works_threshold_pct=80,
        # Ext-3:
        financial_pq_floor_cr=109.55,
    ),
}


# ── Bidder profiles (8 — shared across all 3 tenders) ─────────────────
#
# Per-profile behavior flags (refactor from endswith() pattern in builders):
#   _similar_works_pattern : "three_full" | "one_at_60pct" | "zero_works"
#   _boq_complete          : bool — False marks BoQ as incomplete + unsigned
#   _boq_line_item_count   : int  — number of priced line items on Form 51
#   _emd_bg_anomalous      : bool — True forces expired BG from non-Scheduled bank
#   _solvency_buffer_mult  : float — multiplier applied to required_solvency_cr
#                                    to compute declared_solvency_cr (≥1.0)
#   _skip_statement_vi     : bool — True suppresses Statement-VI fact_sheet
#                                    (forces bid_personnel_check GAP path)
#   _premium_pct_delta     : float — signed % deviation from ECV (LetterOfBid +
#                                    PricedBoQ); negative=under, positive=over
#
# Module 4 forward-compat fields (consumed by communicator module — added to
# all 8 profiles in a single seed pass per L67 cross-module discipline):
#   email_primary, mobile_primary, preferred_notification_channel,
#   preferred_language, portal_username, portal_credential_hash (synthetic
#   placeholder ONLY — never real password material), portal_credential_status,
#   past_blacklist_events[], past_tender_participation[], past_anomaly_flags[],
#   authorized_signatory_name, authorized_signatory_role, communication_address

# Ext-5 Solvency variance — methodology note (applied to all BidderProfiles)
EXT5_SOLVENCY_METHODOLOGY_NOTE = (
    "12-month window per AP-GO-089 Section 4(b); "
    "cert issued by Tahsildar per state default"
)


# Ext-3 Dual Turnover Criterion — methodology note (applied to all BidderProfiles)
EXT3_TURNOVER_METHODOLOGY_NOTE = (
    "Ext-3 Dual Turnover Criterion: construction_turnover_5yr_avg_cr is the "
    "civil-engineering-only revenue from Section A of P&L (5-year average per "
    "AP-GO-059 / MPW-039 construction-experience framing). "
    "financial_turnover_3yr_avg_cr is the operational income from audited "
    "balance sheets per IT Act Sec 44AB (3-year average per MPG-255 / CVC-028 "
    "Financial Standing criterion). For synthetic demo seed, financial set at "
    "70% of construction — this ratio is realistic for diversified contractors "
    "but pure-construction firms typically have financial >= construction. "
    "Audit-flagged here for downstream reviewer visibility."
)


PROFILES = {
    "b1": dict(
        profile_id="bid_synth_profile_b1",
        company_name="M/s Premier AP Constructions Pvt Ltd",
        gstin="37AAACP1234A1Z5",
        pan="AAACP1234A",
        contractor_class="Special",
        registration_state="AndhraPradesh",
        registration_authority="AP State Government per GO Ms No 94/2003",
        registration_certificate_no="AP/SC/PREMIER/2018/0142",
        registration_valid_until="2026-12-31",
        primary_business="Civil construction (buildings + infrastructure)",
        years_in_business=22,
        # Drives Statement I, II, V, VI, VIII, X
        average_5yr_turnover_cr=250.0,
        construction_turnover_5yr_avg_cr=250.0,   # Ext-3 alias (preferred)
        financial_turnover_3yr_avg_cr=175.0,      # Ext-3 NEW
        turnover_methodology_note=EXT3_TURNOVER_METHODOLOGY_NOTE,  # Ext-3
        # Ext-4 ABC formula M-coefficient method (forward-compat for B9)
        abc_formula_M_method="AP_GO_062_M2",
        abc_formula_rule_source="AP-GO-062",
        # Ext-5 Solvency cert validity window
        solvency_cert_validity_window_months=12,
        solvency_cert_source_rule="AP_GO_089_12MO",
        solvency_methodology_note=EXT5_SOLVENCY_METHODOLOGY_NOTE,
        # Ext-2 Compliance documents (8 mandatory per AP-PROC-COMPLIANCE-DOCS-V1)
        company_reg_cert_status="VALID",
        company_reg_cert_node_id=None,
        pan_cert_status="VALID",
        gst_cert_status="VALID",
        epf_esi_cert_status="VALID",
        # epf_esi_cert_value should be per-bidder unique; substituted at insert
        # time using profile_id, but the dict carries a fallback constant
        epf_esi_cert_value="EPF/AP/synthetic/2026/001234",
        form_12_declaration_status="SIGNED",
        poa_status="NOT_REQUIRED",   # B1-B8 all SOLE_BIDDER; B9 will set VALID
        tender_fee_receipt_status="VALID",
        tender_fee_amount_cr=0.10,
        dsc_status="VALID",
        dsc_expiry_date="2027-06-30",
        # Ext-1 JV/Consortium (forward-compat; B1-B8 all SOLE_BIDDER)
        bidder_type="SOLE_BIDDER",
        lead_partner_id=None,
        partner_ids=[],
        jv_agreement_node_id=None,
        jv_agreement_validity_until=None,
        liability_terms=None,
        max_completed_works_value_cr=145.0,
        existing_commitments_cr=180.0,
        abc_M_multiplier=2,
        solvency_cert_source="Tahsildar",
        solvency_cert_validity_months_ago=6,
        litigation_count=0,
        blacklist_status="clean",
        equipment_register_completeness="full_owned",
        key_personnel_count=6,
        # Behavior flags
        _similar_works_pattern="three_full",
        _boq_complete=True,
        _boq_line_item_count=285,
        _emd_bg_anomalous=False,
        _solvency_buffer_mult=1.5,
        _skip_statement_vi=False,
        _skip_bidsubmission=False,                # Ext-8: JV_PARTNERs override to True
        _premium_pct_delta=-5.0,
        # Module 4 forward-compat
        email_primary="bidder1@example.com",
        mobile_primary="+91-9000000001",
        preferred_notification_channel="email",
        preferred_language="English",
        portal_username="premier-ap-constructions",
        portal_credential_hash="synth_hash_b1_premier",
        portal_credential_status="active",
        past_blacklist_events=[],
        past_tender_participation=[
            dict(tender_id="hist_2023_apiic_a", year=2023, outcome="won",  contract_value_cr=85.0),
            dict(tender_id="hist_2023_apcrda_b", year=2023, outcome="lost", contract_value_cr=None),
            dict(tender_id="hist_2024_aphcj_c", year=2024, outcome="won",  contract_value_cr=76.5),
            dict(tender_id="hist_2024_apiic_d", year=2024, outcome="lost", contract_value_cr=None),
            dict(tender_id="hist_2025_apcrda_e",year=2025, outcome="won",  contract_value_cr=80.75),
        ],
        past_anomaly_flags=[],
        authorized_signatory_name="Mr. K. Rao",
        authorized_signatory_role="Managing Director",
        communication_address="3-5-101, Industrial Area, Vijayawada-520001",
    ),
    "b2": dict(
        profile_id="bid_synth_profile_b2",
        company_name="M/s Marginal Construction Pvt Ltd",
        gstin="37AAACM5678B2Z7",
        pan="AAACM5678B",
        contractor_class="Class-I",
        registration_state="AndhraPradesh",
        registration_authority="AP State Government per GO Ms No 94/2003",
        registration_certificate_no="AP/I/MARGINAL/2020/0789",
        registration_valid_until="2027-03-31",
        primary_business="Building construction (mid-tier)",
        years_in_business=12,
        average_5yr_turnover_cr=80.0,
        construction_turnover_5yr_avg_cr=80.0,    # Ext-3
        financial_turnover_3yr_avg_cr=56.0,       # Ext-3
        turnover_methodology_note=EXT3_TURNOVER_METHODOLOGY_NOTE,  # Ext-3
        # Ext-4 ABC formula M-coefficient method (forward-compat for B9)
        abc_formula_M_method="AP_GO_062_M2",
        abc_formula_rule_source="AP-GO-062",
        # Ext-5 Solvency cert validity window
        solvency_cert_validity_window_months=12,
        solvency_cert_source_rule="AP_GO_089_12MO",
        solvency_methodology_note=EXT5_SOLVENCY_METHODOLOGY_NOTE,
        # Ext-2 Compliance documents (8 mandatory per AP-PROC-COMPLIANCE-DOCS-V1)
        company_reg_cert_status="VALID",
        company_reg_cert_node_id=None,
        pan_cert_status="VALID",
        gst_cert_status="VALID",
        epf_esi_cert_status="VALID",
        # epf_esi_cert_value should be per-bidder unique; substituted at insert
        # time using profile_id, but the dict carries a fallback constant
        epf_esi_cert_value="EPF/AP/synthetic/2026/001234",
        form_12_declaration_status="SIGNED",
        poa_status="NOT_REQUIRED",   # B1-B8 all SOLE_BIDDER; B9 will set VALID
        tender_fee_receipt_status="VALID",
        tender_fee_amount_cr=0.10,
        dsc_status="VALID",
        dsc_expiry_date="2027-06-30",
        # Ext-1 JV/Consortium (forward-compat; B1-B8 all SOLE_BIDDER)
        bidder_type="SOLE_BIDDER",
        lead_partner_id=None,
        partner_ids=[],
        jv_agreement_node_id=None,
        jv_agreement_validity_until=None,
        liability_terms=None,
        max_completed_works_value_cr=45.0,
        existing_commitments_cr=60.0,
        abc_M_multiplier=2,
        solvency_cert_source="Bank",
        solvency_cert_validity_months_ago=14,
        litigation_count=0,
        blacklist_status="clean",
        equipment_register_completeness="mixed_owned_leased",
        key_personnel_count=4,
        _similar_works_pattern="one_at_60pct",
        _boq_complete=True,
        _boq_line_item_count=245,
        _emd_bg_anomalous=False,
        _solvency_buffer_mult=1.0,
        _skip_statement_vi=False,
        _skip_bidsubmission=False,                # Ext-8: JV_PARTNERs override to True
        _premium_pct_delta=-2.0,
        email_primary="bidder2@example.com",
        mobile_primary="+91-9000000002",
        preferred_notification_channel="sms",
        preferred_language="Telugu",
        portal_username="marginal-construction",
        portal_credential_hash="synth_hash_b2_marginal",
        portal_credential_status="active",
        past_blacklist_events=[],
        past_tender_participation=[
            dict(tender_id="hist_2023_apphc_a", year=2023, outcome="won",  contract_value_cr=51.0),
            dict(tender_id="hist_2023_apphc_b", year=2023, outcome="lost", contract_value_cr=None),
            dict(tender_id="hist_2024_apphc_c", year=2024, outcome="lost", contract_value_cr=None),
            dict(tender_id="hist_2024_apiic_d", year=2024, outcome="lost", contract_value_cr=None),
            dict(tender_id="hist_2025_apcrda_e",year=2025, outcome="lost", contract_value_cr=None),
        ],
        past_anomaly_flags=[],
        authorized_signatory_name="Mr. M. Reddy",
        authorized_signatory_role="Managing Director",
        communication_address="3-5-202, Industrial Area, Vijayawada-520002",
    ),
    "b3": dict(
        profile_id="bid_synth_profile_b3",
        company_name="M/s Anomalous Builders LLP",
        gstin="37AAACA9999C3Z9",
        pan="AAACA9999C",
        contractor_class="Special",
        registration_state="AndhraPradesh",
        registration_authority="AP State Government per GO Ms No 94/2003",
        registration_certificate_no="AP/SC/ANOMALOUS/2019/0456",
        registration_valid_until="2026-08-31",
        primary_business="Civil construction",
        years_in_business=8,
        average_5yr_turnover_cr=30.0,
        construction_turnover_5yr_avg_cr=30.0,     # Ext-3
        financial_turnover_3yr_avg_cr=21.0,        # Ext-3
        turnover_methodology_note=EXT3_TURNOVER_METHODOLOGY_NOTE,  # Ext-3
        # Ext-4 ABC formula M-coefficient method (forward-compat for B9)
        abc_formula_M_method="AP_GO_062_M2",
        abc_formula_rule_source="AP-GO-062",
        # Ext-5 Solvency cert validity window
        solvency_cert_validity_window_months=12,
        solvency_cert_source_rule="AP_GO_089_12MO",
        solvency_methodology_note=EXT5_SOLVENCY_METHODOLOGY_NOTE,
        # Ext-2 Compliance documents (8 mandatory per AP-PROC-COMPLIANCE-DOCS-V1)
        company_reg_cert_status="VALID",
        company_reg_cert_node_id=None,
        pan_cert_status="VALID",
        gst_cert_status="VALID",
        epf_esi_cert_status="VALID",
        # epf_esi_cert_value should be per-bidder unique; substituted at insert
        # time using profile_id, but the dict carries a fallback constant
        epf_esi_cert_value="EPF/AP/synthetic/2026/001234",
        form_12_declaration_status="SIGNED",
        poa_status="NOT_REQUIRED",   # B1-B8 all SOLE_BIDDER; B9 will set VALID
        tender_fee_receipt_status="VALID",
        tender_fee_amount_cr=0.10,
        dsc_status="VALID",
        dsc_expiry_date="2027-06-30",
        # Ext-1 JV/Consortium (forward-compat; B1-B8 all SOLE_BIDDER)
        bidder_type="SOLE_BIDDER",
        lead_partner_id=None,
        partner_ids=[],
        jv_agreement_node_id=None,
        jv_agreement_validity_until=None,
        liability_terms=None,
        max_completed_works_value_cr=18.0,
        existing_commitments_cr=42.0,
        abc_M_multiplier=3,
        solvency_cert_source="Tahsildar",
        solvency_cert_validity_months_ago=3,
        litigation_count=2,
        blacklist_status="previously_debarred",
        equipment_register_completeness="procurable_only",
        key_personnel_count=2,
        _similar_works_pattern="zero_works",
        _boq_complete=False,
        _boq_line_item_count=198,
        _emd_bg_anomalous=True,
        _solvency_buffer_mult=1.0,
        _skip_statement_vi=False,
        _skip_bidsubmission=False,                # Ext-8: JV_PARTNERs override to True
        _premium_pct_delta=+5.0,
        email_primary="bidder3@example.com",
        mobile_primary="+91-9000000003",
        preferred_notification_channel="portal_only",
        preferred_language="English",
        portal_username="anomalous-builders",
        portal_credential_hash="synth_hash_b3_anomalous",
        portal_credential_status="suspended",
        past_blacklist_events=[
            dict(event_date="2022-08-15", issuing_authority="Hyderabad Metro Rail Ltd",
                 reason="substandard execution + missed milestones on Phase-2 metro depot contract",
                 expiry_date="2024-02-15", current_status="expired_active_appeal"),
        ],
        past_tender_participation=[
            dict(tender_id="hist_2022_hmrl_a", year=2022, outcome="disqualified", contract_value_cr=None),
            dict(tender_id="hist_2023_apiic_b", year=2023, outcome="disqualified", contract_value_cr=None),
            dict(tender_id="hist_2023_appwd_c",year=2023, outcome="lost", contract_value_cr=None),
            dict(tender_id="hist_2024_apiic_d", year=2024, outcome="lost", contract_value_cr=None),
            dict(tender_id="hist_2025_apcrda_e",year=2025, outcome="disqualified", contract_value_cr=None),
        ],
        past_anomaly_flags=[
            dict(anomaly_date="2023-04-10", anomaly_type="cartel_suspicion",
                 tender_id="hist_2023_apiic_b", outcome="dismissed"),
            dict(anomaly_date="2024-09-22", anomaly_type="cartel_suspicion",
                 tender_id="hist_2024_apiic_d", outcome="dismissed"),
        ],
        authorized_signatory_name="Mr. A. Anomalous",
        authorized_signatory_role="Managing Partner",
        communication_address="3-5-303, Industrial Area, Vijayawada-520003",
    ),
    # ──── B4 Borderline-Litigation (FLAGGED_FOR_COMMITTEE_REVIEW target) ────
    "b4": dict(
        profile_id="bid_synth_profile_b4",
        company_name="M/s Borderline Litigation Builders Pvt Ltd",
        gstin="37AAACB4444B4Z4",
        pan="AAACB4444B",
        contractor_class="Special",
        registration_state="AndhraPradesh",
        registration_authority="AP State Government per GO Ms No 94/2003",
        registration_certificate_no="AP/SC/BORDERLINE/2017/0234",
        registration_valid_until="2027-06-30",
        primary_business="Civil + infrastructure construction",
        years_in_business=15,
        average_5yr_turnover_cr=290.0,          # clears HC PQ floor 243.4
        construction_turnover_5yr_avg_cr=290.0,  # Ext-3
        financial_turnover_3yr_avg_cr=203.0,     # Ext-3
        turnover_methodology_note=EXT3_TURNOVER_METHODOLOGY_NOTE,  # Ext-3
        # Ext-4 ABC formula M-coefficient method (forward-compat for B9)
        abc_formula_M_method="AP_GO_062_M2",
        abc_formula_rule_source="AP-GO-062",
        # Ext-5 Solvency cert validity window
        solvency_cert_validity_window_months=12,
        solvency_cert_source_rule="AP_GO_089_12MO",
        solvency_methodology_note=EXT5_SOLVENCY_METHODOLOGY_NOTE,
        # Ext-2 Compliance documents (8 mandatory per AP-PROC-COMPLIANCE-DOCS-V1)
        company_reg_cert_status="VALID",
        company_reg_cert_node_id=None,
        pan_cert_status="VALID",
        gst_cert_status="VALID",
        epf_esi_cert_status="VALID",
        # epf_esi_cert_value should be per-bidder unique; substituted at insert
        # time using profile_id, but the dict carries a fallback constant
        epf_esi_cert_value="EPF/AP/synthetic/2026/001234",
        form_12_declaration_status="SIGNED",
        poa_status="NOT_REQUIRED",   # B1-B8 all SOLE_BIDDER; B9 will set VALID
        tender_fee_receipt_status="VALID",
        tender_fee_amount_cr=0.10,
        dsc_status="VALID",
        dsc_expiry_date="2027-06-30",
        # Ext-1 JV/Consortium (forward-compat; B1-B8 all SOLE_BIDDER)
        bidder_type="SOLE_BIDDER",
        lead_partner_id=None,
        partner_ids=[],
        jv_agreement_node_id=None,
        jv_agreement_validity_until=None,
        liability_terms=None,
        max_completed_works_value_cr=160.0,
        existing_commitments_cr=100.0,
        abc_M_multiplier=2,
        solvency_cert_source="Tahsildar",
        solvency_cert_validity_months_ago=4,
        litigation_count=1,                     # → INELIGIBLE-WARNING on bid_litigation
        blacklist_status="clean",
        equipment_register_completeness="full_owned",
        key_personnel_count=6,
        _similar_works_pattern="three_full",
        _boq_complete=True,
        _boq_line_item_count=290,
        _emd_bg_anomalous=False,
        _solvency_buffer_mult=1.5,
        _skip_statement_vi=False,
        _skip_bidsubmission=False,                # Ext-8: JV_PARTNERs override to True
        _premium_pct_delta=-4.0,
        email_primary="bidder4@example.com",
        mobile_primary="+91-9000000004",
        preferred_notification_channel="whatsapp",
        preferred_language="Both",
        portal_username="borderline-litigation-builders",
        portal_credential_hash="synth_hash_b4_borderline",
        portal_credential_status="active",
        past_blacklist_events=[],
        past_tender_participation=[
            dict(tender_id="hist_2023_apiic_a", year=2023, outcome="won",  contract_value_cr=120.0),
            dict(tender_id="hist_2023_apcrda_b",year=2023, outcome="lost", contract_value_cr=None),
            dict(tender_id="hist_2024_apphc_c", year=2024, outcome="won",  contract_value_cr=95.0),
            dict(tender_id="hist_2024_apiic_d", year=2024, outcome="lost", contract_value_cr=None),
            dict(tender_id="hist_2025_appwd_e", year=2025, outcome="lost", contract_value_cr=None),
        ],
        past_anomaly_flags=[],
        authorized_signatory_name="Mr. B. Singh",
        authorized_signatory_role="Managing Director",
        communication_address="4-7-12, Civil Lines, Tirupati-517501",
    ),
    # ──── B5 Incomplete-Documentation (MARK_FOR_DOC_REVIEW target) ────
    "b5": dict(
        profile_id="bid_synth_profile_b5",
        company_name="M/s Incomplete Submissions Corp Pvt Ltd",
        gstin="37AAACI5555I5Z5",
        pan="AAACI5555I",
        contractor_class="Special",
        registration_state="AndhraPradesh",
        registration_authority="AP State Government per GO Ms No 94/2003",
        registration_certificate_no="AP/SC/INCOMPLETE/2018/0345",
        registration_valid_until="2026-11-30",
        primary_business="Civil + structural construction",
        years_in_business=12,
        average_5yr_turnover_cr=250.0,          # clears HC 243.4
        construction_turnover_5yr_avg_cr=250.0,  # Ext-3
        financial_turnover_3yr_avg_cr=175.0,     # Ext-3
        turnover_methodology_note=EXT3_TURNOVER_METHODOLOGY_NOTE,  # Ext-3
        # Ext-4 ABC formula M-coefficient method (forward-compat for B9)
        abc_formula_M_method="AP_GO_062_M2",
        abc_formula_rule_source="AP-GO-062",
        # Ext-5 Solvency cert validity window
        solvency_cert_validity_window_months=12,
        solvency_cert_source_rule="AP_GO_089_12MO",
        solvency_methodology_note=EXT5_SOLVENCY_METHODOLOGY_NOTE,
        # Ext-2 Compliance documents (8 mandatory per AP-PROC-COMPLIANCE-DOCS-V1)
        company_reg_cert_status="VALID",
        company_reg_cert_node_id=None,
        pan_cert_status="VALID",
        gst_cert_status="VALID",
        epf_esi_cert_status="VALID",
        # epf_esi_cert_value should be per-bidder unique; substituted at insert
        # time using profile_id, but the dict carries a fallback constant
        epf_esi_cert_value="EPF/AP/synthetic/2026/001234",
        form_12_declaration_status="SIGNED",
        poa_status="NOT_REQUIRED",   # B1-B8 all SOLE_BIDDER; B9 will set VALID
        tender_fee_receipt_status="VALID",
        tender_fee_amount_cr=0.10,
        dsc_status="VALID",
        dsc_expiry_date="2027-06-30",
        # Ext-1 JV/Consortium (forward-compat; B1-B8 all SOLE_BIDDER)
        bidder_type="SOLE_BIDDER",
        lead_partner_id=None,
        partner_ids=[],
        jv_agreement_node_id=None,
        jv_agreement_validity_until=None,
        liability_terms=None,
        max_completed_works_value_cr=150.0,
        existing_commitments_cr=110.0,
        abc_M_multiplier=2,
        solvency_cert_source="Tahsildar",
        solvency_cert_validity_months_ago=5,
        litigation_count=0,
        blacklist_status="clean",
        equipment_register_completeness="full_owned",
        key_personnel_count=6,                  # field present but Statement-VI suppressed
        _similar_works_pattern="three_full",
        _boq_complete=True,
        _boq_line_item_count=270,
        _emd_bg_anomalous=False,
        _solvency_buffer_mult=1.5,
        _skip_statement_vi=True,                # → bid_personnel_check GAP
        _skip_bidsubmission=False,                # Ext-8: JV_PARTNERs override to True
        _premium_pct_delta=-3.5,
        email_primary="bidder5@example.com",
        mobile_primary="+91-9000000005",
        preferred_notification_channel="email",
        preferred_language="English",
        portal_username="incomplete-submissions-corp",
        portal_credential_hash="synth_hash_b5_incomplete",
        portal_credential_status="first_login_pending",
        past_blacklist_events=[],
        past_tender_participation=[
            dict(tender_id="hist_2023_apcrda_a", year=2023, outcome="won",  contract_value_cr=110.0),
            dict(tender_id="hist_2023_apiic_b", year=2023, outcome="lost", contract_value_cr=None),
            dict(tender_id="hist_2024_apphc_c", year=2024, outcome="disqualified",
                 contract_value_cr=None, disqualification_reason="documents incomplete at submission deadline"),
            dict(tender_id="hist_2024_apiic_d", year=2024, outcome="lost", contract_value_cr=None),
        ],
        past_anomaly_flags=[],
        authorized_signatory_name="Mr. I. Kumar",
        authorized_signatory_role="Director",
        communication_address="5-9-34, Old Town, Kakinada-533001",
    ),
    # ──── B6 Cartel-Pair-A (QUALIFIED on Tier-2; cartel signal vs B7) ────
    "b6": dict(
        profile_id="bid_synth_profile_b6",
        company_name="M/s Cartel Alpha Construction Pvt Ltd",
        gstin="37AAACX6666X6Z6",
        pan="AAACX6666X",
        contractor_class="Special",
        registration_state="AndhraPradesh",
        registration_authority="AP State Government per GO Ms No 94/2003",
        registration_certificate_no="AP/SC/CARTEL-A/2019/0456",
        registration_valid_until="2027-01-15",
        primary_business="Civil construction (commercial buildings)",
        years_in_business=11,
        average_5yr_turnover_cr=260.0,
        construction_turnover_5yr_avg_cr=260.0,  # Ext-3
        financial_turnover_3yr_avg_cr=182.0,     # Ext-3
        turnover_methodology_note=EXT3_TURNOVER_METHODOLOGY_NOTE,  # Ext-3
        # Ext-4 ABC formula M-coefficient method (forward-compat for B9)
        abc_formula_M_method="AP_GO_062_M2",
        abc_formula_rule_source="AP-GO-062",
        # Ext-5 Solvency cert validity window
        solvency_cert_validity_window_months=12,
        solvency_cert_source_rule="AP_GO_089_12MO",
        solvency_methodology_note=EXT5_SOLVENCY_METHODOLOGY_NOTE,
        # Ext-2 Compliance documents (8 mandatory per AP-PROC-COMPLIANCE-DOCS-V1)
        company_reg_cert_status="VALID",
        company_reg_cert_node_id=None,
        pan_cert_status="VALID",
        gst_cert_status="VALID",
        epf_esi_cert_status="VALID",
        # epf_esi_cert_value should be per-bidder unique; substituted at insert
        # time using profile_id, but the dict carries a fallback constant
        epf_esi_cert_value="EPF/AP/synthetic/2026/001234",
        form_12_declaration_status="SIGNED",
        poa_status="NOT_REQUIRED",   # B1-B8 all SOLE_BIDDER; B9 will set VALID
        tender_fee_receipt_status="VALID",
        tender_fee_amount_cr=0.10,
        dsc_status="VALID",
        dsc_expiry_date="2027-06-30",
        # Ext-1 JV/Consortium (forward-compat; B1-B8 all SOLE_BIDDER)
        bidder_type="SOLE_BIDDER",
        lead_partner_id=None,
        partner_ids=[],
        jv_agreement_node_id=None,
        jv_agreement_validity_until=None,
        liability_terms=None,
        max_completed_works_value_cr=130.0,
        existing_commitments_cr=110.0,
        abc_M_multiplier=2,
        solvency_cert_source="Tahsildar",
        solvency_cert_validity_months_ago=4,
        litigation_count=0,
        blacklist_status="clean",
        equipment_register_completeness="full_owned",
        key_personnel_count=6,
        _similar_works_pattern="three_full",
        _boq_complete=True,
        _boq_line_item_count=275,
        _emd_bg_anomalous=False,
        _solvency_buffer_mult=1.5,
        _skip_statement_vi=False,
        _skip_bidsubmission=False,                # Ext-8: JV_PARTNERs override to True
        _premium_pct_delta=-3.10,               # cartel signal: near-identical w/ B7
        email_primary="bidder6@example.com",
        mobile_primary="+91-9000000006",
        preferred_notification_channel="email",
        preferred_language="Telugu",
        portal_username="cartel-alpha-construction",
        portal_credential_hash="synth_hash_b6_cartel_a",
        portal_credential_status="active",
        past_blacklist_events=[],
        past_tender_participation=[
            dict(tender_id="hist_2023_apiic_a", year=2023, outcome="won",  contract_value_cr=90.0),
            dict(tender_id="hist_2024_apcrda_b",year=2024, outcome="won",  contract_value_cr=85.0),
            dict(tender_id="hist_2024_apphc_c", year=2024, outcome="lost", contract_value_cr=None),
        ],
        past_anomaly_flags=[
            dict(anomaly_date="2024-11-05", anomaly_type="cartel_suspicion",
                 tender_id="hist_2024_apcrda_b", outcome="dismissed"),
        ],
        authorized_signatory_name="Mr. R. Sharma",      # matched-pattern w/ B7
        authorized_signatory_role="Managing Director",
        communication_address="4-7-89, Industrial Estate, Guntur-522001",  # SHARED w/ B7
    ),
    # ──── B7 Cartel-Pair-B (QUALIFIED on Tier-2; cartel signal vs B6) ────
    "b7": dict(
        profile_id="bid_synth_profile_b7",
        company_name="M/s Cartel Beta Construction Pvt Ltd",
        gstin="37AAACY7777Y7Z7",
        pan="AAACY7777Y",
        contractor_class="Special",
        registration_state="AndhraPradesh",
        registration_authority="AP State Government per GO Ms No 94/2003",
        registration_certificate_no="AP/SC/CARTEL-B/2019/0457",
        registration_valid_until="2027-01-20",
        primary_business="Civil construction (commercial buildings)",
        years_in_business=10,
        average_5yr_turnover_cr=270.0,
        construction_turnover_5yr_avg_cr=270.0,  # Ext-3
        financial_turnover_3yr_avg_cr=189.0,     # Ext-3
        turnover_methodology_note=EXT3_TURNOVER_METHODOLOGY_NOTE,  # Ext-3
        # Ext-4 ABC formula M-coefficient method (forward-compat for B9)
        abc_formula_M_method="AP_GO_062_M2",
        abc_formula_rule_source="AP-GO-062",
        # Ext-5 Solvency cert validity window
        solvency_cert_validity_window_months=12,
        solvency_cert_source_rule="AP_GO_089_12MO",
        solvency_methodology_note=EXT5_SOLVENCY_METHODOLOGY_NOTE,
        # Ext-2 Compliance documents (8 mandatory per AP-PROC-COMPLIANCE-DOCS-V1)
        company_reg_cert_status="VALID",
        company_reg_cert_node_id=None,
        pan_cert_status="VALID",
        gst_cert_status="VALID",
        epf_esi_cert_status="VALID",
        # epf_esi_cert_value should be per-bidder unique; substituted at insert
        # time using profile_id, but the dict carries a fallback constant
        epf_esi_cert_value="EPF/AP/synthetic/2026/001234",
        form_12_declaration_status="SIGNED",
        poa_status="NOT_REQUIRED",   # B1-B8 all SOLE_BIDDER; B9 will set VALID
        tender_fee_receipt_status="VALID",
        tender_fee_amount_cr=0.10,
        dsc_status="VALID",
        dsc_expiry_date="2027-06-30",
        # Ext-1 JV/Consortium (forward-compat; B1-B8 all SOLE_BIDDER)
        bidder_type="SOLE_BIDDER",
        lead_partner_id=None,
        partner_ids=[],
        jv_agreement_node_id=None,
        jv_agreement_validity_until=None,
        liability_terms=None,
        max_completed_works_value_cr=135.0,
        existing_commitments_cr=115.0,
        abc_M_multiplier=2,
        solvency_cert_source="Tahsildar",
        solvency_cert_validity_months_ago=5,
        litigation_count=0,
        blacklist_status="clean",
        equipment_register_completeness="full_owned",
        key_personnel_count=6,
        _similar_works_pattern="three_full",
        _boq_complete=True,
        _boq_line_item_count=280,
        _emd_bg_anomalous=False,
        _solvency_buffer_mult=1.5,
        _skip_statement_vi=False,
        _skip_bidsubmission=False,                # Ext-8: JV_PARTNERs override to True
        _premium_pct_delta=-3.05,               # cartel signal: 1.6% off B6
        email_primary="bidder7@example.com",
        mobile_primary="+91-9000000007",
        preferred_notification_channel="email",
        preferred_language="Telugu",
        portal_username="cartel-beta-construction",
        portal_credential_hash="synth_hash_b7_cartel_b",
        portal_credential_status="active",
        past_blacklist_events=[],
        past_tender_participation=[
            dict(tender_id="hist_2023_apiic_a", year=2023, outcome="won",  contract_value_cr=88.0),
            dict(tender_id="hist_2024_apcrda_b",year=2024, outcome="won",  contract_value_cr=87.0),
            dict(tender_id="hist_2024_apphc_c", year=2024, outcome="lost", contract_value_cr=None),
        ],
        past_anomaly_flags=[
            dict(anomaly_date="2024-11-05", anomaly_type="cartel_suspicion",
                 tender_id="hist_2024_apcrda_b", outcome="dismissed"),
        ],
        authorized_signatory_name="Mr. R. Patel",       # matched-pattern w/ B6
        authorized_signatory_role="Managing Director",
        communication_address="4-7-89, Industrial Estate, Guntur-522001",  # SHARED w/ B6
    ),
    # ──── B8 Abnormally-Low (QUALIFIED on Tier-2; ALB signal in BoQ/LoB) ────
    "b8": dict(
        profile_id="bid_synth_profile_b8",
        company_name="M/s Abnormally Low Bidders Pvt Ltd",
        gstin="37AAACZ8888Z8Z8",
        pan="AAACZ8888Z",
        contractor_class="Special",
        registration_state="AndhraPradesh",
        registration_authority="AP State Government per GO Ms No 94/2003",
        registration_certificate_no="AP/SC/ABNORMAL/2020/0567",
        registration_valid_until="2027-05-25",
        primary_business="Civil construction (mixed)",
        years_in_business=9,
        average_5yr_turnover_cr=280.0,
        construction_turnover_5yr_avg_cr=280.0,  # Ext-3
        financial_turnover_3yr_avg_cr=196.0,     # Ext-3
        turnover_methodology_note=EXT3_TURNOVER_METHODOLOGY_NOTE,  # Ext-3
        # Ext-4 ABC formula M-coefficient method (forward-compat for B9)
        abc_formula_M_method="AP_GO_062_M2",
        abc_formula_rule_source="AP-GO-062",
        # Ext-5 Solvency cert validity window
        solvency_cert_validity_window_months=12,
        solvency_cert_source_rule="AP_GO_089_12MO",
        solvency_methodology_note=EXT5_SOLVENCY_METHODOLOGY_NOTE,
        # Ext-2 Compliance documents (8 mandatory per AP-PROC-COMPLIANCE-DOCS-V1)
        company_reg_cert_status="VALID",
        company_reg_cert_node_id=None,
        pan_cert_status="VALID",
        gst_cert_status="VALID",
        epf_esi_cert_status="VALID",
        # epf_esi_cert_value should be per-bidder unique; substituted at insert
        # time using profile_id, but the dict carries a fallback constant
        epf_esi_cert_value="EPF/AP/synthetic/2026/001234",
        form_12_declaration_status="SIGNED",
        poa_status="NOT_REQUIRED",   # B1-B8 all SOLE_BIDDER; B9 will set VALID
        tender_fee_receipt_status="VALID",
        tender_fee_amount_cr=0.10,
        dsc_status="VALID",
        dsc_expiry_date="2027-06-30",
        # Ext-1 JV/Consortium (forward-compat; B1-B8 all SOLE_BIDDER)
        bidder_type="SOLE_BIDDER",
        lead_partner_id=None,
        partner_ids=[],
        jv_agreement_node_id=None,
        jv_agreement_validity_until=None,
        liability_terms=None,
        max_completed_works_value_cr=120.0,
        existing_commitments_cr=100.0,
        abc_M_multiplier=2,
        solvency_cert_source="Tahsildar",
        solvency_cert_validity_months_ago=3,
        litigation_count=0,
        blacklist_status="clean",
        equipment_register_completeness="full_owned",
        key_personnel_count=6,
        _similar_works_pattern="three_full",
        _boq_complete=True,
        _boq_line_item_count=210,               # fewer items consistent w/ ALB-style sparse pricing
        _emd_bg_anomalous=False,
        _solvency_buffer_mult=1.5,
        _skip_statement_vi=False,
        _skip_bidsubmission=False,                # Ext-8: JV_PARTNERs override to True
        _premium_pct_delta=-38.0,               # ALB signal — 38% under ECV
        email_primary="bidder8@example.com",
        mobile_primary="+91-9000000008",
        preferred_notification_channel="sms",
        preferred_language="English",
        portal_username="abnormally-low-bidders",
        portal_credential_hash="synth_hash_b8_alb",
        portal_credential_status="active",
        past_blacklist_events=[],
        past_tender_participation=[
            dict(tender_id="hist_2023_apiic_a", year=2023, outcome="won",
                 contract_value_cr=60.0, note="winning bid 35% below ECV — ALB pattern"),
            dict(tender_id="hist_2024_apcrda_b",year=2024, outcome="won",
                 contract_value_cr=72.0, note="winning bid 30% below ECV — ALB pattern"),
            dict(tender_id="hist_2024_apphc_c", year=2024, outcome="lost", contract_value_cr=None),
            dict(tender_id="hist_2025_apiic_d", year=2025, outcome="won",
                 contract_value_cr=78.0, note="winning bid 28% below ECV — ALB pattern"),
        ],
        past_anomaly_flags=[
            dict(anomaly_date="2024-07-18", anomaly_type="abnormally_low_bid",
                 tender_id="hist_2024_apcrda_b", outcome="confirmed"),
        ],
        authorized_signatory_name="Mr. L. Bidder",
        authorized_signatory_role="CEO",
        communication_address="6-2-101, Sea View Apartments, Visakhapatnam-530001",
    ),
    # ── B9 JV/Consortium DemoBidder (Ext-8) ─────────────────────────────
    # Comprehensive JV that passes every standard evaluation check.
    # Per docs/extensions/B9_demobidder_spec.md (commit 7ceef7a, sections 3-5).
    # Lead-Partner-alone financial values per AP JV norm (NOT collective).
    # 3 JV_PARTNER profiles (b9_lead / b9_p2 / b9_p3) follow below; they
    # carry _skip_bidsubmission=True so the main loop does NOT create
    # BidSubmissions for them (only the JV entity B9 submits bids).
    "b9": dict(
        profile_id="bid_synth_profile_b9",
        company_name=(
            "M/s Comprehensive Standard Builders JV "
            "(Premier Coastal + Northern Engineering + Southern Surveys)"
        ),
        gstin="37AAACJ9999J9Z9",
        pan="AAACJ9999J",
        contractor_class="Special",
        registration_state="AndhraPradesh",
        registration_authority="AP State Government per JV Agreement clause 4.2",
        registration_certificate_no="AP/SC/B9JV/2026/0001",
        registration_valid_until="2027-12-31",
        primary_business="Civil construction (collective: buildings + piling + surveying)",
        years_in_business=5,
        # Ext-3 dual turnover (Lead-Partner-alone values; clears HC 109.55 floor)
        average_5yr_turnover_cr=260.0,
        construction_turnover_5yr_avg_cr=260.0,
        financial_turnover_3yr_avg_cr=230.0,
        turnover_methodology_note=EXT3_TURNOVER_METHODOLOGY_NOTE,
        # Ext-4 ABC: A=160, N=2, M=2 → 540cr (clears HC 365.16 ECV)
        abc_formula_M_method="AP_GO_062_M2",
        abc_formula_rule_source="AP-GO-062",
        # Ext-5 Solvency: 4mo Tahsildar cert ≤ 12mo window
        solvency_cert_validity_window_months=12,
        solvency_cert_source_rule="AP_GO_089_12MO",
        solvency_methodology_note=EXT5_SOLVENCY_METHODOLOGY_NOTE,
        # Ext-2 Compliance documents (all 8 VALID/SIGNED including POA Form-15)
        company_reg_cert_status="VALID",
        company_reg_cert_node_id=None,
        pan_cert_status="VALID",
        gst_cert_status="VALID",
        epf_esi_cert_status="VALID",
        epf_esi_cert_value="EPF/AP/B9JV/2026/001234",
        form_12_declaration_status="SIGNED",
        poa_status="VALID",                       # JV Form-15 POA (distinguishing from B1-B8 NOT_REQUIRED)
        tender_fee_receipt_status="VALID",
        tender_fee_amount_cr=0.10,
        dsc_status="VALID",
        dsc_expiry_date="2027-06-30",
        # Ext-1 JV/Consortium — this is the JV entity
        bidder_type="JV",
        lead_partner_id="bid_synth_profile_b9_lead",
        partner_ids=[
            "bid_synth_profile_b9_lead",
            "bid_synth_profile_b9_p2",
            "bid_synth_profile_b9_p3",
        ],
        jv_agreement_node_id="b9_jv_agreement_2026",
        jv_agreement_validity_until="2027-12-31",
        liability_terms="JOINT_AND_SEVERAL",
        # ABC inputs (Lead-Partner-alone)
        max_completed_works_value_cr=160.0,
        existing_commitments_cr=100.0,
        abc_M_multiplier=2,
        # Solvency
        solvency_cert_source="Tahsildar",
        solvency_cert_validity_months_ago=4,
        # Eligibility
        litigation_count=0,
        blacklist_status="clean",
        equipment_register_completeness="full_owned",
        key_personnel_count=6,
        # Behavior flags
        _similar_works_pattern="three_full",
        _boq_complete=True,
        _boq_line_item_count=312,
        _emd_bg_anomalous=False,
        _solvency_buffer_mult=1.5,
        _skip_statement_vi=False,
        _skip_bidsubmission=False,                # JV entity DOES submit bids
        _premium_pct_delta=-6.0,                  # B9 raw L2 → effective L1 after B8 ALB skip
        # Module 4 forward-compat
        email_primary="bidder9@example.com",
        mobile_primary="+91-9000000009",
        preferred_notification_channel="email",
        preferred_language="Both",
        portal_username="comprehensive-standard-builders-jv",
        portal_credential_hash="synth_hash_b9_jv",
        portal_credential_status="active",
        past_blacklist_events=[],
        past_tender_participation=[
            dict(tender_id="hist_2024_apiic_jv_a", year=2024, outcome="won",
                 contract_value_cr=145.0, note="JV's first major hospital contract"),
            dict(tender_id="hist_2025_apcrda_jv_b", year=2025, outcome="won",
                 contract_value_cr=180.0, note="JV educational complex"),
            dict(tender_id="hist_2025_aphcj_jv_c", year=2025, outcome="lost",
                 contract_value_cr=None),
        ],
        past_anomaly_flags=[],
        authorized_signatory_name="Mr. C. Comprehensive",   # unique 'C.' initial — no cartel signature pattern
        authorized_signatory_role="JV Coordinator (per Form-15 POA)",
        communication_address="Plot 27, MVP Colony, Visakhapatnam-530017",
    ),
    # ── B9.lead — JV_PARTNER #1 (Lead Partner; carries Lead-alone financials) ──
    "b9_lead": dict(
        profile_id="bid_synth_profile_b9_lead",
        company_name="M/s Premier Coastal Construction Pvt Ltd",
        gstin="37AAACL5555L1Z1",
        pan="AAACL5555L",
        contractor_class="Special",
        registration_state="AndhraPradesh",
        registration_authority="AP State Government per GO Ms No 94/2003",
        registration_certificate_no="AP/SC/PCCPL/2004/0019",
        registration_valid_until="2028-03-31",
        primary_business="Civil construction (buildings + hospitals + court complexes)",
        years_in_business=22,
        average_5yr_turnover_cr=260.0,
        construction_turnover_5yr_avg_cr=260.0,
        financial_turnover_3yr_avg_cr=230.0,
        turnover_methodology_note=EXT3_TURNOVER_METHODOLOGY_NOTE,
        abc_formula_M_method="AP_GO_062_M2",
        abc_formula_rule_source="AP-GO-062",
        solvency_cert_validity_window_months=12,
        solvency_cert_source_rule="AP_GO_089_12MO",
        solvency_methodology_note=EXT5_SOLVENCY_METHODOLOGY_NOTE,
        company_reg_cert_status="VALID", company_reg_cert_node_id=None,
        pan_cert_status="VALID", gst_cert_status="VALID",
        epf_esi_cert_status="VALID",
        epf_esi_cert_value="EPF/AP/PCCPL/2004/008812",
        form_12_declaration_status="SIGNED",
        poa_status="VALID",
        tender_fee_receipt_status="VALID", tender_fee_amount_cr=0.10,
        dsc_status="VALID", dsc_expiry_date="2027-06-30",
        bidder_type="JV_PARTNER",
        lead_partner_id=None,
        partner_ids=[],
        jv_agreement_node_id=None,
        jv_agreement_validity_until=None,
        liability_terms=None,
        max_completed_works_value_cr=160.0,
        existing_commitments_cr=100.0,
        abc_M_multiplier=2,
        solvency_cert_source="Tahsildar",
        solvency_cert_validity_months_ago=4,
        litigation_count=0,
        blacklist_status="clean",
        equipment_register_completeness="full_owned",
        key_personnel_count=3,                    # Lead contributes 3 of 6 collective roles
        _similar_works_pattern="three_full",
        _boq_complete=True,
        _boq_line_item_count=312,
        _emd_bg_anomalous=False,
        _solvency_buffer_mult=1.5,
        _skip_statement_vi=False,
        _skip_bidsubmission=True,                 # JV_PARTNER: NO bid submissions
        _premium_pct_delta=0.0,
        email_primary="lead@premiercoastal.example.com",
        mobile_primary="+91-9000000091",
        preferred_notification_channel="email",
        preferred_language="English",
        portal_username="premier-coastal-construction",
        portal_credential_hash="synth_hash_b9_lead",
        portal_credential_status="active",
        past_blacklist_events=[],
        past_tender_participation=[
            dict(tender_id="hist_2022_apiic_lead_a", year=2022, outcome="won", contract_value_cr=145.0),
            dict(tender_id="hist_2023_apcrda_lead_b", year=2023, outcome="won", contract_value_cr=120.5),
            dict(tender_id="hist_2024_aphcj_lead_c", year=2024, outcome="won", contract_value_cr=160.0),
        ],
        past_anomaly_flags=[],
        authorized_signatory_name="Mr. C. Comprehensive",  # Lead's principal — also JV Coordinator
        authorized_signatory_role="Managing Director",
        communication_address="Plot 27, MVP Colony, Visakhapatnam-530017",
        # Ext-1 JV-context metadata (informational; not consumed by validators directly)
        equipment_role_in_jv="Batching plant + tower crane + excavator (3 critical items)",
        personnel_roles_in_jv="Project Manager + Site Engineer + QA Engineer (3 of 6)",
        similar_works_contributed=3,
    ),
    # ── B9.p2 — JV_PARTNER #2 (piling specialist) ──
    "b9_p2": dict(
        profile_id="bid_synth_profile_b9_p2",
        company_name="M/s Northern Engineering Pvt Ltd",
        gstin="36AAACN7777N2Z2",
        pan="AAACN7777N",
        contractor_class="Class-I",
        registration_state="Telangana",
        registration_authority="Telangana State Government",
        registration_certificate_no="TS/CL1/NEPL/2012/0061",
        registration_valid_until="2027-09-30",
        primary_business="Civil engineering (piling + foundations + MEP)",
        years_in_business=14,
        average_5yr_turnover_cr=80.0,
        construction_turnover_5yr_avg_cr=80.0,
        financial_turnover_3yr_avg_cr=65.0,
        turnover_methodology_note=EXT3_TURNOVER_METHODOLOGY_NOTE,
        abc_formula_M_method="AP_GO_062_M2",
        abc_formula_rule_source="AP-GO-062",
        solvency_cert_validity_window_months=12,
        solvency_cert_source_rule="AP_GO_089_12MO",
        solvency_methodology_note=EXT5_SOLVENCY_METHODOLOGY_NOTE,
        company_reg_cert_status="VALID", company_reg_cert_node_id=None,
        pan_cert_status="VALID", gst_cert_status="VALID",
        epf_esi_cert_status="VALID",
        epf_esi_cert_value="EPF/TS/NEPL/2012/003345",
        form_12_declaration_status="SIGNED",
        poa_status="VALID",
        tender_fee_receipt_status="VALID", tender_fee_amount_cr=0.10,
        dsc_status="VALID", dsc_expiry_date="2027-06-30",
        bidder_type="JV_PARTNER",
        lead_partner_id=None,
        partner_ids=[],
        jv_agreement_node_id=None,
        jv_agreement_validity_until=None,
        liability_terms=None,
        max_completed_works_value_cr=45.0,
        existing_commitments_cr=30.0,
        abc_M_multiplier=2,
        solvency_cert_source="Bank",
        solvency_cert_validity_months_ago=6,
        litigation_count=0,
        blacklist_status="clean",
        equipment_register_completeness="full_owned",
        key_personnel_count=2,
        _similar_works_pattern="three_full",
        _boq_complete=True,
        _boq_line_item_count=180,
        _emd_bg_anomalous=False,
        _solvency_buffer_mult=1.5,
        _skip_statement_vi=False,
        _skip_bidsubmission=True,
        _premium_pct_delta=0.0,
        email_primary="p2@northerneng.example.com",
        mobile_primary="+91-9000000092",
        preferred_notification_channel="email",
        preferred_language="English",
        portal_username="northern-engineering",
        portal_credential_hash="synth_hash_b9_p2",
        portal_credential_status="active",
        past_blacklist_events=[],
        past_tender_participation=[
            dict(tender_id="hist_2023_ts_pwd_p2_a", year=2023, outcome="won", contract_value_cr=42.0),
            dict(tender_id="hist_2024_ts_pwd_p2_b", year=2024, outcome="won", contract_value_cr=45.0),
        ],
        past_anomaly_flags=[],
        authorized_signatory_name="Mr. N. Northern",
        authorized_signatory_role="CEO",
        communication_address="2-3-45, Engineers Layout, Hyderabad-500017",
        equipment_role_in_jv="Piling rigs (2 units) + 250 kVA generators (3 units)",
        personnel_roles_in_jv="Safety Officer + MEP Engineer (2 of 6)",
        similar_works_contributed=2,
    ),
    # ── B9.p3 — JV_PARTNER #3 (surveying specialist) ──
    "b9_p3": dict(
        profile_id="bid_synth_profile_b9_p3",
        company_name="M/s Southern Surveys & Services Pvt Ltd",
        gstin="33AAACS3333S3Z3",
        pan="AAACS3333S",
        contractor_class="Class-I",
        registration_state="TamilNadu",
        registration_authority="Tamil Nadu State Government",
        registration_certificate_no="TN/CL1/SSSP/2015/0142",
        registration_valid_until="2027-12-31",
        primary_business="Surveying + GIS + drone mapping services",
        years_in_business=11,
        average_5yr_turnover_cr=45.0,
        construction_turnover_5yr_avg_cr=45.0,
        financial_turnover_3yr_avg_cr=35.0,
        turnover_methodology_note=EXT3_TURNOVER_METHODOLOGY_NOTE,
        abc_formula_M_method="AP_GO_062_M2",
        abc_formula_rule_source="AP-GO-062",
        solvency_cert_validity_window_months=12,
        solvency_cert_source_rule="AP_GO_089_12MO",
        solvency_methodology_note=EXT5_SOLVENCY_METHODOLOGY_NOTE,
        company_reg_cert_status="VALID", company_reg_cert_node_id=None,
        pan_cert_status="VALID", gst_cert_status="VALID",
        epf_esi_cert_status="VALID",
        epf_esi_cert_value="EPF/TN/SSSP/2015/002211",
        form_12_declaration_status="SIGNED",
        poa_status="VALID",
        tender_fee_receipt_status="VALID", tender_fee_amount_cr=0.10,
        dsc_status="VALID", dsc_expiry_date="2027-06-30",
        bidder_type="JV_PARTNER",
        lead_partner_id=None,
        partner_ids=[],
        jv_agreement_node_id=None,
        jv_agreement_validity_until=None,
        liability_terms=None,
        max_completed_works_value_cr=22.0,
        existing_commitments_cr=15.0,
        abc_M_multiplier=2,
        solvency_cert_source="Bank",
        solvency_cert_validity_months_ago=5,
        litigation_count=0,
        blacklist_status="clean",
        equipment_register_completeness="full_owned",
        key_personnel_count=1,
        _similar_works_pattern="three_full",
        _boq_complete=True,
        _boq_line_item_count=95,
        _emd_bg_anomalous=False,
        _solvency_buffer_mult=1.5,
        _skip_statement_vi=False,
        _skip_bidsubmission=True,
        _premium_pct_delta=0.0,
        email_primary="p3@southernsurveys.example.com",
        mobile_primary="+91-9000000093",
        preferred_notification_channel="email",
        preferred_language="English",
        portal_username="southern-surveys-services",
        portal_credential_hash="synth_hash_b9_p3",
        portal_credential_status="active",
        past_blacklist_events=[],
        past_tender_participation=[
            dict(tender_id="hist_2024_tn_pwd_p3_a", year=2024, outcome="won", contract_value_cr=18.0),
        ],
        past_anomaly_flags=[],
        authorized_signatory_name="Mr. S. Southern",
        authorized_signatory_role="Director",
        communication_address="7-1-12, Surveyors Building, Chennai-600017",
        equipment_role_in_jv="Total-station + GPS surveying equipment + drone-mapping setup",
        personnel_roles_in_jv="Surveyor + Total-Station Operator (1 of 6 unique roles)",
        similar_works_contributed=1,
    ),
}


# ── Per-Statement fact builders ───────────────────────────────────────

def build_statement_i(profile: dict, tender: dict) -> dict:
    """Statement I — Annual Financial Turnover (5-year)."""
    avg = profile["average_5yr_turnover_cr"]
    fy_data = []
    # Reproducible smooth 5-year series around the average
    deltas = [-0.10, -0.05, 0.0, 0.07, 0.08]
    for i, fy in enumerate(["2021-22", "2022-23", "2023-24", "2024-25", "2025-26"]):
        fy_data.append(dict(
            year=fy,
            turnover_cr=round(avg * (1 + deltas[i]), 2),
            audited=(i < 4),
            auditor_ref=("ABC & Co Chartered Accountants M.No. 12345"
                         if i < 4 else "(provisional, audit in progress)"),
        ))
    qualified = avg >= tender["pq_turnover_floor_cr"]
    # Ext-3 dual turnover
    fin = profile.get("financial_turnover_3yr_avg_cr")
    fin_floor = tender.get("financial_pq_floor_cr")
    fin_qualified = (fin is not None and fin_floor is not None and fin >= fin_floor)
    return dict(
        bidder_name=profile["company_name"],
        bidder_pan=profile["pan"],
        tender_id=tender["tender_id"],
        tender_nit_no=tender["nit_no"],
        fy_data=fy_data,
        average_5yr_cr=avg,
        pq_floor_cr=tender["pq_turnover_floor_cr"],
        meets_pq_threshold=qualified,
        # Ext-3 dual turnover fields
        financial_3yr_cr=fin,
        financial_pq_floor_cr=fin_floor,
        meets_financial_pq_threshold=fin_qualified,
        _designed_to_trip=(
            "QUALIFIED (turnover ≥ PQ floor)" if qualified else
            f"INELIGIBLE — turnover {avg}cr < PQ floor {tender['pq_turnover_floor_cr']}cr "
            f"(2× annual contract value per CVC-028)"
        ),
    )


def build_statement_ii(profile: dict, tender: dict) -> dict:
    """Statement II — Similar Works Completed (last 10 FY)."""
    # B1: 3 similar works at 100% ECV; B2: 1 at 60%; B3: 0
    threshold_pct = tender["pq_similar_works_threshold_pct"]
    threshold_cr = tender["ecv_cr"] * threshold_pct / 100.0
    pattern = profile.get("_similar_works_pattern", "three_full")
    if pattern == "three_full":
        works = [
            dict(name="Govt Hospital, Vijayawada — Phase 1",
                 client="APIIC", ecv_cr=round(tender["ecv_cr"] * 1.0, 2),
                 award_date="2019-08-15", completion_date="2021-06-30",
                 compliance_pct=100, certificate_ref="EE/H&B/VJA/2021/0317"),
            dict(name="Educational Block, APCRDA Capital City",
                 client="APCRDA", ecv_cr=round(tender["ecv_cr"] * 0.95, 2),
                 award_date="2020-02-10", completion_date="2022-04-18",
                 compliance_pct=100, certificate_ref="EE/APCRDA/2022/0421"),
            dict(name="District Court Building, Kakinada",
                 client="AP-HCJ", ecv_cr=round(tender["ecv_cr"] * 0.90, 2),
                 award_date="2021-05-20", completion_date="2023-09-30",
                 compliance_pct=100, certificate_ref="EE/HCJ/KKD/2023/0907"),
        ]
        meets = True
    elif pattern == "one_at_60pct":
        works = [
            dict(name="Residential Quarters, AP Police Housing",
                 client="APPHC", ecv_cr=round(tender["ecv_cr"] * 0.60, 2),
                 award_date="2022-01-12", completion_date="2024-03-15",
                 compliance_pct=92, certificate_ref="EE/APPHC/2024/0205"),
        ]
        meets = False
    else:    # zero_works
        works = []
        meets = False
    # Ext-6 Counter-signature backfill: every existing work is GOVT-client
    # (APIIC / APCRDA / AP-HCJ / APPHC are all government bodies) with
    # EE-counter-signed completion certificates. Backfill applied per-entry
    # for forward-compat; B9 in Ext-8 may seed mixed GOVT/PSU/PRIVATE shapes.
    for w in works:
        w.setdefault("client_type", "GOVT")
        w.setdefault("counter_signature_status", "EE_SIGNED")
        w.setdefault("tds_certificate_node_id", None)
        w.setdefault("supporting_completion_certificate_node_id", None)
    return dict(
        bidder_name=profile["company_name"],
        tender_id=tender["tender_id"],
        threshold_pct=threshold_pct,
        threshold_value_cr=threshold_cr,
        similar_works=works,
        works_meeting_threshold=sum(1 for w in works
                                    if w["ecv_cr"] >= threshold_cr),
        meets_3_2_1_rule=meets,
        _designed_to_trip=(
            "QUALIFIED (≥3 similar works at ≥{0}% of ECV)".format(threshold_pct)
            if meets else
            "DISQUALIFIED — insufficient similar works per MPW 2022 §3.3.6 "
            "3/2/1 rule (3@40% / 2@50% / 1@80%)"
        ),
    )


def build_statement_iii(profile: dict, tender: dict) -> dict:
    """Statement III — Criterion for Satisfactorily Completed Projects."""
    s2 = build_statement_ii(profile, tender)
    return dict(
        bidder_name=profile["company_name"],
        tender_id=tender["tender_id"],
        works_listed=len(s2["similar_works"]),
        max_single_completion_pct=(max((w["compliance_pct"]
                                        for w in s2["similar_works"]),
                                       default=0)),
        all_at_or_above_80_pct=all(w["compliance_pct"] >= 80
                                   for w in s2["similar_works"]) if
                               s2["similar_works"] else False,
        _designed_to_trip="Mirrors Statement II qualification result",
    )


def build_statement_iv(profile: dict, tender: dict) -> dict:
    """Statement IV — Details of Bidder."""
    return dict(
        bidder_name=profile["company_name"],
        gstin=profile["gstin"],
        pan=profile["pan"],
        registered_office_address="3-5-XXX, Industrial Area, Vijayawada-520001",
        years_in_business=profile["years_in_business"],
        business_type="Private Limited Company" if "LLP" not in
                      profile["company_name"] else "LLP",
        primary_business=profile["primary_business"],
        contractor_class=profile["contractor_class"],
        registration_state=profile["registration_state"],
        registration_authority=profile["registration_authority"],
        registration_certificate_no=profile["registration_certificate_no"],
        registration_valid_until=profile["registration_valid_until"],
        tender_id=tender["tender_id"],
        required_class=tender["required_class"],
        class_eligible_for_tender=(
            profile["contractor_class"] == "Special"
            or (profile["contractor_class"] == "Class-I"
                and tender["ecv_cr"] <= 10.0)
        ),
        _designed_to_trip=(
            "QUALIFIED — class {0} ≥ required {1} for ECV {2}cr".format(
                profile["contractor_class"],
                tender["required_class"], tender["ecv_cr"])
            if (profile["contractor_class"] == "Special"
                or (profile["contractor_class"] == "Class-I"
                    and tender["ecv_cr"] <= 10.0))
            else
            "INELIGIBLE — class {0} insufficient for ECV {1}cr (requires {2}) per AP-GO-092".format(
                profile["contractor_class"], tender["ecv_cr"],
                tender["required_class"])
        ),
    )


def build_statement_v(profile: dict, tender: dict) -> dict:
    """Statement V — Availability of Critical Equipment."""
    completeness = profile["equipment_register_completeness"]
    if completeness == "full_owned":
        equipment = [
            dict(type="Concrete Batching Plant", count=2,
                 status="owned", invoice_ref="INV-2019-CBM-014"),
            dict(type="Tower Crane (40m boom)", count=2,
                 status="owned", invoice_ref="INV-2020-TC-007"),
            dict(type="Concrete Pump (52m)", count=1,
                 status="owned", invoice_ref="INV-2021-CP-019"),
            dict(type="Generator (250 kVA)", count=3,
                 status="owned", invoice_ref="INV-2019-GEN-022"),
            dict(type="Excavator (CAT 320)", count=2,
                 status="owned", invoice_ref="INV-2020-EXC-031"),
        ]
    elif completeness == "mixed_owned_leased":
        equipment = [
            dict(type="Concrete Batching Plant", count=1,
                 status="leased", lease_ref="LEASE-2023-CBM-014"),
            dict(type="Tower Crane", count=1,
                 status="leased", lease_ref="LEASE-2023-TC-007"),
            dict(type="Generator (250 kVA)", count=2,
                 status="owned", invoice_ref="INV-2021-GEN-019"),
        ]
    else:    # procurable_only
        equipment = [
            dict(type="Concrete Batching Plant", count=1,
                 status="procurable", note="To be procured via mob advance"),
            dict(type="Tower Crane", count=1,
                 status="procurable", note="Lease arrangement pending"),
        ]
    return dict(
        bidder_name=profile["company_name"],
        tender_id=tender["tender_id"],
        equipment_register=equipment,
        completeness_assessment=completeness,
        _designed_to_trip=(
            "QUALIFIED — equipment availability demonstrated"
            if completeness == "full_owned"
            else "PARTIAL — mix of owned/leased acceptable"
            if completeness == "mixed_owned_leased"
            else "GAP — procurable-only equipment register; lacks demonstrable access"
        ),
    )


def build_statement_vi(profile: dict, tender: dict) -> dict:
    """Statement VI — Availability of Key Personnel."""
    n = profile["key_personnel_count"]
    roles = [
        "Project Manager / Project-in-Charge",
        "Site / Construction Engineer",
        "Quality Assurance Engineer",
        "Safety Officer",
        "MEP / Electrical Engineer",
        "Surveyor / Total-Station Operator",
    ]
    personnel = []
    for i, role in enumerate(roles):
        if i < n:
            personnel.append(dict(
                role=role,
                name=f"Mr. K. {['Rao','Reddy','Sharma','Kumar','Patel','Singh'][i]}",
                qualification=["B.E. Civil", "B.E. Civil", "M.E. QA",
                               "B.E. + IIT Cert", "B.E. Electrical",
                               "Diploma + Total-Station Cert"][i],
                years_experience=[18, 12, 10, 8, 14, 9][i],
                membership_ref=f"PE/AP/{i+1:04d}",
            ))
        else:
            personnel.append(dict(role=role, name=None, status="vacant — to be hired post-award"))
    return dict(
        bidder_name=profile["company_name"],
        tender_id=tender["tender_id"],
        roles_filled=n,
        roles_total=len(roles),
        personnel=personnel,
        _designed_to_trip=(
            "QUALIFIED — all 6 key roles filled" if n == 6
            else "PARTIAL ({0}/6) — gap in key personnel; "
                 "may be acceptable if filled post-award".format(n) if n >= 4
            else "GAP — severe key personnel deficiency ({0}/6 filled)".format(n)
        ),
    )


def build_statement_vii(profile: dict, tender: dict) -> dict:
    """Statement VII — Litigation History."""
    n = profile["litigation_count"]
    cases = []
    if n >= 1:
        cases.append(dict(
            case_no="O.S. No. 2024/127, Vijayawada Civil Court",
            opposing_party="APIIC", year_filed=2024,
            subject="Contract dispute — payment recovery",
            status="Pending, next hearing 15-Jul-2026",
            disputed_amount_cr=4.2,
        ))
    if n >= 2:
        cases.append(dict(
            case_no="W.P. No. 2023/8721, AP High Court",
            opposing_party="AP Public Works Dept",
            year_filed=2023,
            subject="Termination challenge — substandard work allegation",
            status="Pending — interim stay against debarment list inclusion",
            disputed_amount_cr=11.5,
        ))
    return dict(
        bidder_name=profile["company_name"],
        tender_id=tender["tender_id"],
        litigation_count=n,
        cases=cases,
        _designed_to_trip=(
            "CLEAR — no litigation history with Govt"
            if n == 0 else
            f"FLAGGED — {n} active litigation case(s) with Government; "
            "evaluation committee review required"
        ),
    )


def build_statement_viii(profile: dict, tender: dict) -> dict:
    """Statement VIII — Financial Situation (solvency)."""
    cert_age = profile["solvency_cert_validity_months_ago"]
    cert_source = profile["solvency_cert_source"]
    # Reference: AP-GO-089 → 10% of class minimum, valid 1 year, Tahsildar or Bank
    if profile["contractor_class"] == "Special":
        class_min_cr = 10.0     # >Rs.10cr → Special class
        required_solvency_cr = 1.0    # 10% of 10cr
    elif profile["contractor_class"] == "Class-I":
        class_min_cr = 2.0
        required_solvency_cr = 0.2
    else:
        class_min_cr = 1.0
        required_solvency_cr = 0.1
    declared_solvency_cr = required_solvency_cr * profile.get("_solvency_buffer_mult", 1.0)
    return dict(
        bidder_name=profile["company_name"],
        tender_id=tender["tender_id"],
        certificate_source=cert_source,
        certificate_ref=f"{cert_source[:3].upper()}/AP/{profile['profile_id']}/2024/0123",
        issue_date=f"2026-{(5 - cert_age // 12)% 12 + 1:02d}-15",   # synthetic but consistent w/ age
        validity_months_ago=cert_age,
        is_within_one_year=(cert_age <= 12),
        class_minimum_cr=class_min_cr,
        required_solvency_cr=required_solvency_cr,
        declared_solvency_cr=declared_solvency_cr,
        meets_threshold=(declared_solvency_cr >= required_solvency_cr),
        liquid_assets_cr=declared_solvency_cr * 3,
        credit_facilities_cr=declared_solvency_cr * 2,
        _designed_to_trip=(
            "COMPLIANT — solvency cert valid (<1 year) AND ≥ required threshold"
            if cert_age <= 12 and declared_solvency_cr >= required_solvency_cr
            else
            "STALE — solvency cert {0} months old (>12 months per AP-GO-089)".format(cert_age)
            if cert_age > 12
            else "INSUFFICIENT — declared solvency below class threshold"
        ),
    )


def build_statement_ix(profile: dict, tender: dict) -> dict:
    """Statement IX — Work Plan & Methodology."""
    return dict(
        bidder_name=profile["company_name"],
        tender_id=tender["tender_id"],
        methodology_summary=(
            f"Proposed methodology covers {profile['primary_business']}. "
            f"Mobilisation in 30 days; Phase 1 substructure (pile + raft) "
            f"in months 1-6; Phase 2 superstructure in months 7-{tender['duration_months']-3}; "
            f"finishes + MEP commissioning in final {3} months. "
            f"Equipment deployment per Statement V. "
            f"Quality plan ISO 9001:2015. "
            f"ESMP per APPCB framework."
        ),
        milestone_schedule=[
            dict(milestone="Site mobilisation", month=1, deliverable="Site cleared, base camp ready"),
            dict(milestone="Foundation completion", month=6, deliverable="Substructure RCC complete"),
            dict(milestone="Superstructure 50%", month=tender["duration_months"]//2,
                 deliverable="50% structural completion"),
            dict(milestone="MEP commencement", month=tender["duration_months"]-6,
                 deliverable="MEP works started"),
            dict(milestone="Project handover", month=tender["duration_months"],
                 deliverable="Handover certificate"),
        ],
        _designed_to_trip="Narrative quality not yet evaluated by Tier-2 validators",
    )


def build_statement_x(profile: dict, tender: dict) -> dict:
    """Statement X — Bid Capacity Calculation (ABC)."""
    A = profile["max_completed_works_value_cr"]
    N = tender["duration_months"] / 12.0
    M = profile["abc_M_multiplier"]
    B = profile["existing_commitments_cr"]
    # AP-GO-062 prescribes M=2 exact
    abc = (A * N * M) - B
    qualifies = abc > tender["ecv_cr"]
    return dict(
        bidder_name=profile["company_name"],
        tender_id=tender["tender_id"],
        A_max_one_year_works_cr=A,
        N_completion_years=round(N, 2),
        M_multiplier=M,
        B_existing_commitments_cr=B,
        formula_used=f"ABC = ({A} × {N:.2f} × {M}) − {B} = {abc:.2f} cr",
        computed_abc_cr=round(abc, 2),
        ecv_cr=tender["ecv_cr"],
        qualifies=qualifies,
        formula_correct=(M == 2),
        _designed_to_trip=(
            f"QUALIFIED — ABC {abc:.2f}cr > ECV {tender['ecv_cr']}cr (formula M=2 correct)"
            if qualifies and M == 2
            else
            f"DISQUALIFIED — ABC formula uses M={M} (AP-GO-062 prescribes M=2 exact); "
            f"also ABC {abc:.2f}cr < ECV {tender['ecv_cr']}cr"
            if not qualifies and M != 2
            else
            f"FORMULA_WRONG — ABC numerically passes but formula M={M} violates AP-GO-062 M=2"
            if M != 2
            else
            f"DISQUALIFIED — ABC {abc:.2f}cr < ECV {tender['ecv_cr']}cr"
        ),
    )


STATEMENT_BUILDERS = {
    "Statement-I-AnnualTurnover":      build_statement_i,
    "Statement-II-SimilarWorks":       build_statement_ii,
    "Statement-III-SatisfactoryComplete": build_statement_iii,
    "Statement-IV-BidderDetails":      build_statement_iv,
    "Statement-V-CriticalEquipment":   build_statement_v,
    "Statement-VI-KeyPersonnel":       build_statement_vi,
    "Statement-VII-Litigation":        build_statement_vii,
    "Statement-VIII-FinancialSolvency":build_statement_viii,
    "Statement-IX-WorkPlan":           build_statement_ix,
    "Statement-X-BidCapacity":         build_statement_x,
}


# ── Supplementary builders ────────────────────────────────────────────

def build_letter_of_bid(profile: dict, tender: dict) -> dict:
    """LetterOfBid kg_node properties."""
    premium_pct = profile.get("_premium_pct_delta", -3.0)
    bid_amount_cr = tender["ecv_cr"] * (1.0 + premium_pct / 100.0)
    signatory = (
        f"{profile.get('authorized_signatory_name', 'Mr. K. Rao')}, "
        f"{profile.get('authorized_signatory_role', 'Managing Director')}"
    )
    return dict(
        bidder_name=profile["company_name"],
        tender_nit_no=tender["nit_no"],
        bid_amount_cr=round(bid_amount_cr, 2),
        bid_amount_words=f"Rupees {bid_amount_cr:.2f} crore only",
        premium_pct=round(premium_pct, 2),
        bid_type="Lump-Sum Percentage on ECV",
        bid_validity_days=90,
        emd_bg_reference=f"BG/{profile['profile_id']}/{tender['tender_id']}/EMD-001",
        emd_amount_cr=round(tender["ecv_cr"] * 0.01, 2),  # 1% of ECV
        site_inspection_confirmed=True,
        signing_authority=signatory,
        signature_date="2026-05-10",
    )


def build_emd_bg(profile: dict, tender: dict) -> dict:
    """EMD Bank Guarantee kg_node properties."""
    is_anomalous = profile.get("_emd_bg_anomalous", False)
    return dict(
        bidder_name=profile["company_name"],
        tender_nit_no=tender["nit_no"],
        bg_reference=f"BG/{profile['profile_id']}/{tender['tender_id']}/EMD-001",
        bg_amount_cr=round(tender["ecv_cr"] * 0.01, 2),
        bg_issuing_bank=("State Bank of India, Vijayawada Main Branch"
                         if not is_anomalous else
                         "Cooperative Bank of XYZ, Tirupati"),
        bg_issue_date="2026-05-08",
        bg_expiry_date=("2026-11-08" if not is_anomalous else "2026-04-15"),
        bg_validity_180_days=(not is_anomalous),
        bg_unconditional=True,
        bg_format_per_proforma=True,
        _designed_to_trip=(
            "VALID — 180-day BG from Scheduled Bank"
            if not is_anomalous else
            "EXPIRED — BG validity expired 25-Apr-2026, AND issued by Cooperative Bank "
            "(non-Scheduled) per AP-GO-050 acceptable-forms list"
        ),
    )


def build_priced_boq(profile: dict, tender: dict) -> dict:
    """Priced BoQ kg_node properties (top-line summary)."""
    premium_pct = profile.get("_premium_pct_delta", -3.0)
    bid_amount_cr = tender["ecv_cr"] * (1.0 + premium_pct / 100.0)
    is_complete = profile.get("_boq_complete", True)
    return dict(
        bidder_name=profile["company_name"],
        tender_nit_no=tender["nit_no"],
        total_bid_value_cr=round(bid_amount_cr, 2),
        line_item_count=profile.get("_boq_line_item_count", 250),
        major_heads=[
            "Site preparation + earthwork",
            "Substructure (foundation)",
            "Superstructure (RCC + masonry)",
            "Finishes (plaster, painting, flooring)",
            "MEP (electrical, plumbing, HVAC)",
            "External development",
        ],
        each_page_signed=is_complete,
        rates_in_figures_and_words_consistent=True,
        _designed_to_trip=(
            "COMPLIANT — all line items priced, signed page-by-page"
            if is_complete else
            "INCOMPLETE — fewer line items than scope; some pages unsigned"
        ),
    )


# ── DB helpers ────────────────────────────────────────────────────────

def _delete_prior() -> tuple[int, int, int]:
    """Delete prior bid_synth_* rows. Returns (kg_nodes, fact_sheets, kg_edges)."""
    # kg_nodes
    r = requests.get(f"{REST}/rest/v1/kg_nodes",
        params={"doc_id": "like.bid_synth_*", "select": "node_id"},
        headers=H, timeout=30)
    node_ids = [row["node_id"] for row in r.json()]
    for nid in node_ids:
        requests.delete(f"{REST}/rest/v1/kg_nodes",
            params={"node_id": f"eq.{nid}"}, headers=H, timeout=15)
    # kg_edges
    r = requests.get(f"{REST}/rest/v1/kg_edges",
        params={"doc_id": "like.bid_synth_*", "select": "edge_id"},
        headers=H, timeout=30)
    edge_ids = [row["edge_id"] for row in r.json()]
    for eid in edge_ids:
        requests.delete(f"{REST}/rest/v1/kg_edges",
            params={"edge_id": f"eq.{eid}"}, headers=H, timeout=15)
    # fact_sheets
    r = requests.get(f"{REST}/rest/v1/fact_sheets",
        params={"doc_id": "like.bid_synth_*", "select": "id"},
        headers=H, timeout=30)
    fact_ids = [row["id"] for row in r.json()]
    for fid in fact_ids:
        requests.delete(f"{REST}/rest/v1/fact_sheets",
            params={"id": f"eq.{fid}"}, headers=H, timeout=15)
    return len(node_ids), len(fact_ids), len(edge_ids)


import time


def _post_with_retry(url: str, *, headers: dict, json_body, timeout: int = 30,
                     max_attempts: int = 4) -> "requests.Response":
    """Lightweight retry-with-backoff on transient connection resets.
    Sub-block 1.2's 270+ sequential POSTs exposed Supabase rate-limit
    behavior (ConnectionResetError mid-batch). 4-attempt exponential
    backoff (0.5s/1s/2s) handles it without complicating the call sites."""
    last_exc = None
    for attempt in range(max_attempts):
        try:
            r = requests.post(url, headers=headers, json=json_body, timeout=timeout)
            r.raise_for_status()
            return r
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as exc:
            last_exc = exc
            if attempt == max_attempts - 1:
                break
            time.sleep(0.5 * (2 ** attempt))
    raise last_exc  # type: ignore[misc]


def _insert_node(doc_id: str, node_type: str, label: str,
                 properties: dict, source_ref: str = "synthetic_bid_seed_v1") -> str:
    r = _post_with_retry(f"{REST}/rest/v1/kg_nodes",
        headers={**H, "Prefer": "return=representation"},
        json_body=[dict(doc_id=doc_id, node_type=node_type, label=label,
                        properties=properties, source_ref=source_ref)],
        timeout=30)
    return r.json()[0]["node_id"]


def _insert_edge(doc_id: str, from_id: str, to_id: str, edge_type: str,
                 properties: dict | None = None) -> str:
    r = _post_with_retry(f"{REST}/rest/v1/kg_edges",
        headers={**H, "Prefer": "return=representation"},
        json_body=[dict(doc_id=doc_id, from_node_id=from_id, to_node_id=to_id,
                        edge_type=edge_type, weight=1.0,
                        properties=properties or {})],
        timeout=30)
    return r.json()[0]["edge_id"]


def _insert_fact_sheet(doc_id: str, fact_group: str, extracted_facts: dict,
                       section_heading: str) -> None:
    _post_with_retry(f"{REST}/rest/v1/fact_sheets",
        headers=H,
        json_body=[dict(doc_id=doc_id, fact_group=fact_group,
                        extracted_facts=extracted_facts,
                        section_heading=section_heading,
                        source_file=f"synthetic_bid_seed_{doc_id}",
                        line_start=1, line_end=1,
                        qdrant_similarity=None,
                        extracted_by="synthetic_bid_seed_v1")],
        timeout=30)


# ── Main seeding ──────────────────────────────────────────────────────

def main() -> int:
    print("══ Sub-block 1.1 — Synthetic Bid Data Seeding ══")

    # Pre-snapshot
    pre_kg = int(requests.get(f"{REST}/rest/v1/kg_nodes",
        params={"select": "node_id", "limit": "1"},
        headers={**H, "Prefer": "count=exact"}, timeout=15
        ).headers["Content-Range"].split("/")[1])
    pre_edges = int(requests.get(f"{REST}/rest/v1/kg_edges",
        params={"select": "edge_id", "limit": "1"},
        headers={**H, "Prefer": "count=exact"}, timeout=15
        ).headers["Content-Range"].split("/")[1])
    pre_facts = int(requests.get(f"{REST}/rest/v1/fact_sheets",
        params={"select": "id", "limit": "1"},
        headers={**H, "Prefer": "count=exact"}, timeout=15
        ).headers["Content-Range"].split("/")[1])
    print(f"  pre kg_nodes={pre_kg}  kg_edges={pre_edges}  fact_sheets={pre_facts}")

    # Idempotent cleanup
    n_n, n_f, n_e = _delete_prior()
    if n_n or n_f or n_e:
        print(f"  cleared prior bid_synth_*: {n_n} nodes, {n_f} fact_sheets, {n_e} edges")

    # 1. Insert BidderProfile nodes (3)
    profile_node_ids: dict[str, str] = {}
    for key, prof in PROFILES.items():
        nid = _insert_node(
            doc_id=prof["profile_id"],
            node_type="BidderProfile",
            label=f"{prof['company_name']} ({prof['contractor_class']})",
            properties=prof,
        )
        profile_node_ids[key] = nid
    print(f"  ✓ 3 BidderProfile nodes inserted")

    # 2. Per (bidder × tender): BidSubmission node + 10 fact_sheets + 3 supplementary + edges
    n_submission = 0
    n_supplementary = 0
    n_fact = 0
    n_edge = 0
    for bidder_key, prof in PROFILES.items():
        # Ext-8: JV_PARTNER profiles do NOT submit bids (only the JV entity does)
        if prof.get("_skip_bidsubmission"):
            continue
        for tender_key, tender in TENDERS.items():
            doc_id = f"bid_synth_{bidder_key}_{tender_key}"

            # BidSubmission node
            sub_id = _insert_node(
                doc_id=doc_id,
                node_type="BidSubmission",
                label=f"Bid: {prof['company_name']} → {tender['project_name'][:40]}",
                properties=dict(
                    bidder_profile_id=prof["profile_id"],
                    bidder_name=prof["company_name"],
                    tender_id=tender["tender_id"],
                    tender_nit_no=tender["nit_no"],
                    tender_ecv_cr=tender["ecv_cr"],
                    submission_date="2026-05-10",
                ),
            )
            n_submission += 1

            # 10 Statement fact_sheets (B5 skips Statement-VI to force
            # bid_personnel_check GAP path per L66 vocabulary coverage)
            for fact_group, builder in STATEMENT_BUILDERS.items():
                if (fact_group == "Statement-VI-KeyPersonnel"
                        and prof.get("_skip_statement_vi", False)):
                    continue
                facts = builder(prof, tender)
                _insert_fact_sheet(
                    doc_id=doc_id, fact_group=fact_group,
                    extracted_facts=facts,
                    section_heading=fact_group.replace("-", " "),
                )
                n_fact += 1

            # 3 supplementary nodes
            lob_id = _insert_node(
                doc_id=doc_id, node_type="LetterOfBid",
                label=f"LoB: {prof['company_name']} → {tender['project_name'][:40]}",
                properties=build_letter_of_bid(prof, tender),
            )
            n_supplementary += 1
            emd_id = _insert_node(
                doc_id=doc_id, node_type="EMD_BG",
                label=f"EMD BG: {prof['company_name']} for {tender['nit_no']}",
                properties=build_emd_bg(prof, tender),
            )
            n_supplementary += 1
            boq_id = _insert_node(
                doc_id=doc_id, node_type="PricedBoQ",
                label=f"Priced BoQ: {prof['company_name']} for {tender['nit_no']}",
                properties=build_priced_boq(prof, tender),
            )
            n_supplementary += 1

            # 2 edges per submission: SUBMITTED_BY + BIDS_FOR_TENDER
            _insert_edge(doc_id, sub_id, profile_node_ids[bidder_key],
                         "SUBMITTED_BY",
                         properties=dict(submission_date="2026-05-10"))
            n_edge += 1
            # Tender doc node may not exist yet; record the tender_id as
            # a property on the edge instead of pointing to a node. Use
            # the BidSubmission node as both endpoints with the tender_id
            # in properties — Tier-2 validators will resolve via tender_id.
            _insert_edge(doc_id, sub_id, sub_id, "BIDS_FOR_TENDER",
                         properties=dict(tender_id=tender["tender_id"],
                                         tender_nit_no=tender["nit_no"]))
            n_edge += 1

    # Post-snapshot
    post_kg = int(requests.get(f"{REST}/rest/v1/kg_nodes",
        params={"select": "node_id", "limit": "1"},
        headers={**H, "Prefer": "count=exact"}, timeout=15
        ).headers["Content-Range"].split("/")[1])
    post_edges = int(requests.get(f"{REST}/rest/v1/kg_edges",
        params={"select": "edge_id", "limit": "1"},
        headers={**H, "Prefer": "count=exact"}, timeout=15
        ).headers["Content-Range"].split("/")[1])
    post_facts = int(requests.get(f"{REST}/rest/v1/fact_sheets",
        params={"select": "id", "limit": "1"},
        headers={**H, "Prefer": "count=exact"}, timeout=15
        ).headers["Content-Range"].split("/")[1])

    print()
    print(f"  ✓ {n_submission} BidSubmission nodes inserted")
    print(f"  ✓ {n_supplementary} supplementary nodes inserted (LetterOfBid + EMD + BoQ)")
    print(f"  ✓ {n_fact} fact_sheets rows inserted")
    print(f"  ✓ {n_edge} kg_edges inserted")
    print()
    print(f"  kg_nodes    : {pre_kg} → {post_kg}  (delta={post_kg-pre_kg}, expected +{3+n_submission+n_supplementary})")
    print(f"  kg_edges    : {pre_edges} → {post_edges}  (delta={post_edges-pre_edges}, expected +{n_edge})")
    print(f"  fact_sheets : {pre_facts} → {post_facts}  (delta={post_facts-pre_facts}, expected +{n_fact})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
