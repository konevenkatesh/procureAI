"""End-to-end test for RuleVerificationEngine on a synthetic tender."""
from __future__ import annotations

import json

import pytest

from modules.validator.rule_verification_engine import (
    RuleVerificationEngine,
    ValidationReport,
)


SYNTHETIC_TENDER = """
TENDER NOTICE
Construction of Multi-Storeyed Government Quarters at Amaravati,
Andhra Pradesh — Open Tender on Lump-Sum Turnkey basis with single
responsibility (EPC contract).

Issued by: Andhra Pradesh Capital Region Development Authority (APCRDA)
through AGICL.

Estimated Contract Value: Rs. 10 crore.
Construction period: 18 months from the date of LOA.
Tendering will be conducted through apeprocurement.gov.in.

INSTRUCTIONS TO BIDDERS

Bid Security / Earnest Money Deposit (EMD): The Bidder shall furnish
Earnest Money Deposit of 2% of estimated contract value as Bank
Guarantee, valid for 90 days from the bid submission date.

Performance Guarantee: The successful bidder shall furnish a
Performance Guarantee of 5% of contract value.

Bid Validity: Tenders shall remain valid for 90 days.

ELIGIBILITY:
Participation in this tendering process by forming Joint Venture or
Consortium or Special Purpose Vehicle is NOT ALLOWED. Any contractor
from abroad is NOT permitted.

GENERAL CONDITIONS:
Bills shall be paid as per APSS conditions. Engineer-in-Charge will
be the Chief Engineer, AGICL. Site of work: Nelapadu, Amaravati.
"""


def test_synthetic_tender_end_to_end():
    """The synthetic tender contains 3 deliberate issues:
    1. EMD stated as 2% (AP expects 2.5% — should fire EMD-Shortfall)
    2. No integrity-pact clause despite Rs.10 cr value (should fire
       Missing-Integrity-Pact at HARD_BLOCK)
    3. JV/foreign-bidder ban without justification (should fire
       Criteria-Restriction-Narrow)
    """
    engine = RuleVerificationEngine()
    report: ValidationReport = engine.verify(
        SYNTHETIC_TENDER, document_name="synthetic_amaravati_quarters"
    )

    # ── Tender classification ──
    assert report.classification.is_ap_tender is True
    # EPC because "EPC" + "lump sum" + "turnkey" + "single responsibility"
    assert report.classification.primary_type in ("EPC", "Works")
    assert report.classification.estimated_value == 10_00_00_000

    # ── Cascade computed correctly for AP EPC at Rs.10 cr ──
    p = report.parameters
    assert p.emd_percentage == 2.5
    assert p.integrity_pact_required is True   # Rs.10 cr > Rs.5 cr threshold
    assert p.reverse_tender_mandatory is True  # ≥ Rs.1 cr (AP)
    assert p.bid_validity_days == 180          # EPC default = 180

    # ── Findings should include the 3 deliberate issues ──
    all_findings = report.hard_blocks + report.warnings + report.advisories
    typologies_found = {f.typology_code for f in all_findings}
    assert "EMD-Shortfall" in typologies_found, (
        "Document EMD = 2% should be flagged below AP-expected 2.5%"
    )
    assert "Missing-Integrity-Pact" in typologies_found, (
        "Rs.10 cr tender without integrity pact should be flagged"
    )
    assert "Criteria-Restriction-Narrow" in typologies_found, (
        "JV/foreign-bidder ban without justification should be flagged"
    )

    # The EMD-Shortfall finding should be DEFEATED by AP-GO-050 because the
    # document is an AP tender (defeasibility resolution downgrades to ADVISORY)
    emd_findings = [f for f in all_findings if f.typology_code == "EMD-Shortfall"]
    assert any(f.defeated_by for f in emd_findings), (
        "AP EMD-Shortfall should be defeated by AP-GO-050 (or similar)"
    )

    # ── Sanity on report metadata ──
    assert report.rules_checked > 0
    assert report.processing_time_ms >= 0
    assert report.timestamp.endswith("+00:00")
    assert report.overall_status in ("PASS", "CONDITIONAL", "BLOCK")
