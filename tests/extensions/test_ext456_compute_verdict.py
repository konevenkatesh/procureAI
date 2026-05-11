"""Approach E verification for Module 3 Extensions Ext-4+5+6 (Path B).

Tests the schema-aware compute_verdict() functions in isolation with
constructed inputs. No DB writes; no validator full-runs; no aggregator
cascade; no orphan UUIDs. Sentinel preserved exactly.

Run via:
    pytest tests/extensions/test_ext456_compute_verdict.py -v
OR directly:
    python3 tests/extensions/test_ext456_compute_verdict.py
"""
from __future__ import annotations
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))


# ──────────────────────────────────────────────────────────────────────
# Test A — Ext-4 ABC: B3×HC case (M_violation under AP_GO_062_M2)
# ──────────────────────────────────────────────────────────────────────

def test_ext4_abc_b3_hc_m_violation():
    """B3×HC: M_declared=3, M_method=AP_GO_062_M2 → M_required=2; mismatch."""
    from scripts.bid_abc_check import compute_verdict, M_METHOD_TO_REQUIRED

    # Map M_method (as a validator would)
    M_method = "AP_GO_062_M2"
    M_required = M_METHOD_TO_REQUIRED[M_method]
    assert M_required == 2, "AP_GO_062_M2 must map to M=2"

    # B3 declares M=3 in their bid (synthetic data); arithmetically consistent
    # (3*18*2-42 = 108-42 = 66cr), but capacity insufficient vs HC ECV 365.16cr
    verdict, calc = compute_verdict(
        M=3, A=18, N=2, B=42, declared_ABC=66.0, ECV=365.16,
        M_required=M_required,
    )

    assert verdict == "INELIGIBLE", f"Expected INELIGIBLE, got {verdict}"
    assert calc["M_violation"] is True, "M=3 ≠ required 2 should flag M_violation"
    assert calc["M_required"] == 2
    assert calc["M_declared"] == 3
    assert calc["arithmetic_error"] is False, "3*18*2-42=66 should be arithmetically consistent"
    assert calc["capacity_insufficient"] is True, "66cr < 365.16cr"
    print("  ✓ Test A passed — Ext-4 B3×HC M_method=AP_GO_062_M2 → M_violation correctly fires")


def test_ext4_abc_mpw_m3_path():
    """Counterfactual: if M_method=MPW_M3 then M=3 is REQUIRED, not violation."""
    from scripts.bid_abc_check import compute_verdict, M_METHOD_TO_REQUIRED

    M_required = M_METHOD_TO_REQUIRED["MPW_M3"]
    assert M_required == 3, "MPW_M3 must map to M=3"

    # Same B3 bid (M=3, A=18, N=2, B=42, ABC=66) but under MPW_M3 rule.
    # NOW M=3 matches required → no M_violation. Capacity still fails vs HC.
    verdict, calc = compute_verdict(
        M=3, A=18, N=2, B=42, declared_ABC=66.0, ECV=365.16,
        M_required=M_required,
    )

    assert calc["M_violation"] is False, "M=3 == required 3 under MPW_M3"
    assert calc["capacity_insufficient"] is True, "66cr still < 365.16cr"
    assert verdict == "INELIGIBLE", "still INELIGIBLE due to capacity"
    print("  ✓ Test A2 passed — Ext-4 MPW_M3 path: M=3 is valid; capacity still fails")


def test_ext4_abc_default_preserves_legacy():
    """When M_required parameter omitted, defaults to AP_GO_062_M_REQUIRED (2)."""
    from scripts.bid_abc_check import compute_verdict, AP_GO_062_M_REQUIRED
    assert AP_GO_062_M_REQUIRED == 2
    # Call without M_required → uses default
    verdict, calc = compute_verdict(M=2, A=145, N=1.5, B=180,
                                    declared_ABC=255.0, ECV=85.0)
    assert calc["M_required"] == 2, "default M_required must be 2"
    assert calc["M_violation"] is False
    assert verdict == "QUALIFIED"
    print("  ✓ Test A3 passed — Ext-4 default M_required=2 preserves legacy callers")


# ──────────────────────────────────────────────────────────────────────
# Test B — Ext-5 Solvency: B2×HC case (stale, window=12)
# ──────────────────────────────────────────────────────────────────────

