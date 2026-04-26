"""
Auto-download verified-working source documents into source_documents/central/raw_pdf/.

This is a BOOTSTRAP script — uses only Python stdlib so it can be run before
`pip install`. Only URLs that have been TESTED end-to-end and confirmed to
return real PDFs (not SPA shells, not HTML error pages) are listed here.
URLs that require manual download — because the host site is now a React SPA,
behind a captcha, or behind a search form — are listed in MANUAL_DOWNLOADS
and printed at the end.

Idempotent: skips files that already exist and are >50 KB.
Validates: every downloaded file is checked for the %PDF- magic header before
being kept. Anything that fails the check is moved to .invalid for inspection.

Usage:
    python scripts/download_sources.py
    python scripts/download_sources.py --force          # re-download even if present
    python scripts/download_sources.py --only GFR_2017  # one file
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DOCS_DIR = REPO_ROOT / "source_documents"


# ─────────────────────────────────────────────────────────────────────────────
# Verified download list
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Download:
    filename: str
    url: str
    expected_min_kb: int
    description: str
    dest_subdir: str = "central/raw_pdf"


DOWNLOADS: list[Download] = [
    Download(
        filename="GFR_2017.pdf",
        url="https://doe.gov.in/files/circulars_document/FInal_GFR_upto_31_07_2024.pdf",
        expected_min_kb=1500,
        description="General Financial Rules 2017 (consolidated through 31-07-2024)",
    ),
    Download(
        filename="MPW_2022.pdf",
        url="https://doe.gov.in/files/manuals_documents/Manual_for_Procurement_of_Works_Updated%20June,%202022.pdf",
        expected_min_kb=3000,
        description="Manual for Procurement of Works 2022 (full manual, ~214 pages)",
    ),
    Download(
        filename="MPW_2025_draft.pdf",
        url="https://doe.gov.in/files/circulars_document/Draft_Works_Manual_2nd_Edition.pdf",
        expected_min_kb=2000,
        description="Draft Works Manual 2nd Edition (2025) — supersedes MPW 2022 once finalised",
    ),
    Download(
        filename="MPG_2022.pdf",
        url="https://doe.gov.in/files/manuals_documents/Manual_for_Procurement_of_Goods_Updated%20June,%202022.pdf",
        expected_min_kb=4000,
        description="Manual for Procurement of Goods 2022",
    ),
    Download(
        filename="MPS_2017.pdf",
        url="https://doe.gov.in/files/manuals_documents/Manual_for_Procurement_of_Consultancy_and_Other_Services_2017_0.pdf",
        expected_min_kb=2500,
        description="Manual for Procurement of Consultancy & Other Services 2017",
    ),
    Download(
        filename="MPS_2022.pdf",
        url="https://doe.gov.in/files/manuals_documents/Manual_for_Procurement_of_Consultancy_&_Other_Services_Updated%20June,%202022_1.pdf",
        expected_min_kb=3000,
        description="Manual for Procurement of Consultancy & Other Services 2022 (updated)",
    ),
    Download(
        filename="CVC_consolidated.pdf",
        url="https://eprocure.gov.in/cppp/rulesandprocs/kbadqkdlcswfjdelrquehwuxcfmijmuixngudufgbuubgubfugbububjxcgfvsbdihbgfGhdfgFHytyhRtNTk4Nzg=",
        expected_min_kb=600,
        description="CVC consolidated procurement circulars (88 pages) — eprocure.gov.in mirror",
    ),
    Download(
        filename="CVC_integrity_pact.pdf",
        url="https://cvo.py.gov.in/Circulars_pdf/cvc-adoption_merged.pdf",
        expected_min_kb=4000,
        description="CVC Integrity Pact adoption guidelines — Pondicherry CVO mirror",
    ),
    Download(
        filename="CVC_guidelines_chapters34.pdf",
        url="https://dps.gov.in/pdf/chapters34.pdf",
        expected_min_kb=200,
        description="CVC procurement guidelines (chapters 3-4) — DPS mirror",
    ),
]


# ─────────────────────────────────────────────────────────────────────────────
# Documents that CANNOT be auto-downloaded
# ─────────────────────────────────────────────────────────────────────────────

MANUAL_DOWNLOADS = [
    {
        "filename": "Discrete CVC circulars (eprocurement, negotiation, EMD, splitting)",
        "where": "https://cvc.gov.in",
        "reason": "cvc.gov.in is now a React SPA — direct PDF URLs return the app shell. "
                  "Most rules are already covered in CVC_consolidated.pdf; download individual "
                  "circulars manually only if a specific recent circular is needed.",
    },
    {
        "filename": "DoE_arbitration.pdf",
        "where": "https://doe.gov.in/order-circular/Office%20Memorandum",
        "reason": "Find under Office Memoranda → June 2024 arbitration guidelines.",
    },
    {
        "filename": "MakeInIndia.pdf",
        "where": "https://dpiit.gov.in/public-procurement",
        "reason": "Search 'PPP-MII Order' on DPIIT site — canonical URL changes per amendment.",
    },
    {
        "filename": "MSE_policy.pdf",
        "where": "https://msme.gov.in",
        "reason": "Public Procurement Policy for MSEs — search 'MSE Procurement Policy'.",
    },
    {
        "filename": "GO_Ms_79_2020.pdf  (AP reverse tendering)",
        "where": "https://goir.ap.gov.in",
        "reason": "AP GO Issue Register requires search form (Finance, year=2020, no=79).",
    },
    {
        "filename": "GO_Ms_41_2018.pdf  (AP Water Resources)",
        "where": "https://goir.ap.gov.in",
        "reason": "AP GO Issue Register search form (Water Resources, year=2018, no=41).",
    },
    {
        "filename": "AP_Judicial_Preview.pdf",
        "where": "https://judicialpreview.ap.gov.in",
        "reason": "AP Infrastructure (Transparency) Act 2019 PDF on the Judicial Preview portal.",
    },
    {
        "filename": "AP_Financial_Code.pdf",
        "where": "https://apfinance.gov.in",
        "reason": "AP Financial Code (Volume I) — relevant procurement chapters only.",
    },
    {
        "filename": "AP_PWD_Code.pdf",
        "where": "https://www.apwrd.gov.in",
        "reason": "AP PWD Code — works contract sections.",
    },
]


USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
PDF_MAGIC = b"%PDF-"


def download_one(d: Download, force: bool) -> tuple[str, str]:
    """Returns (status, message). status in {'ok', 'skip', 'fail'}."""
    out_dir = SOURCE_DOCS_DIR / d.dest_subdir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / d.filename

    if out_path.exists() and out_path.stat().st_size > 50_000 and not force:
        return "skip", f"already present ({out_path.stat().st_size / 1024:.0f} KB)"

    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")

    try:
        result = subprocess.run(
            [
                "curl", "--location", "--silent", "--show-error",
                "--max-time", "120",
                "--user-agent", USER_AGENT,
                "--output", str(tmp_path),
                "--write-out", "%{http_code} %{size_download}",
                d.url,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return "fail", "curl not found on PATH"

    if result.returncode != 0:
        tmp_path.unlink(missing_ok=True)
        return "fail", f"curl exit {result.returncode}: {result.stderr.strip()[:200]}"

    info = result.stdout.strip()
    size = tmp_path.stat().st_size if tmp_path.exists() else 0
    size_kb = size / 1024

    if size < d.expected_min_kb * 1024:
        invalid = tmp_path.with_suffix(".invalid")
        tmp_path.rename(invalid)
        return "fail", f"too small ({size_kb:.0f} KB < {d.expected_min_kb} KB) — {info} — saved as {invalid.name}"

    with tmp_path.open("rb") as fh:
        header = fh.read(5)
    if header != PDF_MAGIC:
        invalid = tmp_path.with_suffix(".invalid")
        tmp_path.rename(invalid)
        return "fail", f"not a PDF (header={header!r}) — {info} — saved as {invalid.name}"

    tmp_path.rename(out_path)
    return "ok", f"{size_kb:.0f} KB ({info})"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--force", action="store_true",
                        help="Re-download even if a valid file already exists")
    parser.add_argument("--only", default=None,
                        help="Only download this filename prefix (e.g. GFR_2017)")
    args = parser.parse_args()

    items = DOWNLOADS
    if args.only:
        items = [d for d in DOWNLOADS if d.filename.startswith(args.only)]
        if not items:
            print(f"No download matches --only={args.only}", file=sys.stderr)
            return 1

    if not shutil.which("curl"):
        print("ERROR: curl is required but not on PATH", file=sys.stderr)
        return 2

    print(f"Downloading {len(items)} verified source document(s)...\n")
    counts = {"ok": 0, "skip": 0, "fail": 0}

    for d in items:
        status, msg = download_one(d, force=args.force)
        counts[status] += 1
        marker = {"ok": "[OK]  ", "skip": "[SKIP]", "fail": "[FAIL]"}[status]
        print(f"  {marker}  {d.filename:40s}  {msg}")
        print(f"           {d.description}\n")

    print(f"Summary: {counts['ok']} downloaded, {counts['skip']} skipped, {counts['fail']} failed.")

    if not args.only:
        print("\n" + "=" * 70)
        print("MANUAL DOWNLOADS REQUIRED")
        print("=" * 70)
        print("These documents could not be auto-downloaded (SPA, captcha, search form,")
        print("or no public direct URL). Fetch them in a browser and drop into the")
        print("indicated folder.\n")
        for m in MANUAL_DOWNLOADS:
            print(f"  - {m['filename']}")
            print(f"      where:  {m['where']}")
            print(f"      reason: {m['reason']}\n")

    return 0 if counts["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
