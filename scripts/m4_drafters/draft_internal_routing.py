"""M4.5 Drafter — INTERNAL_ROUTING for workflow handoffs.

Emits 3 routing communications per tender (Clerk → Dealing Officer →
Department Head workflow). Total: 9. Internal-only; ENGLISH-ONLY.
"""
from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from scripts.m4_drafters._common import (  # noqa: E402
    ARTIFACT_DIR, rest_get_range, rest_post, get_tender_info,
    compute_audit_id, delete_prior_communications,
    snapshot_sentinels, assert_sentinel_preserved,
)


COMMUNICATION_TYPE = "INTERNAL_ROUTING"
SOURCE_REF = "module4:draft_internal_routing_v1"
SENDER_ROLE = "SYSTEM"


WORKFLOW_STAGES = [
    {"from": "TENDER_SCRUTINY_COMMITTEE", "to": "CLERK",
     "subject": "Evaluation Complete — Awaiting Clerk Review for Documentation"},
    {"from": "CLERK", "to": "DEALING_OFFICER",
     "subject": "Documentation Verified — Awaiting Dealing Officer Recommendation"},
    {"from": "DEALING_OFFICER", "to": "DEPARTMENT_HEAD",
     "subject": "Recommendation Prepared — Awaiting Department Head Approval"},
]


