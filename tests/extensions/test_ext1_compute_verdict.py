"""Pre-smoke unit tests for Ext-1 bid_jv_consortium_check.compute_verdict.

Approach-E-style unit tests (no DB writes). Verifies the 3-path
JV/Consortium composite verdict logic BEFORE running the validator on
all 24 bids. Each test isolates one path:

  Test 1: SOLE_BIDDER          → QUALIFIED-NOT_APPLICABLE  (Path 1)
  Test 2: JV all-pass          → QUALIFIED                  (Path 2 happy)
  Test 3: JV liability=OTHER   → INELIGIBLE-HARD_BLOCK     (Path 2, sc4 fail)
  Test 4: JV Lead-fin < floor  → INELIGIBLE-HARD_BLOCK     (Path 2, sc5 fail)
  Test 5: CONSORTIUM N=1       → INELIGIBLE-HARD_BLOCK     (Path 2, sc7 fail)
  Test 6: JV_PARTNER           → GAP-DATA_INTEGRITY         (Path 3)
"""
from __future__ import annotations
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))


def _clean_jv_bidder_profile() -> dict:
    """JV with all 8 sub-checks predicted to pass."""
    return {
        "bidder_type":                   "JV",
        "lead_partner_id":               "bid_synth_profile_b9_lead",
        "partner_ids":                   ["bid_synth_profile_b9_lead",
                                          "bid_synth_profile_b9_p2"],
        "jv_agreement_node_id":          "node_jv_agreement_b9",
        "jv_agreement_validity_until":   "2027-12-31",  # future
        "liability_terms":               "JOINT_AND_SEVERAL",
        "poa_status":                    "VALID",  # Ext-2 field reuse
    }


def _clean_tender_props() -> dict:
    return {
        "tender_type":           "Works",
        "financial_pq_floor_cr": 109.55,
        "submission_date":       "2026-05-10",
        "is_ap_tender":          True,
    }


def _clean_lead_partner_props(financial_3yr: float = 260.0) -> dict:
    return {
        "bidder_type":                   "JV_PARTNER",
        "profile_id":                    "bid_synth_profile_b9_lead",
        "financial_turnover_3yr_avg_cr": financial_3yr,
        "past_blacklist_events":         [],
    }


def _clean_partner_props_list(financial_3yr: float = 260.0) -> list[dict]:
    """Two partners, both blacklist-clean."""
    return [
        _clean_lead_partner_props(financial_3yr),
        {
            "bidder_type":                   "JV_PARTNER",
            "profile_id":                    "bid_synth_profile_b9_p2",
            "financial_turnover_3yr_avg_cr": 80.0,
            "past_blacklist_events":         [],
        },
    ]


def test_1_sole_bidder_qualified_not_applicable():
    """B1–B8 path: bidder_type=SOLE_BIDDER → QUALIFIED + NOT_APPLICABLE."""
    from scripts.bid_jv_consortium_check import compute_verdict
    bidder = {
        "bidder_type":                 "SOLE_BIDDER",
        "lead_partner_id":             None,
        "partner_ids":                 [],
        "jv_agreement_node_id":        None,
        "jv_agreement_validity_until": None,
        "liability_terms":             None,
    }
    verdict, calc = compute_verdict(bidder, _clean_tender_props())
    assert verdict == "QUALIFIED", f"Expected QUALIFIED, got {verdict}"
    assert calc["bidder_type"] == "SOLE_BIDDER"
    assert calc["consequence_hint"] == "ADVISORY"
    assert calc["hard_block_sub_checks"] == []
    assert len(calc["jv_evaluation_summary"]) == 1
    assert calc["jv_evaluation_summary"][0]["compliance"] == "NOT_APPLICABLE_SOLE_BIDDER"
    assert calc["decision_reason"] == "qualified_not_applicable_sole_bidder"
    print("  ✓ Test 1 passed — SOLE_BIDDER → QUALIFIED-NOT_APPLICABLE")


def test_2_jv_all_pass_qualified():
    """B9-like clean JV: all 8 sub-checks pass → QUALIFIED."""
    from scripts.bid_jv_consortium_check import compute_verdict
    verdict, calc = compute_verdict(
        _clean_jv_bidder_profile(),
        _clean_tender_props(),
        lead_partner_props=_clean_lead_partner_props(),
        partner_props_list=_clean_partner_props_list(),
    )
    assert verdict == "QUALIFIED", (
        f"Expected QUALIFIED, got {verdict}; failing sub-checks: "
        f"{calc.get('hard_block_sub_checks')}")
    assert calc["bidder_type"] == "JV"
    assert calc["passed_count"] == 8
    assert calc["total_sub_checks"] == 8
    assert calc["hard_block_sub_checks"] == []
    assert calc["consequence_hint"] == "ADVISORY"
    assert calc["partner_count"] == 2
    assert "qualified_jv_consortium_all_8_sub_checks_passed" in calc["decision_reason"]
    # All 8 sub-checks individually compliant
    for sc in calc["jv_evaluation_summary"]:
        assert sc["passed"] is True, f"Sub-check {sc['sub_check']} failed: {sc['detail']}"
        assert sc["compliance"] == "COMPLIANT"
    print("  ✓ Test 2 passed — JV all 8 sub-checks pass → QUALIFIED")


