"""
modules/validation/llm_client.py

Single OpenRouter-compatible LLM call wrapper used by every Tier-1
typology script (PBG, EMD, future Integrity Pact / Judicial Preview /
etc.). Lifted out of the script-level duplicates in
`scripts/tier1_pbg_check.py` and `scripts/tier1_emd_check.py` so:

  - the OpenRouter empty-choices retry logic lives in one place
    (the original transient was observed mid-batch on Vizag —
    upstream returned 200 with `choices=[]`, crashing
    `resp.choices[0]`)
  - changing the model / API endpoint / max_tokens default is a
    one-file edit
  - future async / batch / fallback-model support attaches here

Public API:

    call_llm(system_prompt, user_prompt,
             *, model=None, max_tokens=1024) → str
        Returns the assistant message content (string). Raises
        `RuntimeError("OpenRouter empty choices (after retry)")` if
        the upstream returns no choices on two consecutive attempts.

    parse_llm_json(raw) → dict
        Parse the LLM's JSON response, robust to common malformed-JSON
        patterns surfaced by the L35 strict-quote prompt directive.
        Strips ```json fences, extracts the {…} body if there's
        leading/trailing prose, and falls back to escape-sanitisation
        when `json.loads` rejects on invalid backslash sequences (e.g.
        markdown's `\\.` or `\\(` that the LLM faithfully reproduces
        from source per the L35 verbatim-quote rule).

Module-level constants:
    MODEL    = LLM_MODEL env var (default: qwen/qwen-2.5-72b-instruct)
    BASE_URL = LLM_BASE_URL env var (default: openrouter.ai)
    API_KEY  = LLM_API_KEY env var (REQUIRED — raises on import if missing)

The OpenAI Python SDK is the underlying client. OpenRouter is
OpenAI-compatible at the chat-completions level. The X-Title /
HTTP-Referer headers identify our app on the OpenRouter dashboard
(useful for billing / rate-limit attribution).
"""
from __future__ import annotations

import json
import os
import re
import time


MODEL    = os.getenv("LLM_MODEL",    "qwen/qwen-2.5-72b-instruct")
BASE_URL = os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
API_KEY  = os.getenv("LLM_API_KEY")

if not API_KEY:
    # Fail loud at import time — every Tier-1 typology script depends
    # on this. Better to crash on `from modules.validation.llm_client
    # import call_llm` than silently 401 later.
    raise RuntimeError(
        "LLM_API_KEY is not set in the environment. Source .env "
        "(`set -a && . ./.env && set +a`) before running any "
        "Tier-1 typology script."
    )


# OpenRouter dashboard attribution — does not affect routing or cost.
_EXTRA_HEADERS = {
    "HTTP-Referer": "https://github.com/konevenkatesh/procureAI",
    "X-Title":      "AP Procurement Validator",
}

# How long to wait between the first empty-choices response and the
# retry. Two seconds is enough that transient rate-limit / model-cold-
# start hiccups have cleared.
_RETRY_SLEEP_S = 2


def _build_kwargs(system_prompt: str, user_prompt: str,
                  model: str, max_tokens: int) -> dict:
    return dict(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        temperature=0.0,
        max_tokens=max_tokens,
        extra_headers=_EXTRA_HEADERS,
    )


def call_llm(
    system_prompt: str,
    user_prompt: str,
    *,
    model: str | None = None,
    max_tokens: int = 1024,
) -> str:
    """OpenRouter-compatible LLM call. Returns the content string.

    Includes one retry on empty `resp.choices` after a 2-second sleep.
    Raises `RuntimeError("OpenRouter empty choices (after retry)")` if
    the second attempt also returns no choices.

    Args:
        system_prompt: instructions / role for the assistant
        user_prompt:   the user message
        model:         override module-level MODEL (default: env LLM_MODEL)
        max_tokens:    output cap for this call (default 1024 — pick lower
                       for short JSON responses, higher for verbose extraction)

    Returns:
        the assistant message content as a string (may be empty if the
        model returned an empty content but populated choices — that's
        a model behaviour, not an upstream failure)
    """
    from openai import OpenAI
    client = OpenAI(base_url=BASE_URL.rstrip("/"), api_key=API_KEY)
    kwargs = _build_kwargs(
        system_prompt, user_prompt,
        model=model or MODEL,
        max_tokens=max_tokens,
    )
    resp = client.chat.completions.create(**kwargs)
    if not resp.choices or len(resp.choices) == 0:
        print(
            f"  [llm_client] empty choices on first attempt — "
            f"retrying once after {_RETRY_SLEEP_S}s"
        )
        time.sleep(_RETRY_SLEEP_S)
        resp = client.chat.completions.create(**kwargs)
        if not resp.choices or len(resp.choices) == 0:
            raise RuntimeError("OpenRouter empty choices (after retry)")
    return resp.choices[0].message.content or ""


# Valid JSON escape characters per RFC-8259: " \ / b f n r t u
# Anything else after a backslash is a malformed escape that
# `json.loads` rejects.
_JSON_VALID_ESCAPE_RE = re.compile(r'\\(?!["\\/bfnrtu])')

