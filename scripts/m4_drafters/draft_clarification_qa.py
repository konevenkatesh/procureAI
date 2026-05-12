"""M4.6 Drafter — BIDDER_CLARIFICATION_QA workflow.

Bidders ask doubts during the tender process; procurement officer responds.
Both Q and A are Communication kg_nodes, bilingual EN+TE.

Schema additions (per directive):
  direction:                 BIDDER_INBOUND / OFFICER_OUTBOUND
  parent_communication_id:   for Q→A threading (null on initial Q)
  subject_line:              short topic for inbox display

Seed: 3 synthetic Q&A pairs (one per tender) demonstrating the pattern.
"""
from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from scripts.m4_drafters._common import (  # noqa: E402
    ARTIFACT_DIR, rest_post, get_bidder_profile, get_tender_info,
    compute_audit_id, delete_prior_communications,
    snapshot_sentinels, assert_sentinel_preserved,
)


COMMUNICATION_TYPE = "BIDDER_CLARIFICATION_QA"
SOURCE_REF = "module4:draft_clarification_qa_v1"


# ── Synthetic Q&A seeds (3 pairs, one per tender) ────────────────────

QA_SEEDS = [
    {
        "tender_id": "tender_synth_kurnool",
        "bidder_profile_id": "bid_synth_profile_b9_lead",  # JV Lead Partner asks
        "subject_line": "JV partners — shared PAN registration query",
        "question_en": (
            "Sir/Madam,\n\n"
            "We are submitting our bid for the captioned tender as a JV with two partner "
            "firms. We seek clarification on the following:\n\n"
            "1. Is it acceptable for JV partners to share PAN registration documents at "
            "the JV level instead of submitting individual PAN copies per partner?\n"
            "2. If yes, should the JV PAN be issued separately by the Income Tax Department, "
            "or is it sufficient to attach individual partner PANs with a JV declaration "
            "letter?\n\n"
            "We would appreciate clarification before the bid submission deadline so that "
            "we can prepare our Form-14 (JV Agreement) and Form-15 (Power of Attorney) "
            "documentation accordingly.\n\n"
            "Thank you."
        ),
        "answer_en": (
            "Sir/Madam,\n\n"
            "Thank you for your clarification request. Please find our response to your "
            "queries:\n\n"
            "1. **Individual PAN copies required**: Per AP Standard Tender Document "
            "Clause 1.6.4 read with Form-14 (JV Agreement) provisions, each JV partner "
            "must submit their individual PAN registration certificate. A consolidated "
            "JV-level PAN is NOT mandated.\n\n"
            "2. **JV PAN not required**: There is no requirement for the JV entity to "
            "obtain a separate Income Tax PAN. Individual partner PANs along with the "
            "JV Agreement (Form-14) and Power of Attorney (Form-15) duly notarised are "
            "sufficient for bid evaluation purposes.\n\n"
            "Should your JV win the award, the Letter of Acceptance (LoA) will be issued "
            "to the JV as a named contracting party, and tax matters will be addressed "
            "per the JV Agreement's tax-handling clause.\n\n"
            "Please proceed with bid submission per the standard JV documentation requirements."
        ),
    },
    {
        "tender_id": "tender_synth_ja",
        "bidder_profile_id": "bid_synth_profile_b6",
        "subject_line": "Class-I contractor PQ floor — JA tender clarification",
        "question_en": (
            "Sir/Madam,\n\n"
            "Our firm is a Class-I contractor registered in Andhra Pradesh. We are "
            "interested in bidding for the Andhra Pradesh Judicial Academy tender "
            "(NIT JA/2026/CW/001) but seek clarification on the pre-qualification (PQ) "
            "criteria.\n\n"
            "1. The NIT mentions 'required_class = Special' for this tender. Does this "
            "mean Class-I contractors are excluded from bidding, or can we bid through a "
            "JV with a Special class partner?\n"
            "2. What is the exact construction-turnover PQ floor for this tender? The NIT "
            "mentions a 5-year average; we want to confirm the cut-off amount.\n\n"
            "Thank you."
        ),
        "answer_en": (
            "Sir/Madam,\n\n"
            "Thank you for your clarification request. Please find our response below:\n\n"
            "1. **Class-I contractor eligibility for Judicial Academy tender**: The "
            "captioned tender requires **Special class** contractor registration as the "
            "single-bidder pre-qualification (per AP-GO Ms No. 094/2003 and the NIT "
            "Section III). Class-I contractors may participate ONLY through a Joint "
            "Venture (JV) where the Lead Partner is Special class registered. Form-14 "
            "(JV Agreement) and Form-15 (Power of Attorney) must be submitted.\n\n"
            "2. **Construction turnover PQ floor**: The construction-turnover 5-year "
            "average floor is **₹83.70 crore** for this tender (calculated as 2 × annual "
            "contract value per CVC-028 criterion). Additionally, please note the Ext-3 "
            "financial-turnover floor of **₹37.65 crore** (3-year average, MPG-255 / "
            "CVC-028 Financial Standing criterion).\n\n"
            "Both floors must be cleared for QUALIFIED status. Lead-Partner-alone "
            "financial standing applies for JV bidders.\n\n"
            "We encourage you to proceed if your firm meets the JV partnership pathway."
        ),
    },
    {
        "tender_id": "tender_synth_hc",
        "bidder_profile_id": "bid_synth_profile_b1",
        "subject_line": "Offsite labour mobilisation in Stage 1 — query",
        "question_en": (
            "Sir/Madam,\n\n"
            "We are preparing our bid for the AP High Court Complex construction tender "
            "(NIT HC/APCRDA/2026/PROC/001). We seek clarification on the construction "
            "methodology section, specifically Stage 1 (foundation works):\n\n"
            "1. Is offsite labour mobilisation acceptable for Stage 1 activities (e.g. "
            "rebar fabrication, formwork pre-assembly at offsite yards), or must all "
            "labour work be performed on-site from day 1?\n"
            "2. If offsite mobilisation is acceptable, are there minimum standards "
            "(distance from site, EPF/ESI compliance of offsite yard, etc.) we should "
            "address in our methodology document?\n\n"
            "Thank you."
        ),
        "answer_en": (
            "Sir/Madam,\n\n"
            "Thank you for your detailed clarification request. Please find our response:\n\n"
            "1. **Offsite labour mobilisation acceptable for Stage 1**: Yes, offsite "
            "mobilisation for rebar fabrication, formwork pre-assembly, and other "
            "preparatory activities is acceptable for Stage 1 foundation works. "
            "However, on-site mobilisation of supervisory + safety + QA personnel is "
            "required from day 1 of contract commencement.\n\n"
            "2. **Minimum standards for offsite yards**:\n"
            "   - Offsite yard must be within **75 km** of the project site by road "
            "(per APCRDA logistics norms)\n"
            "   - EPF + ESI compliance must extend to all offsite labour (your bid must "
            "address this in Statement III — Satisfactory Completion)\n"
            "   - Quality control: offsite-fabricated rebar must be tested per IS 1786:2008 "
            "with test certificates submitted to the on-site QA Engineer\n"
            "   - Transportation logistics: pre-approved by Site Engineer; daily movement "
            "report to be maintained\n\n"
            "Please ensure your methodology document (Statement IX — Work Plan) addresses "
            "these standards. The bid must include a separate annexure listing your "
            "designated offsite yard(s) with addresses + EPF registration.\n\n"
            "Best of luck with your bid preparation."
        ),
    },
]


