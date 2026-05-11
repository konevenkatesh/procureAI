"""Pre-smoke unit tests for Ext-2 bid_compliance_documents_check.compute_verdict.

Approach-E-style unit tests (no DB writes). Verifies the composite verdict
logic + MAX-severity aggregation BEFORE running the validator on all 24 bids.
"""
from __future__ import annotations
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))


def _clean_b1_profile() -> dict:
    """All 8 documents VALID/SIGNED/NOT_REQUIRED — predicts QUALIFIED."""
    return {
        "company_reg_cert_status":    "VALID",
        "pan_cert_status":            "VALID",
        "gst_cert_status":            "VALID",
        "epf_esi_cert_status":        "VALID",
        "form_12_declaration_status": "SIGNED",
        "poa_status":                 "NOT_REQUIRED",
        "tender_fee_receipt_status":  "VALID",
        "dsc_status":                 "VALID",
    }


def test_a_all_compliant_qualified():
    """B1-like input: all 8 VALID/SIGNED/NOT_REQUIRED → QUALIFIED."""
    from scripts.bid_compliance_documents_check import compute_verdict
    verdict, calc = compute_verdict(_clean_b1_profile())
    assert verdict == "QUALIFIED", f"Expected QUALIFIED, got {verdict}"
    assert calc["compliant_count"] == 8
    assert calc["hard_block_documents"] == []
    assert calc["remediable_documents"] == []
    assert calc["consequence_hint"] == "ADVISORY"
    assert calc["decision_reason"] == "qualified_all_8_compliance_docs_valid"
    assert all(e["compliance"] == "COMPLIANT" for e in calc["compliance_summary"])
    print("  ✓ Test A passed — 8/8 COMPLIANT → QUALIFIED")


def test_b_one_expired_remediable():
    """B1-like with DSC EXPIRED → INELIGIBLE-WARNING (remediable)."""
    from scripts.bid_compliance_documents_check import compute_verdict
    props = _clean_b1_profile()
    props["dsc_status"] = "EXPIRED"
    verdict, calc = compute_verdict(props)
    assert verdict == "INELIGIBLE", f"Expected INELIGIBLE, got {verdict}"
    assert calc["consequence_hint"] == "WARNING"
    assert "Digital Signature Certificate" in calc["remediable_documents"]
    assert calc["hard_block_documents"] == [], "Should be no hard blocks"
    assert calc["compliant_count"] == 7
    assert "remediable" in calc["decision_reason"]
    print("  ✓ Test B passed — DSC EXPIRED → INELIGIBLE-WARNING (remediable)")


def test_c_one_missing_hard_block():
    """B1-like with POA MISSING → INELIGIBLE-HARD_BLOCK (non-remediable)."""
    from scripts.bid_compliance_documents_check import compute_verdict
    props = _clean_b1_profile()
    props["poa_status"] = "MISSING"
    verdict, calc = compute_verdict(props)
    assert verdict == "INELIGIBLE"
    assert calc["consequence_hint"] == "HARD_BLOCK"
    assert "Power of Attorney" in calc["hard_block_documents"]
    assert calc["compliant_count"] == 7
    assert "hard_block" in calc["decision_reason"]
    print("  ✓ Test C passed — POA MISSING → INELIGIBLE-HARD_BLOCK")


def test_d_hard_block_dominates_remediable():
    """Mix: 1 EXPIRED + 1 MISSING → HARD_BLOCK precedence (MAX-severity)."""
    from scripts.bid_compliance_documents_check import compute_verdict
    props = _clean_b1_profile()
    props["dsc_status"] = "EXPIRED"
    props["company_reg_cert_status"] = "MISSING"
    verdict, calc = compute_verdict(props)
    assert verdict == "INELIGIBLE"
    assert calc["consequence_hint"] == "HARD_BLOCK", "HARD_BLOCK must dominate REMEDIABLE"
    assert "Company Registration Certificate" in calc["hard_block_documents"]
    assert "Digital Signature Certificate" in calc["remediable_documents"]
    assert calc["compliant_count"] == 6
    print("  ✓ Test D passed — HARD_BLOCK precedence over REMEDIABLE (MAX-severity)")


def test_e_null_status_treated_as_hard_block():
    """Missing field (null status) → treated as HARD_BLOCK gap."""
    from scripts.bid_compliance_documents_check import compute_verdict
    props = _clean_b1_profile()
    del props["form_12_declaration_status"]
    verdict, calc = compute_verdict(props)
    assert verdict == "INELIGIBLE"
    assert calc["consequence_hint"] == "HARD_BLOCK"
    assert len(calc["null_status_documents"]) == 1
    assert "Form-12 Declaration" in calc["null_status_documents"][0]
    print("  ✓ Test E passed — null status → HARD_BLOCK gap (bidder must declare)")


if __name__ == "__main__":
    tests = [
        test_a_all_compliant_qualified,
        test_b_one_expired_remediable,
        test_c_one_missing_hard_block,
        test_d_hard_block_dominates_remediable,
        test_e_null_status_treated_as_hard_block,
    ]
    print(f"Running {len(tests)} Ext-2 pre-smoke unit tests...")
    print()
    failures = []
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"  ✗ {t.__name__} FAILED: {e}")
            failures.append((t.__name__, str(e)))
        except Exception as e:
            print(f"  ✗ {t.__name__} ERROR: {type(e).__name__}: {e}")
            failures.append((t.__name__, f"{type(e).__name__}: {e}"))
    print()
    if failures:
        print(f"✗ {len(failures)} of {len(tests)} tests failed")
        sys.exit(1)
    else:
        print(f"✓ All {len(tests)} tests passed")
        sys.exit(0)
