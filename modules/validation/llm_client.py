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

Public API — exactly one function:

    call_llm(system_prompt, user_prompt,
             *, model=None, max_tokens=1024) → str

Returns the assistant message content (string). Raises
`RuntimeError("OpenRouter empty choices (after retry)")` if the
upstream returns no choices on two consecutive attempts.

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

import os
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