def emit_question(qa_seed: dict) -> str:
    """Emit BIDDER_INBOUND question Communication; return node_id."""
    today = _dt.date.today().isoformat()
    bidder_id = qa_seed["bidder_profile_id"]
    tender_id = qa_seed["tender_id"]
    profile = (get_bidder_profile(bidder_id) or {}).get("properties") or {}
    tinfo = get_tender_info(tender_id)

    # Wrap raw question in letter format
    bidder_name = profile.get("company_name", "Bidder")
    content_en = (
        f"# Bidder Clarification Question — Tender {tinfo['nit_no']}\n\n"
        f"**Date:** {today}\n"
        f"**From:** {bidder_name}\n"
        f"  {profile.get('communication_address', '(address on file)')}\n"
        f"  Signatory: {profile.get('authorized_signatory_name', '?')}\n\n"
        f"**To:** Procurement Authority\n\n"
        f"**Tender:** {tinfo['name']}\n"
        f"**NIT No.:** {tinfo['nit_no']}\n"
        f"**Subject:** {qa_seed['subject_line']}\n\n"
        f"---\n\n"
        f"{qa_seed['question_en']}\n\n"
        f"---\n\n"
        f"**Yours faithfully,**\n\n"
        f"{profile.get('authorized_signatory_name', 'Authorised Signatory')}\n"
        f"{bidder_name}\n"
    )

    source_ids: list[str] = []  # No source findings; pure bidder inbound
    audit_id = compute_audit_id(COMMUNICATION_TYPE + "_Q", bidder_id, tender_id,
                                 source_ids + [qa_seed["subject_line"]])

    bkey = bidder_id.replace("bid_synth_profile_", "")
    tkey = tender_id.replace("tender_synth_", "")
    artifact = ARTIFACT_DIR / f"{COMMUNICATION_TYPE}_Q_{bkey}_{tkey}.md"
    artifact.write_text(content_en, encoding="utf-8")

    res = rest_post("kg_nodes", [{
        "doc_id": tender_id, "node_type": "Communication",
        "label": f"BIDDER_CLARIFICATION_Q — {bidder_name[:40]} → {qa_seed['subject_line'][:50]}",
        "properties": {
            "communication_type": COMMUNICATION_TYPE,
            "direction":          "BIDDER_INBOUND",
            "parent_communication_id": None,
            "subject_line":       qa_seed["subject_line"],
            "recipient_bidder_profile_id": None,  # Q is FROM bidder, addressed TO officer
            "recipient_email":    None,
            "recipient_role":     "PROCUREMENT_AUTHORITY",
            "sender_bidder_profile_id": bidder_id,
            "tender_id":          tender_id,
            "sender_role":        "BIDDER",
            "channel":             "PORTAL",
            "language":            "EN",  # to be enriched to EN+TE post-emit
            "status":              "RECEIVED",
            "audit_id":            audit_id,
            "source_finding_node_ids": source_ids,
            "content_en":          content_en,
            "content_te":          None,
            "artifact_path_md":    str(artifact),
            "artifact_path_docx":  None, "artifact_path_pdf": None,
            "extracted_by":        SOURCE_REF,
            "defeated":            False,
            "bidder_name":         bidder_name,
            "tender_name":         tinfo["name"],
            "tender_nit_no":       tinfo["nit_no"],
        },
        "source_ref": SOURCE_REF,
    }])[0]
    print(f"  ✓ Q {bkey:8s} × {tkey:8s}  '{qa_seed['subject_line'][:50]}'  audit={audit_id}")
    return res["node_id"]


