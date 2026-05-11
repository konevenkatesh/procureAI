"""M4.2 Drafter — DISQUALIFICATION letters per AP procurement norm.

Reads EligibilityMatrix WHERE aggregate_verdict=DISQUALIFIED; composes a
bidder-facing rejection letter citing every HARD_BLOCK finding with
rule_id + decision_reason; emits one Communication kg_node per
(bidder, tender) DISQUALIFIED pair.

Current corpus prediction: 6 letters (B2 × 3 tenders + B3 × 3 tenders).
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


COMMUNICATION_TYPE = "DISQUALIFICATION"
SOURCE_REF = "module4:draft_disqualification_letter_v1"
SENDER_ROLE = "SYSTEM"


def fetch_disqualified_eligibility_matrices() -> list[dict]:
    """All EligibilityMatrix rows with aggregate_verdict=DISQUALIFIED."""
    return rest_get_range("kg_nodes", {
        "select": "node_id,doc_id,properties",
        "node_type": "eq.EligibilityMatrix",
        "properties->>aggregate_verdict": "eq.DISQUALIFIED",
    })


def fetch_findings_by_ids(finding_ids: list[str]) -> list[dict]:
    """Batch fetch BidEvaluationFinding rows by node_id."""
    if not finding_ids:
        return []
    or_clause = ",".join(f"node_id.eq.{fid}" for fid in finding_ids)
    return rest_get_range("kg_nodes", {
        "select": "node_id,properties",
        "or": f"({or_clause})",
    })


def compose_content_en(profile: dict, tender_info: dict,
                       hard_block_findings: list[dict],
                       em_node_id: str, em_props: dict) -> str:
    """Compose English Markdown body — DISQUALIFICATION letter template."""
    bidder_name = profile.get("company_name", "Bidder")
    today = _dt.date.today().isoformat()
    tender_name = tender_info["name"]
    nit_no = tender_info["nit_no"]
    n_hard = len(hard_block_findings)

    lines: list[str] = []
    lines.append(f"# Letter of Disqualification — Tender {nit_no}")
    lines.append("")
    lines.append(f"**Date:** {today}")
    lines.append(f"**To:** {bidder_name}")
    lines.append(f"  {profile.get('communication_address', '(address on file)')}")
    lines.append(f"  Attention: {profile.get('authorized_signatory_name', 'Authorised Signatory')}")
    lines.append("")
    lines.append(f"**Tender:** {tender_name}")
    lines.append(f"**NIT No.:** {nit_no}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("Dear Sir/Madam,")
    lines.append("")
    lines.append(
        f"This communication concerns the technical evaluation of your bid submitted for "
        f"the captioned tender. Following review by the Tender Scrutiny Committee, your "
        f"bid has been found **INELIGIBLE** on the following **{n_hard} ground(s)** "
        f"(HARD_BLOCK findings under AP procurement norms):")
    lines.append("")
    for i, f in enumerate(hard_block_findings, 1):
        fp = f.get("properties") or {}
        rule_id = fp.get("rule_id", "?")
        typology = fp.get("typology_code", "?")
        reason = (fp.get("decision_reason") or "")
        rule_nl = (fp.get("rule_natural_language") or fp.get("natural_language") or "")[:240]
        lines.append(f"### {i}. {typology}")
        lines.append("")
        lines.append(f"- **Rule cited:** `{rule_id}`")
        lines.append(f"- **Decision basis:** {reason}")
        if rule_nl:
            lines.append(f"- **Rule text:** *{rule_nl}*")
        lines.append(f"- **Finding reference (for verification):** `{f.get('node_id')}`")
        lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        "Per the AP Standard Tender Document and CVC procurement norms, any HARD_BLOCK "
        "finding renders the bid INELIGIBLE for the present tender. Your Earnest Money "
        "Deposit (EMD) will be refunded per the standard EMD return procedure within "
        "21 days of the issue of this letter, subject to no withholding orders.")
    lines.append("")
    lines.append("### Right to representation")
    lines.append("")
    lines.append(
        "If you believe any of the findings above is factually incorrect, you may submit "
        "a written representation within **7 days of receipt** of this letter to the "
        "Procuring Authority. Each representation must:")
    lines.append("")
    lines.append("- Cite the specific finding reference (e.g. `BidEvaluationFinding <node_id>`) being disputed")
    lines.append("- Provide supporting documentation addressing the rule and decision basis")
    lines.append("- Be signed by the authorised signatory on record (Form-12 declaration)")
    lines.append("")
    lines.append(
        "Representations received after 7 days will not be entertained except under "
        "exceptional circumstances per CVC vigilance norms.")
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
    lines.append(f"- EligibilityMatrix node_id: `{em_node_id}`")
    lines.append(f"- Aggregate verdict: {em_props.get('aggregate_verdict')} "
                 f"({em_props.get('count_qualified', 0)} QUALIFIED + "
                 f"{em_props.get('count_ineligible_hard_block', 0)} HARD_BLOCK + "
                 f"{em_props.get('count_ineligible_warning', 0)} WARNING + "
                 f"{em_props.get('count_gap', 0)} GAP)")
    lines.append(f"- Findings consumed: {n_hard} HARD_BLOCK + EligibilityMatrix = {n_hard + 1} kg_nodes")
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

    rows = fetch_disqualified_eligibility_matrices()
    print(f"\n── {len(rows)} DISQUALIFIED EligibilityMatrix rows ──")

    n_emitted = 0
    for em in rows:
        em_props = em["properties"] or {}
        bidder_profile_id = em_props.get("bidder_profile_id")
        tender_id = em_props.get("tender_id")
        finding_node_ids = em_props.get("finding_node_ids") or []

        if not bidder_profile_id or not tender_id:
            print(f"  ⚠ skipping EM {em['node_id']} — missing bidder/tender ids")
            continue

        profile_node = get_bidder_profile(bidder_profile_id)
        profile = profile_node.get("properties") or {}
        tender_info = get_tender_info(tender_id)

        # Resolve HARD_BLOCK findings from the EligibilityMatrix's finding_node_ids
        all_findings = fetch_findings_by_ids(finding_node_ids)
        hard_block_findings = [
            f for f in all_findings
            if (f.get("properties") or {}).get("evaluation_consequence") == "HARD_BLOCK"
            and (f.get("properties") or {}).get("verdict") == "INELIGIBLE"
        ]

        # Source finding ids = HARD_BLOCK finding node_ids + EM node_id itself
        source_ids = [f["node_id"] for f in hard_block_findings] + [em["node_id"]]
        audit_id = compute_audit_id(COMMUNICATION_TYPE, bidder_profile_id, tender_id, source_ids)

        # Compose content
        content_en = compose_content_en(profile, tender_info, hard_block_findings,
                                         em["node_id"], em_props)

        # Write artifact
        bidder_key = bidder_profile_id.replace("bid_synth_profile_", "")
        tender_key = tender_id.replace("tender_synth_", "")
        artifact_path = ARTIFACT_DIR / f"{COMMUNICATION_TYPE}_{bidder_key}_{tender_key}.md"
        artifact_path.write_text(content_en, encoding="utf-8")

        # Emit Communication kg_node
        comm_props = {
            "communication_type":          COMMUNICATION_TYPE,
            "recipient_bidder_profile_id": bidder_profile_id,
            "recipient_email":             profile.get("email_primary"),
            "recipient_role":              None,  # external recipient
            "tender_id":                   tender_id,
            "sender_role":                 SENDER_ROLE,
            "channel":                     "EMAIL",  # per BidderProfile.preferred_notification_channel
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
            "n_hard_block_findings":       len(hard_block_findings),
            "n_source_findings":           len(source_ids),
            "bidder_name":                 profile.get("company_name"),
            "tender_name":                 tender_info["name"],
            "tender_nit_no":               tender_info["nit_no"],
        }
        label = (f"DISQUALIFICATION letter — {profile.get('company_name','?')[:50]} "
                 f"× {tender_info['name'][:30]} ({len(hard_block_findings)} HARD_BLOCK)")

        emitted = rest_post("kg_nodes", [{
            "doc_id":     tender_id,
            "node_type":  "Communication",
            "label":      label,
            "properties": comm_props,
            "source_ref": SOURCE_REF,
        }])[0]
        n_emitted += 1
        print(f"  ✓ {bidder_key:5s} × {tender_key:8s}  "
              f"{len(hard_block_findings)} HARD_BLOCK  "
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
