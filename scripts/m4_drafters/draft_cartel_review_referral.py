"""M4.5 Drafter — CARTEL_REVIEW internal vigilance referral.

Reads BidAnomalyFinding WHERE anomaly_class=CARTEL_SUSPECT. Internal-only;
addressed to Vigilance Officer (recipient_role); ENGLISH-ONLY (no Telugu
per run-2 directive scope).
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


COMMUNICATION_TYPE = "CARTEL_REVIEW"
SOURCE_REF = "module4:draft_cartel_review_referral_v1"
SENDER_ROLE = "SYSTEM"
RECIPIENT_ROLE = "VIGILANCE_OFFICER"


def compose_content_en(tender_info: dict, af_props: dict,
                       af_node_id: str, bidder_a_props: dict,
                       bidder_b_props: dict) -> str:
    today = _dt.date.today().isoformat()
    severity = af_props.get("aggregate_severity", "?")
    confidence = af_props.get("detection_confidence", "?")
    signals = af_props.get("signals") or []
    primary_names = af_props.get("primary_bidder_names") or []
    x_tender = af_props.get("cross_tender_appearances", 0)
    x_consistent = af_props.get("cross_tender_consistency", False)
    decision_reason = af_props.get("decision_reason", "")
    recommendation = af_props.get("recommendation", "")

    lines = []
    lines.append(f"# INTERNAL VIGILANCE REFERRAL — Cartel-Suspect Pair, Tender {tender_info['nit_no']}")
    lines.append("")
    lines.append(f"**CLASSIFICATION:** INTERNAL — VIGILANCE EYES ONLY")
    lines.append(f"**Date:** {today}")
    lines.append(f"**To:** Vigilance Officer, AP State Procurement Department")
    lines.append(f"**Copy to:** Chief Vigilance Officer (CVO), Anti-Collusion Cell")
    lines.append("")
    lines.append(f"**Tender:** {tender_info['name']}")
    lines.append(f"**NIT No.:** {tender_info['nit_no']}")
    lines.append(f"**Estimated Contract Value:** ₹{tender_info['ecv_cr']:.2f} crore")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("Sir/Madam,")
    lines.append("")
    lines.append(
        f"This referral concerns a **suspected cartel/collusion pair** detected by the "
        f"automated cross-bid anomaly detector during evaluation of the captioned tender. "
        f"The pair has been flagged at **{severity}** severity with **{confidence}** "
        f"detection confidence.")
    lines.append("")
    lines.append("### Implicated bidders")
    lines.append("")
    for n in primary_names:
        lines.append(f"- {n}")
    lines.append("")
    lines.append("### Detected collusion signals")
    lines.append("")
    for i, s in enumerate(signals, 1):
        lines.append(f"{i}. **{s.get('signal_type', '?')}** ({s.get('severity', '?')})")
        lines.append(f"   - Evidence: {s.get('evidence', '?')[:300]}")
        cite = s.get("citation_source", "")
        if cite:
            lines.append(f"   - Citation: *{cite[:200]}*")
        lines.append("")
    lines.append("### Cross-tender pattern")
    lines.append("")
    if x_consistent:
        lines.append(
            f"This pair shows the identical signal pattern across **{x_tender} of 3** "
            f"tenders evaluated in this batch — strongly suggestive of a systemic "
            f"collusion arrangement rather than isolated bid coincidence. Cross-tender "
            f"consistency at this rate elevates severity to HIGH per CVC anti-collusion "
            f"detection norms.")
    else:
        lines.append(
            f"This pair shows the signal pattern in **{x_tender} of 3** tenders only. "
            f"Single-tender appearance is suggestive but not conclusive; recommend "
            f"correlation against historical procurement data before formal action.")
    lines.append("")
    lines.append("### Detector decision")
    lines.append("")
    lines.append(f"> {decision_reason}")
    lines.append("")
    lines.append("### Detector recommendation")
    lines.append("")
    lines.append(f"> {recommendation}")
    lines.append("")
    lines.append("### Vigilance action requested")
    lines.append("")
    lines.append("Per CVC OM Vigilance Aspects on Public Procurement (No. 005/CRD/19 and "
                 "successor OMs), the recommended vigilance actions are:")
    lines.append("")
    lines.append("1. **Defer L1 award decision** on the captioned tender pending vigilance review")
    lines.append("2. **Cross-reference** the implicated bidders' history with prior procurement "
                 "awards (last 3 years) for repeated joint participation")
    lines.append("3. **Investigate** for the identified collusion signals (shared signatory "
                 "patterns, identical EMD bank-branch, address proximity, premium-delta clustering)")
    lines.append("4. **Issue notice** to both bidders requesting clarification under AP State "
                 "Procurement Rules + CVC anti-collusion norms")
    lines.append("5. **Consider blacklist proceedings** if vigilance confirms collusion (per AP "
                 "GO Ms No. 094 / 2003 blacklisting framework)")
    lines.append("")
    lines.append(
        "**This referral is internal and confidential. The implicated bidders have NOT "
        "been notified of this referral. Standard regret letters (REGRET) have been issued "
        "to them on the basis of non-L1 ranking; the underlying cartel signal is reserved "
        "for vigilance review.**")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("**Yours faithfully,**")
    lines.append("")
    lines.append("Tender Scrutiny Committee Secretariat")
    lines.append("(Internal communication generated by SYSTEM)")
    lines.append("")
    lines.append("**Drilldown reference:**")
    lines.append("")
    lines.append(f"- BidAnomalyFinding node_id: `{af_node_id}`")
    lines.append(f"- Anomaly class: CARTEL_SUSPECT")
    lines.append(f"- Severity: {severity}; confidence: {confidence}")
    lines.append(f"- Signals: {len(signals)} (see itemised list above)")
    lines.append(f"- Cross-tender appearances: {x_tender} of 3 tenders")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    print("=" * 76); print(f"  M4.5 Drafter — {COMMUNICATION_TYPE}"); print("=" * 76)
    sentinel_pre = snapshot_sentinels()
    delete_prior_communications(COMMUNICATION_TYPE, SOURCE_REF)

    rows = rest_get_range("kg_nodes", {
        "select": "node_id,properties",
        "node_type": "eq.BidAnomalyFinding",
        "properties->>anomaly_class": "eq.CARTEL_SUSPECT",
    })
    print(f"  {len(rows)} CARTEL_SUSPECT BidAnomalyFindings")
    n_emitted = 0
    for af in rows:
        afp = af["properties"] or {}
        tender_id = afp.get("tender_id")
        primary_ids = afp.get("primary_bidder_ids") or []
        if not (tender_id and primary_ids):
            continue
        tinfo = get_tender_info(tender_id)
        a_props = (get_bidder_profile(primary_ids[0]) or {}).get("properties") or {}
        b_props = (get_bidder_profile(primary_ids[1]) or {}).get("properties") or {} if len(primary_ids) > 1 else {}

        source_ids = [af["node_id"]]
        audit_id = compute_audit_id(COMMUNICATION_TYPE, None, tender_id, source_ids)
        content_en = compose_content_en(tinfo, afp, af["node_id"], a_props, b_props)

        tkey = tender_id.replace("tender_synth_", "")
        artifact = ARTIFACT_DIR / f"{COMMUNICATION_TYPE}_{tkey}.md"
        artifact.write_text(content_en, encoding="utf-8")

        rest_post("kg_nodes", [{
            "doc_id": tender_id, "node_type": "Communication",
            "label": f"CARTEL_REVIEW (internal) — {tinfo['name'][:50]} — pair {','.join(p.replace('bid_synth_profile_','') for p in primary_ids)}",
            "properties": {
                "communication_type": COMMUNICATION_TYPE,
                "recipient_bidder_profile_id": None,  # internal recipient
                "recipient_email": None,
                "recipient_role": RECIPIENT_ROLE,
                "tender_id": tender_id, "sender_role": SENDER_ROLE,
                "channel": "PORTAL", "language": "EN", "status": "DRAFT",
                "audit_id": audit_id, "source_finding_node_ids": source_ids,
                "content_en": content_en, "content_te": None,
                "content_te_status": "english_only_internal",
                "artifact_path_md": str(artifact),
                "artifact_path_docx": None, "artifact_path_pdf": None,
                "extracted_by": SOURCE_REF, "defeated": False,
                "implicated_bidder_ids": primary_ids,
                "implicated_bidder_names": afp.get("primary_bidder_names"),
                "severity": afp.get("aggregate_severity"),
                "detection_confidence": afp.get("detection_confidence"),
                "cross_tender_appearances": afp.get("cross_tender_appearances"),
                "tender_name": tinfo["name"], "tender_nit_no": tinfo["nit_no"],
            },
            "source_ref": SOURCE_REF,
        }])
        n_emitted += 1
        print(f"  ✓ tender={tkey:8s}  pair={primary_ids}  audit={audit_id}")

    sentinel_post = snapshot_sentinels()
    assert_sentinel_preserved(sentinel_pre, sentinel_post)
    print(f"\n✓ {COMMUNICATION_TYPE}: {n_emitted} emitted; sentinel preserved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
