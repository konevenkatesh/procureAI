"""R7.2 — Chunk MEP BoQ PDF into BoQItemSpec records.

Parses HOD_Towers_BoQ_MEP.pdf (380 pp landscape) via pdfplumber.
Column layout (8 cols): S NO | ESTQTY | ITEM DETAILED SPECIFICATION DESCRIPTION
                        | WORK TYPE | SHORT DESCRIPTION | APSS CL. NO | RATE | UOM | AMOUNT

For each line item: extract spec text + work type + short desc + APSS clause + tag discipline
+ extract IS/EN/ASHRAE/UL/EUROVENT citations via regex.

Output: data/extracted/boq_items.jsonl + summary stats.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Iterator

import pdfplumber

REPO = Path(__file__).resolve().parent.parent.parent
PDF_PATH = REPO / "data" / "references" / "HOD_Towers_BoQ_MEP.pdf"
OUT_DIR = REPO / "data" / "extracted"


# Citation regex — captures IS/EN/IEC/ASHRAE/UL/EUROVENT/AHRI references.
CITATION_PATTERNS = [
    re.compile(r"\bIS[\s:]+\d{1,5}(?:[-/:]\d{1,5})?\b"),           # IS 1554, IS:13947, IS-2629
    re.compile(r"\bIS[\s:]*Code\s+\d+", re.IGNORECASE),
    re.compile(r"\bBS[\s:]*EN[\s:]+\d{2,5}", re.IGNORECASE),
    re.compile(r"\bEN[\s:]+\d{2,5}\b"),                            # EN 1886, EN 779
    re.compile(r"\bIEC[\s:]*\d{2,5}(?:-\d+)?", re.IGNORECASE),     # IEC 60439
    re.compile(r"\bASHRAE[\s:]*\d+(\.\d+)?", re.IGNORECASE),       # ASHRAE 52.2
    re.compile(r"\bUL[\s:]+\d{3,5}", re.IGNORECASE),                # UL 1995
    re.compile(r"\bEUROVENT(?:-?[\s:]+(?:Class\s+)?[A-Z]?\d*)?", re.IGNORECASE),
    re.compile(r"\bAHRI[\s:]+\d{2,5}", re.IGNORECASE),
    re.compile(r"\bMERV[\s:]+\d{1,2}", re.IGNORECASE),
    re.compile(r"\bAPSS[\s:]+", re.IGNORECASE),
    re.compile(r"\bNFPA[\s:]+\d{2,5}", re.IGNORECASE),
]


# Discipline classification by WORK TYPE keywords
WORK_TYPE_TO_DISCIPLINE = {
    "HVAC":        ("MEP", "HVAC"),
    "ELECTRICAL":  ("MEP", "Electrical"),
    "FIRE":        ("MEP", "Fire"),
    "LIFT":        ("MEP", "Lifts"),
    "ESCALATOR":   ("MEP", "Lifts"),
    "PLUMBING":    ("MEP", "Plumbing"),
    "PA":          ("MEP", "PA"),
    "BMS":         ("MEP", "BMS"),
    "HSD":         ("MEP", "HSD"),
    "ROAD":        ("Civil", "Roads"),
    "DRAIN":       ("Civil", "Drains"),
    "WATER SUPPLY": ("Civil", "WaterSupply"),
    "SEWERAGE":    ("Civil", "Sewerage"),
    "DUCT":        ("Civil", "UtilityDucts"),
    "PLANTATION":  ("Civil", "Plantation"),
    "BRIDGE":      ("Civil", "Bridges"),
}


def classify_discipline(work_type: str, spec_text: str) -> tuple[str, str]:
    """Returns (parent_discipline, sub_discipline). Falls back to spec-text scan if work_type empty."""
    wt_upper = (work_type or "").upper().strip()

    for kw, (parent, sub) in WORK_TYPE_TO_DISCIPLINE.items():
        if kw in wt_upper:
            return parent, sub

    spec_upper = spec_text.upper()
    for kw, (parent, sub) in WORK_TYPE_TO_DISCIPLINE.items():
        if kw in spec_upper[:200]:  # first ~200 chars covers the item header
            return parent, sub

    return "Unknown", "Unknown"


def extract_citations(spec_text: str) -> list[str]:
    """Extract unique standard citations."""
    cites: set[str] = set()
    for pat in CITATION_PATTERNS:
        for m in pat.finditer(spec_text):
            cites.add(m.group(0).strip())
    return sorted(cites)


def extract_scale_signals(spec_text: str) -> dict:
    """Capture capacity/size/rating signals for retrieval scaling."""
    s = spec_text[:800]  # signals typically in early portion of item description
    sig = {}
    # Capacity: kVA / kW / TR / Liters / mm / m³/min etc.
    for m in re.finditer(r"\b(\d+(?:\.\d+)?)\s*(kVA|kW|MW|HP|TR|RT|CFM|Liters?|L/s|m³/min|cubic\s*metre|kg/m³)\b", s, re.IGNORECASE):
        sig.setdefault("capacities", []).append(f"{m.group(1)} {m.group(2)}")
    # Dimensions: pipe dia / panel size
    for m in re.finditer(r"\b(\d+(?:\.\d+)?)\s*(?:x|×)\s*(\d+(?:\.\d+)?)\s*(?:x|×)?\s*(\d+(?:\.\d+)?)?\s*mm\b", s):
        sig.setdefault("dimensions_mm", []).append(m.group(0))
    # Voltage levels
    for m in re.finditer(r"\b(\d+(?:\.\d+)?)\s*(?:k?V|kV|volt)\b", s, re.IGNORECASE):
        sig.setdefault("voltages", []).append(m.group(0))
    return sig


def parse_boq_table(page) -> list[dict]:
    """Extract rows from a pdfplumber page using table extraction.
    Returns list of {sno, est_qty, spec_text, work_type, short_desc, apss_cl_no, rate, uom, amount}."""
    tables = page.extract_tables(table_settings={
        "vertical_strategy": "lines",
        "horizontal_strategy": "lines",
        "intersection_tolerance": 5,
    })
    rows = []
    for table in tables:
        for raw_row in table:
            if not raw_row or len(raw_row) < 8:
                continue
            cells = [(c or "").strip() for c in raw_row]
            sno_raw = cells[0]
            # Skip headers
            if sno_raw.upper() in {"S NO", "S.NO", "SNO", ""}:
                continue
            if not re.match(r"^\d+$", sno_raw):
                continue
            sno = int(sno_raw)
            est_qty_raw = cells[1].replace(",", "")
            try:
                est_qty = float(est_qty_raw) if est_qty_raw and est_qty_raw not in ("-", "_") else 0.0
            except (ValueError, TypeError):
                est_qty = 0.0
            spec_text = cells[2]
            work_type = cells[3] if len(cells) > 3 else ""
            short_desc = cells[4] if len(cells) > 4 else ""
            apss_cl_no = cells[5] if len(cells) > 5 else ""
            rate_raw = cells[6].replace(",", "") if len(cells) > 6 else ""
            try:
                rate = float(rate_raw) if rate_raw and rate_raw not in ("-",) else 0.0
            except (ValueError, TypeError):
                rate = 0.0
            uom = cells[7] if len(cells) > 7 else ""
            amount_raw = cells[8].replace(",", "") if len(cells) > 8 else ""
            try:
                amount = float(amount_raw) if amount_raw and amount_raw not in ("-",) else 0.0
            except (ValueError, TypeError):
                amount = 0.0

            # Skip rows with no spec text
            if not spec_text or len(spec_text) < 30:
                continue

            rows.append({
                "sno":         sno,
                "est_qty":     est_qty,
                "spec_text":   spec_text,
                "work_type":   work_type,
                "short_desc":  short_desc,
                "apss_cl_no":  apss_cl_no,
                "rate":        rate,
                "uom":         uom,
                "amount":      amount,
            })
    return rows


def chunk_mep_boq() -> list[dict]:
    """Parse all 380 pages of HOD MEP BoQ; return list of items."""
    items: list[dict] = []
    print(f"  Opening {PDF_PATH.name} ({PDF_PATH.stat().st_size:,} bytes)...")

    with pdfplumber.open(PDF_PATH) as pdf:
        total = len(pdf.pages)
        print(f"  Total pages: {total}")

        for i, page in enumerate(pdf.pages, start=1):
            page_items = parse_boq_table(page)
            for it in page_items:
                it["source_page"] = i
                it["source_pdf"] = "HOD_Towers_BoQ_MEP.pdf"
                # Discipline + citations + scale signals
                parent_disc, sub_disc = classify_discipline(it["work_type"], it["spec_text"])
                it["discipline"] = parent_disc
                it["sub_discipline"] = sub_disc
                it["citations"] = extract_citations(it["spec_text"])
                it["scale_signals"] = extract_scale_signals(it["spec_text"])
                items.append(it)

            if i % 50 == 0:
                print(f"    page {i:>3}/{total} → {len(items)} items so far")

    print(f"  Final: {len(items)} BoQ items from {total} pages")
    return items


def deduplicate(items: list[dict], min_overlap: float = 0.85) -> list[dict]:
    """Reduce near-identical items to one canonical entry. Two items are deduped if
    their first 300 chars overlap ≥ min_overlap (Jaccard on 5-word shingles).
    Returns deduped list; each entry has additional `dedup_count` field."""
    def shingles(text: str, k: int = 5) -> set[str]:
        words = re.findall(r"\w+", text.lower())
        return {" ".join(words[i:i+k]) for i in range(len(words) - k + 1)} if len(words) >= k else {text.lower()}

    canonical: list[dict] = []
    canonical_shingles: list[set[str]] = []

    for it in items:
        sh = shingles(it["spec_text"][:400])
        matched_idx = -1
        for j, cs in enumerate(canonical_shingles):
            if not sh or not cs:
                continue
            inter = sh & cs
            union = sh | cs
            sim = len(inter) / len(union) if union else 0.0
            if sim >= min_overlap:
                matched_idx = j
                break
        if matched_idx >= 0:
            canonical[matched_idx]["dedup_count"] = canonical[matched_idx].get("dedup_count", 1) + 1
            # Track total quantity across duplicates
            canonical[matched_idx]["total_qty"] = canonical[matched_idx].get("total_qty", canonical[matched_idx]["est_qty"]) + it["est_qty"]
        else:
            new = dict(it)
            new["dedup_count"] = 1
            new["total_qty"] = it["est_qty"]
            canonical.append(new)
            canonical_shingles.append(sh)

    return canonical


def main() -> None:
    print("R7.2 — Chunk HOD MEP BoQ (380pp)")
    print("=" * 76)

    items = chunk_mep_boq()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    raw_out = OUT_DIR / "boq_items_raw.jsonl"
    with raw_out.open("w") as f:
        for it in items:
            f.write(json.dumps(it) + "\n")
    print(f"  Raw items → {raw_out} ({raw_out.stat().st_size:,} bytes)")

    print("\n  Deduplicating near-identical items...")
    deduped = deduplicate(items)
    dedup_out = OUT_DIR / "boq_items_dedup.jsonl"
    with dedup_out.open("w") as f:
        for it in deduped:
            f.write(json.dumps(it) + "\n")
    print(f"  Dedup items: {len(deduped)} (from {len(items)})  → {dedup_out}")

    # Stats
    by_discipline: dict[str, int] = {}
    by_sub: dict[str, int] = {}
    total_citations = 0
    for it in deduped:
        by_discipline[it["discipline"]] = by_discipline.get(it["discipline"], 0) + 1
        sub = f"{it['discipline']}/{it['sub_discipline']}"
        by_sub[sub] = by_sub.get(sub, 0) + 1
        total_citations += len(it["citations"])

    print(f"\n  By discipline: {by_discipline}")
    print(f"  By sub-discipline: {by_sub}")
    print(f"  Average citations per item: {total_citations / len(deduped):.2f}" if deduped else "  (no items)")


if __name__ == "__main__":
    main()