# Run-based detector — matches a run of N consecutive backslashes
# followed by ONE non-backslash character. Used to repair the more
# difficult case where the LLM emits an EVEN-count backslash run
# (e.g. `\\-`, two literal backslashes + dash) intending it as an
# escape for a markdown-escaped char. JSON parses `\\` as a single
# literal backslash and then sees `-` as a normal char, which IS
# valid — but if the LLM was inconsistent and emitted `\\-` for
# `\-` only sometimes (alongside `\(` for `\(` other times), the
# single-pass `_JSON_VALID_ESCAPE_RE` substitution doubles the
# wrong backslash and produces `\\\\\-` (odd-count + invalid char),
# which still fails. The run-based replacer below sees the full
# context: if the run length is ODD and the next char is NOT a
# valid escape char, append one more backslash so the run is even
# and the trailing char becomes literal. If the run length is EVEN,
# leave it alone (already parses as N/2 literal backslashes plus a
# normal char).
_JSON_BS_RUN_RE = re.compile(r'(\\+)([^\\])')


def _fix_invalid_json_escapes(text: str) -> str:
    """Repair invalid `\\X` JSON escape sequences emitted by LLMs that
    faithfully copy markdown-escaped punctuation (`\\.`, `\\-`, `\\(`,
    `\\)`) without first JSON-escaping each backslash.

    Logic per backslash run:
      • run-length even, next char anything    → already valid (each
                                                  pair `\\` decodes to
                                                  `\\`, next char literal)
      • run-length odd,  next char in valid set → already valid escape
      • run-length odd,  next char NOT in set   → INVALID; append one
                                                  more backslash so the
                                                  run becomes even and
                                                  the next char is
                                                  literal (which is what
                                                  the LLM intended).

    This is run-aware unlike `_JSON_VALID_ESCAPE_RE.sub(r'\\\\', text)`
    which sees each backslash in isolation and over-escapes consecutive
    runs.
    """
    valid_escape_chars = set('"\\/bfnrtu')

    def _repair(m: re.Match) -> str:
        bs   = m.group(1)
        nxt  = m.group(2)
        n    = len(bs)
        if n % 2 == 0:
            return m.group(0)              # already valid
        if nxt in valid_escape_chars:
            return m.group(0)              # already valid escape
        return ('\\' * (n + 1)) + nxt      # add one bs to make run even

    return _JSON_BS_RUN_RE.sub(_repair, text)


def parse_llm_json(raw: str) -> dict:
    """Parse an LLM JSON response, robust to common malformed-JSON
    patterns surfaced by the L35 strict-quote prompt directive.

    Why this lives here and not in evidence_guard.py: every Tier-1
    typology script that asks the LLM for structured output hits
    this same parsing problem. AP source markdown often contains
    `\\.` (escaped period in markdown), `\\(` and `\\)` (escaped
    parentheses), and similar — valid markdown but invalid JSON
    escape sequences. When the L35 prompt directive instructs the
    LLM to "preserve markdown formatting verbatim", those escapes
    leak into its output and break `json.loads`.

    Sequence:
      1. Strip ```json / ``` fences if present.
      2. Extract the {…} body if there's leading/trailing prose.
      3. Try `json.loads`; if it succeeds, return.
      4. If it raises `JSONDecodeError`, re-write any backslash NOT
         followed by a valid JSON escape character with a doubled
         backslash. The original character is preserved as a
         literal — when the resulting Python string round-trips
         through L24's normaliser, the literal backslash + the
         following character matches the source text byte-for-byte.

    Returns the parsed dict (caller is responsible for the schema
    expected — this helper is shape-agnostic).

    Raises `json.JSONDecodeError` if the response is malformed
    beyond the escape-sanitiser's reach (e.g. unbalanced braces,
    missing quotes around keys). Callers that want a softer failure
    can catch that and treat as "LLM produced unparseable output —
    no extraction" — equivalent to the L35 UNVERIFIED branch.
    """
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)
    if not text.startswith("{"):
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            text = m.group(0)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Two failure modes to repair before re-parsing:
    #   (a) Invalid `\X` escapes leaked from L35 strict-quote markdown
    #       reproduction (`\(`, `\)`, `\.`, `\-`).
    #   (b) Literal control characters (TAB, LF, CR) inside string
    #       values. These appear when the LLM faithfully copies
    #       markdown table cells (which are TAB-delimited in our
    #       processed_md output) into the evidence quote per L35.
    # Pass 1: run-aware repair (handles mixed `\-` / `\\-` patterns
    # produced by inconsistent LLM markdown copy-out).
    sanitized = _fix_invalid_json_escapes(text)
    try:
        return json.loads(sanitized, strict=False)
    except json.JSONDecodeError:
        pass

    # Pass 2: legacy single-backslash regex (kept as a safety net in
    # case the run-aware pass doesn't catch everything — e.g. for the
    # original `\.` / `\-` / `\(` / `\)` patterns where there's no
    # preceding backslash).
    sanitized2 = _JSON_VALID_ESCAPE_RE.sub(r'\\\\', text)
    try:
        # strict=False relaxes the JSON spec to allow control chars
        # (chr(0x00)-chr(0x1F)) inside string values. The parsed
        # Python str preserves the literal control character — when
        # round-tripped through L24's normaliser, it matches the
        # source byte-for-byte.
        return json.loads(sanitized2, strict=False)
    except json.JSONDecodeError:
        # Final fallback: also try without the escape-sanitisation
        # (in case strict=False is enough on its own).
        return json.loads(text, strict=False)