def test_ext5_solvency_b2_stale_under_default_window():
    """B2: validity_months_ago=14, window=12 → stale=True."""
    from scripts.bid_solvency_check import compute_verdict, SOLVENCY_VALIDITY_CAP_MONTHS
    assert SOLVENCY_VALIDITY_CAP_MONTHS == 12

    # B2's actual values
    verdict, calc = compute_verdict(
        validity_months_ago=14,
        declared_solvency_cr=0.2,
        required_solvency_cr=0.2,
        validity_window_months=12,
    )

    assert verdict == "INELIGIBLE", f"14mo > 12mo → INELIGIBLE; got {verdict}"
    assert calc["stale"] is True
    assert calc["insufficient"] is False, "0.2 == required 0.2 (not insufficient)"
    assert calc["validity_window_months"] == 12
    print("  ✓ Test B passed — Ext-5 B2 stale at default 12mo window")


def test_ext5_solvency_mpw25_3mo_window():
    """Counterfactual: window=3 (MPW25 path); 4mo cert → stale."""
    from scripts.bid_solvency_check import compute_verdict
    # B1's actual 4mo cert would be STALE under a 3mo window
    verdict, calc = compute_verdict(
        validity_months_ago=4,
        declared_solvency_cr=1.5,
        required_solvency_cr=1.0,
        validity_window_months=3,
    )
    assert calc["stale"] is True, "4mo > 3mo window → stale"
    assert calc["insufficient"] is False
    assert verdict == "INELIGIBLE"
    print("  ✓ Test B2 passed — Ext-5 window=3 (MPW25_3MO) correctly fires stale on 4mo cert")


def test_ext5_solvency_default_preserves_legacy():
    """Window parameter omitted → defaults to SOLVENCY_VALIDITY_CAP_MONTHS=12."""
    from scripts.bid_solvency_check import compute_verdict
    verdict, calc = compute_verdict(validity_months_ago=6,
                                    declared_solvency_cr=1.5,
                                    required_solvency_cr=1.0)
    assert calc["validity_window_months"] == 12, "default window must be 12"
    assert calc["stale"] is False
    assert verdict == "QUALIFIED"
    print("  ✓ Test B3 passed — Ext-5 default window=12 preserves legacy callers")


# ──────────────────────────────────────────────────────────────────────
# Test C — Ext-6 Counter-signature: B1×Kurnool case (all GOVT+EE_SIGNED)
# ──────────────────────────────────────────────────────────────────────

def test_ext6_similar_works_b1_kurnool_all_compliant():
    """B1 Kurnool: 3 GOVT works with EE_SIGNED → all COMPLIANT, QUALIFIED."""
    from scripts.bid_similar_works_check import compute_verdict
    works = [
        dict(name="Govt Hospital, Vijayawada — Phase 1",
             client="APIIC", ecv_cr=85.0,
             client_type="GOVT", counter_signature_status="EE_SIGNED",
             tds_certificate_node_id=None,
             supporting_completion_certificate_node_id=None),
        dict(name="Educational Block",
             client="APCRDA", ecv_cr=80.75,
             client_type="GOVT", counter_signature_status="EE_SIGNED",
             tds_certificate_node_id=None,
             supporting_completion_certificate_node_id=None),
        dict(name="District Court Building",
             client="AP-HCJ", ecv_cr=76.5,
             client_type="GOVT", counter_signature_status="EE_SIGNED",
             tds_certificate_node_id=None,
             supporting_completion_certificate_node_id=None),
    ]
    verdict, calc = compute_verdict(works, ecv_cr=85.0)
    assert verdict == "QUALIFIED", f"Expected QUALIFIED, got {verdict}"
    assert calc["works_count"] == 3
    assert calc["compliant_works_count"] == 3, "All 3 must be COMPLIANT"
    # 3@40%: threshold=34cr; all 3 works ≥34 → branch satisfied
    sb = calc["satisfying_branch"]
    assert sb is not None
    assert sb["n_required"] == 3
    assert sb["count_meeting"] == 3
    # ext6_compliance_summary present
    assert len(calc["ext6_compliance_summary"]) == 3
    for entry in calc["ext6_compliance_summary"]:
        assert entry["compliant"] is True
        assert "GOVT_with_EE_SIGNED" in entry["reason"]
    print("  ✓ Test C passed — Ext-6 B1×Kurnool 3 GOVT+EE_SIGNED works all COMPLIANT, QUALIFIED")


