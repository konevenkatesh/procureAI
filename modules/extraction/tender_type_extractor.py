"""
modules/extraction/tender_type_extractor.py

Extract a reliable `tender_type` for a doc_id by reading the document's
NIT (Notice Inviting Tender) sections with an LLM.

Pipeline per call:
    1. Fetch the first N=3 sections of `node_type=Section` and
       `properties.section_type=NIT` for the doc, in line-order.
    2. Slice each section's body from the source Markdown file using
       its `source_file` + `line_start_local` + `line_end_local`. (The
       v0.3-clean kg_nodes Section schema does not embed full_text on
       the node — we read the source file fresh.)
    3. Concatenate, send to an LLM with the prompt below.
    4. Parse JSON response. confidence ≥ 0.85 → reliable.
    5. Optionally commit `tender_type`, `tender_type_reliable`,
       `tender_type_confidence`, `tender_type_evidence`, and
       `tender_type_source_section` onto the TenderDocument kg_node.

LLM client priority (first matching wins):
    1. `llm_fn(system_prompt, user_prompt) -> str`     — caller-injected
       callable. Used by tests and by no-API-key environments where
       "Claude Code in conversation" embodies the LLM.
    2. OpenAI-compatible endpoint at `$LLM_BASE_URL` (e.g. vLLM).
       Set `LLM_BASE_URL`, `LLM_API_KEY` (optional), `LLM_MODEL`.
       Default model: deepseek-ai/DeepSeek-R1-Distill-Qwen-14B.
    3. Anthropic API at `$ANTHROPIC_API_KEY` with claude-sonnet-4-5.
    4. RuntimeError — no LLM available.

Public API:
    extract_tender_type(doc_id, *, llm_fn=None, n_sections=3) -> dict
    commit_to_kg(doc_id, result) -> None
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


# Roots where processed Markdown files live
PROCESSED_MD_ROOTS: tuple[Path, ...] = (
    REPO / "source_documents" / "e_procurement" / "processed_md",
    REPO / "source_documents" / "sample_tenders" / "processed_md",
)

ALLOWED_TENDER_TYPES: tuple[str, ...] = (
    "Works", "Goods", "Services", "Consultancy", "PPP", "Disposal",
)

RELIABLE_THRESHOLD: float = 0.85

DEFAULT_LLM_MODEL = "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B"


# ── Prompts ────────────────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are a precise procurement-document classifier. "
    "Read the supplied text and return the requested JSON only. "
    "Quote evidence verbatim from the text. Never fabricate."
)


def build_user_prompt(nit_text: str) -> str:
    """Returns the verbatim user-prompt the spec requires."""
    return (
        "You are reading a government procurement tender document from "
        "Andhra Pradesh, India.\n\n"
        "Read the following text and identify the tender type.\n\n"
        f"Text: {nit_text}\n\n"
        "Return JSON only:\n"
        "{\n"
        "  \"tender_type\": one of [Works, Goods, Services, Consultancy, PPP, Disposal],\n"
        "  \"confidence\": float between 0.0 and 1.0,\n"
        "  \"evidence\": exact quote from text that proves type,\n"
        "  \"reasoning\": one sentence explanation\n"
        "}\n\n"
        "Rules:\n"
        "- Works: construction, civil works, EPC, BOT, DBOT\n"
        "- PPP: concession, DBFOT, public-private partnership\n"
        "- Goods: supply, procurement of materials/equipment\n"
        "- Services: non-consulting services, AMC, O&M contracts\n"
        "- Consultancy: design, advisory, feasibility, DPR\n"
        "- Disposal: sale of scrap, auction of assets"
    )


# ── Supabase REST helpers ─────────────────────────────────────────────

def _rest_headers(prefer: str | None = None) -> dict:
    h = {
        "apikey":        settings.supabase_anon_key,
        "Authorization": f"Bearer {settings.supabase_anon_key}",
        "Content-Type":  "application/json",
    }
    if prefer:
        h["Prefer"] = prefer
    return h


def _rest_get(table: str, params: dict) -> list[dict]:
    r = requests.get(
        f"{settings.supabase_rest_url}/rest/v1/{table}",
        params=params,
        headers={**_rest_headers(), "Range-Unit": "items", "Range": "0-1999"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def _rest_patch(table: str, eq_filter: dict, body: dict) -> list[dict]:
    """PATCH with ?col=eq.<value>... filter; returns updated rows."""
    params = {k: f"eq.{v}" for k, v in eq_filter.items()}
    r = requests.patch(
        f"{settings.supabase_rest_url}/rest/v1/{table}",
        params=params,
        json=body,
        headers=_rest_headers(prefer="return=representation"),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


# ── NIT-text fetcher ──────────────────────────────────────────────────

def _slice_source_file(filename: str, line_start: int, line_end: int) -> str:
    """Read a slice of lines [line_start, line_end] (inclusive, 1-indexed)
    from a processed-MD file located in any known root."""
    for root in PROCESSED_MD_ROOTS:
        p = root / filename
        if p.exists():
            lines = p.read_text(encoding="utf-8").splitlines()
            ls = max(1, line_start)
            le = min(len(lines), line_end)
            return "\n".join(lines[ls - 1:le])
    raise FileNotFoundError(
        f"Source file '{filename}' not found in any processed_md root: "
        + ", ".join(str(r) for r in PROCESSED_MD_ROOTS)
    )


def fetch_nit_text(doc_id: str, n_sections: int = 1, max_chars: int = 800) -> tuple[str, list[dict]]:
    """Fetch the first `n_sections` NIT-typed Section nodes (in line
    order), slice each one's body from its source MD file, and return
    the concatenation truncated to `max_chars` total.

    Default `n_sections=1, max_chars=800` is the tender_type pattern:
    the 'Name of the Work:' declaration is reliably in those first
    bytes; sending more pushes generic ITB boilerplate into context and
    confuses smaller models. Wider windows are needed for facts that
    live in NIT data tables (e.g. Estimated Contract Value, Concession
    Period) which usually appear after the project-name preamble.

    Returns:
        (text, list_of_section_descriptors)
    """
    sections = _rest_get("kg_nodes", {
        "select":    "node_id,properties",
        "doc_id":    f"eq.{doc_id}",
        "node_type": "eq.Section",
    })
    if not sections:
        raise ValueError(f"No Section nodes in kg_nodes for doc_id={doc_id}")

    nit = [s for s in sections
           if (s["properties"] or {}).get("section_type") == "NIT"]

    if nit:
        # Normal path — pick the first NIT section(s) in line order.
        nit.sort(key=lambda s: int((s["properties"] or {}).get("line_start") or 0))
        nit = nit[: max(1, n_sections)]
    else:
        # Fallback for docs without an NIT-typed section (e.g. concession
        # agreements / DCAs that don't have an NIT preamble — Tirupathi
        # DCA is 100% GCC). The project-name declaration in these docs
        # is reliably in the first heading-block of the document body —
        # the same byte position the NIT preamble would have occupied.
        # Take the earliest sections by line_start_local. Still LLM-only
        # downstream — no regex on the body.
        sections.sort(key=lambda s: int(
            (s["properties"] or {}).get("line_start_local")
            or (s["properties"] or {}).get("line_start") or 0
        ))
        nit = sections[: max(1, n_sections)]
        print(
            f"  [tender_type_extractor] doc_id={doc_id}: no section_type=NIT "
            f"found; falling back to first {len(nit)} section(s) by "
            f"line_start_local. heading: "
            f"{(nit[0]['properties'] or {}).get('heading')!r}"
        )

    parts: list[str] = []
    descriptors: list[dict] = []
    total_chars = 0
    for s in nit:
        if total_chars >= max_chars:
            break
        p = s["properties"] or {}
        source_file = p.get("source_file") or ""
        ls = int(p.get("line_start_local") or p.get("line_start") or 1)
        le = int(p.get("line_end_local")   or p.get("line_end")   or ls)
        body = _slice_source_file(source_file, ls, le)
        remaining = max_chars - total_chars
        chunk = body[:remaining]
        parts.append(chunk)
        total_chars += len(chunk)
        descriptors.append({
            "node_id":          s["node_id"],
            "heading":          p.get("heading"),
            "source_file":      source_file,
            "line_start_local": ls,
            "line_end_local":   le,
            "body_chars_total": len(body),
            "body_chars_sent":  len(chunk),
        })

    text = "\n\n---\n\n".join(parts)
    return text, descriptors


# ── LLM client (priority chain) ───────────────────────────────────────

LLMFn = Callable[[str, str], str]


def _call_llm(system: str, user: str, *, llm_fn: LLMFn | None = None) -> str:
    """Run system+user through the first available LLM client.
    Returns the raw text response (caller parses JSON)."""
    if llm_fn is not None:
        return llm_fn(system, user)

    # 2. OpenAI-compatible endpoint (OpenRouter / vLLM / Ollama / etc.)
    base_url = os.environ.get("LLM_BASE_URL")
    if base_url:
        from openai import OpenAI
        client = OpenAI(
            base_url=base_url.rstrip("/"),
            api_key=os.environ.get("LLM_API_KEY", "EMPTY"),
        )
        model = os.environ.get("LLM_MODEL", DEFAULT_LLM_MODEL)
        # OpenRouter requires HTTP-Referer + X-Title for attribution and
        # rate-limit accounting (per https://openrouter.ai/docs/api).
        # Other OpenAI-compat backends ignore unknown headers, so it's
        # safe to send these unconditionally.
        extra_headers = {
            "HTTP-Referer": os.environ.get(
                "LLM_HTTP_REFERER",
                "https://github.com/konevenkatesh/procureAI",
            ),
            "X-Title": os.environ.get("LLM_X_TITLE", "AP Procurement Validator"),
        }
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            temperature=0.0,
            max_tokens=512,
            extra_headers=extra_headers,
        )
        return resp.choices[0].message.content or ""

    # 3. Anthropic
    if os.environ.get("ANTHROPIC_API_KEY"):
        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=512,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in msg.content if hasattr(b, "text"))

    raise RuntimeError(
        "No LLM available. Pass llm_fn=… OR set LLM_BASE_URL "
        "(OpenAI-compatible vLLM endpoint) OR ANTHROPIC_API_KEY."
    )


def _parse_response(raw: str) -> dict:
    """Strip ```json fences if any; json.loads; validate keys."""
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```\s*$",       "", text)
    # Many models prefix a chain-of-thought before the JSON. Pull the
    # outermost {...} block as fallback.
    if not text.lstrip().startswith("{"):
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            text = m.group(0)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM did not return valid JSON. Raw: {raw[:500]!r}") from e

    tt = data.get("tender_type")
    if tt not in ALLOWED_TENDER_TYPES:
        raise ValueError(
            f"LLM returned tender_type={tt!r}, not in {ALLOWED_TENDER_TYPES}"
        )
    try:
        conf = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    if not (0.0 <= conf <= 1.0):
        conf = max(0.0, min(1.0, conf))
    return {
        "tender_type": tt,
        "confidence":  conf,
        "evidence":   (data.get("evidence")  or "").strip(),
        "reasoning":  (data.get("reasoning") or "").strip(),
    }


