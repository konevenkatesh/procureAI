"""Tests for engines.parameter_cascade.ParameterCascadeEngine."""
from __future__ import annotations

import pytest

from engines.parameter_cascade import ParameterCascadeEngine, TenderInputs


ENGINE = ParameterCascadeEngine()


# ─────────────────────────────────────────────────────────────────────────────
# 1. AP works tender Rs.50 lakh — verify EMD = 2.5%, reverse-tender NOT mandatory
# ─────────────────────────────────────────────────────────────────────────────

def test_ap_works_50_lakh():
    p = ENGINE.compute(TenderInputs(
        department="R&B",
        tender_type="Works",
        estimated_value=50_00_000,        # Rs.50 lakh
        duration_months=18,
        procurement_method="Open",
        is_ap_tender=True,
    ))
    assert p.emd_percentage == 2.5, "AP EMD should be 2.5% (GO 94/2003)"
    assert p.emd_amount == 1_25_000.0, "EMD amount = 2.5% × Rs.50L = Rs.1.25L"
    assert p.reverse_tender_mandatory is False, "Below Rs.1 cr threshold"
    assert p.e_procurement_mandatory is True, "Above AP Rs.1 lakh threshold"
    assert p.price_adjustment_applicable is True, "Rs.40L+ AND ≥ 6 months"
    assert p.dlp_months == 24
    assert p.arbitration_allowed is False, "AP Works route claims to civil court"
    assert p.open_tender_required is True, "Above Rs.2,500 AP works threshold"


# Bonus boundary: AP works at exactly Rs.1 crore — reverse-tender becomes mandatory
def test_ap_works_1_crore_reverse_tender_boundary():
    p = ENGINE.compute(TenderInputs(
        department="R&B",
        tender_type="Works",
        estimated_value=1_00_00_000,      # Rs.1 crore
        duration_months=12,
        procurement_method="Open",
        is_ap_tender=True,
    ))
    assert p.reverse_tender_mandatory is True


# ─────────────────────────────────────────────────────────────────────────────
# 2. Central goods tender Rs.1 crore — verify EMD = 2%, PBG = 5%
# ─────────────────────────────────────────────────────────────────────────────

def test_central_goods_1_crore():
    p = ENGINE.compute(TenderInputs(
        department="DGS&D",
        tender_type="Goods",
        estimated_value=1_00_00_000,      # Rs.1 crore
        duration_months=6,
        procurement_method="Open",
        is_ap_tender=False,
    ))
    assert p.emd_percentage == 2.0, "Central EMD per GFR Rule 170"
    assert p.emd_amount == 2_00_000.0, "2% × Rs.1 cr = Rs.2 lakh"
    assert p.pbg_percentage == 5.0, "Central goods PBG = 5% (MPW default)"
    assert p.pbg_amount == 5_00_000.0
    assert p.reverse_tender_mandatory is False, "Central reverse tendering is permissive"
    assert p.arbitration_allowed is True, "Central retains arbitration option"
    assert p.e_procurement_mandatory is True, "Above Rs.2 lakh CVC threshold"


# ─────────────────────────────────────────────────────────────────────────────
# 3. AP consultancy QCBS Rs.5 crore — verify Integrity Pact required + 2-cover
# ─────────────────────────────────────────────────────────────────────────────

def test_ap_consultancy_5_crore_integrity_pact():
    p = ENGINE.compute(TenderInputs(
        department="MAUD",
        tender_type="Consultancy",
        estimated_value=5_00_00_000,      # Rs.5 crore
        duration_months=12,
        procurement_method="Open",
        is_ap_tender=True,
    ))
    assert p.integrity_pact_required is True, "Above Rs.5 crore CVC threshold"
    assert p.two_cover_required is True, "QCBS consultancy is always two-envelope"
    assert p.bid_validity_days == 180, "Consultancy default = 180 days"
    assert p.dlp_months == 6
    assert p.judicial_preview_required is False, "Only Works/EPC trigger JP"
    assert p.reverse_tender_mandatory is True, "Above AP Rs.1 cr threshold"


# ─────────────────────────────────────────────────────────────────────────────
# 4. AP infrastructure Rs.200 crore — verify Judicial Preview required
# ─────────────────────────────────────────────────────────────────────────────

def test_ap_infrastructure_200_crore_judicial_preview():
    p = ENGINE.compute(TenderInputs(
        department="MAUD",
        tender_type="Works",
        estimated_value=200_00_00_000,    # Rs.200 crore
        duration_months=36,
        procurement_method="Open",
        is_ap_tender=True,
    ))
    assert p.judicial_preview_required is True, "AP JP Act 2019 applies ≥ Rs.100 cr"
    assert p.integrity_pact_required is True
    assert p.reverse_tender_mandatory is True
    assert p.price_adjustment_applicable is True
    assert p.bid_validity_days == 90  # Works/Open
    assert p.bg_validity_days == 120
    assert p.two_cover_required is True


# ─────────────────────────────────────────────────────────────────────────────
# 5. MSE participation — verify EMD capped at Rs.2 lakh
# ─────────────────────────────────────────────────────────────────────────────

def test_mse_emd_capped_at_2_lakh():
    p = ENGINE.compute(TenderInputs(
        department="R&B",
        tender_type="Works",
        estimated_value=10_00_00_000,     # Rs.10 crore — uncapped EMD would be Rs.25 lakh
        duration_months=24,
        procurement_method="Open",
        is_ap_tender=True,
        mse_participation=True,
    ))
    assert p.emd_mse_capped is True
    assert p.emd_amount == 2_00_000.0, "EMD capped at Rs.2 lakh per MSME policy"
    # The percentage stays at 2.5% (informational); the cap is on the absolute amount
    assert p.emd_percentage == 2.5


# ─────────────────────────────────────────────────────────────────────────────
# 6. Startup participation — verify EMD waived
# ─────────────────────────────────────────────────────────────────────────────

def test_startup_emd_waived():
    p = ENGINE.compute(TenderInputs(
        department="ITE&C",
        tender_type="Goods",
        estimated_value=50_00_000,        # Rs.50 lakh
        duration_months=6,
        procurement_method="Open",
        is_ap_tender=True,
        startup_participation=True,
    ))
    assert p.emd_percentage == 0.0, "Startup EMD waived per DPIIT policy"
    assert p.emd_amount == 0.0
    assert p.emd_mse_capped is False, "Cap flag stays False — EMD is waived, not capped"


# ─────────────────────────────────────────────────────────────────────────────
# Consistency / structural tests
# ─────────────────────────────────────────────────────────────────────────────

def test_bg_validity_is_bid_validity_plus_30():
    """Universal: BG validity always equals bid validity + 30 days."""
    for tender_type in ("Works", "Goods", "Consultancy", "EPC"):
        p = ENGINE.compute(TenderInputs(
            department="X", tender_type=tender_type,
            estimated_value=1_00_00_000, duration_months=12,
            procurement_method="Open", is_ap_tender=True,
        ))
        assert p.bg_validity_days == p.bid_validity_days + 30, (
            f"{tender_type}: BG validity should be bid validity + 30"
        )


def test_anti_collusion_always_required():
    p = ENGINE.compute(TenderInputs(
        department="X", tender_type="Goods",
        estimated_value=10_00_000, duration_months=3,
        procurement_method="Limited", is_ap_tender=True,
    ))
    assert p.anti_collusion_form_required is True
