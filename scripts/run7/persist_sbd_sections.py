"""R7.1 — Persist 27 SBDSection kg_nodes to Supabase.

For each of 9 sections:
  - 2 raw nodes: one per source doc (hod / lps)
  - 1 canonical node: HOD content with LPS variance metadata

Total: 27 SBDSection rows.

kg_nodes additive — does NOT touch the 7 hard sentinels.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from builder.config import settings  # noqa: E402

REST = settings.supabase_rest_url
H = {
    "apikey": settings.supabase_anon_key,
    "Authorization": f"Bearer {settings.supabase_anon_key}",
    "Content-Type": "application/json",
}

CLASSIF_FILE = Path("/tmp/section_classification.json")
EXTRACTED = REPO / "data" / "extracted"


SOURCE_REF = "module1:sbd_section_v1"


SECTION_META = {
    "NIT": {
        "name": "Notice Inviting Tender",
        "hod_pp": (3, 5),
        "lps_pp": (3, 6),
        "applicable_disciplines": ["ALL"],
        "placeholders": [
            "tender_number", "tender_subject", "ecv_inr", "ecv_words",
            "period_of_completion", "dlp_months", "form_of_contract",
            "eligible_class", "bid_validity_days", "bid_security_inr",
            "bid_security_in_favour_of", "transaction_fee_inr",
            "bid_doc_start_date", "bid_doc_close_date", "pre_bid_meeting_date",
            "bid_submission_due_date", "bid_opening_date", "loa_date",
            "department_name", "officer_inviting_bids", "address",
            "contact_details", "email", "name_of_project", "name_of_work",
        ],
    },
    "section_I": {
        "name": "Instructions to Bidders (ITB)",
        "hod_pp": (7, 24), "lps_pp": (8, 26),
        "applicable_disciplines": ["ALL"],
        "placeholders": [],  # pure boilerplate
    },
    "section_II": {
        "name": "Bid Data Sheet (BDS)",
        "hod_pp": (25, 37), "lps_pp": (27, 39),
        "applicable_disciplines": ["ALL"],
        "placeholders": [
            "employer_name", "address", "officer_inviting_bids",
            "bid_security_amount", "bid_validity_days",
            "deadline_for_questions", "pre_bid_meeting_dt",
            "bid_submission_dt", "bid_opening_dt", "evaluation_currency",
        ],
    },
    "section_III": {
        "name": "Evaluation and Qualification Criteria",
        "hod_pp": (38, 47), "lps_pp": (40, 49),
        "applicable_disciplines": ["ALL"],
        "placeholders": [
            "avg_turnover_floor_cr", "similar_works_threshold_pct",
            "available_bid_capacity_formula_M", "solvency_floor_pct",
            "key_personnel_min_count", "equipment_register_completeness",
        ],
    },
    "section_IV": {
        "name": "Bidding Forms",
        "hod_pp": (48, 88), "lps_pp": (50, 92),
        "applicable_disciplines": ["ALL"],
        "placeholders": ["tender_number", "tender_subject"],
    },
    "section_V": {
        "name": "Fraud and Corruption",
        "hod_pp": (89, 96), "lps_pp": (93, 94),
        "applicable_disciplines": ["ALL"],
        "placeholders": [],
    },
    "section_VI": {
        "name": "Works' Requirements",
        "hod_pp": (98, 163), "lps_pp": (96, 167),
        "applicable_disciplines": ["MEP", "Civil"],
        "placeholders": [
            "name_of_work", "scope_summary_bullets", "discipline_mix",
            "quantity_matrix", "schedule_a_provisions",
            "key_personnel_role_list",
        ],
        "sub_blocks": [
            "scope_of_work", "schedule_a_part_b", "tech_spec_annexure_pointer",
            "esmp_appendix_i", "esmp_appendix_ii", "key_personnel",
            "design_basis_pointer", "drawings_pointer",
            "supplementary_information_51_clauses",
        ],
    },
    "section_VII": {
        "name": "General Conditions of Contract (GCC)",
        "hod_pp": (165, 197), "lps_pp": (169, 200),
        "applicable_disciplines": ["ALL"],
        "placeholders": [],
    },
    "section_VIII": {
        "name": "Particular Conditions of Contract (PCC)",
        "hod_pp": (198, 228), "lps_pp": (201, 229),
        "applicable_disciplines": ["ALL"],
        "placeholders": [
            "GCC_1_1_a_employer", "GCC_1_1_b_chief_engineer",
            "GCC_1_1_c_engineer_in_charge", "GCC_1_1_j_commencement_date",
            "GCC_1_1_k_period_months", "GCC_1_1_l_base_date",
            "GCC_1_1_cc_pmc", "GCC_1_1_gg_site_location",
            "GCC_1_1_jj_works_scope_summary", "GCC_7_1_subcontracting_pct",
            "GCC_27_3_milestone_table", "GCC_35_3_dlp_months",
            "GCC_41_payment_schedule", "GCC_44_1_seigniorage_rates",
        ],
    },
    "section_IX": {
        "name": "Contract Forms",
        "hod_pp": (229, 244), "lps_pp": (230, 246),
        "applicable_disciplines": ["ALL"],
        "placeholders": [
            "loa_amount", "pbg_amount", "advance_payment_amount",
            "successful_bidder_name", "contract_signing_date",
        ],
    },
}


def _post(rows: list[dict]) -> list[dict]:
    r = requests.post(
        f"{REST}/rest/v1/kg_nodes",
        json=rows,
        headers={**H, "Prefer": "return=representation"},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


def _delete_prior_sbd_sections() -> int:
    rows = requests.get(
        f"{REST}/rest/v1/kg_nodes",
        params={"select": "node_id", "node_type": "eq.SBDSection", "source_ref": f"eq.{SOURCE_REF}"},
        headers=H,
        timeout=30,
    ).json()
    for row in rows:
        requests.delete(
            f"{REST}/rest/v1/kg_nodes",
            params={"node_id": f"eq.{row['node_id']}"},
            headers=H,
            timeout=30,
        )
    return len(rows)


def main() -> None:
    classif = json.loads(CLASSIF_FILE.read_text())

    n_cleaned = _delete_prior_sbd_sections()
    if n_cleaned:
        print(f"  cleanup: removed {n_cleaned} prior SBDSection row(s)")

    rows_to_insert: list[dict] = []

    for section_id, meta in SECTION_META.items():
        cls_data = classif["sections"][section_id]
        classification = cls_data["classification"]
        similarity = cls_data["jaccard_similarity"]

        # Raw HOD node
        hod_text = (EXTRACTED / "hod" / f"{section_id}.txt").read_text(encoding="utf-8", errors="replace")
        rows_to_insert.append({
            "doc_id":    f"sbd_section_{section_id}_hod",
            "node_type": "SBDSection",
            "label":     f"SBDSection {section_id} (HOD): {meta['name']} (pp.{meta['hod_pp'][0]}-{meta['hod_pp'][1]})",
            "properties": {
                "section_id":             section_id,
                "name":                   meta["name"],
                "source_doc":             "hod_towers_agicl",
                "source_pdf":             "HOD_Towers_BID_DOCUMENT.pdf",
                "page_range":             list(meta["hod_pp"]),
                "n_pages":                meta["hod_pp"][1] - meta["hod_pp"][0] + 1,
                "classification":         classification,
                "similarity_to_other":    similarity,
                "applicable_disciplines": meta["applicable_disciplines"],
                "placeholders":           meta["placeholders"],
                "content_md":             hod_text,
                "content_bytes":          len(hod_text),
                "sub_blocks":             meta.get("sub_blocks", []),
                "is_canonical":           False,
            },
            "source_ref": SOURCE_REF,
        })

        # Raw LPS node
        lps_text = (EXTRACTED / "lps" / f"{section_id}.txt").read_text(encoding="utf-8", errors="replace")
        rows_to_insert.append({
            "doc_id":    f"sbd_section_{section_id}_lps",
            "node_type": "SBDSection",
            "label":     f"SBDSection {section_id} (LPS): {meta['name']} (pp.{meta['lps_pp'][0]}-{meta['lps_pp'][1]})",
            "properties": {
                "section_id":             section_id,
                "name":                   meta["name"],
                "source_doc":             "lps_zone11_adcl",
                "source_pdf":             "LPS_Zone11_ADCL_BID_DOCUMENT.pdf",
                "page_range":             list(meta["lps_pp"]),
                "n_pages":                meta["lps_pp"][1] - meta["lps_pp"][0] + 1,
                "classification":         classification,
                "similarity_to_other":    similarity,
                "applicable_disciplines": meta["applicable_disciplines"],
                "placeholders":           meta["placeholders"],
                "content_md":             lps_text,
                "content_bytes":          len(lps_text),
                "sub_blocks":             meta.get("sub_blocks", []),
                "is_canonical":           False,
            },
            "source_ref": SOURCE_REF,
        })

        # Canonical node (uses HOD content as baseline; flagged is_canonical=True)
        rows_to_insert.append({
            "doc_id":    f"sbd_section_{section_id}_canonical",
            "node_type": "SBDSection",
            "label":     f"SBDSection {section_id} (Canonical): {meta['name']}",
            "properties": {
                "section_id":             section_id,
                "name":                   meta["name"],
                "source_doc":             "canonical",
                "source_pdf":             "merged_hod_lps",
                "page_range":             [],
                "n_pages":                0,
                "classification":         classification,
                "similarity_to_other":    similarity,
                "applicable_disciplines": meta["applicable_disciplines"],
                "placeholders":           meta["placeholders"],
                "content_md":             hod_text,  # HOD as baseline (more comprehensive)
                "lps_alternate_md":       lps_text,
                "content_bytes":          len(hod_text),
                "sub_blocks":             meta.get("sub_blocks", []),
                "is_canonical":           True,
                "baseline_source":        "hod_towers_agicl",
                "alternate_source":       "lps_zone11_adcl",
            },
            "source_ref": SOURCE_REF,
        })

    # Insert in batches of 10 to keep payload reasonable
    inserted = []
    for batch_start in range(0, len(rows_to_insert), 10):
        batch = rows_to_insert[batch_start:batch_start + 10]
        inserted.extend(_post(batch))

    print(f"\n  ✓ Inserted {len(inserted)} SBDSection kg_nodes")
    by_class: dict[str, int] = {}
    by_section: dict[str, int] = {}
    for row in inserted:
        p = row["properties"]
        c = p["classification"]
        s = p["section_id"]
        by_class[c] = by_class.get(c, 0) + 1
        by_section[s] = by_section.get(s, 0) + 1

    print(f"  Per-section row counts (expect 3 each = 9 sections × 3): {by_section}")
    print(f"  Classification totals: {by_class}")


if __name__ == "__main__":
    main()
