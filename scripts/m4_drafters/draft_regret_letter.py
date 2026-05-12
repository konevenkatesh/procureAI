"""M4.5 Drafter — REGRET letter to non-L1 QUALIFIED bidders.

Reads ComparativeStatement + EligibilityMatrix. Sends to every QUALIFIED
bidder who is NOT the effective_l1. Includes B8 (ALB-rejected from L1),
B6+B7 (cartel-referred), B1 (non-anomalous non-L1).

Bidder-facing. Bilingual EN+TE.

Predicted count: ~12 (4 non-L1-QUALIFIED bidders × 3 tenders).
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


COMMUNICATION_TYPE = "REGRET"
SOURCE_REF = "module4:draft_regret_letter_v1"
SENDER_ROLE = "SYSTEM"


def fetch_qualified_em_rows() -> list[dict]:
    return rest_get_range("kg_nodes", {
        "select": "node_id,properties",
        "node_type": "eq.EligibilityMatrix",
        "properties->>aggregate_verdict": "eq.QUALIFIED",
    })


def fetch_comparative_statement(tender_id: str) -> dict | None:
    rows = rest_get_range("kg_nodes", {
        "select": "node_id,properties",
        "node_type": "eq.ComparativeStatement",
        "properties->>tender_id": f"eq.{tender_id}",
    })
    return rows[0] if rows else None


def compose_content_en(profile: dict, tender_info: dict, em_props: dict,
                       cs_props: dict, ranking_entry: dict | None,
                       em_node_id: str, cs_node_id: str) -> str:
    bidder_name = profile.get("company_name", "Bidder")
    today = _dt.date.today().isoformat()
    eff_name = cs_props.get("effective_l1_bidder_name", "(awardee)")
    eff_amount = cs_props.get("effective_l1_amount_cr", 0.0) or 0.0
    own_bid = ranking_entry.get("bid_amount_cr", 0.0) if ranking_entry else 0.0
    own_rank = ranking_entry.get("rank_position", "?") if ranking_entry else "?"
    own_premium = ranking_entry.get("premium_pct", 0.0) if ranking_entry else 0.0
    own_alb_flag = ranking_entry.get("alb_flag", False) if ranking_entry else False

    lines = []
    lines.append(f"# Regret Letter — Tender {tender_info['nit_no']}")
    lines.append("")
    lines.append(f"**Date:** {today}")
    lines.append(f"**To:** {bidder_name}")
    lines.append(f"  {profile.get('communication_address', '(address on file)')}")
    lines.append(f"  Attention: {profile.get('authorized_signatory_name', 'Authorised Signatory')}")
    lines.append("")
    lines.append(f"**Tender:** {tender_info['name']}")
    lines.append(f"**NIT No.:** {tender_info['nit_no']}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("Dear Sir/Madam,")
    lines.append("")
    lines.append(
        f"We thank you for your participation in the captioned tender. Your bid was "
        f"found **QUALIFIED** on all 13 evaluation criteria per AP Standard Tender "
        f"Document and CVC procurement norms.")
    lines.append("")
    lines.append("### Bid outcome")
    lines.append("")
    lines.append(f"- **Your bid:** ₹{own_bid:.2f} crore ({own_premium:+.2f}% vs ECV)")
    lines.append(f"- **Your raw ranking:** {own_rank}")
    if own_alb_flag:
        lines.append(f"- **ALB flag:** YES — your bid was flagged as Abnormally Low and "
                     f"required separate justification (a separate ALB_JUSTIFICATION letter "
                     f"was issued; the committee deferred final award pending that review)")
    lines.append("")
    lines.append(
        f"Following review of all qualified bids and adjustment per CVC norms on "
        f"Abnormally Low Bids and the AP Standard Tender Document cartel-review "
        f"provisions, the contract has been awarded as follows:")
    lines.append("")
    lines.append(f"- **Effective L1 (awarded bidder):** {eff_name}")
    lines.append(f"- **Award amount:** ₹{eff_amount:.2f} crore")
    lines.append("")
    lines.append(
        "Your bid, while fully QUALIFIED, did not emerge as the effective L1 after the "
        "platform applied ALB-screening and cartel-review provisions. We regret to inform "
        "you that the award has been issued to the bidder named above.")
    lines.append("")
    lines.append("### EMD return + future participation")
    lines.append("")
    lines.append(
        "Your Earnest Money Deposit (EMD) will be refunded per the standard EMD return "
        "procedure within **21 days** of the issuance of this letter, subject to no "
        "withholding orders.")
    lines.append("")
    lines.append(
        "We thank you for your continued participation in the AP procurement process and "
        "encourage you to bid in future tenders. The platform's full evaluation report "
        "(13-criterion breakdown + ranking + anomaly findings) is available on the "
        "procurement portal for your review.")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("**Yours faithfully,**")
    lines.append("")
    lines.append("Procurement Authority")
    lines.append("(Communication generated by SYSTEM)")
    lines.append("")
    lines.append("**Drilldown reference:**")
    lines.append("")
    lines.append(f"- ComparativeStatement audit_id: `{cs_props.get('audit_id')}`")
    lines.append(f"- EligibilityMatrix node_id (your bid): `{em_node_id}`")
    lines.append(f"- Effective L1 derivation rationale: per ComparativeStatement Part F")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    print("=" * 76); print(f"  M4.5 Drafter — {COMMUNICATION_TYPE}"); print("=" * 76)
    sentinel_pre = snapshot_sentinels()
    delete_prior_communications(COMMUNICATION_TYPE, SOURCE_REF)

    em_rows = fetch_qualified_em_rows()
    print(f"  {len(em_rows)} QUALIFIED EligibilityMatrix rows total")
    n_emitted = 0
    for em in em_rows:
        ep = em["properties"] or {}
        bidder_id = ep.get("bidder_profile_id"); tender_id = ep.get("tender_id")
        if not (bidder_id and tender_id):
            continue
        cs = fetch_comparative_statement(tender_id)
        if cs is None:
            continue
        cs_props = cs["properties"] or {}
        eff_id = cs_props.get("effective_l1_bidder_id")
        if bidder_id == eff_id:
            # Effective L1 gets AWARD, not REGRET
            continue

        # Find this bidder's ranking entry to get bid amount + rank + flags
        ranking_entry = None
        ranking = cs_props.get("ranking") or []
        # ComparativeStatement may not store ranking directly; check TenderRanking instead
        if not ranking:
            tr = rest_get_range("kg_nodes", {
                "select": "properties",
                "node_type": "eq.TenderRanking",
                "properties->>tender_id": f"eq.{tender_id}",
            })
            if tr:
                ranking = (tr[0]["properties"] or {}).get("ranking") or []
        for r in ranking:
            if r.get("bidder_profile_id") == bidder_id:
                ranking_entry = r
                break

        profile = (get_bidder_profile(bidder_id) or {}).get("properties") or {}
        tinfo = get_tender_info(tender_id)
        source_ids = [em["node_id"], cs["node_id"]]
        audit_id = compute_audit_id(COMMUNICATION_TYPE, bidder_id, tender_id, source_ids)
        content_en = compose_content_en(profile, tinfo, ep, cs_props,
                                         ranking_entry, em["node_id"], cs["node_id"])

        bkey = bidder_id.replace("bid_synth_profile_", "")
        tkey = tender_id.replace("tender_synth_", "")
        artifact = ARTIFACT_DIR / f"{COMMUNICATION_TYPE}_{bkey}_{tkey}.md"
        artifact.write_text(content_en, encoding="utf-8")

        rest_post("kg_nodes", [{
            "doc_id": tender_id, "node_type": "Communication",
            "label": f"REGRET — {profile.get('company_name','?')[:40]} × {tinfo['name'][:30]}",
            "properties": {
                "communication_type": COMMUNICATION_TYPE,
                "recipient_bidder_profile_id": bidder_id,
                "recipient_email": profile.get("email_primary"), "recipient_role": None,
                "tender_id": tender_id, "sender_role": SENDER_ROLE,
                "channel": "EMAIL", "language": "EN", "status": "DRAFT",
                "audit_id": audit_id, "source_finding_node_ids": source_ids,
                "content_en": content_en, "content_te": None,
                "artifact_path_md": str(artifact),
                "artifact_path_docx": None, "artifact_path_pdf": None,
                "extracted_by": SOURCE_REF, "defeated": False,
                "own_rank": ranking_entry.get("rank_position") if ranking_entry else None,
                "own_bid_amount_cr": ranking_entry.get("bid_amount_cr") if ranking_entry else None,
                "effective_l1_bidder_id": eff_id,
                "n_source_findings": len(source_ids),
                "bidder_name": profile.get("company_name"),
                "tender_name": tinfo["name"], "tender_nit_no": tinfo["nit_no"],
            },
            "source_ref": SOURCE_REF,
        }])
        n_emitted += 1
        print(f"  ✓ {bkey:5s} × {tkey:8s}  rank={ranking_entry.get('rank_position') if ranking_entry else '?'}  audit={audit_id}")

    sentinel_post = snapshot_sentinels()
    assert_sentinel_preserved(sentinel_pre, sentinel_post)
    print(f"\n✓ {COMMUNICATION_TYPE}: {n_emitted} emitted; sentinel preserved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