def _attribution_string() -> str:
    """Build the `tender_type_extracted_by` value, suffixed with the
    backend that produced the result so audit logs can tell `gemma4`
    extractions apart from `qwen-2.5-72b@openrouter` extractions."""
    base_url = (os.environ.get("LLM_BASE_URL") or "").lower()
    if "openrouter.ai" in base_url:
        backend = "openrouter"
    elif "anthropic.com" in base_url:
        backend = "anthropic"
    elif "11434" in base_url or "ollama" in base_url:
        backend = "ollama"
    elif "deepseek.com" in base_url:
        backend = "deepseek"
    elif "openai.com" in base_url:
        backend = "openai"
    elif base_url:
        # Strip scheme + path so it's URL-safe in the label
        backend = base_url.replace("https://", "").replace("http://", "").split("/")[0]
    else:
        backend = "injected"   # llm_fn path, no remote backend
    return f"llm:nit_section_classifier@{backend}"


# ── Public API ────────────────────────────────────────────────────────

def extract_tender_type(
    doc_id: str,
    *,
    llm_fn: LLMFn | None = None,
    n_sections: int = 1,
    max_chars: int = 800,
) -> dict:
    """Run the full extraction pipeline. Returns:

        {
            "tender_type":     str,        # Works | Goods | Services | …
            "confidence":      float,      # 0.0 - 1.0
            "evidence":        str,        # quote from doc
            "reasoning":       str,
            "source_section":  str,        # heading(s) joined with " | "
            "reliable":        bool,       # confidence >= 0.85
            "raw_response":    str,        # for debugging
            "nit_text_chars":  int,        # length of text we sent
        }

    Default scope is the FIRST NIT section, FIRST 800 chars. The 'Name
    of the Work:' declaration is reliably in those first 800 chars; we
    learned the hard way that sending more dilutes the signal and
    blows past gemma4's 8192-token context window.

    Does NOT write to the database. Call `commit_to_kg(doc_id, result)`
    afterwards if you want the result persisted on the TenderDocument
    kg_node."""
    nit_text, descriptors = fetch_nit_text(doc_id, n_sections=n_sections, max_chars=max_chars)
    user = build_user_prompt(nit_text)
    raw = _call_llm(SYSTEM_PROMPT, user, llm_fn=llm_fn)
    parsed = _parse_response(raw)
    confidence = parsed["confidence"]
    return {
        "tender_type":    parsed["tender_type"],
        "confidence":     confidence,
        "evidence":       parsed["evidence"],
        "reasoning":      parsed["reasoning"],
        "source_section": " | ".join(d["heading"] or "(unknown)" for d in descriptors),
        "reliable":       confidence >= RELIABLE_THRESHOLD,
        "raw_response":   raw,
        "nit_text_chars": len(nit_text),
    }


