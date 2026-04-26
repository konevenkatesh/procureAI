# Source Documents — Download Checklist

> Drop downloaded files into the indicated folders **using the exact filenames below**.
> The processing pipeline (`scripts/process_all_documents.py`) expects these names.
>
> ✅ = downloaded   ⬜ = pending

---

## CENTRAL — `source_documents/central/raw_pdf/`

### Primary statutes & manuals (must-have)

- ⬜ **`GFR_2017.pdf`** — General Financial Rules 2017
  - Where: https://doe.gov.in/sites/default/files/GFR2017_0.pdf
  - Why: PRIMARY rule source. Chapters 5 & 6 are most rule-dense.
  - Est. size: ~270 pages

- ⬜ **`MPW_2022.pdf`** — Manual for Procurement of Works 2022
  - Where: https://doe.gov.in → Procurement Manuals
  - Why: All Works-tender mandatory clauses, EMD/PBG formulas.
  - Est. size: ~400 pages (largest single doc)

- ⬜ **`MPG_2024.pdf`** — Manual for Procurement of Goods 2024
  - Where: https://doe.gov.in → Procurement Manuals
  - Why: All Goods-tender rules.
  - Est. size: ~300 pages

- ⬜ **`MPS_2025.pdf`** — Manual for Procurement of Consultancy & Other Services 2025
  - Where: https://doe.gov.in → Procurement Manuals
  - Why: Consultancy + Services tender rules.
  - Est. size: ~200 pages

### CVC circulars (vigilance — small files but high-severity rules)

- ⬜ **`CVC_eprocurement.pdf`** — e-Procurement circular (12/04/2011)
  - Where: https://cvc.gov.in/sites/default/files/12042011.pdf
  - Why: Mandates e-procurement for ≥ ₹2 lakh.

- ⬜ **`CVC_negotiation.pdf`** — Post-tender negotiation ban
  - Where: https://cvc.gov.in → Circulars

- ⬜ **`CVC_integrity_pact.pdf`** — Integrity Pact guidelines
  - Where: https://cvc.gov.in → Circulars

- ⬜ **`CVC_EMD.pdf`** — EMD and bank guarantee circular
  - Where: https://cvc.gov.in → Circulars

- ⬜ **`CVC_splitting.pdf`** — Splitting of works circular
  - Where: https://cvc.gov.in → Circulars

### Other central

- ⬜ **`DoE_arbitration.pdf`** — DoE arbitration guidelines (June 2024)
  - Where: https://doe.gov.in → Office Memoranda

- ⬜ **`MakeInIndia.pdf`** — Public Procurement (Preference to Make in India) Order
  - Where: https://dpiit.gov.in → Public Procurement

- ⬜ **`MSE_policy.pdf`** — Public Procurement Policy for MSEs
  - Where: https://msme.gov.in → Public Procurement Policy

---

## AP STATE — `source_documents/ap_state/raw_pdf/`

### High-priority (these are the AP-killer rules)

- ⬜ **`GO_Ms_79_2020.pdf`** — Finance Dept GO.Ms.79 (Sept 2020) — **Reverse tendering ≥ ₹1 cr**
  - Where: https://goir.ap.gov.in → search "79 Finance 2020"
  - Why: HARD_BLOCK rule that no central rule contains.

- ⬜ **`AP_Judicial_Preview.pdf`** — AP Infrastructure (Transparency) Act 2019 (Judicial Preview)
  - Where: https://judicialpreview.ap.gov.in → Act PDF
  - Why: Tenders ≥ ₹100 cr go to retired HC judge for 15-day review.

- ⬜ **`GO_Ms_41_2018.pdf`** — Water Resources GO.Ms.41 (2018)
  - Where: https://goir.ap.gov.in → search "41 Water Resources 2018"
  - Why: Department-specific sanction threshold overrides.

### AP codes

- ⬜ **`AP_Financial_Code.pdf`** — AP Financial Code (Volume I, relevant chapters on procurement)
  - Where: https://apfinance.gov.in → Codes & Manuals

- ⬜ **`AP_PWD_Code.pdf`** — AP PWD Code (works contract sections only)
  - Where: https://www.apwrd.gov.in → Manuals (or AP Engineer-in-Chief office)

---

## SAMPLE TENDERS — `source_documents/sample_tenders/raw/`

These are the hackathon-provided files (you should already have them).

- ⬜ **`RFP_PMC_Fishing_Harbours.docx`** — Real RFP for Project Management Consultancy
- ⬜ **`Corrigendum_1.docx`** — Corrigendum issued against the RFP
- ⬜ **`Evaluation_Statements.docx`** — Bid evaluation outputs from real tender

---

## What I do once these are dropped in

1. You: drop files into the `raw_pdf/` (or `raw/`) folders with the exact filenames above.
2. You run: `python scripts/process_all_documents.py`
   → Docling converts every PDF/DOCX to structured Markdown in the matching `processed_md/` folder.
3. You run: `python scripts/prepare_extraction_batches.py`
   → Splits the Markdown into ~2K-token sections, writes batch files into `data/extraction_batches/`.
4. **You ask me (Claude Code) to extract a batch** — I read the batch file and write JSON rules to `data/extraction_results/`.
5. You run: `python scripts/load_extracted_rules.py` → pushes results into Postgres as `pending` candidates.
6. You run: `python builder/review_cli.py review --batch 30` → human review (approve/reject/modify).
7. Repeat for clause generation, Telugu, SHACL, test cases.

---

## Optional / nice-to-have (Phase 2)

- ⬜ **AP RTGS SOPs** — if you can get internal SOPs from RTGS
- ⬜ **Department circulars** — Panchayat Raj, Roads & Buildings, Health
- ⬜ **CAG audit reports** on AP procurement (last 5 years) — to validate our risk typology
- ⬜ **eProcurement portal sample tenders** (10–20 from apeprocurement.gov.in) — for calibration

---

## Notes on file handling

- `raw_pdf/` files are **gitignored** (too large). The repo tracks the *processed Markdown* output instead.
- If you must work offline, you can also commit the raw PDFs by removing the `*.pdf` line from `.gitignore` — but this will bloat the repo to ~500MB+.
- Filenames are case-sensitive on Linux. Use exactly the names listed above.
