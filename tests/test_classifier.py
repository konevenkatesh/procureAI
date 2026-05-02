"""Tests for engines.classifier.TenderClassifier."""
from __future__ import annotations

from pathlib import Path

import pytest

from engines.classifier import TenderClassifier, TenderClassification


CLF = TenderClassifier()


# ─────────────────────────────────────────────────────────────────────────────
# Synthetic fixtures matching the 3 hackathon documents (which aren't on disk)
# Each fixture is intentionally minimal but contains the salient signals.
# ─────────────────────────────────────────────────────────────────────────────

RFP_PMC_FISHING_HARBOURS = """
REQUEST FOR PROPOSAL (RFP)
Selection of Project Management Consultant (PMC) for the Construction of
Fishing Harbours along the Coast of Andhra Pradesh

Issued by: Department of Fisheries, Government of Andhra Pradesh

Selection Method: Quality and Cost Based Selection (QCBS)
The consulting firm shall submit a Technical Proposal and Financial Proposal
in two separate sealed envelopes (two-cover system).

Terms of Reference (TOR):
The Consultant shall provide construction supervision and project management
consulting services for the development of fishing harbours.

Estimated Value: Rs. 15 crore for 36 months consulting services.

Contract period: 36 months from the effective date.

Key Personnel required:
- Team Leader (15 years experience)
- Senior Marine Engineer (12 years)
- Quality Engineer

Financial proposal must be submitted in sealed cover.
Bid security (EMD) of Rs. 30 lakh required.
Tendering will be through apeprocurement.gov.in.
"""

CORRIGENDUM_1 = """
CORRIGENDUM No. 1

To: Construction of Andhra Pradesh Judicial Academy with Pile Foundation,
Super Structure, RCC Framed Construction, MEP Works, External Development
on EPC Lump-Sum Turnkey basis with single responsibility.

Issued by: Andhra Pradesh Capital Region Development Authority (APCRDA)
through AGICL.

Estimated Contract Value: Rs. 350 crore.
Construction period: 24 months.

Engineering, Procurement and Construction (EPC) — single responsibility
contract. Bill of Quantities (BOQ) shall not apply for the lump-sum
component; schedule of rates applies only to reimbursable items.

Bid security (EMD) shall be 2.5% of bid amount.
Performance Guarantee: 5% of contract value.
Reverse tendering will be conducted as per GO Ms No 79/2020.
Two-cover system: Technical bid + Financial bid.
"""

EVALUATION_STATEMENTS = """
EVALUATION STATEMENT

Tender for Construction of Multi-Storeyed Quarters of 432 Apartment units
in 18 towers (S+12) for Hon'ble MLAs and All India Services Officers
near Nelapadu, Amaravati, Andhra Pradesh.

Issued by: APCRDA / AGICL
Procurement Method: Open Tender — EPC lump-sum turnkey contract.

Estimated Cost: Rs. 1200 crore. Construction period: 36 months.

Technical Bid Evaluation:
Bidder              Technical Score   Status
ABC Constructions   85                Qualified
XYZ Infra           72                Qualified
LMN Engg            65                Disqualified

Financial Bid Evaluation will be carried out for technically qualified
bidders only. Two-cover system applies.

Bill of Quantities (BOQ) attached as Annexure-I.
Engineer-in-Charge: Chief Engineer, AGICL.
Site of work: Amaravati, Andhra Pradesh.
"""


# ─────────────────────────────────────────────────────────────────────────────
# 1. RFP_PMC_Fishing_Harbours → Consultancy / QCBS
# ─────────────────────────────────────────────────────────────────────────────

def test_rfp_pmc_fishing_harbours_classifies_as_consultancy_qcbs():
    r: TenderClassification = CLF.classify(RFP_PMC_FISHING_HARBOURS)
    assert r.primary_type == "Consultancy", f"got {r.primary_type}"
    assert r.procurement_method == "QCBS"
    assert r.cover_system == "Two"
    assert r.estimated_value == 15_00_00_000  # Rs.15 cr
    assert r.duration_months == 36
    assert r.is_ap_tender is True
    assert r.confidence >= 0.75
    assert r.needs_human_confirmation is False


# ─────────────────────────────────────────────────────────────────────────────
# 2. Corrigendum_1 → Works/EPC
# ─────────────────────────────────────────────────────────────────────────────

def test_corrigendum_1_classifies_as_epc():
    r = CLF.classify(CORRIGENDUM_1)
    assert r.primary_type == "EPC", f"got {r.primary_type}"
    assert r.cover_system == "Two"
    assert r.estimated_value == 350_00_00_000  # Rs.350 cr
    assert r.is_ap_tender is True
    assert "has_corrigendum" in r.special_flags
    assert "has_reverse_tendering" in r.special_flags
    assert r.confidence >= 0.75


# ─────────────────────────────────────────────────────────────────────────────
# 3. Evaluation_Statements → Works/EPC
# ─────────────────────────────────────────────────────────────────────────────

def test_evaluation_statements_classifies_as_works_or_epc():
    r = CLF.classify(EVALUATION_STATEMENTS)
    assert r.primary_type in ("EPC", "Works"), f"got {r.primary_type}"
    assert r.cover_system == "Two"
    assert r.estimated_value == 1200_00_00_000  # Rs.1200 cr
    assert r.is_ap_tender is True
    assert r.procurement_method == "Open"
    assert "has_evaluation_form" in r.special_flags
    assert r.department in ("APCRDA", "AGICL")


# ─────────────────────────────────────────────────────────────────────────────
# Real-document tests — using the corpus we have on disk
# ─────────────────────────────────────────────────────────────────────────────

REPO = Path(__file__).resolve().parent.parent
PROCESSED_MD = REPO / "source_documents" / "e_procurement" / "processed_md"


@pytest.mark.skipif(not PROCESSED_MD.exists(), reason="processed_md not on disk")
def test_real_rfp_tirupathi_is_epc_or_consultancy():
    """RFP_Tirupathi is a 12 MW WtE Plant on PPP basis — should classify as EPC."""
    text = (PROCESSED_MD / "RFP_Tirupathi_NITI_01042026.md").read_text()
    r = CLF.classify(text)
    assert r.primary_type in ("EPC", "Consultancy"), f"got {r.primary_type}"
    assert r.is_ap_tender is True


@pytest.mark.skipif(not PROCESSED_MD.exists(), reason="processed_md not on disk")
def test_real_judicial_academy_is_epc():
    """Judicial Academy is an APCRDA construction tender on EPC lump-sum basis."""
    text = (PROCESSED_MD / "Bid Document of Judicial Academy.md").read_text()
    r = CLF.classify(text)
    assert r.primary_type in ("EPC", "Works"), f"got {r.primary_type}"
    assert r.is_ap_tender is True


# ─────────────────────────────────────────────────────────────────────────────
# Edge cases
# ─────────────────────────────────────────────────────────────────────────────

def test_empty_text_returns_unknown():
    r = CLF.classify("")
    assert r.primary_type == "Unknown"
    assert r.confidence == 0.0
    assert r.needs_human_confirmation is True


def test_low_confidence_when_only_one_signal():
    r = CLF.classify("This document mentions construction of a road.")
    # Only "construction of" matches Works; no value, no method, no cover.
    assert r.confidence < 0.75
    assert r.needs_human_confirmation is True


def test_multilateral_funding_world_bank():
    text = "This is a Works contract funded by the World Bank under IBRD loan agreement."
    r = CLF.classify(text)
    assert r.funding_source == "world_bank"
