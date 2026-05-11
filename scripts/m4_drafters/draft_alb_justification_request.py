"""M4.2 Drafter — ALB_JUSTIFICATION request per CVC norms.

Reads BidAnomalyFinding WHERE anomaly_class=ALB_CORROBORATION; composes
bidder-facing letter demanding cost justification + additional securities;
cites cross_tender_consistency as severity multiplier; emits one
Communication kg_node per (ALB bidder, tender) pair.

Current corpus prediction: 3 letters (B8 × 3 tenders).
"""
from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from scripts.m4_drafters._common import (  # noqa: E402
    ARTIFACT_DIR, rest_get_range, rest_post, get_bidder_profile,
    get_tender_info, compute_audit_id, delete_prior_communications,
    snapshot_sentinels, assert_sentinel_preserved,
)


COMMUNICATION_TYPE = "ALB_JUSTIFICATION"
SOURCE_REF = "module4:draft_alb_justification_request_v1"
SENDER_ROLE = "SYSTEM"


def fetch_alb_anomalies() -> list[dict]:
    return rest_get_range("kg_nodes", {
        "select": "node_id,doc_id,properties",
        "node_type": "eq.BidAnomalyFinding",
        "properties->>anomaly_class": "eq.ALB_CORROBORATION",
    })


def fetch_tender_ranking(tender_id: str) -> dict | None:
    rows = rest_get_range("kg_nodes", {
        "select": "node_id,properties",
        "node_type": "eq.TenderRanking",
        "properties->>tender_id": f"eq.{tender_id}",
    })
    return rows[0] if rows else None


def fetch_bid_submission(bidder_id: str, tender_id: str) -> dict | None:
    rows = rest_get_range("kg_nodes", {
        "select": "node_id,properties",
        "node_type": "eq.BidSubmission",
        "properties->>bidder_profile_id": f"eq.{bidder_id}",
        "properties->>tender_id":         f"eq.{tender_id}",
    })
    return rows[0] if rows else None


