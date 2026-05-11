"""M4.2 Drafter — AWARD notification for effective L1 bidder.

Reads ComparativeStatement.effective_l1_bidder_id; composes
bidder-facing award letter naming the bidder, the bid amount, the
premium, and the L1 derivation rationale (raw L1 ALB skip + any
cartel deferrals); emits one Communication kg_node per tender.

Current corpus prediction: 3 letters (B9 × 3 tenders).
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


COMMUNICATION_TYPE = "AWARD"
SOURCE_REF = "module4:draft_award_notification_v1"
SENDER_ROLE = "SYSTEM"


def fetch_comparative_statements_with_effective_l1() -> list[dict]:
    return rest_get_range("kg_nodes", {
        "select": "node_id,doc_id,properties",
        "node_type": "eq.ComparativeStatement",
        "properties->>effective_l1_bidder_id": "not.is.null",
    })


def fetch_qualified_findings_for_bidder_tender(bidder_id: str, tender_id: str) -> list[dict]:
    return rest_get_range("kg_nodes", {
        "select": "node_id,properties",
        "node_type": "eq.BidEvaluationFinding",
        "properties->>bidder_profile_id": f"eq.{bidder_id}",
        "properties->>tender_id":         f"eq.{tender_id}",
        "properties->>verdict":           "eq.QUALIFIED",
    })


def fetch_tender_ranking(tender_id: str) -> dict | None:
    rows = rest_get_range("kg_nodes", {
        "select": "node_id,properties",
        "node_type": "eq.TenderRanking",
        "properties->>tender_id": f"eq.{tender_id}",
    })
    return rows[0] if rows else None


def compose_content_en(profile: dict, tender_info: dict,
                       cs_props: dict, tr_props: dict | None,
                       qualified_findings: list[dict]) -> str:
    """Compose English Markdown body — AWARD letter template."""
    bidder_name = profile.get("company_name", "Bidder")
    today = _dt.date.today().isoformat()
    tender_name = tender_info["name"]
    nit_no = tender_info["nit_no"]
    eff_amount = cs_props.get("effective_l1_amount_cr") or 0.0
    eff_premium = ((eff_amount - tender_info["ecv_cr"]) / tender_info["ecv_cr"] * 100.0
                   if tender_info["ecv_cr"] else 0.0)
    raw_l1_name = cs_props.get("l1_winner_bidder_name", "(raw L1)")
    raw_l1_amount = cs_props.get("l1_amount_cr") or 0.0
    alb_flag = cs_props.get("l1_alb_flag", False)
    eff_rationale = cs_props.get("effective_l1_rationale", "")

    lines: list[str] = []
    lines.append(f"# Letter of Award — Tender {nit_no}")
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
        f"We are pleased to inform you that, following review by the Tender Scrutiny "
        f"Committee, your bid has been adjudged the **effective L1** for the captioned "
        f"tender.")
    lines.append("")
    lines.append("### Award details")
    lines.append("")
    lines.append(f"- **Bidder:** {bidder_name}")
    lines.append(f"- **Bid amount:** ₹{eff_amount:.2f} crore")
    lines.append(f"- **Premium vs ECV:** {eff_premium:+.2f}%")
    lines.append(f"- **Tender method:** Item Rate (lowest bid wins among QUALIFIED)")
    lines.append("")
    lines.append("### Effective L1 derivation")
    lines.append("")
    if alb_flag:
        lines.append(
            f"The raw L1 bidder was **{raw_l1_name}** at ₹{raw_l1_amount:.2f} crore "
            f"(premium {((raw_l1_amount - tender_info['ecv_cr'])/tender_info['ecv_cr']*100):+.2f}%). "
            f"Per CVC norms on Abnormally Low Bids (ALB), the raw L1 was flagged as ALB "
            f"and required to submit cost justification (a separate ALB_JUSTIFICATION letter "
            f"has been issued). Pending that review, the effective L1 falls to the next "
            f"non-ALB, non-cartel-suspect bidder — your bid."
        )
    else:
        lines.append(f"Your bid was the raw L1 and has been adjudged the effective L1 on merit.")
    lines.append("")
    lines.append(f"*Rationale (verbatim from ComparativeStatement):*")
    lines.append("")
    lines.append(f"> {eff_rationale}")
    lines.append("")
    lines.append("### Evaluation summary")
    lines.append("")
    n_q = len(qualified_findings)
    lines.append(f"Your bid was found **QUALIFIED** on all {n_q} evaluation criteria:")
    lines.append("")
    for i, f in enumerate(qualified_findings, 1):
        fp = f.get("properties") or {}
        rule_id = fp.get("rule_id", "?")
        typology = fp.get("typology_code", "?")
        lines.append(f"{i}. `{typology}` — rule `{rule_id}` — verdict QUALIFIED")
    lines.append("")
    lines.append("### Next steps — LoA (Letter of Acceptance) issuance")
    lines.append("")
    lines.append(
        "Subject to issuance of the formal Letter of Acceptance (LoA) per APCRDA "
        "contract terms, you are required to report at the Procuring Authority's "
        "office within **14 days of receipt** of this letter with the following:")
    lines.append("")
    lines.append("- Original Letter of Bid signed by authorised signatory")
    lines.append("- Performance Bank Guarantee (PBG) at the contractually specified rate")
    lines.append("- Original EMD instrument (will be returned upon PBG submission)")
    lines.append("- Initial mobilisation advance request (optional, subject to Mobilisation Advance norms)")
    lines.append("")
    lines.append(
        "Failure to report within 14 days will result in forfeiture of your EMD per "
        "the EMD forfeiture clauses of the tender.")
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
    lines.append(f"- ComparativeStatement audit_id: `{cs_props.get('audit_id')}`")
    lines.append(f"- TenderRanking node_id: `{cs_props.get('tender_ranking_node_id')}`")
    if tr_props:
        n_rank = len(tr_props.get('ranking') or [])
        lines.append(f"- Ranking entries (5-bidder QUALIFIED set): {n_rank}")
    lines.append(f"- QUALIFIED findings consumed: {n_q}")
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

    cs_rows = fetch_comparative_statements_with_effective_l1()
    print(f"\n── {len(cs_rows)} ComparativeStatements with effective_l1 ──")

    n_emitted = 0
    for cs in cs_rows:
        cs_props = cs["properties"] or {}
        tender_id = cs_props.get("tender_id")
        eff_id = cs_props.get("effective_l1_bidder_id")
        if not (tender_id and eff_id):
            continue

        profile_node = get_bidder_profile(eff_id)
        profile = profile_node.get("properties") or {}
        tender_info = get_tender_info(tender_id)
        qualified_findings = fetch_qualified_findings_for_bidder_tender(eff_id, tender_id)
        tr = fetch_tender_ranking(tender_id)
        tr_props = tr["properties"] if tr else None

        # Source finding ids = QUALIFIED findings + ComparativeStatement + TenderRanking
        source_ids = [f["node_id"] for f in qualified_findings] + [cs["node_id"]]
        if tr:
            source_ids.append(tr["node_id"])
        audit_id = compute_audit_id(COMMUNICATION_TYPE, eff_id, tender_id, source_ids)

        content_en = compose_content_en(profile, tender_info, cs_props, tr_props,
                                         qualified_findings)

        bidder_key = eff_id.replace("bid_synth_profile_", "")
        tender_key = tender_id.replace("tender_synth_", "")
        artifact_path = ARTIFACT_DIR / f"{COMMUNICATION_TYPE}_{bidder_key}_{tender_key}.md"
        artifact_path.write_text(content_en, encoding="utf-8")

        comm_props = {
            "communication_type":          COMMUNICATION_TYPE,
            "recipient_bidder_profile_id": eff_id,
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
            "effective_l1_amount_cr":      cs_props.get("effective_l1_amount_cr"),
            "raw_l1_alb_flag":             cs_props.get("l1_alb_flag"),
            "n_qualified_findings":        len(qualified_findings),
            "n_source_findings":           len(source_ids),
            "bidder_name":                 profile.get("company_name"),
            "tender_name":                 tender_info["name"],
            "tender_nit_no":               tender_info["nit_no"],
        }
        label = (f"AWARD letter — {profile.get('company_name','?')[:50]} × "
                 f"{tender_info['name'][:30]} (₹{cs_props.get('effective_l1_amount_cr', 0.0):.2f}cr)")

        emitted = rest_post("kg_nodes", [{
            "doc_id":     tender_id,
            "node_type":  "Communication",
            "label":      label,
            "properties": comm_props,
            "source_ref": SOURCE_REF,
        }])[0]
        n_emitted += 1
        print(f"  ✓ {bidder_key:8s} × {tender_key:8s}  "
              f"₹{cs_props.get('effective_l1_amount_cr', 0.0):.2f}cr  "
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