def commit_to_kg(doc_id: str, result: dict) -> dict:
    """Patch the TenderDocument kg_node properties with the extractor's
    output. Returns the updated node row."""
    nodes = _rest_get("kg_nodes", {
        "select":    "node_id,properties",
        "doc_id":    f"eq.{doc_id}",
        "node_type": "eq.TenderDocument",
    })
    if not nodes:
        raise ValueError(f"No TenderDocument node for doc_id={doc_id}")
    node = nodes[0]
    new_props = dict(node["properties"] or {})
    # Authoritative tender_type fields (replace the unreliable
    # `tender_type_classified` / `tender_type_reliable=false` left
    # behind by the regex classifier).
    new_props["tender_type"]                 = result["tender_type"]
    new_props["tender_type_reliable"]        = bool(result["reliable"])
    new_props["tender_type_confidence"]      = result["confidence"]
    new_props["tender_type_evidence"]        = (result["evidence"] or "")[:500]
    new_props["tender_type_source_section"]  = result["source_section"]
    new_props["tender_type_extracted_by"]    = _attribution_string()
    # Also record exactly which model/version was used; useful for
    # invalidating cached results when we upgrade the model.
    new_props["tender_type_model"]           = os.environ.get("LLM_MODEL", "")
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
    """Extract + (optionally) commit. Returns the full result dict
    plus a `committed` boolean."""
    result = extract_tender_type(doc_id, llm_fn=llm_fn)
    if commit:
        commit_to_kg(doc_id, result)
        result["committed"] = True
    else:
        result["committed"] = False
    return result


# ── CLI usage ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Extract tender_type for a doc_id")
    parser.add_argument("doc_id", help="doc_id to extract for")
    parser.add_argument("--no-commit", action="store_true",
                        help="don't write back to kg_nodes")
    parser.add_argument("--print-nit", action="store_true",
                        help="just print the NIT text the extractor would send")
    args = parser.parse_args()

    if args.print_nit:
        text, descriptors = fetch_nit_text(args.doc_id)
        print(f"== NIT sections fetched: {len(descriptors)} ==")
        for d in descriptors:
            print(f"  {d['heading']}  ({d['source_file']} L{d['line_start_local']}-{d['line_end_local']})")
        print()
        print(f"== Concatenated NIT text ({len(text):,} chars) ==")
        print(text[:5000])
        if len(text) > 5000:
            print(f"\n…[truncated; total {len(text)} chars]…")
        sys.exit(0)

    result = run(args.doc_id, commit=not args.no_commit)
    print(json.dumps(result, indent=2, default=str))
