"""R7.1 — Extract 9 sections from each BID DOCUMENT PDF using pdftotext -layout.

Output: 18 cleaned .md files (9 sections × 2 source docs) under data/extracted/{hod,lps}/.
Section page ranges sourced from /tmp/sbd_corpus_inventory.md.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import NamedTuple

REPO = Path(__file__).resolve().parent.parent.parent
PDF_DIR = REPO / "data" / "references"
OUT_DIR = REPO / "data" / "extracted"


class SectionRange(NamedTuple):
    section_id: str            # "NIT", "section_I", "section_II" ...
    name: str
    first_page: int
    last_page: int


# Page ranges per the pre-flight inventory (HOD Towers 244pp / LPS Zone-11 246pp)
HOD_SECTIONS: list[SectionRange] = [
    SectionRange("NIT",          "Notice Inviting Tender",                 3,   5),
    SectionRange("section_I",    "Instructions to Bidders (ITB)",          7,  24),
    SectionRange("section_II",   "Bid Data Sheet (BDS)",                  25,  37),
    SectionRange("section_III",  "Evaluation and Qualification Criteria", 38,  47),
    SectionRange("section_IV",   "Bidding Forms",                         48,  88),
    SectionRange("section_V",    "Fraud and Corruption",                  89,  96),
    SectionRange("section_VI",   "Works' Requirements",                   98, 163),
    SectionRange("section_VII",  "General Conditions of Contract (GCC)", 165, 197),
    SectionRange("section_VIII", "Particular Conditions of Contract",    198, 228),
    SectionRange("section_IX",   "Contract Forms",                       229, 244),
]

LPS_SECTIONS: list[SectionRange] = [
    SectionRange("NIT",          "Notice Inviting Tender",                 3,   6),
    SectionRange("section_I",    "Instructions to Bidders (ITB)",          8,  26),
    SectionRange("section_II",   "Bid Data Sheet (BDS)",                  27,  39),
    SectionRange("section_III",  "Evaluation and Qualification Criteria", 40,  49),
    SectionRange("section_IV",   "Bidding Forms",                         50,  92),
    SectionRange("section_V",    "Fraud and Corruption",                  93,  94),
    SectionRange("section_VI",   "Works' Requirements",                   96, 167),
    SectionRange("section_VII",  "General Conditions of Contract (GCC)", 169, 200),
    SectionRange("section_VIII", "Particular Conditions of Contract",    201, 229),
    SectionRange("section_IX",   "Contract Forms",                       230, 246),
]


def extract_section(pdf_path: Path, sect: SectionRange, out_path: Path) -> int:
    """Extract pp [first..last] from pdf_path to out_path as plain text with layout preserved."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # pdftotext -layout preserves the column structure (essential for tables)
    cmd = [
        "pdftotext", "-layout",
        "-f", str(sect.first_page),
        "-l", str(sect.last_page),
        str(pdf_path), str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"pdftotext failed: {result.stderr}")
    return out_path.stat().st_size


def extract_all() -> dict[str, dict[str, int]]:
    """Returns {doc: {section_id: bytes_extracted}}."""
    results: dict[str, dict[str, int]] = {"hod": {}, "lps": {}}

    hod_pdf = PDF_DIR / "HOD_Towers_BID_DOCUMENT.pdf"
    lps_pdf = PDF_DIR / "LPS_Zone11_ADCL_BID_DOCUMENT.pdf"

    for sect in HOD_SECTIONS:
        out = OUT_DIR / "hod" / f"{sect.section_id}.txt"
        n = extract_section(hod_pdf, sect, out)
        results["hod"][sect.section_id] = n
        print(f"  HOD {sect.section_id:14s} pp.{sect.first_page:3d}-{sect.last_page:3d}  → {n:>7d} bytes  ({sect.name})")

    for sect in LPS_SECTIONS:
        out = OUT_DIR / "lps" / f"{sect.section_id}.txt"
        n = extract_section(lps_pdf, sect, out)
        results["lps"][sect.section_id] = n
        print(f"  LPS {sect.section_id:14s} pp.{sect.first_page:3d}-{sect.last_page:3d}  → {n:>7d} bytes  ({sect.name})")

    return results


if __name__ == "__main__":
    print("R7.1 — Extracting 9 sections × 2 reference docs (18 files)")
    print("=" * 76)
    results = extract_all()
    total_hod = sum(results["hod"].values())
    total_lps = sum(results["lps"].values())
    print(f"\nTotals: HOD {total_hod:>9,d} bytes  |  LPS {total_lps:>9,d} bytes")
    print(f"Output dir: {OUT_DIR}")
