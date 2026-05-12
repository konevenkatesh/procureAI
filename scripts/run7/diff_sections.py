"""R7.1 — Classify each section by HOD/LPS similarity.

Reads the 18 extracted .txt files; for each of 9 sections, computes:
  - Normalised line-set Jaccard similarity (paragraph fingerprint)
  - Section classification: BOILERPLATE / TEMPLATE+PLACEHOLDERS / PROJECT-SPECIFIC

Output: /tmp/section_classification.json
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent.parent
EXTRACTED = REPO / "data" / "extracted"
OUTFILE = Path("/tmp/section_classification.json")


SECTIONS = [
    ("NIT",          "Notice Inviting Tender"),
    ("section_I",    "Instructions to Bidders (ITB)"),
    ("section_II",   "Bid Data Sheet (BDS)"),
    ("section_III",  "Evaluation and Qualification Criteria"),
    ("section_IV",   "Bidding Forms"),
    ("section_V",    "Fraud and Corruption"),
    ("section_VI",   "Works' Requirements"),
    ("section_VII",  "General Conditions of Contract (GCC)"),
    ("section_VIII", "Particular Conditions of Contract"),
    ("section_IX",   "Contract Forms"),
]


def normalize_line(line: str) -> str:
    """Strip page numbers, dates, project-specific tokens to focus on structural identity."""
    s = line.strip()
    # Drop empty lines
    if not s:
        return ""
    # Drop bare page numbers (e.g. "23" or "Page 23 of 244")
    if re.fullmatch(r"\d+", s):
        return ""
    if re.fullmatch(r"Page\s+\d+\s+of\s+\d+", s, re.IGNORECASE):
        return ""
    # Normalize project-specific tokens to placeholders so they don't reduce similarity
    s = re.sub(r"AGICL\.?", "<EMPLOYER>", s)
    s = re.sub(r"ADCL", "<EMPLOYER>", s)
    s = re.sub(r"APCRDA", "<AUTHORITY>", s)
    # Dates like 27.04.2026 / 08.05.2026
    s = re.sub(r"\b\d{1,2}\.\d{1,2}\.\d{4}\b", "<DATE>", s)
    s = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", "<DATE>", s)
    # NIT numbers
    s = re.sub(r"NIT\s+No[:\.]?\s*\S+", "<NIT_NO>", s, flags=re.IGNORECASE)
    # ECV in Rupees (Rs.XX,XX,XX,XXX OR Rs.XXX.XX Crores)
    s = re.sub(r"Rs\.?\s*[\d,\.]+(\s*(Crores?|Lakhs?))?", "<ECV>", s, flags=re.IGNORECASE)
    s = re.sub(r"₹\s*[\d,\.]+", "<ECV>", s)
    # Whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s


def fingerprint_lines(text: str) -> set[str]:
    """Set of normalized non-empty content lines (≥30 chars to skip table separators)."""
    lines = text.splitlines()
    normed = {normalize_line(l) for l in lines}
    return {l for l in normed if len(l) >= 30}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    inter = a & b
    union = a | b
    return len(inter) / len(union) if union else 0.0


def classify(similarity: float, section_id: str) -> str:
    """Map similarity → classification per pre-flight inventory rules.
       NIT/II/III have ECV-level differences but structurally identical → TEMPLATE+PLACEHOLDERS.
       Section VI has heavy project content → PROJECT-SPECIFIC.
    """
    if similarity >= 0.85:
        return "BOILERPLATE"
    if similarity >= 0.50:
        return "TEMPLATE+PLACEHOLDERS"
    return "PROJECT-SPECIFIC"


def main() -> None:
    print("R7.1 — Classifying 9 sections by HOD/LPS similarity")
    print("=" * 86)
    print(f"{'Section':14s} {'Name':40s} {'Sim':>6s}  Classification")
    print("-" * 86)

    out: dict[str, Any] = {"sections": {}}

    for section_id, name in SECTIONS:
        hod_path = EXTRACTED / "hod" / f"{section_id}.txt"
        lps_path = EXTRACTED / "lps" / f"{section_id}.txt"

        if not (hod_path.exists() and lps_path.exists()):
            print(f"  MISSING: {section_id}")
            continue

        hod_text = hod_path.read_text(encoding="utf-8", errors="replace")
        lps_text = lps_path.read_text(encoding="utf-8", errors="replace")

        hod_fp = fingerprint_lines(hod_text)
        lps_fp = fingerprint_lines(lps_text)
        sim = jaccard(hod_fp, lps_fp)
        cls = classify(sim, section_id)

        print(f"  {section_id:14s} {name[:40]:40s} {sim:>6.3f}  {cls}")

        out["sections"][section_id] = {
            "name":               name,
            "hod_path":           str(hod_path.relative_to(REPO)),
            "lps_path":           str(lps_path.relative_to(REPO)),
            "hod_bytes":          hod_path.stat().st_size,
            "lps_bytes":          lps_path.stat().st_size,
            "hod_lines_norm":     len(hod_fp),
            "lps_lines_norm":     len(lps_fp),
            "lines_shared":       len(hod_fp & lps_fp),
            "lines_union":        len(hod_fp | lps_fp),
            "jaccard_similarity": round(sim, 4),
            "classification":     cls,
        }

    OUTFILE.write_text(json.dumps(out, indent=2))
    print(f"\nWrote {OUTFILE}")

    # Summary counts
    counts: dict[str, int] = {}
    for sect_data in out["sections"].values():
        counts[sect_data["classification"]] = counts.get(sect_data["classification"], 0) + 1
    print(f"Classification distribution: {counts}")


if __name__ == "__main__":
    main()
