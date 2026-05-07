"""
modules/extraction/project_brief_extractor.py

LLM-driven extraction of project facts from a procurement officer's
brief. Mirrors the shape of `tender_type_extractor.py` and
`tender_facts_extractor.py` but reads a *project brief* (free text or
a converted PDF/DPR) instead of an already-existing tender document.

Public API:
    extract_project_brief(text: str, *, llm_fn=None) -> dict

The returned dict has the schema:
    {
      "fields": {
        "<field_name>": {
          "value":      <extracted value or None>,
          "confidence": float in [0.0, 1.0],
          "source":     "extracted" | "default" | "not_found",
          "evidence":   "verbatim quote from input text or None"
        },
        …
      },
      "summary": {
        "n_required_filled":  int,
        "n_required_missing": int,
        "n_important_filled": int,
        "n_optional_filled":  int,
        "ready_for_gate1":    bool,    # all REQUIRED fields filled
      },
      "raw_llm_response":  str,
      "model":             str,
    }

REQUIRED fields (must have before drafting can proceed):
    project_name        : str
    tender_type         : Works | EPC | PPP | Goods | Services | Consultancy
    ecv_cr              : float          (Rs. crore)
    duration_months     : int
    department          : str            (acronym)
    is_ap_tender        : bool

IMPORTANT fields (flag if missing; officer can fill at Gate 1):
    location            : str
    nit_number          : str            (department assigns)
    funding_source      : State | Central | MDB | PPP | Mixed
    contractor_class    : Special | Class-I | Class-II | Class-III | Class-IV | Class-V
                          (derived from ecv_cr per AP-GO-094 if missing)

OPTIONAL fields (regulatory defaults applied if absent):
    dlp_months          : int  (default 24 per AP-GO-084)
    bid_validity_days   : int  (default 90 per AP-GO-067)
    scope_description   : str
    pre_bid_date        : ISO date
    bid_due_date        : ISO date

LLM client priority (same as tender_type_extractor):
    1. Caller-injected `llm_fn(system, user) -> str`
    2. OpenAI-compatible endpoint at $LLM_BASE_URL (OpenRouter / vLLM)
    3. Anthropic SDK with $ANTHROPIC_API_KEY
    4. RuntimeError otherwise.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parent.parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from builder.config import settings  # noqa: F401  (kept for parity with siblings)
from modules.validation.llm_client import parse_llm_json


# ── Field schema ──────────────────────────────────────────────────────

REQUIRED_FIELDS: tuple[str, ...] = (
    "project_name",
    "tender_type",
    "ecv_cr",
    "duration_months",
    "department",
    "is_ap_tender",
)
IMPORTANT_FIELDS: tuple[str, ...] = (
    "location",
    "nit_number",
    "funding_source",
    "contractor_class",
)
OPTIONAL_FIELDS: tuple[str, ...] = (
    "dlp_months",
    "bid_validity_days",
    "scope_description",
    "pre_bid_date",
    "bid_due_date",
)
ALL_FIELDS: tuple[str, ...] = REQUIRED_FIELDS + IMPORTANT_FIELDS + OPTIONAL_FIELDS

ALLOWED_TENDER_TYPES   = ("Works", "EPC", "PPP", "Goods", "Services", "Consultancy")
ALLOWED_FUNDING        = ("State", "Central", "MDB", "PPP", "Mixed")
ALLOWED_CLASSES        = ("Special", "Class-I", "Class-II", "Class-III", "Class-IV", "Class-V")

# AP departments / agencies — used to infer is_ap_tender from
# department alone when the LLM doesn't surface that flag explicitly.
AP_AGENCIES = {
    "APCRDA", "AGICL", "APIIC", "NREDCAP", "APMSIDC", "APSRTC",
    "APSPDCL", "APEPDCL", "APCPDCL", "APSPCB", "APMDC", "APRTA",
    "GVMC", "GVSCCL", "APUWSSP", "APRRDA", "APPWD", "APR&B", "APIIA",
    "TIDCO", "NTRUHS", "APUFIDC", "APBCL",
}


# ── Regulatory defaults (per the 24 Tier-1 validators) ────────────────

REGULATORY_DEFAULTS = {
    "dlp_months":         24,    # AP-GO-084
    "bid_validity_days":  90,    # AP-GO-067
    "funding_source":     "State",
}


def _ap_class_for_ecv(ecv_cr: float) -> str:
    """AP class-of-bidders mapping per AP-GO-094."""
    ecv_lakh = ecv_cr * 100
    if ecv_cr > 10:    return "Special"
    if ecv_cr >= 2:    return "Class-I"
    if ecv_cr >= 1:    return "Class-II"
    if ecv_lakh >= 50: return "Class-III"
    if ecv_lakh >= 10: return "Class-IV"
    return "Class-V"


# ── Prompts ───────────────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are a precise procurement-document fact extractor reading a "
    "project brief (free text or DPR excerpt) from a procurement officer "
    "in Andhra Pradesh, India. Return JSON only. Quote evidence verbatim "
    "from the input. Never fabricate. If a field is not present in the "
    "text, return null with confidence 0.0."
)


def build_user_prompt(brief_text: str) -> str:
    return (
        "Read the following project brief and extract structured "
        "procurement facts.\n\n"
        f"Project brief:\n\"\"\"\n{brief_text}\n\"\"\"\n\n"
        "Return JSON only with the following exact schema. For EACH "
        "field return value, confidence (0.0–1.0), and evidence "
        "(verbatim quote from the brief, or null if not found):\n\n"
        "{\n"
        "  \"project_name\":      {\"value\": <string>,         \"confidence\": <float>, \"evidence\": <quote or null>},\n"
        "  \"tender_type\":       {\"value\": <one of [Works, EPC, PPP, Goods, Services, Consultancy]>, \"confidence\": <float>, \"evidence\": <quote or null>},\n"
        "  \"ecv_cr\":            {\"value\": <float in Rs. Crore>, \"confidence\": <float>, \"evidence\": <quote or null>},\n"
        "  \"duration_months\":   {\"value\": <integer>,        \"confidence\": <float>, \"evidence\": <quote or null>},\n"
        "  \"department\":        {\"value\": <acronym string>, \"confidence\": <float>, \"evidence\": <quote or null>},\n"
        "  \"is_ap_tender\":      {\"value\": <true|false>,     \"confidence\": <float>, \"evidence\": <quote or null>},\n"
        "  \"location\":          {\"value\": <city/town/dist>, \"confidence\": <float>, \"evidence\": <quote or null>},\n"
        "  \"nit_number\":        {\"value\": <NIT No string>,  \"confidence\": <float>, \"evidence\": <quote or null>},\n"
        "  \"funding_source\":    {\"value\": <one of [State, Central, MDB, PPP, Mixed]>, \"confidence\": <float>, \"evidence\": <quote or null>},\n"
        "  \"contractor_class\":  {\"value\": <one of [Special, Class-I, Class-II, Class-III, Class-IV, Class-V]>, \"confidence\": <float>, \"evidence\": <quote or null>},\n"
        "  \"dlp_months\":        {\"value\": <integer>,        \"confidence\": <float>, \"evidence\": <quote or null>},\n"
        "  \"bid_validity_days\": {\"value\": <integer>,        \"confidence\": <float>, \"evidence\": <quote or null>},\n"
        "  \"scope_description\": {\"value\": <string>,         \"confidence\": <float>, \"evidence\": <quote or null>},\n"
        "  \"pre_bid_date\":      {\"value\": <ISO date YYYY-MM-DD>, \"confidence\": <float>, \"evidence\": <quote or null>},\n"
        "  \"bid_due_date\":      {\"value\": <ISO date YYYY-MM-DD>, \"confidence\": <float>, \"evidence\": <quote or null>}\n"
        "}\n\n"
        "Extraction rules:\n"
        "- ecv_cr: convert ANY currency mention to Rs. Crore. "
        "'Rs. 85 crore' → 85.0; 'Rs. 8,50,00,000' → 8.5; 'Rs. 125.5 Crore' "
        "→ 125.5. If only a sub-crore amount is given (e.g. 'Rs. 75 lakh'), "
        "convert to Crore (0.75).\n"
        "- duration_months: convert ANY duration to months. '18 months' → "
        "18; '2 years' → 24; '1.5 years' → 18.\n"
        "- tender_type: infer from project description. 'Construction of "
        "X' / 'building work' / 'civil works' / 'EPC' → Works. "
        "'Concession' / 'DBFOT' / 'PPP' → PPP. 'Supply of X' / "
        "'procurement of equipment' → Goods. 'Operation and maintenance' "
        "/ 'AMC' → Services. 'Design / DPR / advisory' → Consultancy. "
        "Use 'EPC' (not 'Works') only if the brief explicitly says EPC, "
        "Engineering-Procurement-Construction, or turnkey.\n"
        "- department: extract the ACRONYM (APCRDA / APIIC / NREDCAP / "
        "AGICL / etc.). If only a full name is given, derive the acronym "
        "from parenthesised initialism or the capital letters.\n"
        "- is_ap_tender: true if the brief mentions Andhra Pradesh, AP, "
        "or any AP-State agency (APCRDA, APIIC, AGICL, NREDCAP, "
        "APMSIDC, GVMC, GVSCCL, etc.). Else false.\n"
        "- location: city or town for the project; null if not stated.\n"
        "- funding_source: 'State' = state-government funded; 'Central' "
        "= central-government funded; 'MDB' = World Bank, ADB, JICA, "
        "AIIB; 'PPP' = private-sector funded under concession; 'Mixed' "
        "= multi-source. Default to 'State' if AP-State agency is "
        "named and no other funding source is mentioned.\n"
        "- contractor_class: derive from ecv_cr if not stated explicitly. "
        "Per AP-GO-094: ECV > Rs.10 Cr → Special; Rs.2-10 Cr → Class-I; "
        "Rs.1-2 Cr → Class-II; Rs.50 Lakh - 1 Cr → Class-III; "
        "Rs.10-50 Lakh → Class-IV; ≤ Rs.10 Lakh → Class-V.\n"
        "- dlp_months / bid_validity_days: only fill these if EXPLICITLY "
        "stated in the brief. Otherwise return null with confidence 0.0 "
        "— the workflow applies regulatory defaults (24mo / 90 days) "
        "downstream.\n"
        "- nit_number: only fill if the brief explicitly cites a NIT No. "
        "(format like '130/PROC/.../2026'). If the officer is going to "
        "auto-generate it, return null.\n\n"
        "Confidence guide:\n"
        "- 0.95-1.00 : value is verbatim in the text\n"
        "- 0.80-0.94 : value is unambiguous but lightly inferred (e.g. "
        "'construction of X' → tender_type=Works)\n"
        "- 0.50-0.79 : value is a reasonable inference (e.g. is_ap_tender "
        "from agency acronym alone)\n"
        "- 0.00      : value not present in text — return null"
    )


# ── LLM client (matches tender_type_extractor priority chain) ─────────

LLMFn = Callable[[str, str], str]
DEFAULT_LLM_MODEL = os.environ.get("LLM_MODEL", "qwen/qwen-2.5-72b-instruct")


def _call_llm(system: str, user: str, *, llm_fn: LLMFn | None = None) -> str:
    if llm_fn is not None:
        return llm_fn(system, user)

    base_url = os.environ.get("LLM_BASE_URL")
    if base_url:
        from openai import OpenAI
        client = OpenAI(
            base_url=base_url.rstrip("/"),
            api_key=os.environ.get("LLM_API_KEY", "EMPTY"),
        )
        extra_headers = {
            "HTTP-Referer": os.environ.get(
                "LLM_HTTP_REFERER",
                "https://github.com/konevenkatesh/procureAI",
            ),
            "X-Title": os.environ.get("LLM_X_TITLE", "AP Procurement Drafter"),
        }
        resp = client.chat.completions.create(
            model=DEFAULT_LLM_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            temperature=0.0,
            max_tokens=2048,
            extra_headers=extra_headers,
        )
        return resp.choices[0].message.content or ""

    if os.environ.get("ANTHROPIC_API_KEY"):
        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in msg.content if hasattr(b, "text"))

    raise RuntimeError(
        "No LLM available. Pass llm_fn=… OR set LLM_BASE_URL OR ANTHROPIC_API_KEY."
    )


# ── Validation + post-processing ──────────────────────────────────────

def _validate_field(name: str, raw: Any) -> dict:
    """Validate one field's LLM response shape. Returns the canonical
    `{value, confidence, source, evidence}` dict."""
    if not isinstance(raw, dict):
        return {"value": None, "confidence": 0.0, "source": "not_found", "evidence": None}

    value      = raw.get("value")
    confidence = raw.get("confidence")
    evidence   = raw.get("evidence")

    if isinstance(confidence, (int, float)):
        confidence = max(0.0, min(1.0, float(confidence)))
    else:
        confidence = 0.0

    # Type coerce + range-check by field
    if value in ("", "null", "None"):
        value = None

    if value is not None:
        try:
            if name == "ecv_cr":
                value = float(value)
            elif name in ("duration_months", "dlp_months", "bid_validity_days"):
                value = int(round(float(value)))
            elif name == "is_ap_tender":
                value = bool(value) if not isinstance(value, str) else (value.lower() in {"true", "yes", "1"})
            elif name == "tender_type" and value not in ALLOWED_TENDER_TYPES:
                value = None; confidence = 0.0
            elif name == "funding_source" and value not in ALLOWED_FUNDING:
                value = None; confidence = 0.0
            elif name == "contractor_class" and value not in ALLOWED_CLASSES:
                value = None; confidence = 0.0
            elif name in ("pre_bid_date", "bid_due_date"):
                # Allow ISO date OR date-like string; pass through as str
                value = str(value)
            else:
                value = str(value)
        except (TypeError, ValueError):
            value = None
            confidence = 0.0

    if value is None:
        return {"value": None, "confidence": 0.0,
                "source": "not_found", "evidence": None}
    return {"value": value, "confidence": confidence,
            "source": "extracted",
            "evidence": (str(evidence) if evidence else None)}


def _apply_defaults_and_derivations(fields: dict[str, dict]) -> dict[str, dict]:
    """Post-LLM cleanup:
       - Apply regulatory defaults for missing OPTIONAL fields.
       - Derive contractor_class from ecv_cr if missing.
       - Derive is_ap_tender from department acronym if missing.
       - Derive funding_source = 'State' if AP agency + nothing else stated.
       - Set source='default' or source='derived' as appropriate.
    """
    out = dict(fields)

    # 1. Derive is_ap_tender from department if LLM didn't surface it
    if (out.get("is_ap_tender", {}).get("value") is None
        and out.get("department", {}).get("value")):
        dept = (out["department"]["value"] or "").upper().strip()
        if dept in AP_AGENCIES or any(ag in dept for ag in AP_AGENCIES):
            out["is_ap_tender"] = {
                "value": True, "confidence": 0.85,
                "source": "derived",
                "evidence": f"Inferred from department='{dept}' (AP-State agency)",
            }

    # 2. Derive contractor_class from ecv_cr if missing
    if (out.get("contractor_class", {}).get("value") is None
        and out.get("ecv_cr", {}).get("value") is not None
        and out.get("is_ap_tender", {}).get("value") is True):
        cl = _ap_class_for_ecv(float(out["ecv_cr"]["value"]))
        out["contractor_class"] = {
            "value": cl, "confidence": 0.95,
            "source": "derived",
            "evidence": f"Derived from ecv_cr={out['ecv_cr']['value']} per AP-GO-094",
        }

    # 3. Default funding_source to 'State' on AP-State agency tenders
    if (out.get("funding_source", {}).get("value") is None
        and out.get("is_ap_tender", {}).get("value") is True):
        out["funding_source"] = {
            "value": "State", "confidence": 0.70,
            "source": "default",
            "evidence": "Default for AP-State agency tenders",
        }

    # 4. Apply regulatory defaults on OPTIONAL fields
    for fname, default_val in REGULATORY_DEFAULTS.items():
        if out.get(fname, {}).get("value") is None:
            out[fname] = {
                "value": default_val, "confidence": 1.0,
                "source": "default",
                "evidence": f"Regulatory default: {fname}={default_val}",
            }

    return out


# ── Public API ────────────────────────────────────────────────────────

def extract_project_brief(
    brief_text: str,
    *,
    llm_fn: LLMFn | None = None,
) -> dict:
    """Extract project facts from a brief. See module docstring for schema."""
    if not brief_text or not brief_text.strip():
        raise ValueError("brief_text is empty")

    raw = _call_llm(SYSTEM_PROMPT, build_user_prompt(brief_text), llm_fn=llm_fn)
    parsed = parse_llm_json(raw)

    fields: dict[str, dict] = {}
    for fname in ALL_FIELDS:
        fields[fname] = _validate_field(fname, parsed.get(fname))

    fields = _apply_defaults_and_derivations(fields)

    n_required_filled  = sum(1 for f in REQUIRED_FIELDS if fields[f]["value"] is not None)
    n_required_missing = len(REQUIRED_FIELDS) - n_required_filled
    n_important_filled = sum(1 for f in IMPORTANT_FIELDS if fields[f]["value"] is not None)
    n_optional_filled  = sum(1 for f in OPTIONAL_FIELDS  if fields[f]["value"] is not None)

    return {
        "fields":  fields,
        "summary": {
            "n_required_filled":  n_required_filled,
            "n_required_missing": n_required_missing,
            "n_important_filled": n_important_filled,
            "n_optional_filled":  n_optional_filled,
            "ready_for_gate1":    n_required_missing == 0,
        },
        "raw_llm_response": raw,
        "model":            DEFAULT_LLM_MODEL,
        "extracted_at":     datetime.now(timezone.utc).isoformat(),
    }


# ── CLI test harness ──────────────────────────────────────────────────

DEFAULT_DEMO_BRIEF = (
    "We need to issue a tender for construction of a new District "
    "Hospital at Kurnool with 3 floors, total built-up area 15,000 "
    "sqm. Budget is Rs.85 crore. APIIC is the implementing agency. "
    "The work should complete in 18 months. This is a state "
    "government funded project."
)


def _cli_demo() -> int:
    """Print the extraction for a brief read from stdin (or demo default)."""
    if sys.stdin.isatty():
        brief = DEFAULT_DEMO_BRIEF
        print(f"# (no stdin — using demo brief)\n# {brief}\n")
    else:
        piped = sys.stdin.read()
        brief = piped.strip() or DEFAULT_DEMO_BRIEF
    result = extract_project_brief(brief)
    print(json.dumps(result, indent=2, default=str))
    return 0 if result["summary"]["ready_for_gate1"] else 1


if __name__ == "__main__":
    raise SystemExit(_cli_demo())