def test_3_jv_liability_other_hard_block():
    """JV with liability_terms='OTHER' → INELIGIBLE on sc4."""
    from scripts.bid_jv_consortium_check import compute_verdict
    bidder = _clean_jv_bidder_profile()
    bidder["liability_terms"] = "OTHER"  # not JOINT_AND_SEVERAL
    verdict, calc = compute_verdict(
        bidder,
        _clean_tender_props(),
        lead_partner_props=_clean_lead_partner_props(),
        partner_props_list=_clean_partner_props_list(),
    )
    assert verdict == "INELIGIBLE", f"Expected INELIGIBLE, got {verdict}"
    assert calc["consequence_hint"] == "HARD_BLOCK"
    assert "JOINT_AND_SEVERAL_LIABILITY" in calc["hard_block_sub_checks"]
    assert calc["passed_count"] == 7  # 7 of 8 pass; only sc4 fails
    sc4 = next(sc for sc in calc["jv_evaluation_summary"]
               if sc["sub_check"] == "JOINT_AND_SEVERAL_LIABILITY")
    assert sc4["passed"] is False
    assert "JOINT_AND_SEVERAL" in sc4["detail"]
    print("  ✓ Test 3 passed — JV liability=OTHER → INELIGIBLE-HARD_BLOCK")


def test_4_jv_lead_financial_below_floor_hard_block():
    """JV Lead Partner financial below tender PQ floor → INELIGIBLE on sc5."""
    from scripts.bid_jv_consortium_check import compute_verdict
    # Tender PQ floor=109.55cr; Lead has only 50cr → fails sc5
    verdict, calc = compute_verdict(
        _clean_jv_bidder_profile(),
        _clean_tender_props(),
        lead_partner_props=_clean_lead_partner_props(financial_3yr=50.0),
        partner_props_list=_clean_partner_props_list(financial_3yr=50.0),
    )
    assert verdict == "INELIGIBLE", f"Expected INELIGIBLE, got {verdict}"
    assert calc["consequence_hint"] == "HARD_BLOCK"
    assert "LEAD_PARTNER_FINANCIAL" in calc["hard_block_sub_checks"]
    assert calc["passed_count"] == 7
    sc5 = next(sc for sc in calc["jv_evaluation_summary"]
               if sc["sub_check"] == "LEAD_PARTNER_FINANCIAL")
    assert sc5["passed"] is False
    assert "50" in sc5["detail"] and "109.55" in sc5["detail"]
    print("  ✓ Test 4 passed — Lead financial 50cr < 109.55cr floor → INELIGIBLE-HARD_BLOCK")


def test_5_consortium_partner_count_one_hard_block():
    """CONSORTIUM with only 1 partner → INELIGIBLE on sc7."""
    from scripts.bid_jv_consortium_check import compute_verdict
    bidder = _clean_jv_bidder_profile()
    bidder["bidder_type"] = "CONSORTIUM"
    bidder["partner_ids"] = ["bid_synth_profile_b9_lead"]  # only 1; min=2
    verdict, calc = compute_verdict(
        bidder,
        _clean_tender_props(),
        lead_partner_props=_clean_lead_partner_props(),
        partner_props_list=[_clean_lead_partner_props()],  # only 1 partner
    )
    assert verdict == "INELIGIBLE", f"Expected INELIGIBLE, got {verdict}"
    assert calc["consequence_hint"] == "HARD_BLOCK"
    assert "PARTNER_COUNT" in calc["hard_block_sub_checks"]
    assert calc["bidder_type"] == "CONSORTIUM"
    assert calc["partner_count"] == 1
    sc7 = next(sc for sc in calc["jv_evaluation_summary"]
               if sc["sub_check"] == "PARTNER_COUNT")
    assert sc7["passed"] is False
    assert "partner_count=1" in sc7["detail"]
    print("  ✓ Test 5 passed — CONSORTIUM partner_count=1 → INELIGIBLE-HARD_BLOCK")


def test_6_jv_partner_gap_data_integrity():
    """JV_PARTNER attempting to submit a bid directly → GAP-DATA_INTEGRITY."""
    from scripts.bid_jv_consortium_check import compute_verdict
    bidder = {
        "bidder_type":                 "JV_PARTNER",
        "lead_partner_id":             None,
        "partner_ids":                 [],
        "jv_agreement_node_id":        None,
        "jv_agreement_validity_until": None,
        "liability_terms":             None,
    }
    verdict, calc = compute_verdict(bidder, _clean_tender_props())
    assert verdict == "GAP_INSUFFICIENT_DATA", (
        f"Expected GAP_INSUFFICIENT_DATA, got {verdict}")
    assert calc["bidder_type"] == "JV_PARTNER"
    assert calc["consequence_hint"] == "WARNING"
    assert calc["hard_block_sub_checks"] == []
    assert len(calc["jv_evaluation_summary"]) == 1
    assert calc["jv_evaluation_summary"][0]["compliance"] == "GAP_DATA_INTEGRITY"
    assert "should_not_submit_bids_directly" in calc["decision_reason"]
    print("  ✓ Test 6 passed — JV_PARTNER → GAP-DATA_INTEGRITY")


if __name__ == "__main__":
    tests = [
        test_1_sole_bidder_qualified_not_applicable,
        test_2_jv_all_pass_qualified,
        test_3_jv_liability_other_hard_block,
        test_4_jv_lead_financial_below_floor_hard_block,
        test_5_consortium_partner_count_one_hard_block,
        test_6_jv_partner_gap_data_integrity,
    ]
    print(f"Running {len(tests)} Ext-1 pre-smoke unit tests...")
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
