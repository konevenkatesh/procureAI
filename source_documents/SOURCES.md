# Source Documents — Download Checklist

> Drop downloaded files into the indicated folders **using the exact filenames below**.
> The processing pipeline (`scripts/process_all_documents.py`) processes whatever is
> in the `raw_pdf/` (or `raw/`) folders, so filenames are mainly for traceability.
>
> 🤖 = auto-downloads via `python scripts/download_sources.py`
> ⬜ = needs manual download (browser; URL listed below)
> ✅ = present in repo

---

## Quick start: auto-download what's possible

```bash
python scripts/download_sources.py
```
This pulls **9 verified-working PDFs** (~33 MB) into `source_documents/central/raw_pdf/`
and prints a list of what still needs manual download.

URLs are tested for SPA shells, magic-byte validation, and minimum size before
the file is kept. Failed downloads are saved as `.invalid` for inspection.

---

## CENTRAL — `source_documents/central/raw_pdf/`

### Auto-downloads (run `scripts/download_sources.py`)

- 🤖 **`GFR_2017.pdf`** (2 MB) — General Financial Rules 2017, consolidated through 31-07-2024
- 🤖 **`MPW_2022.pdf`** (4 MB) — Manual for Procurement of Works 2022, full ~214-page manual
- 🤖 **`MPW_2025_draft.pdf`** (2.8 MB) — Draft Works Manual 2nd Edition (will supersede MPW 2022)
- 🤖 **`MPG_2022.pdf`** (5.3 MB) — Manual for Procurement of Goods 2022
- 🤖 **`MPS_2017.pdf`** (3.6 MB) — Manual for Procurement of Consultancy & Other Services 2017
- 🤖 **`MPS_2022.pdf`** (4.4 MB) — Manual for Procurement of Consultancy & Other Services 2022 (updated)
- 🤖 **`CVC_consolidated.pdf`** (890 KB, 88 pages) — CVC procurement circulars consolidated, eprocure.gov.in mirror
- 🤖 **`CVC_integrity_pact.pdf`** (8.8 MB) — CVC Integrity Pact adoption guidelines, Pondicherry CVO mirror
- 🤖 **`CVC_guidelines_chapters34.pdf`** (325 KB) — CVC procurement guidelines (chapters 3-4), DPS mirror

### Manual download required

- ⬜ **Discrete CVC circulars** (e-procurement, post-tender negotiation ban, EMD, splitting)
  - Where: https://cvc.gov.in
  - Why manual: cvc.gov.in is now a React SPA and direct PDF URLs return the app shell.
  - Note: most rule content is already in `CVC_consolidated.pdf`. Download individual circulars only if a specific recent one is needed.

- ⬜ **`DoE_arbitration.pdf`** — DoE arbitration guidelines, June 2024
  - Where: https://doe.gov.in/order-circular/Office%20Memorandum

- ⬜ **`MakeInIndia.pdf`** — Public Procurement (Preference to Make in India) Order
  - Where: https://dpiit.gov.in/public-procurement
  - Why manual: canonical URL changes per amendment — search "PPP-MII Order"

- ⬜ **`MSE_policy.pdf`** — Public Procurement Policy for MSEs
  - Where: https://msme.gov.in (search "MSE Procurement Policy")

---

## AP STATE — `source_documents/ap_state/raw_pdf/`

All AP state docs require manual download — the AP GO Issue Register requires
form-based search and the other AP portals are similarly behind interfaces.

- ⬜ **`GO_Ms_79_2020.pdf`** — Finance Dept GO.Ms.79 (Sept 2020) — **Reverse tendering ≥ ₹1 cr**
  - Where: https://goir.ap.gov.in (search: department=Finance, year=2020, no=79)
  - Why critical: HARD_BLOCK rule that no central rule contains.

- ⬜ **`AP_Judicial_Preview.pdf`** — AP Infrastructure (Transparency) Act 2019
  - Where: https://judicialpreview.ap.gov.in
  - Why critical: tenders ≥ ₹100 cr go to retired HC judge for 15-day review.

- ⬜ **`GO_Ms_41_2018.pdf`** — Water Resources GO.Ms.41 (2018)
  - Where: https://goir.ap.gov.in (search: department=Water Resources, year=2018, no=41)
  - Why important: department-specific sanction threshold overrides.

- ⬜ **`AP_Financial_Code.pdf`** — AP Financial Code (Volume I, procurement chapters)
  - Where: https://apfinance.gov.in → Codes & Manuals

- ⬜ **`AP_PWD_Code.pdf`** — AP PWD Code (works contract sections only)
  - Where: https://www.apwrd.gov.in → Manuals

---

## SAMPLE TENDERS — `source_documents/sample_tenders/raw/`

Hackathon-provided files (already in your possession).

- ⬜ **`RFP_PMC_Fishing_Harbours.docx`** — Real RFP for Project Management Consultancy
- ⬜ **`Corrigendum_1.docx`** — Corrigendum issued against the RFP
- ⬜ **`Evaluation_Statements.docx`** — Bid evaluation outputs from real tender

---

## What I do once these are dropped in

1. Auto-fetch what's possible:
   ```bash
   python scripts/download_sources.py
   ```
2. Download anything still marked ⬜ via browser into the matching folder.
3. Convert all docs to Markdown:
   ```bash
   python scripts/process_all_documents.py
   ```
4. Build extraction batches:
   ```bash
   python scripts/prepare_extraction_batches.py
   ```
5. **Ask Claude Code (in chat) to extract a batch** — I read the batch JSON and write rule JSON to `data/extraction_results/`.
6. Load to Postgres:
   ```bash
   python scripts/load_extracted_rules.py
   ```
7. Human review:
   ```bash
   python builder/review_cli.py review --batch 30
   ```
8. Repeat for clauses, Telugu, SHACL, test cases.

---

## Optional / nice-to-have (Phase 2)

- ⬜ **AP RTGS SOPs** — internal SOPs from RTGS team if obtainable
- ⬜ **Department circulars** — Panchayat Raj, Roads & Buildings, Health
- ⬜ **CAG audit reports** on AP procurement (last 5 years) — to validate risk typology
- ⬜ **eProcurement portal sample tenders** (10–20 from apeprocurement.gov.in) — for calibration

---

## Notes on file handling

- `raw_pdf/` files are **gitignored** (too large). The repo tracks the *processed Markdown* output only by default.
- If you must work fully offline, remove the `*.pdf` line from `.gitignore` to commit the PDFs (will bloat repo by ~50 MB).
- Filenames are case-sensitive. Use exactly the names listed above so future automation matches.
