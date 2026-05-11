"""Module 3 Sub-block 1.1 — Synthetic bid data generator.

Bootstraps the data needed to develop and verify Tier-2 Bid Evaluator
validators (Sub-blocks 3-6). NO actual Tier-2 validator runs in this
script — pure data-layer seeding.

== Scope ==
  3 tenders (Kurnool, JA, HC — using stable synthetic tender_id
  references; the drafter-output doc_ids are short-loop development
  artefacts that change per regen, so we use deterministic
  `tender_synth_<key>` identifiers in the bid data).

  3 bidder profiles per tender = 9 BidSubmission total:
    B1 — "Clean Contractor"      (Special class, 250cr turnover, M=2, clean)
    B2 — "Marginal — Class/Solv" (Class-I, 80cr turnover, stale solvency)
    B3 — "Anomalous"             (Special class, 30cr turnover, M=3 wrong, litigation, debarment)

  10 Statement rows per bid in `fact_sheets` (90 total).

  3 supplementary kg_nodes per bid (LetterOfBid + EMD BG + PricedBoQ) = 27.

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
        pq_turnover_floor_cr=121.7,   # 2× annual contract value
        pq_similar_works_threshold_pct=80,
    ),
    "ja": dict(
        tender_id="tender_synth_ja",
        project_name="Construction of Andhra Pradesh Judicial Academy",
        ecv_cr=125.5,
        duration_months=24,
        nit_no="130/MAU61-USI0HB(BG)/7/2026",
        required_class="Special",
        pq_turnover_floor_cr=83.7,    # 2× (125.5/3)
        pq_similar_works_threshold_pct=80,
    ),
    "hc": dict(
        tender_id="tender_synth_hc",
        project_name="Construction of the new Andhra Pradesh High Court complex",
        ecv_cr=365.16,
        duration_months=24,
        nit_no="HC/APCRDA/2026/PROC/001",
        required_class="Special",
        pq_turnover_floor_cr=243.4,   # 2× (365.16/3)
        pq_similar_works_threshold_pct=80,
    ),
}


# ── Bidder profiles (3 — shared across all 3 tenders) ─────────────────

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
        max_completed_works_value_cr=145.0,       # A factor in ABC
        existing_commitments_cr=180.0,            # B factor in ABC
        abc_M_multiplier=2,                       # Correct per AP-GO-062
        solvency_cert_source="Tahsildar",
        solvency_cert_validity_months_ago=6,      # <12mo → COMPLIANT
        litigation_count=0,
        blacklist_status="clean",
        equipment_register_completeness="full_owned",
        key_personnel_count=6,
    ),
    "b2": dict(
        profile_id="bid_synth_profile_b2",
        company_name="M/s Marginal Construction Pvt Ltd",
        gstin="37AAACM5678B2Z7",
        pan="AAACM5678B",
        contractor_class="Class-I",   # ECV >2cr & ≤10cr → Class-I (insufficient for ECV>10cr)
        registration_state="AndhraPradesh",
        registration_authority="AP State Government per GO Ms No 94/2003",
        registration_certificate_no="AP/I/MARGINAL/2020/0789",
        registration_valid_until="2027-03-31",
        primary_business="Building construction (mid-tier)",
        years_in_business=12,
        average_5yr_turnover_cr=80.0,
        max_completed_works_value_cr=45.0,
        existing_commitments_cr=60.0,
        abc_M_multiplier=2,
        solvency_cert_source="Bank",
        solvency_cert_validity_months_ago=14,     # >12mo → STALE per AP-GO-089
        litigation_count=0,
        blacklist_status="clean",
        equipment_register_completeness="mixed_owned_leased",
        key_personnel_count=4,                    # 4 of 6 → GAP
    ),
    "b3": dict(
        profile_id="bid_synth_profile_b3",
        company_name="M/s Anomalous Builders LLP",
        gstin="37AAACA9999C3Z9",
        pan="AAACA9999C",
        contractor_class="Special",   # Class is fine, but other factors fail
        registration_state="AndhraPradesh",
        registration_authority="AP State Government per GO Ms No 94/2003",
        registration_certificate_no="AP/SC/ANOMALOUS/2019/0456",
        registration_valid_until="2026-08-31",
        primary_business="Civil construction",
        years_in_business=8,
        average_5yr_turnover_cr=30.0,              # Way below PQ floor
        max_completed_works_value_cr=18.0,
        existing_commitments_cr=42.0,
        abc_M_multiplier=3,                        # Wrong per AP-GO-062 (M=2 required)
        solvency_cert_source="Tahsildar",
        solvency_cert_validity_months_ago=3,
        litigation_count=2,                        # Active litigation w/ Govt
        blacklist_status="previously_debarred",   # 18-month debar 2023, expired
        equipment_register_completeness="procurable_only",   # Severe gap
        key_personnel_count=2,                    # 2 of 6 → severe gap
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
    return dict(
        bidder_name=profile["company_name"],
        bidder_pan=profile["pan"],
        tender_id=tender["tender_id"],
        tender_nit_no=tender["nit_no"],
        fy_data=fy_data,
        average_5yr_cr=avg,
        pq_floor_cr=tender["pq_turnover_floor_cr"],
        meets_pq_threshold=qualified,
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
    if profile["profile_id"].endswith("b1"):
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
    elif profile["profile_id"].endswith("b2"):
        works = [
            dict(name="Residential Quarters, AP Police Housing",
                 client="APPHC", ecv_cr=round(tender["ecv_cr"] * 0.60, 2),
                 award_date="2022-01-12", completion_date="2024-03-15",
                 compliance_pct=92, certificate_ref="EE/APPHC/2024/0205"),
        ]
        meets = False
    else:
        works = []
        meets = False
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
    declared_solvency_cr = required_solvency_cr * 1.5 if profile["profile_id"].endswith("b1") else required_solvency_cr
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
    bid_amount_cr = tender["ecv_cr"] * (0.95 if profile["profile_id"].endswith("b1")
                                         else 0.98 if profile["profile_id"].endswith("b2")
                                         else 1.05)   # B3 over-bid (uncompetitive)
    premium_pct = round((bid_amount_cr - tender["ecv_cr"]) / tender["ecv_cr"] * 100, 2)
    return dict(
        bidder_name=profile["company_name"],
        tender_nit_no=tender["nit_no"],
        bid_amount_cr=round(bid_amount_cr, 2),
        bid_amount_words=f"Rupees {bid_amount_cr:.2f} crore only",
        premium_pct=premium_pct,
        bid_type="Lump-Sum Percentage on ECV",
        bid_validity_days=90,
        emd_bg_reference=f"BG/{profile['profile_id']}/{tender['tender_id']}/EMD-001",
        emd_amount_cr=round(tender["ecv_cr"] * 0.01, 2),  # 1% of ECV
        site_inspection_confirmed=True,
        signing_authority="Mr. K. Rao, Managing Director",
        signature_date="2026-05-10",
    )


def build_emd_bg(profile: dict, tender: dict) -> dict:
    """EMD Bank Guarantee kg_node properties."""
    is_anomalous = profile["profile_id"].endswith("b3")
    return dict(
        bidder_name=profile["company_name"],
        tender_nit_no=tender["nit_no"],
        bg_reference=f"BG/{profile['profile_id']}/{tender['tender_id']}/EMD-001",
        bg_amount_cr=round(tender["ecv_cr"] * 0.01, 2),
        bg_issuing_bank=("State Bank of India, Vijayawada Main Branch"
                         if not is_anomalous else
                         "Cooperative Bank of XYZ, Tirupati"),
        bg_issue_date="2026-05-08",
        bg_expiry_date=("2026-11-08" if not is_anomalous else "2026-04-15"),  # B3 expired
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
    bid_amount_cr = tender["ecv_cr"] * (0.95 if profile["profile_id"].endswith("b1")
                                         else 0.98 if profile["profile_id"].endswith("b2")
                                         else 1.05)
    return dict(
        bidder_name=profile["company_name"],
        tender_nit_no=tender["nit_no"],
        total_bid_value_cr=round(bid_amount_cr, 2),
        line_item_count=(285 if profile["profile_id"].endswith("b1")
                         else 245 if profile["profile_id"].endswith("b2")
                         else 198),    # B3 incomplete BoQ
        major_heads=[
            "Site preparation + earthwork",
            "Substructure (foundation)",
            "Superstructure (RCC + masonry)",
            "Finishes (plaster, painting, flooring)",
            "MEP (electrical, plumbing, HVAC)",
            "External development",
        ],
        each_page_signed=(not profile["profile_id"].endswith("b3")),
        rates_in_figures_and_words_consistent=True,
        _designed_to_trip=(
            "COMPLIANT — all line items priced, signed page-by-page"
            if not profile["profile_id"].endswith("b3") else
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


def _insert_node(doc_id: str, node_type: str, label: str,
                 properties: dict, source_ref: str = "synthetic_bid_seed_v1") -> str:
    r = requests.post(f"{REST}/rest/v1/kg_nodes",
        headers={**H, "Prefer": "return=representation"},
        json=[dict(doc_id=doc_id, node_type=node_type, label=label,
                   properties=properties, source_ref=source_ref)],
        timeout=30)
    r.raise_for_status()
    return r.json()[0]["node_id"]


def _insert_edge(doc_id: str, from_id: str, to_id: str, edge_type: str,
                 properties: dict | None = None) -> str:
    r = requests.post(f"{REST}/rest/v1/kg_edges",
        headers={**H, "Prefer": "return=representation"},
        json=[dict(doc_id=doc_id, from_node_id=from_id, to_node_id=to_id,
                   edge_type=edge_type, weight=1.0,
                   properties=properties or {})],
        timeout=30)
    r.raise_for_status()
    return r.json()[0]["edge_id"]


def _insert_fact_sheet(doc_id: str, fact_group: str, extracted_facts: dict,
                       section_heading: str) -> None:
    r = requests.post(f"{REST}/rest/v1/fact_sheets",
        headers=H,
        json=[dict(doc_id=doc_id, fact_group=fact_group,
                   extracted_facts=extracted_facts,
                   section_heading=section_heading,
                   source_file=f"synthetic_bid_seed_{doc_id}",
                   line_start=1, line_end=1,
                   qdrant_similarity=None,
                   extracted_by="synthetic_bid_seed_v1")],
        timeout=30)
    r.raise_for_status()


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

            # 10 Statement fact_sheets
            for fact_group, builder in STATEMENT_BUILDERS.items():
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