def emit_answer(qa_seed: dict, question_node_id: str) -> str:
    today = _dt.date.today().isoformat()
    bidder_id = qa_seed["bidder_profile_id"]
    tender_id = qa_seed["tender_id"]
    profile = (get_bidder_profile(bidder_id) or {}).get("properties") or {}
    tinfo = get_tender_info(tender_id)
    bidder_name = profile.get("company_name", "Bidder")

    content_en = (
        f"# Reply to Clarification Query — Tender {tinfo['nit_no']}\n\n"
        f"**Date:** {today}\n"
        f"**From:** Procurement Authority\n"
        f"**To:** {bidder_name}\n"
        f"  {profile.get('communication_address', '(address on file)')}\n"
        f"  Attention: {profile.get('authorized_signatory_name', '?')}\n\n"
        f"**Tender:** {tinfo['name']}\n"
        f"**NIT No.:** {tinfo['nit_no']}\n"
        f"**Subject:** Re: {qa_seed['subject_line']}\n"
        f"**In reply to:** Communication node `{question_node_id}` (your query dated {today})\n\n"
        f"---\n\n"
        f"{qa_seed['answer_en']}\n\n"
        f"---\n\n"
        f"**Yours faithfully,**\n\n"
        f"Procurement Authority\n"
        f"(Reply generated by SYSTEM; signed-off by Dealing Officer before dispatch)\n\n"
        f"**Drilldown reference:**\n\n"
        f"- Question communication: `{question_node_id}`\n"
        f"- Subject: {qa_seed['subject_line']}\n"
    )

    source_ids = [question_node_id]
    audit_id = compute_audit_id(COMMUNICATION_TYPE + "_A", bidder_id, tender_id, source_ids)

    bkey = bidder_id.replace("bid_synth_profile_", "")
    tkey = tender_id.replace("tender_synth_", "")
    artifact = ARTIFACT_DIR / f"{COMMUNICATION_TYPE}_A_{bkey}_{tkey}.md"
    artifact.write_text(content_en, encoding="utf-8")

    res = rest_post("kg_nodes", [{
        "doc_id": tender_id, "node_type": "Communication",
        "label": f"BIDDER_CLARIFICATION_A — Re: {qa_seed['subject_line'][:60]} → {bidder_name[:30]}",
        "properties": {
            "communication_type": COMMUNICATION_TYPE,
            "direction":          "OFFICER_OUTBOUND",
            "parent_communication_id": question_node_id,
            "subject_line":       f"Re: {qa_seed['subject_line']}",
            "recipient_bidder_profile_id": bidder_id,
            "recipient_email":    profile.get("email_primary"),
            "recipient_role":     None,
            "tender_id":          tender_id,
            "sender_role":        "PROCUREMENT_AUTHORITY",
            "channel":            "EMAIL",
            "language":           "EN",  # to be enriched to EN+TE post-emit
            "status":             "DRAFT",
            "audit_id":           audit_id,
            "source_finding_node_ids": source_ids,
            "content_en":         content_en,
            "content_te":         None,
            "artifact_path_md":   str(artifact),
            "artifact_path_docx": None, "artifact_path_pdf": None,
            "extracted_by":       SOURCE_REF,
            "defeated":           False,
            "bidder_name":        bidder_name,
            "tender_name":        tinfo["name"],
            "tender_nit_no":      tinfo["nit_no"],
        },
        "source_ref": SOURCE_REF,
    }])[0]
    print(f"  ✓ A {bkey:8s} × {tkey:8s}  parent={question_node_id[:8]}  audit={audit_id}")
    return res["node_id"]


def main() -> int:
    print("=" * 76); print(f"  M4.6 Drafter — {COMMUNICATION_TYPE}"); print("=" * 76)
    sentinel_pre = snapshot_sentinels()
    n_prior = delete_prior_communications(COMMUNICATION_TYPE, SOURCE_REF)
    if n_prior:
        print(f"  cleanup: deleted {n_prior} prior")

    print(f"\n── 3 Q&A pairs to emit ──")
    n_emitted = 0
    for seed in QA_SEEDS:
        q_id = emit_question(seed)
        a_id = emit_answer(seed, q_id)
        n_emitted += 2

    sentinel_post = snapshot_sentinels()
    assert_sentinel_preserved(sentinel_pre, sentinel_post)
    print(f"\n✓ {COMMUNICATION_TYPE}: {n_emitted} emitted (3 Q + 3 A); sentinel preserved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
