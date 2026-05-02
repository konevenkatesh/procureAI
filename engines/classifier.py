"""
engines/classifier.py

TenderClassifier — keyword-based, fast, no-LLM classifier that reads raw
tender document text and produces a structured `TenderClassification`.

The classifier is intentionally rule-based:
  - Each type (Works / Goods / Consultancy / EPC) has a keyword bag.
  - Match counts produce a score; highest score wins, with tie-break rules.
  - Estimated value is extracted via regex over rupee patterns.
  - Cover system, procurement method, AP-tender flag, and funding source
    are detected via additional keyword heuristics.
  - Confidence reflects keyword density AND structured-data presence.

The classifier intentionally returns `needs_human_confirmation=True` when
confidence < 0.75. Callers should escalate ambiguous cases to a human
reviewer or to an LLM-backed classifier.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────────────────
# Output model
# ─────────────────────────────────────────────────────────────────────────────

PrimaryType  = Literal["Works", "Goods", "Consultancy", "EPC", "Services", "Unknown"]
ProcMethod   = Literal["Open", "Limited", "Single", "Reverse", "QCBS", "QBS", "Unknown"]
CoverSystem  = Literal["Single", "Two", "Three", "Unknown"]
FundingSrc   = Literal["state", "central", "world_bank", "adb", "jica", "multilateral", "unknown"]


class TenderClassification(BaseModel):
    primary_type: PrimaryType
    procurement_method: ProcMethod
    cover_system: CoverSystem
    estimated_value: float | None = None
    duration_months: int | None = None
    department: str | None = None
    is_ap_tender: bool = False
    funding_source: FundingSrc = "unknown"
    special_flags: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    needs_human_confirmation: bool


# ─────────────────────────────────────────────────────────────────────────────
# Keyword signals
# ─────────────────────────────────────────────────────────────────────────────

WORKS_KEYWORDS = [
    "civil engineering", "construction of", "bill of quantities",
    "schedule of rates", "boq", "works contract",
    "earthwork", "pcc", "rcc", "structural steel", "form of bid",
    "site of work", "engineer-in-charge",
]

GOODS_KEYWORDS = [
    "supply of", "procurement of", "gem", "rate contract",
    "purchase of", "specifications for", "delivery period",
    "stores procurement", "indent", "supplier",
]

CONSULTANCY_KEYWORDS = [
    "qcbs", "qbs", "lcs", "fbs", "pmc",
    "technical proposal", "financial proposal", "rfp",
    "request for proposal", "terms of reference", "tor",
    "key personnel", "man-months", "consulting services",
    "consulting firm", "consultant", "deliverables",
]

EPC_KEYWORDS = [
    "epc", "engineering procurement construction",
    "lump sum", "turnkey", "single responsibility",
    "design build operate", "design and build", "dbot",
    "dbfot", "concessionaire", "concession agreement",
]

AP_KEYWORDS = [
    "apeprocurement.gov.in", "go ms", "ap state", "andhra pradesh",
    "apss", "reverse tendering", "ap pwd", "apcrda", "agicl",
    "amaravati", "vizag", "vijayawada", "tirupati", "kakinada",
    "judicial preview", "telugu", "tahsildar",
]

# NOTE: short tokens like "ida", "adb" need WORD-BOUNDARY matching to avoid
# false positives ("validity" contains "ida"). Use compiled regexes here.
MULTILATERAL_PATTERNS: dict[str, list[re.Pattern]] = {
    "world_bank": [
        re.compile(r"\bworld\s+bank\b", re.I),
        re.compile(r"\bIBRD\b"),
        re.compile(r"\bIDA\b"),
    ],
    "jica": [
        re.compile(r"\bJICA\b"),
        re.compile(r"\bjapan\s+international\b", re.I),
    ],
    "adb": [
        re.compile(r"\bADB\b"),
        re.compile(r"\basian\s+development\s+bank\b", re.I),
    ],
}

PROC_METHOD_PATTERNS = [
    (re.compile(r"\bqcbs\b",                                re.I), "QCBS"),
    (re.compile(r"\bqbs\b",                                 re.I), "QBS"),
    (re.compile(r"reverse\s+(?:tender|auction|bidd?ing)",   re.I), "Reverse"),
    (re.compile(r"open\s+tender",                           re.I), "Open"),
    (re.compile(r"limited\s+tender",                        re.I), "Limited"),
    (re.compile(r"single\s+tender",                         re.I), "Single"),
]

# Value extraction — match crore, lakh, and bare-rupee patterns
VALUE_PATTERNS = [
    # "Rs. 350 crore" / "₹350 crores" / "INR 350 cr"
    (re.compile(r"(?:rs\.?|inr|₹|rupees?)\s*([\d,]+(?:\.\d+)?)\s*(?:crore|cr\.?|crores)", re.I), 1_00_00_000),
    # "Rs. 50 lakh" / "₹50 lakhs"
    (re.compile(r"(?:rs\.?|inr|₹|rupees?)\s*([\d,]+(?:\.\d+)?)\s*(?:lakh|lakhs|lacs?)", re.I), 1_00_000),
    # bare crore / lakh without Rs prefix (less reliable, lower priority)
    (re.compile(r"\b([\d,]+(?:\.\d+)?)\s*(?:crore|crores)\b", re.I), 1_00_00_000),
    (re.compile(r"\b([\d,]+(?:\.\d+)?)\s*(?:lakh|lakhs|lacs?)\b", re.I), 1_00_000),
    # "Rs. 5,00,00,000" — Indian-style grouping
    (re.compile(r"(?:rs\.?|inr|₹)\s*([\d,]+)(?:\.\d+)?(?!\s*(?:crore|lakh|cr|lac))", re.I), 1),
]

DURATION_PATTERNS = [
    re.compile(r"(?:contract|completion|construction|implementation|project)\s+(?:period|duration|term)\s*(?:of|is|=|:)?\s*(\d+)\s*months?", re.I),
    re.compile(r"(?:contract|completion|construction|implementation|project)\s+(?:period|duration|term)\s*(?:of|is|=|:)?\s*(\d+)\s*years?", re.I),
    re.compile(r"\b(\d+)\s*months?\s+(?:from|after)\s+", re.I),
    re.compile(r"\b(\d+)\s*years?\s+(?:from|after)\s+", re.I),
]

# Cover-system signals
COVER_TWO   = re.compile(r"two[\s-]+(?:cover|envelope|bid|stage)", re.I)
COVER_THREE = re.compile(r"three[\s-]+(?:cover|envelope|stage)|pre[\s-]?qualification", re.I)
TECH_BID    = re.compile(r"technical\s+(?:bid|proposal|envelope)", re.I)
FIN_BID     = re.compile(r"financial\s+(?:bid|proposal|envelope)", re.I)

# Special flags
SPECIAL_FLAG_PATTERNS = {
    "has_jv":                 re.compile(r"\bjoint\s+venture\b|\bJV\b", re.I),
    "has_consortium":         re.compile(r"\bconsortium\b", re.I),
    "has_integrity_pact":     re.compile(r"integrity\s+pact", re.I),
    "has_reverse_tendering":  re.compile(r"reverse\s+(?:tender|auction)", re.I),
    "has_judicial_preview":   re.compile(r"judicial\s+preview", re.I),
    "has_corrigendum":        re.compile(r"\bcorrigend(?:um|a)\b", re.I),
    "has_evaluation_form":    re.compile(r"evaluation\s+statement|statement\s+(?:of|for)\s+evaluation", re.I),
}


# ─────────────────────────────────────────────────────────────────────────────
# Internal scoring helper
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class _Score:
    works: int = 0
    goods: int = 0
    consultancy: int = 0
    epc: int = 0


def _normalise(text: str) -> str:
    """Lowercase + collapse hyphens/underscores to spaces so 'lump-sum' matches
    keyword 'lump sum', 'two-cover' matches 'two cover', etc."""
    t = text.lower()
    t = re.sub(r"[-_/]", " ", t)
    return t


def _count_hits(text_lower: str, keywords: list[str]) -> int:
    return sum(1 for kw in keywords if kw in text_lower)


def _score_text(text: str) -> _Score:
    t = _normalise(text)
    return _Score(
        works=_count_hits(t, WORKS_KEYWORDS),
        goods=_count_hits(t, GOODS_KEYWORDS),
        consultancy=_count_hits(t, CONSULTANCY_KEYWORDS),
        epc=_count_hits(t, EPC_KEYWORDS),
    )


def _pick_primary(s: _Score) -> tuple[PrimaryType, int]:
    """Pick the dominant type.

    EPC subsumes Works (Works + design + procurement + single responsibility),
    so when EPC has ≥ 2 hits we prefer EPC over Works regardless of Works count.
    Consultancy outranks otherwise on raw count.
    """
    # EPC override — explicit "epc" keyword + at least one other EPC signal
    if s.epc >= 2 and s.consultancy <= s.epc:
        return "EPC", s.epc

    candidates = [("Consultancy", s.consultancy), ("EPC", s.epc),
                  ("Works", s.works), ("Goods", s.goods)]
    candidates.sort(key=lambda kv: kv[1], reverse=True)
    top_label, top_score = candidates[0]
    if top_score == 0:
        return "Unknown", 0
    return top_label, top_score


def _detect_proc_method(text: str) -> ProcMethod:
    for pat, label in PROC_METHOD_PATTERNS:
        if pat.search(text):
            return label
    return "Unknown"


def _detect_cover_system(text: str) -> CoverSystem:
    if COVER_THREE.search(text):
        return "Three"
    if COVER_TWO.search(text):
        return "Two"
    if TECH_BID.search(text) and FIN_BID.search(text):
        return "Two"
    return "Single"


# ── Label-aware value extraction ──
# Within 50 characters BEFORE a candidate monetary value, look for:
#   * SKIP labels   — value is an EMD / security amount, not the contract value
#   * PREFER labels — value IS the contract / tender / project cost
# A "preferred" hit is returned immediately. "Skip" hits are excluded entirely.
# "Neutral" hits (no label nearby) are kept as fallback if nothing preferred is
# found.
_VALUE_SKIP_LABELS = re.compile(
    r"\b(?:EMD|earnest\s+money|bid\s+security|performance\s+(?:security|guarantee)|"
    r"retention|liquidated\s+damages|deposit|registration\s+fee|tender\s+fee|"
    r"liquid\s+assets?|cash\s+flow|credit\s+lines?|working\s+capital|line\s+of\s+credit|"
    r"insurance|premium|cover\s+fee|net\s+worth|annual\s+turnover|min(?:imum)?\s+turnover|"
    r"penalty|fine|interest|royalty|seignorage)\b",
    re.IGNORECASE,
)
_VALUE_PREFER_LABELS = re.compile(
    r"\b(?:estimated\s+(?:cost|value)|contract\s+value|tender\s+value|project\s+cost|"
    r"total\s+(?:cost|value)|estimated\s+contract\s+value|estimate\s+amount|"
    r"value\s+of\s+(?:the\s+)?(?:contract|works|project|tender))\b",
    re.IGNORECASE,
)
_LOOKBEHIND_CHARS = 50


def _label_around(text: str, start: int) -> str:
    """Return up to 50 chars of text before `start` (lower-cased)."""
    lo = max(0, start - _LOOKBEHIND_CHARS)
    return text[lo:start].lower()


def _extract_value(text: str) -> float | None:
    """Extract the most likely tender value.

    Collects ALL candidates whose 50-char left context contains a PREFER
    label (estimated cost / contract value / project cost / etc.) and
    returns the MAXIMUM. Rationale: in tender documents the actual contract
    value is typically the largest "estimated value" mentioned — smaller
    PREFER-labeled hits are usually regulatory thresholds (e.g. "for
    contracts with estimated contract value exceeding Rs.100 lakhs ...")
    that quote a different value than the actual contract.

    Candidates whose left context contains a SKIP label
    (EMD, bid security, performance security, retention, liquid assets,
    cash flow, annual turnover, etc.) are dropped entirely.

    Returns None if no PREFER-labeled value is found — we'd rather report
    "value unknown" than mis-pick a non-contract value.
    Sanity-checks each value to lie in [Rs.10K, Rs.10,000 cr].
    """
    candidates: list[float] = []
    for pat, multiplier in VALUE_PATTERNS:
        for m in pat.finditer(text):
            raw = m.group(1).replace(",", "")
            try:
                v = float(raw) * multiplier
            except ValueError:
                continue
            if not (10_000 <= v <= 1_00_00_00_00_000):
                continue
            label_window = _label_around(text, m.start())
            if _VALUE_SKIP_LABELS.search(label_window):
                continue
            if _VALUE_PREFER_LABELS.search(label_window):
                candidates.append(v)
    return max(candidates) if candidates else None


def _extract_duration_months(text: str) -> int | None:
    for pat in DURATION_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        try:
            n = int(m.group(1))
        except ValueError:
            continue
        # If the regex matched a "years" pattern, multiply.
        if "year" in pat.pattern:
            n *= 12
        # Sanity: 1 month to 20 years
        if 1 <= n <= 240:
            return n
    return None


def _extract_department(text: str) -> str | None:
    candidates = [
        "GVSCCL", "APCRDA", "AGICL", "MAUD", "MA&UD",
        "I&CAD", "ICAD", "T,R&B", "TR&B", "PR&RD", "Panchayat Raj",
        "Water Resources", "TMC", "VMC", "NREDCAP", "APRWSEA",
        "Public Works Department", "PWD",
    ]
    t_lower = text.lower()
    for cand in candidates:
        if cand.lower() in t_lower:
            return cand
    return None


def _detect_funding(text: str) -> FundingSrc:
    """Detect funding source. Multilateral checks use word-boundary regex
    to avoid false positives like "validity" containing "ida"."""
    for src in ("world_bank", "jica", "adb"):
        if any(p.search(text) for p in MULTILATERAL_PATTERNS[src]):
            return src
    t = text.lower()
    if any(kw in t for kw in (
        "centrally sponsored", "central government", "ministry of",
        "government of india", "niti aayog", "niti ayog",
    )):
        return "central"
    if any(kw in t for kw in (
        "government of andhra pradesh", "ap state", "state-funded",
        "state of andhra pradesh",
    )):
        return "state"
    return "unknown"


def _detect_ap(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in AP_KEYWORDS)


def _extract_special_flags(text: str) -> list[str]:
    return [name for name, pat in SPECIAL_FLAG_PATTERNS.items() if pat.search(text)]


def _compute_confidence(
    primary_score: int,
    primary_type: PrimaryType,
    value: float | None,
    cover: CoverSystem,
    proc_method: ProcMethod,
    second_score: int,
) -> float:
    """Confidence policy:
       - 3+ keywords + value: 0.90+
       - 2 keywords or value-only: 0.75-0.89
       - 1 keyword or conflicting top-2: < 0.75
       - top-1 vs top-2 tied or near-tied → penalise
    """
    if primary_type == "Unknown":
        return 0.0

    base = min(0.6 + 0.10 * primary_score, 0.95)

    if value is not None:
        base += 0.05
    if cover != "Single" or proc_method != "Unknown":
        base += 0.03

    # Penalise when 2nd-place type is close
    if second_score >= primary_score - 1 and second_score >= 2:
        base -= 0.20

    return max(0.0, min(round(base, 2), 1.0))


# ─────────────────────────────────────────────────────────────────────────────
# Public class
# ─────────────────────────────────────────────────────────────────────────────

class TenderClassifier:
    """Keyword-based tender classifier.

    Usage:
        result = TenderClassifier().classify(open(path).read())
        if result.needs_human_confirmation:
            # escalate to LLM or human reviewer
            ...
    """

    HUMAN_CONFIRMATION_THRESHOLD = 0.75

    def classify(self, document_text: str) -> TenderClassification:
        if not document_text or not document_text.strip():
            return TenderClassification(
                primary_type="Unknown",
                procurement_method="Unknown",
                cover_system="Unknown",
                confidence=0.0,
                needs_human_confirmation=True,
            )

        scores = _score_text(document_text)
        primary, primary_score = _pick_primary(scores)

        # second-best for confidence calc
        all_scores = sorted(
            [scores.works, scores.goods, scores.consultancy, scores.epc],
            reverse=True,
        )
        second_score = all_scores[1] if len(all_scores) > 1 else 0

        proc_method = _detect_proc_method(document_text)
        cover = _detect_cover_system(document_text)
        value = _extract_value(document_text)
        duration = _extract_duration_months(document_text)
        department = _extract_department(document_text)
        is_ap = _detect_ap(document_text)
        funding = _detect_funding(document_text)
        flags = _extract_special_flags(document_text)

        confidence = _compute_confidence(
            primary_score=primary_score,
            primary_type=primary,
            value=value,
            cover=cover,
            proc_method=proc_method,
            second_score=second_score,
        )

        return TenderClassification(
            primary_type=primary,
            procurement_method=proc_method,
            cover_system=cover,
            estimated_value=value,
            duration_months=duration,
            department=department,
            is_ap_tender=is_ap,
            funding_source=funding,
            special_flags=flags,
            confidence=confidence,
            needs_human_confirmation=confidence < self.HUMAN_CONFIRMATION_THRESHOLD,
        )
