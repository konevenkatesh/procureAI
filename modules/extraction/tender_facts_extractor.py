"""
modules/extraction/tender_facts_extractor.py

Same pattern as `tender_type_extractor.py` — first NIT section, first
800 chars, OpenAI-compatible LLM call, structured JSON, commit to the
TenderDocument kg_node.

Extracts TWO facts in a SINGLE LLM call:

    estimated_value_cr      project value in crores (1 crore = 1e7 INR)
    integrity_pact_required does the bid doc explicitly require an
                            Integrity Pact / CIPP declaration

A combined call is roughly half the latency / cost of two separate
calls and uses one OpenRouter request slot per document. The model
returns one outer JSON object; we parse it into the per-fact result
dicts the spec calls for.

Public API:
    extract_facts(doc_id, *, llm_fn=None, n_sections=1, max_chars=800) -> dict
    commit_to_kg(doc_id, result) -> dict
    run(doc_id, *, llm_fn=None, commit=True) -> dict
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Callable

import requests

# Repo root on sys.path so absolute imports resolve when run as a script
REPO = Path(__file__).resolve().parent.parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from builder.config import settings

# Reuse the helpers we already audited in tender_type_extractor.py.
# Crossing module boundaries on _-prefixed names is normally bad form,
# but these are package-internal helpers shared across the extraction
# package and not part of the public API.
from modules.extraction.tender_type_extractor import (
    fetch_nit_text,
    _call_llm,
    _attribution_string,
    _rest_get,
    _rest_patch,
    LLMFn,
)


RELIABLE_THRESHOLD: float = 0.85


# ── Prompts ───────────────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are a precise procurement-document fact extractor. Read the "
    "supplied text and return the requested JSON only. Quote evidence "
    "verbatim from the text. Never fabricate values or evidence."
)


def build_user_prompt(nit_text: str) -> str:
    """The two-fact extraction prompt. Uses the same structure as
    tender_type_extractor — strict JSON, verbatim evidence, explicit
    null when not found so the caller can act on absence of evidence
    without ambiguity."""
    return (
        "You are reading a government procurement tender document from "
        "Andhra Pradesh, India.\n\n"
        "Read the following NIT text and extract two facts.\n\n"
        f"Text: {nit_text}\n\n"
        "Return JSON only:\n"
        "{\n"
        "  \"estimated_value\": {\n"
        "    \"value_cr\": float between 0 and 100000 OR null if not found,\n"
        "    \"confidence\": float between 0.0 and 1.0,\n"
        "    \"evidence\": exact quote from text containing the value, or empty string\n"
        "  },\n"
        "  \"integrity_pact\": {\n"
        "    \"required\": true or false or null if no clear signal,\n"
        "    \"confidence\": float between 0.0 and 1.0,\n"
        "    \"evidence\": exact quote from text, or empty string\n"
        "  }\n"
        "}\n\n"
        "Rules for estimated_value:\n"
        "- Look for: 'Estimated Cost', 'Approximate Cost', 'Project Cost', "
        "'Estimated Contract Value', 'ECV', 'PAC', 'Total Project Cost'.\n"
        "- Convert to crores: 1 crore = 10,000,000 (1 cr = 1 Cr = 1.0).\n"
        "  Examples:\n"
        "    'Rs.1,25,49,94,048.00' = 125.50 crores (Indian numbering: 1,25,49,94,048)\n"
        "    'INR 257.51 crore'     = 257.51 crores (already in crores)\n"
        "    'Rs. 350 cr'           = 350.0 crores\n"
        "    'Rs. 50 lakh'          = 0.50 crores (1 lakh = 0.01 crore)\n"
        "- If value not found in the supplied text: value_cr=null, confidence=0.\n\n"
        "Rules for integrity_pact:\n"
        "- required=true if doc explicitly REQUIRES IP / CIPP / Code of Integrity.\n"
        "- required=false if doc explicitly states IP is NOT required.\n"
        "- required=null if no mention OR signal is ambiguous.\n"
        "- Markers: 'Integrity Pact', 'IP clause', 'CIPP', 'Code of Integrity for "
        "Public Procurement', 'integrity agreement'.\n"
        "- A passing reference (e.g. listed in a forms checklist) does NOT count "
        "as 'required' unless the doc clearly mandates it. Use null when unclear."
    )


# ── Response parser ───────────────────────────────────────────────────

def _strip_json_fences(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)
    return text


def _parse_response(raw: str) -> dict:
    """Strip code fences, extract outer {...}, json.loads, validate keys.

    Returns a dict with two sub-dicts:
        estimated_value_cr:
            value_cr:    float | None
            confidence:  float
            evidence:    str
        integrity_pact_required:
            required:    bool | None
            confidence:  float
            evidence:    str
    """
    text = _strip_json_fences(raw)
    if not text.lstrip().startswith("{"):
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            text = m.group(0)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"LLM did not return valid JSON. Raw: {raw[:500]!r}"
        ) from e

    ev = data.get("estimated_value") or {}
    ip = data.get("integrity_pact") or {}

    # Validate value_cr
    raw_val = ev.get("value_cr")
    value_cr: float | None
    if raw_val is None:
        value_cr = None
    else:
        try:
            value_cr = float(raw_val)
            if not (0.0 <= value_cr <= 100_000.0):
                # Out of plausible range — treat as not extracted.
                value_cr = None
        except (TypeError, ValueError):
            value_cr = None

    # Validate value confidence
    try:
        ev_conf = float(ev.get("confidence", 0.0))
    except (TypeError, ValueError):
        ev_conf = 0.0
    ev_conf = max(0.0, min(1.0, ev_conf))
    if value_cr is None:
        ev_conf = 0.0

    # Validate required (must be true/false/null only)
    raw_req = ip.get("required")
    if raw_req is True or raw_req is False:
        required = bool(raw_req)
    elif isinstance(raw_req, str):
        s = raw_req.strip().lower()
        if   s in ("true", "yes"):  required = True
        elif s in ("false", "no"):  required = False
        else:                        required = None
    else:
        required = None

    try:
        ip_conf = float(ip.get("confidence", 0.0))
    except (TypeError, ValueError):
        ip_conf = 0.0
    ip_conf = max(0.0, min(1.0, ip_conf))
    if required is None:
        ip_conf = min(ip_conf, 0.5)  # null result is never high-confidence

    return {
        "estimated_value_cr": {
            "value_cr":  value_cr,
            "confidence": ev_conf,
            "evidence":   (ev.get("evidence") or "").strip(),
            "reasoning":  (ev.get("reasoning") or "").strip(),
        },
        "integrity_pact_required": {
            "required":   required,
            "confidence": ip_conf,
            "evidence":   (ip.get("evidence") or "").strip(),
            "reasoning":  (ip.get("reasoning") or "").strip(),
        },
    }


# ── Public API ────────────────────────────────────────────────────────

def extract_facts(
    doc_id: str,
    *,
    llm_fn: LLMFn | None = None,
    n_sections: int = 1,
    max_chars: int = 800,
) -> dict:
    """Run the combined extraction. Returns:

        {
          "doc_id":  str,
          "nit_text_chars": int,
          "source_section": str,
          "raw_response": str,
          "estimated_value_cr": {
              value_cr, confidence, evidence, reasoning,
              reliable: bool,         # confidence >= 0.85 AND value_cr is not None
          },
          "integrity_pact_required": {
              required, confidence, evidence, reasoning,
              reliable: bool,         # confidence >= 0.85 AND required is not None
          },
        }

    Does NOT write to the database. Call commit_to_kg(doc_id, result)
    afterwards if you want the result persisted on the TenderDocument
    kg_node."""
    nit_text, descriptors = fetch_nit_text(doc_id, n_sections=n_sections, max_chars=max_chars)
    user = build_user_prompt(nit_text)
    raw = _call_llm(SYSTEM_PROMPT, user, llm_fn=llm_fn)
    parsed = _parse_response(raw)

    ev = parsed["estimated_value_cr"]
    ip = parsed["integrity_pact_required"]
    ev["reliable"] = bool(ev["confidence"] >= RELIABLE_THRESHOLD and ev["value_cr"] is not None)
    ip["reliable"] = bool(ip["confidence"] >= RELIABLE_THRESHOLD and ip["required"] is not None)

    return {
        "doc_id":         doc_id,
        "nit_text_chars": len(nit_text),
        "source_section": " | ".join(d["heading"] or "(unknown)" for d in descriptors),
        "raw_response":   raw,
        "estimated_value_cr":      ev,
        "integrity_pact_required": ip,
    }


def commit_to_kg(doc_id: str, result: dict) -> dict:
    """Patch the TenderDocument kg_node with both extracted facts.

    Field naming is parallel to the tender_type fields written by
    `tender_type_extractor.commit_to_kg`:

        estimated_value_cr                       float | null
        estimated_value_cr_reliable              bool
        estimated_value_cr_confidence            float
        estimated_value_cr_evidence              str
        estimated_value_cr_extracted_by          str
        estimated_value_cr_model                 str

        integrity_pact_required                  bool | null
        integrity_pact_required_reliable         bool
        integrity_pact_required_confidence       float
        integrity_pact_required_evidence         str
        integrity_pact_required_extracted_by     str
        integrity_pact_required_model            str

    The legacy unreliable fields (`estimated_value_cr_classified`,
    `estimated_value_reliable=false`, etc.) are NOT touched — they
    remain as audit history of what the regex classifier produced
    before this LLM-based extraction landed."""
    nodes = _rest_get("kg_nodes", {
        "select":    "node_id,properties",
        "doc_id":    f"eq.{doc_id}",
        "node_type": "eq.TenderDocument",
    })
    if not nodes:
        raise ValueError(f"No TenderDocument node for doc_id={doc_id}")
    node = nodes[0]
    new_props = dict(node["properties"] or {})

    ev = result["estimated_value_cr"]
    ip = result["integrity_pact_required"]
    attribution = _attribution_string()
    model = os.environ.get("LLM_MODEL", "")

    # Estimated value (in crores — single source of truth)
    new_props["estimated_value_cr"]            = ev["value_cr"]
    new_props["estimated_value_cr_reliable"]   = bool(ev["reliable"])
    new_props["estimated_value_cr_confidence"] = ev["confidence"]
    new_props["estimated_value_cr_evidence"]   = (ev["evidence"] or "")[:500]
    new_props["estimated_value_cr_extracted_by"] = attribution
    new_props["estimated_value_cr_model"]      = model

    # Integrity pact requirement
    new_props["integrity_pact_required"]            = ip["required"]
    new_props["integrity_pact_required_reliable"]   = bool(ip["reliable"])
    new_props["integrity_pact_required_confidence"] = ip["confidence"]
    new_props["integrity_pact_required_evidence"]   = (ip["evidence"] or "")[:500]
    new_props["integrity_pact_required_extracted_by"] = attribution
    new_props["integrity_pact_required_model"]      = model

    updated = _rest_patch(
        "kg_nodes",
        {"node_id": node["node_id"]},
        {"properties": new_props},
    )
    return updated[0] if updated else {}


def run(
    doc_id: str,
    *,
    llm_fn: LLMFn | None = None,
    commit: bool = True,
) -> dict:
    """Extract + (optionally) commit. Returns full result + `committed` flag."""
    result = extract_facts(doc_id, llm_fn=llm_fn)
    if commit:
        commit_to_kg(doc_id, result)
        result["committed"] = True
    else:
        result["committed"] = False
    return result


# ── CLI ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Extract estimated_value_cr + integrity_pact_required for a doc_id")
    parser.add_argument("doc_id", help="doc_id to extract for")
    parser.add_argument("--no-commit", action="store_true",
                        help="don't write back to kg_nodes")
    args = parser.parse_args()

    result = run(args.doc_id, commit=not args.no_commit)
    print(json.dumps(result, indent=2, default=str))
