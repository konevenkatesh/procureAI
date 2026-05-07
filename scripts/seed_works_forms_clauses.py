"""
scripts/seed_works_forms_clauses.py

One-shot seed: insert (or upsert) 13 standard AP Works tender Forms
proformas into the `clause_templates` table. These are the bidder
statements + standard bank-guarantee/contract proformas that every AP
Works tender carries — they're not project-specific, so they live in
the knowledge layer alongside the policy clauses.

Closes Gap 1 from the drafter-vs-real-JA comparison: the real JA
tender has 49 Forms; the prior knowledge layer had 17. These 13
additional forms bring coverage to ~30 — still partial but covers
the most-cited standard proformas.

Each form:
  clause_type = 'DRAFTING_CLAUSE'
  position_section = 'Volume-I/Section-5/Forms'
  applicable_tender_types = ['Works', 'EPC']
  mandatory = True
  text_english = the actual proforma text with {{name}} blanks
  parameters = list of {{name}} placeholder definitions

Run:
    python3 scripts/seed_works_forms_clauses.py

The script uses POST with `Prefer: resolution=merge-duplicates` so
it's safe to re-run; existing rows with the same clause_id are
overwritten with the latest text.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from builder.config import settings


REST = settings.supabase_rest_url
H = {
    "apikey":        settings.supabase_anon_key,
    "Authorization": f"Bearer {settings.supabase_anon_key}",
    "Content-Type":  "application/json",
    "Prefer":        "resolution=merge-duplicates,return=representation",
}


# Helper to build a parameter spec
def _p(name: str, label: str, ptype: str = "text", example: str = "") -> dict:
    return {
        "name":       name,
        "label":      label,
        "param_type": ptype,
        "formula":    None,
        "cap":        None,
        "example":    example,
    }


# ── 13 Forms proformas ────────────────────────────────────────────────

CLAUSES: list[dict] = [
    # 1 — Statement-I: Annual Financial Turnover
    {
        "clause_id":  "CLAUSE-STATEMENT-I-TURNOVER-001",
        "title":      "Statement-I — Annual Financial Turnover (last 5 years)",
        "text_english": (
            "**STATEMENT-I — ANNUAL FINANCIAL TURNOVER**\n\n"
            "Bidder Name: {{bidder_name}}  \n"
            "Tender No: {{nit_number}}, Dt: {{issue_date}}  \n"
            "Project: {{project_name}}\n\n"
            "| Financial Year | Annual Turnover (Rs. in Crore) | Audited / Provisional | Statutory Auditor's Reference |\n"
            "|---|---|---|---|\n"
            "| {{fy1_label}} | {{fy1_turnover}} | {{fy1_status}} | {{fy1_auditor_ref}} |\n"
            "| {{fy2_label}} | {{fy2_turnover}} | {{fy2_status}} | {{fy2_auditor_ref}} |\n"
            "| {{fy3_label}} | {{fy3_turnover}} | {{fy3_status}} | {{fy3_auditor_ref}} |\n"
            "| {{fy4_label}} | {{fy4_turnover}} | {{fy4_status}} | {{fy4_auditor_ref}} |\n"
            "| {{fy5_label}} | {{fy5_turnover}} | {{fy5_status}} | {{fy5_auditor_ref}} |\n\n"
            "**Average Annual Turnover (5-year):** Rs. {{avg_turnover_cr}} Crore\n\n"
            "I/We certify that the above turnover figures are extracted from the audited "
            "Profit & Loss Statements for the respective years and have been counter-"
            "signed by our Statutory Auditor / Chartered Accountant.\n\n"
            "Signature of Authorised Signatory: ____________________  \n"
            "Name: {{signatory_name}}  \n"
            "Designation: {{signatory_designation}}  \n"
            "Date: {{statement_date}}  \n"
            "Place: {{statement_place}}\n\n"
            "**Statutory Auditor / CA Counter-signature:** ____________________  \n"
            "Auditor Name: {{auditor_name}}  \n"
            "Membership No (ICAI): {{auditor_membership_no}}  \n"
            "Firm Registration No: {{auditor_firm_no}}"
        ),
        "parameters": [
            _p("bidder_name", "Bidder firm name", "text", "M/s ABC Constructions Pvt Ltd"),
            _p("fy1_label", "FY-5 label", "text", "2021-22"),
            _p("fy1_turnover", "FY-5 turnover (Rs. Cr)", "currency", "85.40"),
            _p("fy1_status", "FY-5 audited / provisional", "text", "Audited"),
            _p("fy1_auditor_ref", "FY-5 auditor reference", "text", "M/s XYZ & Co., Chartered Accountants"),
            _p("fy2_label", "FY-4 label", "text", "2022-23"),
            _p("fy2_turnover", "FY-4 turnover", "currency", "92.10"),
            _p("fy2_status", "FY-4 status", "text", "Audited"),
            _p("fy2_auditor_ref", "FY-4 auditor reference", "text", "M/s XYZ & Co."),
            _p("fy3_label", "FY-3 label", "text", "2023-24"),
            _p("fy3_turnover", "FY-3 turnover", "currency", "108.50"),
            _p("fy3_status", "FY-3 status", "text", "Audited"),
            _p("fy3_auditor_ref", "FY-3 auditor reference", "text", "M/s XYZ & Co."),
            _p("fy4_label", "FY-2 label", "text", "2024-25"),
            _p("fy4_turnover", "FY-2 turnover", "currency", "131.20"),
            _p("fy4_status", "FY-2 status", "text", "Audited"),
            _p("fy4_auditor_ref", "FY-2 auditor reference", "text", "M/s XYZ & Co."),
            _p("fy5_label", "FY-1 label", "text", "2025-26"),
            _p("fy5_turnover", "FY-1 turnover", "currency", "147.80"),
            _p("fy5_status", "FY-1 status", "text", "Provisional"),
            _p("fy5_auditor_ref", "FY-1 auditor reference", "text", "M/s XYZ & Co. (Provisional)"),
            _p("avg_turnover_cr", "5-year average (Rs. Cr)", "currency", "113.00"),
            _p("signatory_name", "Authorised signatory name", "text", "Mr. A. K. Sharma"),
            _p("signatory_designation", "Signatory designation", "text", "Director (Technical)"),
            _p("statement_date", "Statement date", "date", "2026-04-15"),
            _p("statement_place", "Statement place", "text", "Hyderabad"),
            _p("auditor_name", "Statutory auditor name", "text", "Mr. R. Krishna Murthy"),
            _p("auditor_membership_no", "ICAI membership", "text", "M/216487"),
            _p("auditor_firm_no", "Auditor firm reg", "text", "FRN: 010234S"),
        ],
        "position_order": 100,
    },

    # 2 — Statement-II: Similar Works Completed
    {
        "clause_id":  "CLAUSE-STATEMENT-II-SIMILAR-WORKS-001",
        "title":      "Statement-II — Similar Works Completed (last 10 financial years)",
        "text_english": (
            "**STATEMENT-II — DETAILS OF SIMILAR WORKS COMPLETED AS PRIME CONTRACTOR**\n\n"
            "Bidder Name: {{bidder_name}}  \n"
            "Tender No: {{nit_number}}\n\n"
            "_(Bidder shall list ALL similar civil engineering works of value not less "
            "than 50% of the Estimated Value executed during the LAST TEN financial "
            "years. Each entry shall be supported by a completion certificate issued "
            "by the Executive Engineer or equivalent authority, counter-signed by the "
            "Superintending Engineer or equivalent — per AP-GO-061 / AP-GO-062.)_\n\n"
            "| Sl | Name of Work | Client / Employer | ECV (Rs. Cr) | Award Date | Completion Date | Compliance % | Certificate Reference |\n"
            "|---|---|---|---|---|---|---|---|\n"
            "| 1 | {{work1_name}} | {{work1_client}} | {{work1_ecv}} | {{work1_award_date}} | {{work1_completion_date}} | {{work1_compliance_pct}} | {{work1_cert_ref}} |\n"
            "| 2 | {{work2_name}} | {{work2_client}} | {{work2_ecv}} | {{work2_award_date}} | {{work2_completion_date}} | {{work2_compliance_pct}} | {{work2_cert_ref}} |\n"
            "| 3 | {{work3_name}} | {{work3_client}} | {{work3_ecv}} | {{work3_award_date}} | {{work3_completion_date}} | {{work3_compliance_pct}} | {{work3_cert_ref}} |\n\n"
            "_(Add additional rows below as required. Photocopies of completion "
            "certificates and counter-signatures shall be enclosed.)_\n\n"
            "**Maximum value of similar works executed in any one financial year (Component A):** "
            "Rs. {{max_yearly_works_cr}} Crore — relevant for the Available Bid Capacity formula "
            "ABC = (A × N × 2) − B per AP-GO-062.\n\n"
            "I/We certify that the above information is true and complete to the best of "
            "my/our knowledge.\n\n"
            "Signature: ____________________  \n"
            "Name: {{signatory_name}}  \n"
            "Designation: {{signatory_designation}}  \n"
            "Date: {{statement_date}}"
        ),
        "parameters": [
            _p("bidder_name", "Bidder firm name", "text", "M/s ABC Constructions Pvt Ltd"),
            _p("work1_name", "Work-1 name", "text", "Construction of District Court Complex, Vijayawada"),
            _p("work1_client", "Work-1 client", "text", "AP Judicial Academy / APCRDA"),
            _p("work1_ecv", "Work-1 ECV (Cr)", "currency", "85.50"),
            _p("work1_award_date", "Work-1 award date", "date", "2022-03-15"),
            _p("work1_completion_date", "Work-1 completion date", "date", "2024-09-30"),
            _p("work1_compliance_pct", "Work-1 compliance", "text", "100%"),
            _p("work1_cert_ref", "Work-1 certificate ref", "text", "EE/Vij/2024/Comp-145"),
            _p("work2_name", "Work-2 name", "text", "Construction of MLA Quarters Block-A, Amaravati"),
            _p("work2_client", "Work-2 client", "text", "APCRDA"),
            _p("work2_ecv", "Work-2 ECV (Cr)", "currency", "112.40"),
            _p("work2_award_date", "Work-2 award date", "date", "2021-08-20"),
            _p("work2_completion_date", "Work-2 completion date", "date", "2023-12-15"),
            _p("work2_compliance_pct", "Work-2 compliance", "text", "100%"),
            _p("work2_cert_ref", "Work-2 certificate ref", "text", "EE/AMR/2023/Comp-078"),
            _p("work3_name", "Work-3 name", "text", "Hospital Building (G+5), Tirupati"),
            _p("work3_client", "Work-3 client", "text", "APMSIDC"),
            _p("work3_ecv", "Work-3 ECV (Cr)", "currency", "67.80"),
            _p("work3_award_date", "Work-3 award date", "date", "2020-06-10"),
            _p("work3_completion_date", "Work-3 completion date", "date", "2022-08-22"),
            _p("work3_compliance_pct", "Work-3 compliance", "text", "100%"),
            _p("work3_cert_ref", "Work-3 certificate ref", "text", "EE/TPT/2022/Comp-201"),
            _p("max_yearly_works_cr", "Max yearly works value", "currency", "112.40"),
            _p("signatory_name", "Authorised signatory name", "text", "Mr. A. K. Sharma"),
            _p("signatory_designation", "Signatory designation", "text", "Director (Technical)"),
            _p("statement_date", "Statement date", "date", "2026-04-15"),
        ],
        "position_order": 101,
    },

    # 3 — Statement-III: Equipment List
    {
        "clause_id":  "CLAUSE-STATEMENT-III-EQUIPMENT-001",
        "title":      "Statement-III — Critical Equipment Inventory",
        "text_english": (
            "**STATEMENT-III — CRITICAL EQUIPMENT INVENTORY (Owned / Leased / Procurable)**\n\n"
            "Bidder Name: {{bidder_name}}  \n"
            "Tender No: {{nit_number}}\n\n"
            "_(Bidder shall list all critical plant and equipment proposed for deployment "
            "on this contract. Owned equipment shall be supported by purchase invoices; "
            "leased / hired equipment shall be supported by lease agreements. Equipment "
            "to be procured against mobilisation advance shall be marked 'Procurable'. "
            "Per AP-GO-062, availability of key equipment is a qualification requirement.)_\n\n"
            "| Sl | Equipment Type / Description | Owned / Leased / Procurable | Number | Capacity / Specification | Year of Manufacture | Reference |\n"
            "|---|---|---|---|---|---|---|\n"
            "| 1 | {{eq1_type}} | {{eq1_status}} | {{eq1_number}} | {{eq1_capacity}} | {{eq1_year}} | {{eq1_ref}} |\n"
            "| 2 | {{eq2_type}} | {{eq2_status}} | {{eq2_number}} | {{eq2_capacity}} | {{eq2_year}} | {{eq2_ref}} |\n"
            "| 3 | {{eq3_type}} | {{eq3_status}} | {{eq3_number}} | {{eq3_capacity}} | {{eq3_year}} | {{eq3_ref}} |\n"
            "| 4 | {{eq4_type}} | {{eq4_status}} | {{eq4_number}} | {{eq4_capacity}} | {{eq4_year}} | {{eq4_ref}} |\n"
            "| 5 | {{eq5_type}} | {{eq5_status}} | {{eq5_number}} | {{eq5_capacity}} | {{eq5_year}} | {{eq5_ref}} |\n\n"
            "_(Add rows as required.)_\n\n"
            "Signature: ____________________  \n"
            "Name: {{signatory_name}}  \n"
            "Date: {{statement_date}}"
        ),
        "parameters": [
            _p("bidder_name", "Bidder firm name", "text", "M/s ABC Constructions Pvt Ltd"),
            _p("eq1_type", "Equipment-1 type", "text", "Tower Crane"),
            _p("eq1_status", "Equipment-1 status", "text", "Owned"),
            _p("eq1_number", "Equipment-1 count", "text", "2"),
            _p("eq1_capacity", "Equipment-1 capacity", "text", "8 ton, 50 m height"),
            _p("eq1_year", "Equipment-1 year", "text", "2023"),
            _p("eq1_ref", "Equipment-1 reference", "text", "Invoice INV/2023/TC-045"),
            _p("eq2_type", "Equipment-2 type", "text", "Concrete Batch Mix Plant"),
            _p("eq2_status", "Equipment-2 status", "text", "Owned"),
            _p("eq2_number", "Equipment-2 count", "text", "1"),
            _p("eq2_capacity", "Equipment-2 capacity", "text", "60 cum/hr"),
            _p("eq2_year", "Equipment-2 year", "text", "2022"),
            _p("eq2_ref", "Equipment-2 reference", "text", "Invoice INV/2022/CBP-012"),
            _p("eq3_type", "Equipment-3 type", "text", "Concrete Pumps"),
            _p("eq3_status", "Equipment-3 status", "text", "Owned"),
            _p("eq3_number", "Equipment-3 count", "text", "3"),
            _p("eq3_capacity", "Equipment-3 capacity", "text", "Boom 36-42 m"),
            _p("eq3_year", "Equipment-3 year", "text", "2023"),
            _p("eq3_ref", "Equipment-3 reference", "text", "Schwing Stetter S-42"),
            _p("eq4_type", "Equipment-4 type", "text", "Hydraulic Excavators"),
            _p("eq4_status", "Equipment-4 status", "text", "Leased"),
            _p("eq4_number", "Equipment-4 count", "text", "2"),
            _p("eq4_capacity", "Equipment-4 capacity", "text", "20 ton class"),
            _p("eq4_year", "Equipment-4 year", "text", "2024"),
            _p("eq4_ref", "Equipment-4 reference", "text", "L&T Komatsu PC210"),
            _p("eq5_type", "Equipment-5 type", "text", "Diesel Generator Sets"),
            _p("eq5_status", "Equipment-5 status", "text", "Owned"),
            _p("eq5_number", "Equipment-5 count", "text", "2"),
            _p("eq5_capacity", "Equipment-5 capacity", "text", "250 kVA"),
            _p("eq5_year", "Equipment-5 year", "text", "2022"),
            _p("eq5_ref", "Equipment-5 reference", "text", "Cummins make"),
            _p("signatory_name", "Authorised signatory name", "text", "Mr. A. K. Sharma"),
            _p("statement_date", "Statement date", "date", "2026-04-15"),
        ],
        "position_order": 102,
    },

    # 4 — Statement-IV: Key Personnel
    {
        "clause_id":  "CLAUSE-STATEMENT-IV-PERSONNEL-001",
        "title":      "Statement-IV — Key Personnel for Site Deployment",
        "text_english": (
            "**STATEMENT-IV — KEY PERSONNEL PROPOSED FOR DEPLOYMENT**\n\n"
            "Bidder Name: {{bidder_name}}  \n"
            "Tender No: {{nit_number}}\n\n"
            "_(Per AP-GO-062, each bidder shall demonstrate availability of key "
            "personnel with adequate experience for the work. Photocopies of degree "
            "certificates, experience certificates, and EPF/payroll records shall be "
            "enclosed.)_\n\n"
            "| Sl | Role | Name | Qualification | Years of Experience | Membership / Registration |\n"
            "|---|---|---|---|---|---|\n"
            "| 1 | Project Manager / Project-in-Charge | {{p1_name}} | {{p1_qualification}} | {{p1_experience}} | {{p1_membership}} |\n"
            "| 2 | Site / Construction Engineer | {{p2_name}} | {{p2_qualification}} | {{p2_experience}} | {{p2_membership}} |\n"
            "| 3 | Quality Assurance Engineer | {{p3_name}} | {{p3_qualification}} | {{p3_experience}} | {{p3_membership}} |\n"
            "| 4 | Safety Officer | {{p4_name}} | {{p4_qualification}} | {{p4_experience}} | {{p4_membership}} |\n"
            "| 5 | MEP / Electrical Engineer | {{p5_name}} | {{p5_qualification}} | {{p5_experience}} | {{p5_membership}} |\n"
            "| 6 | Surveyor / Total-Station Operator | {{p6_name}} | {{p6_qualification}} | {{p6_experience}} | {{p6_membership}} |\n\n"
            "Signature: ____________________  \n"
            "Name: {{signatory_name}}  \n"
            "Date: {{statement_date}}"
        ),
        "parameters": [
            _p("bidder_name", "Bidder firm name", "text", "M/s ABC Constructions Pvt Ltd"),
            _p("p1_name", "Project Manager name", "text", "Mr. R. Subramanyam"),
            _p("p1_qualification", "Project Manager qualification", "text", "B.E. (Civil), M.Tech (Construction Mgmt)"),
            _p("p1_experience", "Project Manager experience", "text", "22 years"),
            _p("p1_membership", "Project Manager registration", "text", "AMIE/F-89234"),
            _p("p2_name", "Site Engineer name", "text", "Mr. K. Venkateswarlu"),
            _p("p2_qualification", "Site Engineer qualification", "text", "B.Tech (Civil)"),
            _p("p2_experience", "Site Engineer experience", "text", "12 years"),
            _p("p2_membership", "Site Engineer registration", "text", "ICCE/M-45612"),
            _p("p3_name", "QA Engineer name", "text", "Mr. P. Anand"),
            _p("p3_qualification", "QA Engineer qualification", "text", "B.E. (Civil), NABL Cert"),
            _p("p3_experience", "QA Engineer experience", "text", "10 years"),
            _p("p3_membership", "QA Engineer registration", "text", "ISI Quality Auditor 2024"),
            _p("p4_name", "Safety Officer name", "text", "Mr. T. Suresh"),
            _p("p4_qualification", "Safety Officer qualification", "text", "Diploma + ADIS Safety"),
            _p("p4_experience", "Safety Officer experience", "text", "15 years"),
            _p("p4_membership", "Safety Officer registration", "text", "RLI ADIS-2017-0892"),
            _p("p5_name", "MEP Engineer name", "text", "Mr. L. Ramesh"),
            _p("p5_qualification", "MEP Engineer qualification", "text", "B.E. (Electrical), Grade-A licence"),
            _p("p5_experience", "MEP Engineer experience", "text", "14 years"),
            _p("p5_membership", "MEP Engineer registration", "text", "AP Electrical Inspectorate Lic A-12387"),
            _p("p6_name", "Surveyor name", "text", "Mr. V. Krishna"),
            _p("p6_qualification", "Surveyor qualification", "text", "Diploma (Civil)"),
            _p("p6_experience", "Surveyor experience", "text", "8 years"),
            _p("p6_membership", "Surveyor registration", "text", "Total Station + GPS certified"),
            _p("signatory_name", "Authorised signatory name", "text", "Mr. A. K. Sharma"),
            _p("statement_date", "Statement date", "date", "2026-04-15"),
        ],
        "position_order": 103,
    },

    # 5 — Statement-V: Ongoing Commitments
    {
        "clause_id":  "CLAUSE-STATEMENT-V-COMMITMENTS-001",
        "title":      "Statement-V — Ongoing Commitments and Existing Works (B-factor)",
        "text_english": (
            "**STATEMENT-V — VALUE OF EXISTING COMMITMENTS AND ON-GOING WORKS**\n\n"
            "Bidder Name: {{bidder_name}}  \n"
            "Tender No: {{nit_number}}\n\n"
            "_(Per AP-GO-062 / AP-GO-064, the value of all existing commitments and "
            "on-going works to be completed during the period of completion of the "
            "works for which this Bid is invited shall be certified by the Engineer-in-"
            "Charge of the relevant Government department / undertaking, of rank not "
            "below Executive Engineer, counter-signed by the Superintending Engineer "
            "or equivalent. The B-factor in the Available Bid Capacity formula "
            "ABC = (A × N × 2) − B is derived from this statement.)_\n\n"
            "| Sl | Project Name | Client / Employer | ECV (Rs. Cr) | Date of Commencement | Stipulated Completion Date | Balance Work (%) | Balance Value (Rs. Cr) | Engineer's Reference |\n"
            "|---|---|---|---|---|---|---|---|---|\n"
            "| 1 | {{onw1_name}} | {{onw1_client}} | {{onw1_ecv}} | {{onw1_start}} | {{onw1_end}} | {{onw1_balance_pct}} | {{onw1_balance_cr}} | {{onw1_eng_ref}} |\n"
            "| 2 | {{onw2_name}} | {{onw2_client}} | {{onw2_ecv}} | {{onw2_start}} | {{onw2_end}} | {{onw2_balance_pct}} | {{onw2_balance_cr}} | {{onw2_eng_ref}} |\n"
            "| 3 | {{onw3_name}} | {{onw3_client}} | {{onw3_ecv}} | {{onw3_start}} | {{onw3_end}} | {{onw3_balance_pct}} | {{onw3_balance_cr}} | {{onw3_eng_ref}} |\n\n"
            "**Total balance work value due during the next {{n_months}} months "
            "(B-factor):** Rs. {{b_factor_cr}} Crore.\n\n"
            "Signature: ____________________  \n"
            "Name: {{signatory_name}}  \n"
            "Date: {{statement_date}}\n\n"
            "**Engineer-in-Charge Counter-signature** (rank not below Executive Engineer):  \n"
            "Name: {{eng_in_charge_name}}  \n"
            "Designation: {{eng_in_charge_designation}}  \n"
            "Department: {{eng_in_charge_department}}  \n"
            "Signature: ____________________  Date: {{eng_in_charge_date}}\n\n"
            "**Superintending Engineer Counter-signature**:  \n"
            "Name: {{se_name}}  \n"
            "Signature: ____________________  Date: {{se_date}}"
        ),
        "parameters": [
            _p("bidder_name", "Bidder firm name", "text", "M/s ABC Constructions Pvt Ltd"),
            _p("onw1_name", "Ongoing-1 name", "text", "Construction of MLC Quarters Block-B, Amaravati"),
            _p("onw1_client", "Ongoing-1 client", "text", "APCRDA"),
            _p("onw1_ecv", "Ongoing-1 ECV (Cr)", "currency", "98.50"),
            _p("onw1_start", "Ongoing-1 start", "date", "2025-01-15"),
            _p("onw1_end", "Ongoing-1 end", "date", "2027-01-14"),
            _p("onw1_balance_pct", "Ongoing-1 balance %", "text", "55%"),
            _p("onw1_balance_cr", "Ongoing-1 balance (Cr)", "currency", "54.18"),
            _p("onw1_eng_ref", "Ongoing-1 engineer ref", "text", "EE-AMR/2026/Cert-12"),
            _p("onw2_name", "Ongoing-2 name", "text", "All India Services Officers Quarters, Nelapadu"),
            _p("onw2_client", "Ongoing-2 client", "text", "APCRDA"),
            _p("onw2_ecv", "Ongoing-2 ECV (Cr)", "currency", "76.20"),
            _p("onw2_start", "Ongoing-2 start", "date", "2024-09-01"),
            _p("onw2_end", "Ongoing-2 end", "date", "2026-08-31"),
            _p("onw2_balance_pct", "Ongoing-2 balance %", "text", "30%"),
            _p("onw2_balance_cr", "Ongoing-2 balance (Cr)", "currency", "22.86"),
            _p("onw2_eng_ref", "Ongoing-2 engineer ref", "text", "EE-NLP/2026/Cert-08"),
            _p("onw3_name", "Ongoing-3 name", "text", "—"),
            _p("onw3_client", "Ongoing-3 client", "text", "—"),
            _p("onw3_ecv", "Ongoing-3 ECV", "currency", "—"),
            _p("onw3_start", "Ongoing-3 start", "date", "—"),
            _p("onw3_end", "Ongoing-3 end", "date", "—"),
            _p("onw3_balance_pct", "Ongoing-3 balance %", "text", "—"),
            _p("onw3_balance_cr", "Ongoing-3 balance", "currency", "—"),
            _p("onw3_eng_ref", "Ongoing-3 engineer ref", "text", "—"),
            _p("n_months", "Period of completion (months)", "text", "24"),
            _p("b_factor_cr", "Total B-factor (Cr)", "currency", "77.04"),
            _p("signatory_name", "Authorised signatory name", "text", "Mr. A. K. Sharma"),
            _p("statement_date", "Statement date", "date", "2026-04-15"),
            _p("eng_in_charge_name", "Engineer-in-Charge name", "text", "Sri P. Ramana Reddy"),
            _p("eng_in_charge_designation", "EIC designation", "text", "Executive Engineer (B&R)"),
            _p("eng_in_charge_department", "EIC department", "text", "APCRDA"),
            _p("eng_in_charge_date", "EIC counter-sign date", "date", "2026-04-12"),
            _p("se_name", "SE counter-signing name", "text", "Sri B. Veera Raju"),
            _p("se_date", "SE counter-sign date", "date", "2026-04-13"),
        ],
        "position_order": 104,
    },

    # 6 — Statement-VI: Liquid Assets
    {
        "clause_id":  "CLAUSE-STATEMENT-VI-LIQUID-ASSETS-001",
        "title":      "Statement-VI — Liquid Assets and Credit Lines",
        "text_english": (
            "**STATEMENT-VI — LIQUID ASSETS / CREDIT FACILITIES / SOLVENCY**\n\n"
            "Bidder Name: {{bidder_name}}  \n"
            "Tender No: {{nit_number}}\n\n"
            "_(Per AP-GO-062, the bidder shall demonstrate liquid assets / credit "
            "facilities / solvency certificates equivalent to the estimated cash flow "
            "for 3 months in the peak construction period.)_\n\n"
            "| Source | Amount (Rs. in Crore) | Issuing Bank / Authority | Reference / Certificate No | Validity |\n"
            "|---|---|---|---|---|\n"
            "| Cash in current account | {{cash_amount}} | {{cash_bank}} | {{cash_ref}} | {{cash_validity}} |\n"
            "| Fixed Deposit Receipts | {{fd_amount}} | {{fd_bank}} | {{fd_ref}} | {{fd_validity}} |\n"
            "| Sanctioned Credit Lines (CC / OD) | {{cl_amount}} | {{cl_bank}} | {{cl_ref}} | {{cl_validity}} |\n"
            "| Solvency Certificate (Bank) | {{sc_bank_amount}} | {{sc_bank_name}} | {{sc_bank_ref}} | {{sc_bank_validity}} |\n"
            "| Solvency Certificate (Tahsildar / Mandal Revenue Officer) | {{sc_tah_amount}} | {{sc_tah_authority}} | {{sc_tah_ref}} | {{sc_tah_validity}} |\n"
            "| **Total Available Liquidity** | **{{total_liquidity_cr}}** | — | — | — |\n\n"
            "**Required Liquidity** (per AP-GO-062): equivalent to cash flow for "
            "{{required_months}} months in peak construction period = "
            "Rs. {{required_liquidity_cr}} Crore. The bidder declares total available "
            "liquidity exceeds this requirement.\n\n"
            "Signature: ____________________  \n"
            "Name: {{signatory_name}}  \n"
            "Date: {{statement_date}}\n\n"
            "_All certificates listed shall be enclosed in original or self-attested "
            "photocopy form. Solvency certificates shall be valid for not less than "
            "1 year from date of issue per AP-GO-089._"
        ),
        "parameters": [
            _p("bidder_name", "Bidder firm name", "text", "M/s ABC Constructions Pvt Ltd"),
            _p("cash_amount", "Cash amount (Cr)", "currency", "8.50"),
            _p("cash_bank", "Cash holding bank", "text", "Union Bank of India, Hyderabad"),
            _p("cash_ref", "Cash reference", "text", "CA No. 0343-1010-0081-181"),
            _p("cash_validity", "Cash validity", "text", "Current"),
            _p("fd_amount", "FD amount (Cr)", "currency", "12.40"),
            _p("fd_bank", "FD bank", "text", "SBI, Vijayawada"),
            _p("fd_ref", "FD reference", "text", "FDR Nos. 4521 / 4522 / 4523"),
            _p("fd_validity", "FD validity", "text", "Maturity 2026-09-30"),
            _p("cl_amount", "Credit line amount (Cr)", "currency", "25.00"),
            _p("cl_bank", "Credit line bank", "text", "Bank of Baroda, Hyderabad"),
            _p("cl_ref", "Credit line ref", "text", "Sanction Letter SL/2025/CC/0048"),
            _p("cl_validity", "Credit line validity", "text", "Valid till 2026-12-31"),
            _p("sc_bank_amount", "Bank solvency amount (Cr)", "currency", "15.00"),
            _p("sc_bank_name", "Bank solvency issuing", "text", "Union Bank of India, Hyderabad"),
            _p("sc_bank_ref", "Bank solvency ref", "text", "Cert No. UBI/HYD/SOLV/2026/0234"),
            _p("sc_bank_validity", "Bank solvency validity", "text", "Issued 2026-03-15; valid 1 year"),
            _p("sc_tah_amount", "Tahsildar solvency amount (Cr)", "currency", "10.00"),
            _p("sc_tah_authority", "Tahsildar authority", "text", "Tahsildar, Hyderabad (East) Mandal"),
            _p("sc_tah_ref", "Tahsildar solvency ref", "text", "Cert No. T/HYD-E/SOLV/2026/0048"),
            _p("sc_tah_validity", "Tahsildar solvency validity", "text", "Issued 2026-03-20; valid 1 year"),
            _p("total_liquidity_cr", "Total liquidity (Cr)", "currency", "70.90"),
            _p("required_months", "Required liquidity months", "text", "3"),
            _p("required_liquidity_cr", "Required liquidity (Cr)", "currency", "31.40"),
            _p("signatory_name", "Authorised signatory name", "text", "Mr. A. K. Sharma"),
            _p("statement_date", "Statement date", "date", "2026-04-15"),
        ],
        "position_order": 105,
    },

    # 7 — PBG Proforma (Bank Guarantee)
    {
        "clause_id":  "CLAUSE-PBG-PROFORMA-001",
        "title":      "Performance Bank Guarantee — Standard Proforma (10% of Contract Value)",
        "text_english": (
            "**PERFORMANCE BANK GUARANTEE (PROFORMA)**\n\n"
            "_(To be furnished on the letterhead of a Nationalised / Scheduled Commercial "
            "Bank, on non-judicial stamp paper of value as applicable, in the format below. "
            "Acceptable forms per AP-GO-175: Bank Guarantee, Insurance Surety Bond, or "
            "Electronic Bank Guarantee.)_\n\n"
            "**To,**  \n"
            "The {{employer_designation}}  \n"
            "{{employer_name}}  \n"
            "{{employer_address}}\n\n"
            "**Bank Guarantee No: {{bg_number}}**  \n"
            "**Date: {{bg_issue_date}}**\n\n"
            "WHEREAS M/s {{contractor_name}}, having its registered office at "
            "{{contractor_address}} (hereinafter called \"the Contractor\") has been "
            "awarded the contract for **{{project_name}}** under Letter of Acceptance "
            "(LoA) No. {{loa_number}} dated {{loa_date}} for a contract value of "
            "**Rs. {{contract_value_cr}} Crore (Rs. {{contract_value_rupees}} only)**;\n\n"
            "AND WHEREAS the Contractor is required to furnish to the Employer a "
            "Performance Security in the form of a Bank Guarantee equal to "
            "**{{pbg_pct}}% of the contract value, viz. Rs. {{pbg_amount_cr}} Crore "
            "(Rs. {{pbg_amount_rupees}} only)** for the due performance of the contract;\n\n"
            "NOW THEREFORE, in consideration of the Employer agreeing to enter into the "
            "said contract with the Contractor, **{{bank_name}} (\"the Bank\"), through "
            "its branch at {{bank_branch}},** unconditionally and irrevocably undertakes "
            "to pay to the Employer, on first written demand without demur and without "
            "the Employer assigning any reason whatsoever, any sum or sums up to a "
            "maximum of **Rs. {{pbg_amount_cr}} Crore (Rs. {{pbg_amount_rupees}}).** "
            "The Bank further agrees that this Guarantee shall be valid until **60 days "
            "after the expiry of the Defects Liability Period, i.e. until "
            "{{bg_validity_date}}**.\n\n"
            "Notwithstanding anything to the contrary, this Bank Guarantee shall stand "
            "discharged only upon the Employer issuing a written certificate that the "
            "Contractor has duly performed all his obligations under the contract.\n\n"
            "For {{bank_name}},  \n"
            "Authorised Signatory: ____________________  \n"
            "Name: {{bg_signatory_name}}  \n"
            "Designation: {{bg_signatory_designation}}  \n"
            "Bank Seal & Stamp\n\n"
            "_Per AP-GO-175 (the Performance Security cap reduced to 5% in March 2024 "
            "and restored to 10% as default per AP-State framework), the issuing bank "
            "shall be a Nationalised Bank, Scheduled Bank, or Public Sector Bank in "
            "India. The Guarantee shall be unconditional and irrevocable._"
        ),
        "parameters": [
            _p("employer_designation", "Employer designation", "text", "Managing Director, APCRDA"),
            _p("employer_name", "Employer organisation", "text", "Andhra Pradesh Capital Region Development Authority"),
            _p("employer_address", "Employer address", "text", "APCRDA Project Office, Rayapudi, Amaravati 522237"),
            _p("bg_number", "Bank Guarantee number", "text", "UBI/HYD/PBG/2026/0145"),
            _p("bg_issue_date", "Bank Guarantee issue date", "date", "2026-06-01"),
            _p("contractor_name", "Contractor name", "text", "ABC Constructions Pvt Ltd"),
            _p("contractor_address", "Contractor address", "text", "Plot 12, Banjara Hills, Hyderabad 500034"),
            _p("project_name", "Project name", "text", "Construction of Andhra Pradesh Judicial Academy"),
            _p("loa_number", "LoA number", "text", "APCRDA/LoA/2026/045"),
            _p("loa_date", "LoA date", "date", "2026-05-25"),
            _p("contract_value_cr", "Contract value (Cr)", "currency", "125.50"),
            _p("contract_value_rupees", "Contract value (Indian fmt)", "text", "1,25,50,00,000.00"),
            _p("pbg_pct", "PBG percentage", "text", "10"),
            _p("pbg_amount_cr", "PBG amount (Cr)", "currency", "12.55"),
            _p("pbg_amount_rupees", "PBG amount (Indian fmt)", "text", "12,55,00,000.00"),
            _p("bank_name", "Issuing bank name", "text", "Union Bank of India"),
            _p("bank_branch", "Issuing bank branch", "text", "Banjara Hills Branch, Hyderabad"),
            _p("bg_validity_date", "BG validity end date", "date", "2031-06-01"),
            _p("bg_signatory_name", "BG signatory name", "text", "Mr. S. K. Rao"),
            _p("bg_signatory_designation", "BG signatory designation", "text", "Branch Manager"),
        ],
        "position_order": 106,
    },

    # 8 — Advance Payment Guarantee
    {
        "clause_id":  "CLAUSE-APG-PROFORMA-001",
        "title":      "Advance Payment Guarantee (APG) — Standard Bank Guarantee Proforma",
        "text_english": (
            "**ADVANCE PAYMENT GUARANTEE (PROFORMA)**\n\n"
            "_(To be furnished by a Nationalised / Scheduled Bank where the Contractor "
            "draws an Advance Payment under the contract. The Guarantee shall be "
            "auto-reducing in proportion to the recovery of advance from running "
            "bills.)_\n\n"
            "**To,**  \n"
            "The {{employer_designation}}  \n"
            "{{employer_name}}  \n"
            "{{employer_address}}\n\n"
            "**Bank Guarantee No: {{apg_number}}**  \n"
            "**Date: {{apg_issue_date}}**\n\n"
            "WHEREAS M/s {{contractor_name}} (\"the Contractor\") has been awarded the "
            "contract for **{{project_name}}** under LoA No. {{loa_number}} dated "
            "{{loa_date}} for a contract value of Rs. {{contract_value_cr}} Crore;\n\n"
            "AND WHEREAS in terms of the said contract, the Contractor is entitled to "
            "draw an **Advance Payment of Rs. {{advance_amount_cr}} Crore "
            "(Rs. {{advance_amount_rupees}} only)** — being **{{advance_pct}}%** of the "
            "contract value — against an unconditional, irrevocable Bank Guarantee for "
            "an equivalent amount;\n\n"
            "NOW THEREFORE, **{{bank_name}}** through its branch at **{{bank_branch}}** "
            "unconditionally and irrevocably undertakes to pay to the Employer, on first "
            "written demand without demur and without the Employer assigning any reason, "
            "any sum or sums up to a maximum of **Rs. {{advance_amount_cr}} Crore.**\n\n"
            "This Guarantee is auto-reducing in proportion to the recovery of advance "
            "made from running bills as per the contract conditions. The Guarantee shall "
            "be valid until **{{apg_validity_date}}** — being the date by which the "
            "advance shall be fully recovered from running bills, plus a buffer period "
            "of {{apg_buffer_days}} days.\n\n"
            "For {{bank_name}},  \n"
            "Authorised Signatory: ____________________  \n"
            "Name: {{apg_signatory_name}}  \n"
            "Bank Seal & Stamp"
        ),
        "parameters": [
            _p("employer_designation", "Employer designation", "text", "Managing Director, APCRDA"),
            _p("employer_name", "Employer organisation", "text", "APCRDA"),
            _p("employer_address", "Employer address", "text", "APCRDA Project Office, Rayapudi, Amaravati"),
            _p("apg_number", "APG Guarantee number", "text", "UBI/HYD/APG/2026/0048"),
            _p("apg_issue_date", "APG issue date", "date", "2026-06-15"),
            _p("contractor_name", "Contractor name", "text", "ABC Constructions Pvt Ltd"),
            _p("project_name", "Project name", "text", "Construction of Andhra Pradesh Judicial Academy"),
            _p("loa_number", "LoA number", "text", "APCRDA/LoA/2026/045"),
            _p("loa_date", "LoA date", "date", "2026-05-25"),
            _p("contract_value_cr", "Contract value (Cr)", "currency", "125.50"),
            _p("advance_amount_cr", "Advance amount (Cr)", "currency", "12.55"),
            _p("advance_amount_rupees", "Advance (Indian fmt)", "text", "12,55,00,000.00"),
            _p("advance_pct", "Advance percentage", "text", "10"),
            _p("bank_name", "Issuing bank", "text", "Union Bank of India"),
            _p("bank_branch", "Bank branch", "text", "Banjara Hills, Hyderabad"),
            _p("apg_validity_date", "APG validity end", "date", "2027-09-30"),
            _p("apg_buffer_days", "APG buffer days", "text", "60"),
            _p("apg_signatory_name", "APG signatory name", "text", "Mr. S. K. Rao"),
        ],
        "position_order": 107,
    },

    # 9 — Letter of Acceptance form
    {
        "clause_id":  "CLAUSE-LOA-FORM-001",
        "title":      "Letter of Acceptance (LoA) — Standard Form",
        "text_english": (
            "**LETTER OF ACCEPTANCE (LoA) — STANDARD FORM**\n\n"
            "_(Issued by the Employer to the Successful Bidder per ITB §41. The Successful "
            "Bidder shall sign and return the Contract Agreement within 14 days of "
            "receipt of this LoA, failing which the bid registration shall be suspended "
            "for one year and the Bid Security forfeited per G.O.Ms.No.259, T,R&B "
            "(Roads-V) Dept., dt.6.9.2008.)_\n\n"
            "**Letter No: {{loa_number}}**  \n"
            "**Date: {{loa_date}}**\n\n"
            "**To,**  \n"
            "M/s {{contractor_name}}  \n"
            "{{contractor_address}}\n\n"
            "**Sub: Award of Contract for {{project_name}} — Letter of Acceptance**\n\n"
            "**Ref:**  \n"
            "(i) NIT No. {{nit_number}}, Dt: {{nit_date}}  \n"
            "(ii) Your Bid dated {{bid_date}}  \n"
            "(iii) Technical evaluation by Tender Committee on {{tech_eval_date}}  \n"
            "(iv) Financial evaluation by Tender Committee on {{fin_eval_date}}\n\n"
            "Sir,\n\n"
            "1. With reference to the above, the {{employer_designation}}, "
            "{{employer_name}}, is pleased to ACCEPT your bid for the above-named work "
            "for a contract value of **Rs. {{contract_value_cr}} Crore "
            "(Rs. {{contract_value_rupees}} only)** at a percentage of "
            "**{{premium_or_discount}}** with respect to the Estimated Contract Value "
            "(ECV) of Rs. {{ecv_cr}} Crore, on the terms and conditions of the bidding "
            "document.\n\n"
            "2. You are required to:  \n"
            "(a) Furnish a Performance Bank Guarantee for **Rs. {{pbg_amount_cr}} Crore "
            "({{pbg_pct}}% of contract value)** valid until 60 days after the expiry of "
            "the Defects Liability Period — within {{pbg_furnishing_days}} days from "
            "the date of this LoA;  \n"
            "(b) Sign and return the Contract Agreement within **14 (fourteen) days** "
            "from the date of receipt of this LoA;  \n"
            "(c) Mobilise to the Site within {{mobilisation_days}} days of signing the "
            "Contract Agreement.\n\n"
            "3. **Failure to submit the PBG OR sign the Contract Agreement within the "
            "stipulated time** shall constitute sufficient grounds for the annulment of "
            "this Award and forfeiture of the Bid Security per ITB §42.2.\n\n"
            "Yours faithfully,  \n"
            "{{employer_signatory_name}}  \n"
            "{{employer_designation}}  \n"
            "{{employer_name}}  \n\n"
            "_(Office Seal)_"
        ),
        "parameters": [
            _p("loa_number", "LoA number", "text", "APCRDA/LoA/2026/045"),
            _p("loa_date", "LoA date", "date", "2026-05-25"),
            _p("contractor_name", "Contractor name", "text", "ABC Constructions Pvt Ltd"),
            _p("contractor_address", "Contractor address", "text", "Plot 12, Banjara Hills, Hyderabad 500034"),
            _p("project_name", "Project name", "text", "Construction of Andhra Pradesh Judicial Academy"),
            _p("nit_number", "NIT number", "text", "100/PROC/APCRDA/1/2026"),
            _p("nit_date", "NIT date", "date", "2026-04-15"),
            _p("bid_date", "Bid date", "date", "2026-05-08"),
            _p("tech_eval_date", "Technical evaluation date", "date", "2026-05-08"),
            _p("fin_eval_date", "Financial evaluation date", "date", "2026-05-11"),
            _p("employer_designation", "Employer designation", "text", "Managing Director"),
            _p("employer_name", "Employer organisation", "text", "Andhra Pradesh Capital Region Development Authority (APCRDA)"),
            _p("contract_value_cr", "Contract value (Cr)", "currency", "125.50"),
            _p("contract_value_rupees", "Contract value (Indian)", "text", "1,25,50,00,000.00"),
            _p("premium_or_discount", "Premium / discount %", "text", "0.00% (par)"),
            _p("ecv_cr", "ECV (Cr)", "currency", "125.50"),
            _p("pbg_amount_cr", "PBG amount (Cr)", "currency", "12.55"),
            _p("pbg_pct", "PBG percentage", "text", "10"),
            _p("pbg_furnishing_days", "PBG submission days", "text", "14"),
            _p("mobilisation_days", "Mobilisation days", "text", "21"),
            _p("employer_signatory_name", "Employer signatory", "text", "Sri P. Ramana Reddy, IAS"),
        ],
        "position_order": 108,
    },

    # 10 — Contract Agreement form
    {
        "clause_id":  "CLAUSE-CONTRACT-AGREEMENT-001",
        "title":      "Contract Agreement — Standard Form",
        "text_english": (
            "**CONTRACT AGREEMENT — STANDARD FORM**\n\n"
            "_(To be signed on non-judicial stamp paper of value as applicable, between "
            "the Employer and the Contractor within 14 days of LoA issuance per "
            "ITB §41.)_\n\n"
            "**THIS AGREEMENT** is made on this **{{contract_signing_date}}** between "
            "**{{employer_name}}**, represented by **{{employer_designation}}** "
            "(hereinafter called \"the Employer\") OF THE ONE PART, AND  \n"
            "**M/s {{contractor_name}}**, having its registered office at "
            "{{contractor_address}}, represented by {{contractor_signatory_name}}, "
            "{{contractor_signatory_designation}} (hereinafter called \"the "
            "Contractor\") OF THE OTHER PART.\n\n"
            "**WHEREAS** the Employer is desirous that certain Works should be executed, "
            "viz. **{{project_name}}**, AND HAS ACCEPTED THE BID by the Contractor for "
            "the execution and completion of such Works AND THE REMEDYING OF DEFECTS "
            "therein, for the sum of **Rs. {{contract_value_cr}} Crore "
            "(Rs. {{contract_value_rupees}} only) (hereinafter called \"the Contract "
            "Price\")**.\n\n"
            "**NOW THIS AGREEMENT WITNESSETH** as follows:\n\n"
            "1. In this Agreement, words and expressions shall have the same meanings "
            "as are respectively assigned to them in the General Conditions of Contract "
            "(GCC) hereinafter referred to.\n\n"
            "2. The following documents shall be deemed to form and be read and construed "
            "as part of this Agreement, in the order of precedence:  \n"
            "(a) Letter of Acceptance dated {{loa_date}};  \n"
            "(b) Bid submitted by the Contractor;  \n"
            "(c) Particular Conditions of Contract (PCC);  \n"
            "(d) General Conditions of Contract (GCC);  \n"
            "(e) Specifications;  \n"
            "(f) Drawings;  \n"
            "(g) Bill of Quantities (BoQ);  \n"
            "(h) Notice Inviting Tender (NIT) and Bid Data Sheet (BDS).\n\n"
            "3. In consideration of the payments to be made by the Employer to the "
            "Contractor as hereinafter mentioned, the Contractor hereby covenants with "
            "the Employer to execute and complete the Works AND remedy any defects "
            "therein in conformity in all respects with the provisions of the Contract.\n\n"
            "4. The Employer hereby covenants to pay the Contractor in consideration of "
            "the execution and completion of the Works AND the remedying of defects "
            "therein the Contract Price OR such other sum as may become payable under "
            "the provisions of the Contract at the times and in the manner prescribed.\n\n"
            "**Time of Performance:**  \n"
            "Date of Commencement: {{commencement_date}}  \n"
            "Period of Completion: {{duration_months}} months  \n"
            "Stipulated Date of Completion: {{stipulated_completion_date}}  \n"
            "Defects Liability Period: 24 months from date of Completion (per AP-GO-084)\n\n"
            "**IN WITNESS WHEREOF** the parties hereto have caused this Agreement to be "
            "executed on the day and year first above written.\n\n"
            "**For and on behalf of the Employer:**  \n"
            "{{employer_signatory_name}}  \n"
            "{{employer_designation}}  \n"
            "Signature: ____________________  Date: {{contract_signing_date}}  \n"
            "(Office Seal)\n\n"
            "**For and on behalf of the Contractor:**  \n"
            "{{contractor_signatory_name}}  \n"
            "{{contractor_signatory_designation}}  \n"
            "Signature: ____________________  Date: {{contract_signing_date}}  \n"
            "(Firm Seal)\n\n"
            "**Witnesses:**  \n"
            "1. Name: {{witness1_name}}  Address: {{witness1_address}}  Signature: ________  \n"
            "2. Name: {{witness2_name}}  Address: {{witness2_address}}  Signature: ________"
        ),
        "parameters": [
            _p("contract_signing_date", "Contract signing date", "date", "2026-06-08"),
            _p("employer_name", "Employer name", "text", "APCRDA"),
            _p("employer_designation", "Employer designation", "text", "Managing Director, APCRDA"),
            _p("contractor_name", "Contractor name", "text", "ABC Constructions Pvt Ltd"),
            _p("contractor_address", "Contractor address", "text", "Plot 12, Banjara Hills, Hyderabad 500034"),
            _p("contractor_signatory_name", "Contractor signatory name", "text", "Mr. A. K. Sharma"),
            _p("contractor_signatory_designation", "Contractor signatory designation", "text", "Director (Technical)"),
            _p("project_name", "Project name", "text", "Construction of Andhra Pradesh Judicial Academy"),
            _p("contract_value_cr", "Contract value (Cr)", "currency", "125.50"),
            _p("contract_value_rupees", "Contract value (Indian)", "text", "1,25,50,00,000.00"),
            _p("loa_date", "LoA date", "date", "2026-05-25"),
            _p("commencement_date", "Commencement date", "date", "2026-06-15"),
            _p("duration_months", "Duration months", "text", "24"),
            _p("stipulated_completion_date", "Stipulated completion", "date", "2028-06-14"),
            _p("employer_signatory_name", "Employer signatory name", "text", "Sri P. Ramana Reddy, IAS"),
            _p("witness1_name", "Witness 1 name", "text", "Mr. K. Srinivas Rao"),
            _p("witness1_address", "Witness 1 address", "text", "APCRDA Office"),
            _p("witness2_name", "Witness 2 name", "text", "Mrs. R. Lakshmi"),
            _p("witness2_address", "Witness 2 address", "text", "APCRDA Office"),
        ],
        "position_order": 109,
    },

    # 11 — Manufacturer's Authorisation Form
    {
        "clause_id":  "CLAUSE-MANUFACTURER-AUTH-001",
        "title":      "Manufacturer's Authorisation Form (MAF) — Standard Format",
        "text_english": (
            "**MANUFACTURER'S AUTHORISATION FORM (MAF)**\n\n"
            "_(To be furnished on the manufacturer's letterhead, signed by an authorised "
            "signatory of the manufacturer, where the bidder is bidding as an authorised "
            "representative / dealer of an OEM for specialised equipment / materials.)_\n\n"
            "**To,**  \n"
            "The {{employer_designation}}  \n"
            "{{employer_name}}  \n"
            "{{employer_address}}\n\n"
            "**Sub: Manufacturer's Authorisation for {{equipment_description}} for "
            "Tender No. {{nit_number}}**\n\n"
            "Dear Sir,\n\n"
            "We, **{{manufacturer_name}}**, having our registered office at "
            "**{{manufacturer_address}}**, who are established and reputable manufacturers "
            "of **{{equipment_description}}** with manufacturing facilities at "
            "{{manufacturing_location}}, do hereby AUTHORISE **M/s {{authorised_dealer_name}}** "
            "(\"the Bidder\") of {{authorised_dealer_address}} to submit a Bid AND "
            "subsequently, in case of Award of Contract, to negotiate and conclude the "
            "contract on our behalf for the supply and installation of "
            "{{equipment_description}} manufactured by us against the above-referenced "
            "Tender.\n\n"
            "We hereby undertake the following:\n\n"
            "1. We shall provide **the warranty / guarantee for the equipment supplied** "
            "as stipulated in the Tender Documents — covering both manufacturing defects "
            "and performance, for a period of {{warranty_period}}.\n\n"
            "2. We shall **support the Bidder** with all necessary technical literature, "
            "drawings, parts manuals, and test certificates as required for evaluation "
            "and during execution.\n\n"
            "3. We shall ensure **timely supply of spares** for a period of {{spares_period}} "
            "from the date of supply.\n\n"
            "4. We confirm that the equipment offered is in accordance with the technical "
            "specifications stipulated in the Tender Documents and complies with applicable "
            "BIS / IS / ISO / IEC standards as listed in the technical specifications.\n\n"
            "5. This Authorisation is valid until **{{maf_validity_date}}**.\n\n"
            "Yours faithfully,  \n"
            "For **{{manufacturer_name}}**,  \n"
            "Authorised Signatory: ____________________  \n"
            "Name: {{maf_signatory_name}}  \n"
            "Designation: {{maf_signatory_designation}}  \n"
            "Date: {{maf_date}}  \n"
            "(Manufacturer's Seal)"
        ),
        "parameters": [
            _p("employer_designation", "Employer designation", "text", "Managing Director, APCRDA"),
            _p("employer_name", "Employer name", "text", "APCRDA"),
            _p("employer_address", "Employer address", "text", "APCRDA Project Office, Rayapudi, Amaravati"),
            _p("equipment_description", "Equipment description", "text", "HVAC Variable Refrigerant Volume (VRV/VRF) Systems"),
            _p("nit_number", "NIT number", "text", "100/PROC/APCRDA/1/2026"),
            _p("manufacturer_name", "Manufacturer name", "text", "Daikin Airconditioning India Pvt Ltd"),
            _p("manufacturer_address", "Manufacturer address", "text", "Daikin Tower, Sector 32, Gurgaon 122001"),
            _p("manufacturing_location", "Manufacturing facility", "text", "Neemrana Plant, Rajasthan"),
            _p("authorised_dealer_name", "Bidder / dealer name", "text", "ABC Constructions Pvt Ltd"),
            _p("authorised_dealer_address", "Bidder / dealer address", "text", "Plot 12, Banjara Hills, Hyderabad"),
            _p("warranty_period", "Warranty period", "text", "5 years comprehensive"),
            _p("spares_period", "Spares support period", "text", "10 years"),
            _p("maf_validity_date", "MAF validity end", "date", "2027-12-31"),
            _p("maf_signatory_name", "MAF signatory name", "text", "Mr. T. Yamamoto"),
            _p("maf_signatory_designation", "MAF signatory designation", "text", "Country Sales Director, India"),
            _p("maf_date", "MAF issue date", "date", "2026-04-10"),
        ],
        "position_order": 110,
    },

    # 12 — Bid Security (BG) form
    {
        "clause_id":  "CLAUSE-BID-SECURITY-FORM-001",
        "title":      "Bid Security (Bank Guarantee) — Standard Form",
        "text_english": (
            "**BID SECURITY — BANK GUARANTEE PROFORMA**\n\n"
            "_(To be furnished by a Government / Nationalised / Public Sector / Scheduled "
            "Bank in lieu of EMD per ITB §19. Acceptable forms per AP-GO-050: NEFT/RTGS, "
            "Bank Guarantee, Insurance Surety Bond, or Electronic Bank Guarantee. "
            "Validity: 180 days from the last bid-submission date.)_\n\n"
            "**To,**  \n"
            "The {{employer_designation}}  \n"
            "{{employer_name}}  \n"
            "{{employer_address}}\n\n"
            "**Bank Guarantee No: {{bs_number}}**  \n"
            "**Date: {{bs_issue_date}}**\n\n"
            "WHEREAS **M/s {{bidder_name}}** (\"the Bidder\") having its registered "
            "office at {{bidder_address}} is participating in Tender No. **{{nit_number}} "
            "Dt {{nit_date}}** for the work of **{{project_name}}**, with an Estimated "
            "Contract Value (ECV) of Rs. {{ecv_cr}} Crore, AND\n\n"
            "WHEREAS the Bidder is required to furnish a Bid Security in the form of a "
            "Bank Guarantee for **Rs. {{bs_amount_cr}} Crore "
            "(Rs. {{bs_amount_rupees}} only)** — being **{{bs_pct}}% of the ECV** — "
            "valid for 180 days from the last bid-submission date;\n\n"
            "NOW THEREFORE, **{{bank_name}}** through its branch at **{{bank_branch}}** "
            "unconditionally and irrevocably undertakes to pay to the Employer, on first "
            "written demand without demur and without the Employer assigning any reason, "
            "any sum or sums up to a maximum of **Rs. {{bs_amount_cr}} Crore "
            "(Rs. {{bs_amount_rupees}})**.\n\n"
            "This Guarantee is valid **from {{bs_issue_date}} until {{bs_validity_date}} "
            "(180 days from the last bid-submission date)**, and is subject to the "
            "following conditions:\n\n"
            "(a) The Guarantee shall be FORFEITED in whole if the Bidder withdraws or "
            "modifies the Bid during the period of bid validity, OR fails to sign the "
            "Contract Agreement after issue of LoA, OR fails to furnish the required "
            "Performance Security per ITB §42.\n\n"
            "(b) The Guarantee shall be RELEASED to the Bidder upon: (i) the Bidder being "
            "unsuccessful and the Award being given to another bidder; OR (ii) the "
            "successful Bidder submitting the Performance Security and signing the "
            "Contract Agreement.\n\n"
            "For **{{bank_name}}**,  \n"
            "Authorised Signatory: ____________________  \n"
            "Name: {{bs_signatory_name}}  \n"
            "Designation: {{bs_signatory_designation}}  \n"
            "Bank Seal & Stamp"
        ),
        "parameters": [
            _p("employer_designation", "Employer designation", "text", "Managing Director, APCRDA"),
            _p("employer_name", "Employer name", "text", "APCRDA"),
            _p("employer_address", "Employer address", "text", "APCRDA Project Office, Rayapudi, Amaravati"),
            _p("bs_number", "Bid Security BG number", "text", "UBI/HYD/BS/2026/0089"),
            _p("bs_issue_date", "Bid Security issue date", "date", "2026-05-05"),
            _p("bidder_name", "Bidder name", "text", "ABC Constructions Pvt Ltd"),
            _p("bidder_address", "Bidder address", "text", "Plot 12, Banjara Hills, Hyderabad 500034"),
            _p("nit_number", "NIT number", "text", "100/PROC/APCRDA/1/2026"),
            _p("nit_date", "NIT date", "date", "2026-04-15"),
            _p("project_name", "Project name", "text", "Construction of Andhra Pradesh Judicial Academy"),
            _p("ecv_cr", "ECV (Cr)", "currency", "125.50"),
            _p("bs_amount_cr", "Bid Security amount (Cr)", "currency", "1.255"),
            _p("bs_amount_rupees", "Bid Security amount (Indian fmt)", "text", "1,25,50,000.00"),
            _p("bs_pct", "Bid Security percentage", "text", "1"),
            _p("bank_name", "Issuing bank", "text", "Union Bank of India"),
            _p("bank_branch", "Bank branch", "text", "Banjara Hills, Hyderabad"),
            _p("bs_validity_date", "Bid Security validity end", "date", "2026-11-04"),
            _p("bs_signatory_name", "Bid Security signatory name", "text", "Mr. S. K. Rao"),
            _p("bs_signatory_designation", "Bid Security signatory designation", "text", "Branch Manager"),
        ],
        "position_order": 111,
    },

    # 13 — Sub-Contractor Declaration
    {
        "clause_id":  "CLAUSE-SUBCONTRACTOR-DECL-001",
        "title":      "Sub-Contractor Declaration — Standard Form",
        "text_english": (
            "**SUB-CONTRACTOR DECLARATION**\n\n"
            "_(Per ITB §34: the total value of works to be awarded on sub-contracting "
            "shall NOT exceed 50% of the contract value. Sub-contractors shall meet "
            "the qualification and other eligibility criteria with reference to the "
            "criteria of the prime contractor in proportion to the value of work "
            "proposed to be sub-contracted. The Contractor SHALL OBTAIN written "
            "approval from the Employer prior to engaging any sub-contractor.)_\n\n"
            "Bidder Name: **{{bidder_name}}**  \n"
            "Tender No: {{nit_number}}\n\n"
            "**Section A — Sub-contractors proposed at the time of Bid (if any):**\n\n"
            "| Sl | Sub-contractor Name | Specialisation | Value (Rs. Cr) | % of Contract Value | Qualification / Registration | Sub-contractor's Class |\n"
            "|---|---|---|---|---|---|---|\n"
            "| 1 | {{sub1_name}} | {{sub1_specialisation}} | {{sub1_value_cr}} | {{sub1_pct}} | {{sub1_registration}} | {{sub1_class}} |\n"
            "| 2 | {{sub2_name}} | {{sub2_specialisation}} | {{sub2_value_cr}} | {{sub2_pct}} | {{sub2_registration}} | {{sub2_class}} |\n"
            "| 3 | {{sub3_name}} | {{sub3_specialisation}} | {{sub3_value_cr}} | {{sub3_pct}} | {{sub3_registration}} | {{sub3_class}} |\n\n"
            "**Total proposed sub-contracting value:** Rs. {{total_sub_value_cr}} Crore "
            "= **{{total_sub_pct}}%** of contract value.  \n"
            "_(Total shall not exceed 50% of contract value per ITB §34.)_\n\n"
            "**Section B — Bidder's Declaration:**\n\n"
            "I/We M/s {{bidder_name}} hereby DECLARE that:  \n"
            "(a) The proposed sub-contractors listed in Section A are independent firms "
            "with their own equipment and personnel, and are not affiliated to the "
            "Bidder in any controlling capacity except as disclosed.  \n"
            "(b) Each proposed sub-contractor's qualifications shall be VERIFIED by "
            "the Tender Accepting Authority before sub-contracting approval is granted.  \n"
            "(c) The Bidder shall remain SOLELY RESPONSIBLE to the Employer for the "
            "execution and quality of all sub-contracted work.  \n"
            "(d) Any sub-contracting NOT disclosed in this Declaration but discovered "
            "during execution shall be a ground for contract termination AND debarment.\n\n"
            "Signature: ____________________  \n"
            "Name: {{signatory_name}}  \n"
            "Designation: {{signatory_designation}}  \n"
            "Date: {{declaration_date}}  \n"
            "(Firm Seal)"
        ),
        "parameters": [
            _p("bidder_name", "Bidder firm name", "text", "M/s ABC Constructions Pvt Ltd"),
            _p("nit_number", "NIT number", "text", "100/PROC/APCRDA/1/2026"),
            _p("sub1_name", "Sub-contractor 1 name", "text", "M/s XYZ Electrical Pvt Ltd"),
            _p("sub1_specialisation", "Sub-1 specialisation", "text", "Internal & External Electrical"),
            _p("sub1_value_cr", "Sub-1 value (Cr)", "currency", "18.00"),
            _p("sub1_pct", "Sub-1 percentage", "text", "14.34%"),
            _p("sub1_registration", "Sub-1 registration", "text", "AP Class-I Electrical Contractor"),
            _p("sub1_class", "Sub-1 class", "text", "Class-I"),
            _p("sub2_name", "Sub-contractor 2 name", "text", "M/s PQR Mechanical & HVAC"),
            _p("sub2_specialisation", "Sub-2 specialisation", "text", "HVAC + Plumbing"),
            _p("sub2_value_cr", "Sub-2 value (Cr)", "currency", "22.00"),
            _p("sub2_pct", "Sub-2 percentage", "text", "17.53%"),
            _p("sub2_registration", "Sub-2 registration", "text", "GST + ISO 9001:2015"),
            _p("sub2_class", "Sub-2 class", "text", "—"),
            _p("sub3_name", "Sub-contractor 3 name", "text", "M/s LMN IT Solutions"),
            _p("sub3_specialisation", "Sub-3 specialisation", "text", "Structured Cabling + AV/IT"),
            _p("sub3_value_cr", "Sub-3 value (Cr)", "currency", "8.50"),
            _p("sub3_pct", "Sub-3 percentage", "text", "6.77%"),
            _p("sub3_registration", "Sub-3 registration", "text", "ISO 27001 + RDBMS Cert"),
            _p("sub3_class", "Sub-3 class", "text", "—"),
            _p("total_sub_value_cr", "Total sub-contract value", "currency", "48.50"),
            _p("total_sub_pct", "Total sub-contract %", "text", "38.65"),
            _p("signatory_name", "Signatory name", "text", "Mr. A. K. Sharma"),
            _p("signatory_designation", "Signatory designation", "text", "Director (Technical)"),
            _p("declaration_date", "Declaration date", "date", "2026-04-15"),
        ],
        "position_order": 112,
    },
]


# ── Insert / upsert ──────────────────────────────────────────────────

def upsert_clauses() -> tuple[int, int]:
    """POST with merge-duplicates resolution. Returns (n_inserted, n_skipped)."""
    n_ok = 0
    n_err = 0
    today = date.today().isoformat()
    for c in CLAUSES:
        body = {
            "clause_id":               c["clause_id"],
            "title":                   c["title"],
            "text_english":            c["text_english"],
            "text_telugu":             None,
            "parameters":              c["parameters"],
            "applicable_tender_types": ["Works", "EPC"],
            "mandatory":               True,
            "position_section":        "Volume-I/Section-5/Forms",
            "position_order":          c["position_order"],
            "cross_references":        [],
            "rule_ids":                [],
            "valid_from":              today,
            "valid_until":             None,
            "human_verified":          False,
            "clause_type":             "DRAFTING_CLAUSE",
        }
        r = requests.post(f"{REST}/rest/v1/clause_templates",
                          json=[body], headers=H, timeout=30)
        if r.ok:
            n_ok += 1
            print(f"  ✓ {c['clause_id']}")
        else:
            n_err += 1
            print(f"  ✗ {c['clause_id']}: {r.status_code} {r.text[:200]}")
    return n_ok, n_err


def main() -> int:
    print("=" * 76)
    print(f"  Seeding {len(CLAUSES)} standard AP Works Forms proformas")
    print(f"  position_section: Volume-I/Section-5/Forms")
    print(f"  applicable_tender_types: ['Works', 'EPC']")
    print("=" * 76)
    n_ok, n_err = upsert_clauses()
    print()
    print(f"  Inserted/upserted: {n_ok}")
    print(f"  Errors:            {n_err}")

    if n_err == 0:
        print()
        print("  Verify with:")
        print("    SELECT clause_id, title, position_order")
        print("    FROM clause_templates")
        print("    WHERE clause_id IN (")
        for c in CLAUSES:
            print(f"      '{c['clause_id']}',")
        print("    );")
    return 0 if n_err == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