# ──────────────────────────────────────────────────────────────────────
# Test D — Ext-6 negative: counterfactual with non-compliant work
# ──────────────────────────────────────────────────────────────────────

def test_ext6_negative_private_missing_tds():
    """Counterfactual: 2 GOVT compliant + 1 PRIVATE missing TDS → 2 effective works.

    Kurnool ECV 85cr. 3@40%=34cr threshold; 2@50%=42.5cr; 1@80%=68cr.
    After filtering: 2 compliant works.
    - 3@40%: need 3 but only 2 compliant → fail
    - 2@50%: need 2 ≥42.5cr. Both compliant works at 85/80.75 → 2 meet → pass
    Expected: QUALIFIED via 2@50% branch (not 3@40%)
    """
    from scripts.bid_similar_works_check import compute_verdict
    works = [
        dict(name="Govt Hospital", client="APIIC", ecv_cr=85.0,
             client_type="GOVT", counter_signature_status="EE_SIGNED"),
        dict(name="Educational Block", client="APCRDA", ecv_cr=80.75,
             client_type="GOVT", counter_signature_status="EE_SIGNED"),
        dict(name="Private Tower", client="ABC Corp", ecv_cr=76.5,
             client_type="PRIVATE", counter_signature_status="NOT_REQUIRED",
             tds_certificate_node_id=None),  # ← missing TDS
    ]
    verdict, calc = compute_verdict(works, ecv_cr=85.0)
    assert calc["works_count"] == 3
    assert calc["compliant_works_count"] == 2, "Private without TDS excluded"
    # 3@40% needs 3 — only 2 compliant; should fail this branch
    b3_40 = next(b for b in calc["branches"] if b["n_required"] == 3)
    assert b3_40["count_meeting"] == 2 and b3_40["passed"] is False
    # 2@50% needs 2; both compliant works ≥42.5cr → pass
    b2_50 = next(b for b in calc["branches"] if b["n_required"] == 2)
    assert b2_50["count_meeting"] == 2 and b2_50["passed"] is True
    assert verdict == "QUALIFIED"
    # Sanity-check the ext6 summary
    non_compliant = [e for e in calc["ext6_compliance_summary"] if not e["compliant"]]
    assert len(non_compliant) == 1
    assert non_compliant[0]["client_type"] == "PRIVATE"
    assert "PRIVATE_missing_tds" in non_compliant[0]["reason"]
    print("  ✓ Test D passed — Ext-6 counterfactual: PRIVATE without TDS correctly excluded; verdict still QUALIFIED via 2@50% branch")


def test_ext6_legacy_entry_assumed_compliant():
    """A work entry without Ext-6 fields (pre-backfill) is assumed compliant."""
    from scripts.bid_similar_works_check import compute_verdict
    works = [
        dict(name="Legacy Work A", client="X", ecv_cr=85.0),  # no Ext-6 fields
        dict(name="Legacy Work B", client="Y", ecv_cr=80.0),
        dict(name="Legacy Work C", client="Z", ecv_cr=76.0),
    ]
    verdict, calc = compute_verdict(works, ecv_cr=85.0)
    assert calc["compliant_works_count"] == 3, "Legacy entries assumed compliant"
    assert verdict == "QUALIFIED"
    print("  ✓ Test E passed — Ext-6 legacy entries (no Ext-6 fields) assumed compliant for backward compat")


# ──────────────────────────────────────────────────────────────────────
# Runner
# ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_ext4_abc_b3_hc_m_violation,
        test_ext4_abc_mpw_m3_path,
        test_ext4_abc_default_preserves_legacy,
        test_ext5_solvency_b2_stale_under_default_window,
        test_ext5_solvency_mpw25_3mo_window,
        test_ext5_solvency_default_preserves_legacy,
        test_ext6_similar_works_b1_kurnool_all_compliant,
        test_ext6_negative_private_missing_tds,
        test_ext6_legacy_entry_assumed_compliant,
    ]
    print(f"Running {len(tests)} Approach E unit tests for Ext-4+5+6...")
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
        for name, msg in failures:
            print(f"  - {name}: {msg}")
        sys.exit(1)
    else:
        print(f"✓ All {len(tests)} tests passed")
        sys.exit(0)
