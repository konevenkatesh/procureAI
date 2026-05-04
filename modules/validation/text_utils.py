"""
modules/validation/text_utils.py

Pre-LLM text-shaping helpers for Tier-1 typology scripts. Today this
holds one helper, lifted out of `scripts/tier1_bid_validity_check.py`
where it was first developed (see L26):

    smart_truncate(text, window=3000, keywords=None) → str

Why this lives here and not in `evidence_guard.py`:
- `evidence_guard` is a POST-LLM verifier (does the LLM's evidence
  quote actually exist in the section it claims to come from?).
- `smart_truncate` is a PRE-LLM windower (which slice of the section
  do we let the LLM see in the first place?).
Different concerns, different lifecycle. Keeping them separate keeps
each module's purpose obvious from its name.

When to use which truncator:
- Head + tail (60/40) — short clauses that cluster near section starts
  or ends. PBG ("furnish Performance Security ... 2.5%") and EMD
  ("furnish Bid Security ... 1%") fit this; their script-local
  `_truncate_for_rerank` is fine.
- Keyword-aware window (this module) — short values buried in long
  BDS-rewrite tables or "Instructions to Tenderers" blocks. Bid-validity
  was the first typology to need it; on JA the literal "NINETY (90)
  days" line sat at offset 11,527 of a 13,282-char section, in the
  elided middle of head+tail truncation. See L26 for the failure mode
  and the fix.

Lift candidates: any future typology whose value can be a single row in
a long ITB-rewrite or BDS-summary table — e.g. PVC-Missing (price-
variation formula buried in Schedule), Two-Bid-System indicators, or
contractor-class single-row entries.
"""
from __future__ import annotations

import re


# Default vocabulary for bid-validity. Callers with a different
# typology pass their own `keywords` list. A pattern can be a literal
# substring (e.g. "bid validity") or a regex (e.g. r"validity[^.]{0,50}days").
_DEFAULT_BID_VALIDITY_KEYWORDS = [
    r"bid validity",
    r"bids shall remain valid",
    r"validity period",
    r"remain valid for",
    # Spelled-out day counts (cover 30/60/90/120/180 day common values)
    r"\bninety\b", r"\bsixty\b", r"\bthirty\b", r"\beighty\b",
    r"one hundred twenty", r"hundred eighty",
    # Patterns: "validity ... days" / "days ... validity" within ~50 chars
    r"validity[^.]{0,50}days",
    r"days[^.]{0,50}validity",
]


def smart_truncate(
    text: str,
    window: int = 3000,
    *,
    keywords: list[str] | None = None,
) -> str:
    """Centre a `window`-sized slice on the earliest keyword hit in
    the section. If no keyword matches, fall back to head+tail
    (2400 / 1600) so the LLM still sees both ends.

    Args:
        text:     full section body
        window:   target slice size in chars (default 3000)
        keywords: list of regex/literal patterns to search for; if None
                  uses the bid-validity default vocabulary

    Returns:
        a string of length ≤ `window` (plus elision markers) ready to
        embed in a rerank prompt block

    Returns the full text unchanged if `len(text) <= window`.

    Window=3000 with K=15 candidates ≈ 45K chars in the rerank prompt
    (well within qwen-2.5-72b's 128K context).
    """
    if len(text) <= window:
        return text

    kws = keywords if keywords is not None else _DEFAULT_BID_VALIDITY_KEYWORDS
    text_lower = text.lower()
    earliest = len(text)
    for kw in kws:
        m = re.search(kw, text_lower)
        if m and m.start() < earliest:
            earliest = m.start()

    if earliest < len(text):
        # Centre the window on the earliest keyword hit
        half = window // 2
        start = max(0, earliest - half)
        end   = min(len(text), earliest + half)
        prefix = "[... section start elided ...]\n\n" if start > 0 else ""
        suffix = "\n\n[... section end elided ...]"     if end < len(text) else ""
        return prefix + text[start:end] + suffix

    # No keyword hit — keep both ends so the LLM sees structure
    return text[:2400] + "\n\n[... middle of section elided ...]\n\n" + text[-1600:]
