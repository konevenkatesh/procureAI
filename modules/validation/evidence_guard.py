"""
modules/validation/evidence_guard.py

Hallucination guard for Tier-1 LLM extractions. Verifies that the
LLM-returned evidence quote actually exists in the chosen-candidate
section text BEFORE any kg_node is materialised. See L24 in
LESSONS_LEARNED.md.

Why: even with the prompt rule "Evidence MUST be an exact substring",
the LLM can fabricate a plausible-sounding quote when its assigned
section has no answer to give. On Vizag (which has no EMD-percentage
line in any of its 5 source volumes), an early run of EMD extraction
returned the literal wording from JA — a different document. A
ValidationFinding could have shipped to the CAG audit with verbatim-
looking but fabricated evidence.

How: two-stage check, stdlib-only.
    1. Substring match on aggressively-normalised text  → score 100
    2. difflib SequenceMatcher partial-ratio fallback (sliding window
       of len(evidence) over full_text)                  → score = ratio×100
       PASS if score >= threshold (default 85).

rapidfuzz would expose ``fuzz.partial_ratio`` directly with the same
semantics, but it's not installed in this venv. ``difflib`` is stdlib,
no new dependency. If rapidfuzz is later added to requirements, the
helper can be one-line-swapped without changing the public API.

Public API — exactly one function:

    verify_evidence_in_section(evidence, full_text, *, threshold=85)
        → (passed: bool, score: int, method: str)

The caller MUST discard the LLM extraction (force found=False, skip
materialise) when passed=False — that signals the LLM fabricated the
quote and we have no real evidence to record.

Usage pattern (mirrors scripts/tier1_pbg_check.py):

    from modules.validation.evidence_guard import verify_evidence_in_section

    passed, score, method = verify_evidence_in_section(evidence, section["full_text"])
    print(f"  evidence_verified : {passed}  (score={score}, method={method})")
    if not passed:
        print("  HALLUCINATION_DETECTED — discarding extraction")
        found   = False
        section = None

    # On materialise, persist these as audit fields:
    finding_props = {
        ...,
        "evidence_in_source":    passed,
        "evidence_verified":     passed,    # same value today; reserved for
                                            # future "human-confirmed" override
        "evidence_match_score":  score,
        "evidence_match_method": method,
    }
"""
from __future__ import annotations

import difflib
import re


# Aggressive normalisation: drop markdown markers, drop HTML <br>,
# collapse whitespace. Both sides of the match pass through this so
# superficial markup or whitespace differences in the LLM quote do
# not trigger HALLUCINATION_DETECTED.
_HTML_BR  = re.compile(r"<br\s*/?>", re.IGNORECASE)
_MD_NOISE = re.compile(r"[\*\_\|\\`]+")
_WS       = re.compile(r"\s+")
# pymupdf4llm-style markdown converters escape periods, dashes, and
# percent signs in body text. The LLM strips these in its evidence
# quotes, so the source side has e.g. "E\.M\.D\." while the LLM has
# "E.M.D.". Unescape them BEFORE the generic _MD_NOISE pass so the
# remaining content lines up cleanly for the substring fast-path.
_BACKSLASH_DOT   = re.compile(r"\\\.")
_BACKSLASH_DASH  = re.compile(r"\\-")
_BACKSLASH_PCT   = re.compile(r"\\%")


def _normalise_for_match(s: str) -> str:
    """Lowercase + unescape markdown-converter escapes + drop markdown
    markers + drop <br> + collapse whitespace."""
    s = (s or "").lower()
    # Unescape markdown-converter escapes first
    s = _BACKSLASH_DOT.sub(".", s)
    s = _BACKSLASH_DASH.sub("-", s)
    s = _BACKSLASH_PCT.sub("%", s)
    s = _HTML_BR.sub(" ", s)
    s = _MD_NOISE.sub(" ", s)
    s = _WS.sub(" ", s).strip()
    return s


# Evidence cap for the partial-ratio fallback — keeps the O(N×M)
# sliding window bounded. The LLM evidence quotes in this project are
# typically 50–250 chars; 500 is a generous cap. Pathological 5000-char
# evidence would otherwise blow runtime past a few seconds.
_EVIDENCE_MATCH_CAP = 500
# PASS threshold — matches the original spec (fuzz.partial_ratio >= 85).
_DEFAULT_THRESHOLD  = 85


def verify_evidence_in_section(
    evidence: str,
    full_text: str,
    *,
    threshold: int = _DEFAULT_THRESHOLD,
) -> tuple[bool, int, str]:
    """Verify an LLM evidence quote against the section's full text.

    Args:
        evidence:  the verbatim quote returned by the LLM
        full_text: the body text of the chosen candidate section
        threshold: pass threshold for the partial-ratio fallback (0-100)

    Returns:
        (passed, score, method)
            passed:  True if evidence is locatable in full_text
            score:   0-100 (100 for substring hit; ratio×100 for partial)
            method:  "substring" | "partial_ratio" | "no_match" | "empty"

    The caller MUST discard the extraction when passed=False.
    """
    if not evidence or not full_text:
        return False, 0, "empty"

    n_ev   = _normalise_for_match(evidence)
    n_full = _normalise_for_match(full_text)
    if not n_ev or not n_full:
        return False, 0, "empty"

    # Stage 1 — cheap substring check on normalised text
    if n_ev in n_full:
        return True, 100, "substring"

    # Stage 2 — difflib partial-ratio with a coarse-then-fine sliding
    # window. Cap evidence to keep the inner loop bounded.
    short = n_ev[:_EVIDENCE_MATCH_CAP]
    if len(short) >= len(n_full):
        ratio = difflib.SequenceMatcher(None, short, n_full).ratio()
        score = int(ratio * 100)
        return score >= threshold, score, ("partial_ratio" if score >= threshold else "no_match")

    win_len = len(short)
    # Coarse pass — stride is 1/4 of the window so we don't miss
    # the right neighbourhood
    coarse_stride = max(1, win_len // 4)
    coarse_winner = -1
    best_ratio = 0.0
    for i in range(0, len(n_full) - win_len + 1, coarse_stride):
        window = n_full[i:i + win_len]
        r = difflib.SequenceMatcher(None, short, window).ratio()
        if r > best_ratio:
            best_ratio = r
            coarse_winner = i
    # Fine pass — refine a window-width neighbourhood around the
    # coarse winner with stride 1
    if coarse_winner >= 0:
        lo = max(0, coarse_winner - coarse_stride)
        hi = min(len(n_full) - win_len + 1, coarse_winner + coarse_stride + 1)
        for i in range(lo, hi):
            window = n_full[i:i + win_len]
            r = difflib.SequenceMatcher(None, short, window).ratio()
            if r > best_ratio:
                best_ratio = r

    score = int(best_ratio * 100)
    return score >= threshold, score, ("partial_ratio" if score >= threshold else "no_match")
