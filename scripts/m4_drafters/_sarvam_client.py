"""M4.4 — Sarvam-M Telugu translation client with DPDP pseudonymisation.

Endpoint: POST https://api.sarvam.ai/translate
Auth:     api-subscription-key header (key from env SARVAM_API_KEY)

DPDP pseudonymisation: bidder PII (company name, PAN, GSTIN, mobile,
email, address, signatory) MUST be replaced with anonymous tokens
before any call to the external API. After translation, tokens are
restored. PII never crosses the API boundary; only the template body
text + token placeholders do.

Cache: filesystem-keyed on SHA256(pseudonymised_en_text). Identical
pseudonymised input → cached Telugu output without API call. Cache at
/tmp/sarvam_cache/. Idempotent re-runs cost zero API calls after first.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Iterable

import requests

# ── Constants ────────────────────────────────────────────────────────

API_URL = "https://api.sarvam.ai/translate"
CACHE_DIR = Path("/tmp/sarvam_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Sarvam /translate input limit (per Sarvam docs, ~1000-1500 chars per call;
# we chunk on paragraph boundaries to keep well under limit).
MAX_CHARS_PER_REQUEST = 900


# ── Logging — NEVER include API key ──────────────────────────────────

logger = logging.getLogger("sarvam_client")
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s sarvam %(levelname)s %(message)s"))
    logger.addHandler(h)
    logger.setLevel(logging.INFO)


# ── DPDP pseudonymisation ────────────────────────────────────────────

def _build_pseudonymisation_map(bidder_props: dict, tender_info: dict | None = None
                                 ) -> list[tuple[str, str]]:
    """Build a list of (original_pii, token) substitutions for one
    bidder + tender context. Order matters: longest tokens first so we
    don't accidentally substring-replace inside longer ones (e.g.
    'GST' inside 'GSTIN')."""
    pairs: list[tuple[str, str]] = []

    def add(value: str | None, token: str) -> None:
        if value and isinstance(value, str) and value.strip():
            pairs.append((value.strip(), token))

    # Bidder PII (per DPDP Act §7 purpose limitation)
    add(bidder_props.get("company_name"),               "<COMPANY>")
    add(bidder_props.get("authorized_signatory_name"),  "<SIGNATORY>")
    add(bidder_props.get("authorized_signatory_role"),  "<SIGNATORY_ROLE>")
    add(bidder_props.get("communication_address"),      "<ADDRESS>")
    add(bidder_props.get("email_primary"),              "<EMAIL>")
    add(bidder_props.get("mobile_primary"),             "<MOBILE>")
    add(bidder_props.get("gstin"),                      "<GSTIN>")
    add(bidder_props.get("pan"),                        "<PAN>")
    add(bidder_props.get("registration_certificate_no"),"<REG_CERT>")
    add(bidder_props.get("epf_esi_cert_value"),         "<EPF_ESI>")
    add(bidder_props.get("portal_username"),            "<PORTAL_USER>")

    # Tender NIT (tender-specific identifier — not strictly PII but
    # included for cache stability)
    if tender_info:
        add(tender_info.get("nit_no"), "<NIT>")

    # Sort by descending length so larger strings substitute first
    pairs.sort(key=lambda kv: -len(kv[0]))
    return pairs


def pseudonymise(text: str, pairs: list[tuple[str, str]]) -> str:
    """Replace PII strings with anonymous tokens. Operates left-to-right
    on a copy of the text; no regex magic needed since pairs are
    deduplicated by build_pseudonymisation_map."""
    out = text
    for original, token in pairs:
        if original in out:
            out = out.replace(original, token)
    return out


def restore_pseudonyms(text: str, pairs: list[tuple[str, str]]) -> str:
    """Reverse mapping: tokens → original PII strings."""
    out = text
    for original, token in pairs:
        if token in out:
            out = out.replace(token, original)
    return out


# ── Cache helpers ────────────────────────────────────────────────────

def _cache_key(pseudonymised_en: str, target_lang: str = "te-IN") -> str:
    h = hashlib.sha256(f"{target_lang}|{pseudonymised_en}".encode("utf-8")).hexdigest()
    return h[:32]  # 32-char prefix is plenty


def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.json"


def _cache_get(key: str) -> str | None:
    p = _cache_path(key)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data.get("translated_text")
        except Exception:
            return None
    return None


def _cache_put(key: str, source_en: str, translated_te: str) -> None:
    p = _cache_path(key)
    p.write_text(json.dumps({
        "cache_key":       key,
        "source_en":       source_en,
        "translated_text": translated_te,
        "cached_at":       time.strftime("%Y-%m-%dT%H:%M:%S"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")


# ── Sarvam API call (with retry + chunking) ──────────────────────────

def _api_translate_chunk(text: str, source_lang: str = "en-IN",
                          target_lang: str = "te-IN",
                          max_attempts: int = 4) -> str:
    """Single-chunk translation. Caller must ensure len(text) <= MAX_CHARS_PER_REQUEST."""
    key = os.environ.get("SARVAM_API_KEY")
    if not key:
        raise RuntimeError("SARVAM_API_KEY env var not set")

    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            r = requests.post(
                API_URL,
                json={
                    "input":                 text,
                    "source_language_code":  source_lang,
                    "target_language_code":  target_lang,
                    "mode":                  "formal",
                    "speaker_gender":        "Male",
                },
                headers={"api-subscription-key": key},
                timeout=60,
            )
            r.raise_for_status()
            data = r.json()
            return data.get("translated_text", "") or ""
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
                requests.exceptions.HTTPError) as exc:
            last_exc = exc
            if attempt == max_attempts - 1:
                break
            # 1s, 2s, 4s exponential backoff (Sarvam rate limit guard)
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Sarvam translate failed after {max_attempts} attempts: {last_exc}")


def _chunk_by_paragraphs(text: str, max_chars: int = MAX_CHARS_PER_REQUEST) -> list[str]:
    """Split on paragraph boundaries (double newline). If a single paragraph
    exceeds max_chars, split it on sentence boundaries."""
    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current = ""
    for p in paragraphs:
        # If adding this paragraph would overflow, flush current
        if current and len(current) + len(p) + 2 > max_chars:
            chunks.append(current)
            current = ""
        # If the paragraph itself overflows, split on sentence boundaries
        if len(p) > max_chars:
            sentences = re.split(r"(?<=[.!?])\s+", p)
            for s in sentences:
                if current and len(current) + len(s) + 1 > max_chars:
                    chunks.append(current); current = ""
                current = (current + " " + s).strip() if current else s
        else:
            current = (current + "\n\n" + p).strip() if current else p
    if current:
        chunks.append(current)
    return chunks


def translate_to_telugu(text_en: str, bidder_props: dict,
                         tender_info: dict | None = None) -> dict:
    """Translate English text to Telugu with DPDP pseudonymisation + cache.

    Returns:
      {
        "translated_text":   Telugu Markdown (PII restored)
        "from_cache":        bool — True if any chunk hit cache
        "n_chunks":          int
        "n_api_calls":       int
        "n_cache_hits":      int
      }
    """
    pairs = _build_pseudonymisation_map(bidder_props, tender_info)
    pseudonymised_en = pseudonymise(text_en, pairs)

    chunks = _chunk_by_paragraphs(pseudonymised_en)
    translated_parts: list[str] = []
    n_api = 0
    n_cache = 0

    for chunk in chunks:
        key = _cache_key(chunk)
        cached = _cache_get(key)
        if cached is not None:
            translated_parts.append(cached)
            n_cache += 1
            continue
        translated = _api_translate_chunk(chunk)
        _cache_put(key, chunk, translated)
        translated_parts.append(translated)
        n_api += 1

    # Reassemble + restore PII
    pseudonymised_te = "\n\n".join(translated_parts)
    final_te = restore_pseudonyms(pseudonymised_te, pairs)

    return {
        "translated_text": final_te,
        "from_cache":      (n_api == 0),
        "n_chunks":        len(chunks),
        "n_api_calls":     n_api,
        "n_cache_hits":    n_cache,
    }


# ── DPDP audit verification helper ───────────────────────────────────

def verify_no_pii_in_text(text: str, bidder_props: dict) -> list[str]:
    """Return list of pseudonymisation tokens that leaked through (should
    always be empty after restore_pseudonyms; this is a unit-test aid)."""
    pairs = _build_pseudonymisation_map(bidder_props)
    leaked = []
    for original, token in pairs:
        if token in text:
            leaked.append(token)
    return leaked