def compose_content_en(tender_info: dict, stage: dict, cs_props: dict,
                       cs_node_id: str) -> str:
    today = _dt.date.today().isoformat()
    eff_name = cs_props.get("effective_l1_bidder_name", "(awardee)")
    eff_amount = cs_props.get("effective_l1_amount_cr", 0.0) or 0.0
    qualified = cs_props.get("qualified_count", 0)
    disqualified = cs_props.get("disqualified_count", 0)
    flagged = cs_props.get("flagged_count", 0)
    mark_for_doc = cs_props.get("mark_for_doc_count", 0)
    cartel_pairs = cs_props.get("cartel_suspect_pairs") or []
    alb_bidders = cs_props.get("alb_corroboration_bidders") or []

    lines = []
    lines.append(f"# {stage['subject']} — Tender {tender_info['nit_no']}")
    lines.append("")
    lines.append(f"**CLASSIFICATION:** INTERNAL — WORKFLOW ROUTING")
    lines.append(f"**Date:** {today}")
    lines.append(f"**From:** {stage['from']}")
    lines.append(f"**To:** {stage['to']}")
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
        f"The captioned tender has completed the **{stage['from']}** review stage and is "
        f"forwarded to **{stage['to']}** for the next workflow action. Summary of the "
        f"evaluation outcome is captured below.")
    lines.append("")
    lines.append("### Evaluation Summary")
    lines.append("")
    lines.append(f"- Total bidders received: 9")
    lines.append(f"- **QUALIFIED:** {qualified}")
    lines.append(f"- **FLAGGED for committee review:** {flagged}")
    lines.append(f"- **MARKED for documentation review:** {mark_for_doc}")
    lines.append(f"- **DISQUALIFIED:** {disqualified}")
    lines.append("")
    lines.append("### Effective L1 (post-anomaly adjustment)")
    lines.append("")
    lines.append(f"- **Awardee:** {eff_name}")
    lines.append(f"- **Award amount:** ₹{eff_amount:.2f} crore")
    lines.append("")
    lines.append("### Anomaly Findings")
    lines.append("")
    if cartel_pairs:
        lines.append(f"- **CARTEL_SUSPECT pairs flagged:** {len(cartel_pairs)} (separate vigilance referral issued)")
    if alb_bidders:
        lines.append(f"- **ALB_CORROBORATION bidders:** {len(alb_bidders)} (ALB justification requests issued)")
    if not (cartel_pairs or alb_bidders):
        lines.append("- No anomaly findings for this tender.")
    lines.append("")
    lines.append("### Action requested from " + stage["to"])
    lines.append("")
    if stage["to"] == "CLERK":
        lines.append("- Verify completeness of evaluation documentation (ComparativeStatement DOCX/PDF)")
        lines.append("- Cross-check all bidder communications drafted (DISQUAL/AWARD/ALB/FLAGGED/DOC_REVIEW/REGRET)")
        lines.append("- Forward to Dealing Officer with checklist sign-off")
    elif stage["to"] == "DEALING_OFFICER":
        lines.append("- Review evaluation findings + effective L1 derivation rationale")
        lines.append("- Prepare draft recommendation note for Department Head")
        lines.append("- Flag any vigilance concerns (cartel referral status)")
        lines.append("- Forward to Department Head with recommendation")
    elif stage["to"] == "DEPARTMENT_HEAD":
        lines.append("- Final approval of effective L1 award (or rejection with reasons)")
        lines.append("- Sign-off on bidder communications dispatch")
        lines.append("- Authorise LoA issuance to effective L1 winner")
        lines.append("- Confirm vigilance referral status for cartel cases (separate review track)")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("**Yours faithfully,**")
    lines.append("")
    lines.append(stage["from"])
    lines.append("(Internal workflow handoff generated by SYSTEM)")
    lines.append("")
    lines.append("**Drilldown reference:**")
    lines.append("")
    lines.append(f"- ComparativeStatement audit_id: `{cs_props.get('audit_id')}`")
    lines.append(f"- ComparativeStatement node_id: `{cs_node_id}`")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    print("=" * 76); print(f"  M4.5 Drafter — {COMMUNICATION_TYPE}"); print("=" * 76)
    sentinel_pre = snapshot_sentinels()
    delete_prior_communications(COMMUNICATION_TYPE, SOURCE_REF)

    cs_rows = rest_get_range("kg_nodes", {
        "select": "node_id,doc_id,properties",
        "node_type": "eq.ComparativeStatement",
    })
    print(f"  {len(cs_rows)} ComparativeStatements × {len(WORKFLOW_STAGES)} stages each")
    n_emitted = 0
    for cs in cs_rows:
        cs_props = cs["properties"] or {}
        tender_id = cs_props.get("tender_id")
        tinfo = get_tender_info(tender_id)
        for stage in WORKFLOW_STAGES:
            source_ids = [cs["node_id"]]
            audit_id = compute_audit_id(COMMUNICATION_TYPE,
                                         f"{stage['from']}_to_{stage['to']}",
                                         tender_id, source_ids)
            content_en = compose_content_en(tinfo, stage, cs_props, cs["node_id"])

            tkey = tender_id.replace("tender_synth_", "")
            artifact = ARTIFACT_DIR / f"{COMMUNICATION_TYPE}_{stage['to'].lower()}_{tkey}.md"
            artifact.write_text(content_en, encoding="utf-8")

            rest_post("kg_nodes", [{
                "doc_id": tender_id, "node_type": "Communication",
                "label": f"INTERNAL_ROUTING — {stage['from']} → {stage['to']} — {tinfo['name'][:30]}",
                "properties": {
                    "communication_type": COMMUNICATION_TYPE,
                    "recipient_bidder_profile_id": None,
                    "recipient_email": None,
                    "recipient_role": stage["to"],
                    "tender_id": tender_id, "sender_role": stage["from"],
                    "channel": "PORTAL", "language": "EN", "status": "DRAFT",
                    "audit_id": audit_id, "source_finding_node_ids": source_ids,
                    "content_en": content_en, "content_te": None,
                    "content_te_status": "english_only_internal",
                    "artifact_path_md": str(artifact),
                    "artifact_path_docx": None, "artifact_path_pdf": None,
                    "extracted_by": SOURCE_REF, "defeated": False,
                    "workflow_stage_from": stage["from"],
                    "workflow_stage_to":   stage["to"],
                    "tender_name": tinfo["name"], "tender_nit_no": tinfo["nit_no"],
                },
                "source_ref": SOURCE_REF,
            }])
            n_emitted += 1
            print(f"  ✓ {stage['from']:30s} → {stage['to']:18s} × {tkey:8s}")

    sentinel_post = snapshot_sentinels()
    assert_sentinel_preserved(sentinel_pre, sentinel_post)
    print(f"\n✓ {COMMUNICATION_TYPE}: {n_emitted} emitted; sentinel preserved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