def compose_content_en(profile: dict, tender_info: dict,
                       af_props: dict, tr_props: dict | None,
                       bid_amount_cr: float, premium_pct: float,
                       af_node_id: str) -> str:
    """Compose English Markdown body — ALB_JUSTIFICATION letter template."""
    bidder_name = profile.get("company_name", "Bidder")
    today = _dt.date.today().isoformat()
    tender_name = tender_info["name"]
    nit_no = tender_info["nit_no"]
    severity = af_props.get("aggregate_severity", "?")
    confidence = af_props.get("detection_confidence", "?")
    x_tender_appearances = af_props.get("cross_tender_appearances", 0)
    x_tender_consistent = af_props.get("cross_tender_consistency", False)
    alb_threshold = tr_props.get("alb_threshold_cr", 0.0) if tr_props else 0.0
    avg_qualified = tr_props.get("average_qualified_bid_cr", 0.0) if tr_props else 0.0
    multiplier = tr_props.get("alb_multiplier", 0.80) if tr_props else 0.80
    shortfall = max(0.0, alb_threshold - bid_amount_cr)

    lines: list[str] = []
    lines.append(f"# ALB Justification Request — Tender {nit_no}")
    lines.append("")
    lines.append(f"**Date:** {today}")
    lines.append(f"**To:** {bidder_name}")
    lines.append(f"  {profile.get('communication_address', '(address on file)')}")
    lines.append(f"  Attention: {profile.get('authorized_signatory_name', 'Authorised Signatory')}")
    lines.append("")
    lines.append(f"**Tender:** {tender_name}")
    lines.append(f"**NIT No.:** {nit_no}")
    lines.append(f"**Estimated Contract Value (ECV):** ₹{tender_info['ecv_cr']:.2f} crore")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("Dear Sir/Madam,")
    lines.append("")
    lines.append(
        f"Following review by the Tender Scrutiny Committee, your bid has been flagged as "
        f"an **Abnormally Low Bid (ALB)** per CVC Office Memorandum on Vigilance Aspects "
        f"of Public Procurement (No. 005/CRD/19 and successor OMs).")
    lines.append("")
    lines.append("### Quantitative basis")
    lines.append("")
    lines.append(f"- **Your bid:** ₹{bid_amount_cr:.2f} crore ({premium_pct:+.2f}% vs ECV)")
    lines.append(f"- **Average of QUALIFIED bids:** ₹{avg_qualified:.2f} crore")
    lines.append(f"- **ALB threshold (average × {multiplier}):** ₹{alb_threshold:.2f} crore")
    lines.append(f"- **Shortfall below threshold:** ₹{shortfall:.2f} crore")
    lines.append("")
    lines.append("### Cross-tender consistency signal")
    lines.append("")
    if x_tender_consistent:
        lines.append(
            f"This is **not an isolated incident**. Your bidding pattern shows this "
            f"low-bid signature across **{x_tender_appearances} of 3** tenders evaluated in "
            f"this batch. Cross-tender consistency at this rate is treated as a "
            f"**HIGH-severity** systemic signal per CVC vigilance norms, not an outlier.")
    else:
        lines.append(
            f"This appears to be an isolated bid (1 of 3 tenders). The single-tender ALB "
            f"flag is still material but is treated as a {severity}-severity signal per "
            f"CVC vigilance norms.")
    lines.append("")
    lines.append(
        f"- Severity: **{severity}**")
    lines.append(
        f"- Detection confidence: **{confidence}**")
    lines.append("")
    lines.append("### Required submissions (within 7 days of receipt)")
    lines.append("")
    lines.append(
        "Per CVC ALB norms, you are required to submit, **within 7 calendar days** of "
        "receipt of this letter, ALL of the following documents addressed to the "
        "Procuring Authority:")
    lines.append("")
    lines.append(
        "1. **Detailed cost analysis** demonstrating viability of contract execution at "
        "the bid amount, broken down by:")
    lines.append("   - Direct material cost (with supplier quotations annexed)")
    lines.append("   - Labour cost (with wage rates per AP State Minimum Wages Act)")
    lines.append("   - Equipment cost (owned vs leased breakdown; rental rates if leased)")
    lines.append("   - Overheads + profit margin (justification for sub-industry-standard margin)")
    lines.append("   - Contingency provision (if any)")
    lines.append("")
    lines.append(
        "2. **Audited financial statements** for the last 3 financial years showing "
        "capacity to absorb pricing risk:")
    lines.append("   - Profit & Loss statements (signed by Chartered Accountant)")
    lines.append("   - Balance sheet with working capital position")
    lines.append("   - Cash-flow statement")
    lines.append("")
    lines.append(
        "3. **Additional Performance Bank Guarantee** equal to the shortfall "
        f"(₹{shortfall:.2f} crore) as additional security, OR an undertaking to provide "
        "100% advance PBG covering the full contract value, OR audited evidence of "
        "prior ALB-bid execution at similar margins.")
    lines.append("")
    lines.append(
        "4. **Bidder undertaking** that the bid amount represents a *bona fide* technical "
        "+ commercial proposal and is NOT a cover bid, price-fixing arrangement, or "
        "deliberately-low strategic bid intended for subsequent variation claims.")
    lines.append("")
    lines.append("### Consequences of non-submission or unsatisfactory response")
    lines.append("")
    lines.append(
        "Per CVC norms and AP Standard Tender Document Clauses on ALB treatment:")
    lines.append("")
    lines.append(
        "- Failure to submit within 7 days will result in **rejection of your bid** as "
        "INELIGIBLE for this tender.")
    lines.append("- Unsatisfactory cost analysis (cost build-up cannot demonstrate viability) "
                 "will result in rejection at the discretion of the Tender Scrutiny Committee.")
    lines.append("- Persistent ALB pattern across tenders may invite vigilance referral to "
                 "the AP State Chief Vigilance Officer for review of past procurement awards "
                 "and possible blacklist proceedings.")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("**Yours faithfully,**")
    lines.append("")
    lines.append("Procuring Authority")
    lines.append("(Communication generated by SYSTEM; signed-off via approval workflow before dispatch)")
    lines.append("")
    lines.append(f"**Drilldown reference:**")
    lines.append("")
    lines.append(f"- BidAnomalyFinding node_id: `{af_node_id}`")
    lines.append(f"- Anomaly class: ALB_CORROBORATION")
    lines.append(f"- Aggregate severity: {severity}; confidence: {confidence}")
    lines.append(f"- Cross-tender appearances: {x_tender_appearances} of 3 tenders")
    lines.append(f"- Cross-tender consistency: {x_tender_consistent}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    print("=" * 76)
    print(f"  M4.2 Drafter — {COMMUNICATION_TYPE}")
    print(f"  source_ref: {SOURCE_REF}")
    print("=" * 76)

    sentinel_pre = snapshot_sentinels()
    print(f"\n── Pre snapshot ──")
    for k, v in sentinel_pre.items():
        print(f"  {k:25s}: {v}")

    n_prior = delete_prior_communications(COMMUNICATION_TYPE, SOURCE_REF)
    if n_prior:
        print(f"\n  cleanup: deleted {n_prior} prior {COMMUNICATION_TYPE} Communication(s)")

    af_rows = fetch_alb_anomalies()
    print(f"\n── {len(af_rows)} ALB_CORROBORATION BidAnomalyFindings ──")

    n_emitted = 0
    for af in af_rows:
        af_props = af["properties"] or {}
        primary_bidders = af_props.get("primary_bidder_ids") or []
        if not primary_bidders:
            continue
        bidder_id = primary_bidders[0]
        tender_id = af_props.get("tender_id")
        if not tender_id:
            continue

        profile_node = get_bidder_profile(bidder_id)
        profile = profile_node.get("properties") or {}
        tender_info = get_tender_info(tender_id)
        tr = fetch_tender_ranking(tender_id)
        tr_props = tr["properties"] if tr else None

        # Find the bidder's BidSubmission to get bid_amount_cr
        bid_sub = fetch_bid_submission(bidder_id, tender_id)
        bid_amount = 0.0
        if bid_sub:
            # bid_amount_cr lives on LetterOfBid, not BidSubmission; pull from
            # TenderRanking.ranking[] which has it
            if tr_props:
                for r in tr_props.get("ranking", []):
                    if r.get("bidder_profile_id") == bidder_id:
                        bid_amount = r.get("bid_amount_cr", 0.0)
                        break
        ecv = tender_info["ecv_cr"]
        premium_pct = ((bid_amount - ecv) / ecv * 100.0) if ecv else 0.0

        # Source finding ids = BidAnomalyFinding + BidSubmission + TenderRanking
        source_ids = [af["node_id"]]
        if bid_sub:
            source_ids.append(bid_sub["node_id"])
        if tr:
            source_ids.append(tr["node_id"])
        audit_id = compute_audit_id(COMMUNICATION_TYPE, bidder_id, tender_id, source_ids)

        content_en = compose_content_en(profile, tender_info, af_props, tr_props,
                                         bid_amount, premium_pct, af["node_id"])

        bidder_key = bidder_id.replace("bid_synth_profile_", "")
        tender_key = tender_id.replace("tender_synth_", "")
        artifact_path = ARTIFACT_DIR / f"{COMMUNICATION_TYPE}_{bidder_key}_{tender_key}.md"
        artifact_path.write_text(content_en, encoding="utf-8")

        comm_props = {
            "communication_type":          COMMUNICATION_TYPE,
            "recipient_bidder_profile_id": bidder_id,
            "recipient_email":             profile.get("email_primary"),
            "recipient_role":              None,
            "tender_id":                   tender_id,
            "sender_role":                 SENDER_ROLE,
            "channel":                     "EMAIL",
            "language":                    "EN",
            "status":                      "DRAFT",
            "audit_id":                    audit_id,
            "source_finding_node_ids":     source_ids,
            "content_en":                  content_en,
            "content_te":                  None,
            "artifact_path_md":            str(artifact_path),
            "artifact_path_docx":          None,
            "artifact_path_pdf":           None,
            "extracted_by":                SOURCE_REF,
            "defeated":                    False,
            "bid_amount_cr":               bid_amount,
            "premium_pct":                 premium_pct,
            "alb_threshold_cr":            tr_props.get("alb_threshold_cr") if tr_props else None,
            "severity":                    af_props.get("aggregate_severity"),
            "cross_tender_consistency":    af_props.get("cross_tender_consistency"),
            "cross_tender_appearances":    af_props.get("cross_tender_appearances"),
            "n_source_findings":           len(source_ids),
            "bidder_name":                 profile.get("company_name"),
            "tender_name":                 tender_info["name"],
            "tender_nit_no":               tender_info["nit_no"],
        }
        label = (f"ALB_JUSTIFICATION — {profile.get('company_name','?')[:50]} × "
                 f"{tender_info['name'][:30]} (₹{bid_amount:.2f}cr; {premium_pct:+.2f}%)")

        emitted = rest_post("kg_nodes", [{
            "doc_id":     tender_id,
            "node_type":  "Communication",
            "label":      label,
            "properties": comm_props,
            "source_ref": SOURCE_REF,
        }])[0]
        n_emitted += 1
        print(f"  ✓ {bidder_key:5s} × {tender_key:8s}  "
              f"₹{bid_amount:.2f}cr ({premium_pct:+.2f}%)  "
              f"x_tender={af_props.get('cross_tender_appearances')}of3  "
              f"audit_id={audit_id}  → {emitted['node_id']}")

    sentinel_post = snapshot_sentinels()
    print(f"\n── Post snapshot ──")
    for k, v in sentinel_post.items():
        delta = v - sentinel_pre[k]
        marker = "" if delta == 0 else f" (Δ={delta:+d})"
        print(f"  {k:25s}: {v}{marker}")

    assert_sentinel_preserved(sentinel_pre, sentinel_post)
    print(f"\n✓ Sentinel preserved (only Communication grew by +{n_emitted})")
    print("=" * 76)
    print(f"  M4.2 {COMMUNICATION_TYPE} drafter complete — {n_emitted} letter(s) emitted")
    print("=" * 76)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
