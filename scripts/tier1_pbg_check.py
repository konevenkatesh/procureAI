"""
scripts/tier1_pbg_check.py

Tier-1 PBG-Shortfall check, BGE-M3 + LLM, NO regex.

Per the v0.4 spec (replacing the regex pattern matchers):

  1. BGE-M3 embed the first two sentences of
     CLAUSE-AP-CONTRACTOR-SECURITY-DEPOSIT-001.text_english.
  2. Query the shared Qdrant `tender_sections` collection, filtered by
     doc_id, for the top-1 most-similar section (cosine).
  3. Fetch that section's full_text from PostgreSQL document_sections.
  4. Send the full_text to qwen-2.5-72b on OpenRouter:
        "What percentage is specified for Performance Security or
         Security Deposit? Return JSON: {percentage, evidence, found}."
  5. Compare to AP-GO-175's 10% threshold. If found < 10 → violation.
  6. Materialise a ValidationFinding kg_node carrying the LLM evidence
     quote (verbatim from the section), plus a VIOLATES_RULE edge from
     the Section → RuleNode for AP-GO-175.

Tested on vizag_ugss_exp_001 only. Reports:
    - matched section heading + line range
    - cosine similarity score
    - raw LLM JSON
    - extracted percentage + evidence quote
    - per-stage timing
"""
from __future__ import annotations

import os
import sys
import time
import json
import re
import requests
from pathlib import Path

# Quiet the HuggingFace tokenizer warning — irrelevant for our use case.
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from builder.config import settings


# ── Constants ─────────────────────────────────────────────────────────

# doc_id can be overridden via CLI argv[1]; defaults to Vizag.
DOC_ID = sys.argv[1] if len(sys.argv) > 1 else "vizag_ugss_exp_001"
RULE_ID = "AP-GO-175"
RULE_THRESHOLD_PCT = 10.0
TYPOLOGY = "PBG-Shortfall"
SEVERITY = "HARD_BLOCK"

CLAUSE_TEMPLATE_ID = "CLAUSE-AP-CONTRACTOR-SECURITY-DEPOSIT-001"

QDRANT_URL  = os.environ.get("QDRANT_URL", "http://localhost:6333")
COLLECTION  = "tender_sections"

LLM_BASE_URL = os.environ["LLM_BASE_URL"]
LLM_API_KEY  = os.environ["LLM_API_KEY"]
LLM_MODEL    = os.environ["LLM_MODEL"]


# ── Supabase REST helpers (same pattern as elsewhere in the codebase) ──

REST = settings.supabase_rest_url
H = {"apikey": settings.supabase_anon_key,
     "Authorization": f"Bearer {settings.supabase_anon_key}"}


def rest_get(path, params=None):
    r = requests.get(f"{REST}/rest/v1/{path}", params=params or {}, headers=H, timeout=30)
    r.raise_for_status()
    return r.json()


def rest_post(path, body):
    r = requests.post(
        f"{REST}/rest/v1/{path}",
        json=body,
        headers={**H, "Content-Type": "application/json", "Prefer": "return=representation"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


# ── Step 1: BGE-M3 query vector ───────────────────────────────────────

def embed_query(text: str) -> list[float]:
    """Load BGE-M3 (cached on the function attribute) and embed `text`,
    L2-normalised so cosine similarity matches Qdrant's 'Cosine' metric."""
    cache = getattr(embed_query, "_model", None)
    if cache is None:
        from sentence_transformers import SentenceTransformer
        m = SentenceTransformer("BAAI/bge-m3")
        m.max_seq_length = 1024
        embed_query._model = m
        cache = m
    vec = cache.encode(text, normalize_embeddings=True, show_progress_bar=False)
    return vec.tolist()


def first_two_sentences(text: str) -> str:
    """Crude sentence split — splits on the first two '. ' boundaries.
    Good enough for the clause template's well-punctuated English.
    Returns the original text unchanged if fewer than 2 sentences."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return " ".join(parts[:2]).strip()


# ── Step 2: Qdrant top-1 similarity search ────────────────────────────

# Fix B — restrict PBG retrieval to the section_types that actually
# carry PBG clauses (ITB / GCC / PCC / SCC / NIT). This drops bond-form
# templates ("Forms"), retention/payments sections under "Specifications",
# and Insurance Surety Bond instructions ("Forms") out of contention —
# all of which were dominating top-1 for JA and High Court because they
# lexically overlap the AP-FC clause query without containing the actual
# percentage.
PBG_SECTION_TYPES = ["ITB", "GCC", "PCC", "SCC", "NIT"]


def qdrant_topk(
    query_vec: list[float],
    doc_id: str,
    k: int = 5,
    section_types: list[str] | None = None,
) -> list[dict]:
    """Filter by doc_id (and optionally a section_type allowlist),
    return top-k sections by cosine similarity.

    `section_types`, when provided, becomes a Qdrant `MatchAny` filter:
    only points whose payload.section_type appears in the list are
    considered. The top-1 search ranks against this restricted index.
    """
    must: list[dict] = [{"key": "doc_id", "match": {"value": doc_id}}]
    if section_types:
        must.append({"key": "section_type",
                     "match": {"any": list(section_types)}})
    body = {
        "query":         query_vec,
        "limit":         k,
        "with_payload":  True,
        "filter":        {"must": must},
    }
    r = requests.post(
        f"{QDRANT_URL}/collections/{COLLECTION}/points/query",
        json=body,
        timeout=30,
    )
    r.raise_for_status()
    points = r.json()["result"]["points"]
    if not points:
        raise RuntimeError(
            f"No Qdrant points for doc_id={doc_id} "
            f"(section_types filter={section_types!r})"
        )
    return points


def qdrant_top1(
    query_vec: list[float],
    doc_id: str,
    section_types: list[str] | None = None,
) -> dict:
    """Back-compat shim — top-k=1 with optional section_type filter."""
    return qdrant_topk(query_vec, doc_id, k=1, section_types=section_types)[0]


# ── Step 3: resolve matched payload → kg_node Section + full_text ────

# Roots where processed Markdown files live (mirror tender_type_extractor)
PROCESSED_MD_ROOTS = (
    REPO / "source_documents" / "e_procurement" / "processed_md",
    REPO / "source_documents" / "sample_tenders" / "processed_md",
)


def _slice_source_file(filename: str, line_start: int, line_end: int) -> str:
    """Read the [line_start, line_end] inclusive slice from the source
    Markdown file."""
    for root in PROCESSED_MD_ROOTS:
        p = root / filename
        if p.exists():
            lines = p.read_text(encoding="utf-8").splitlines()
            ls = max(1, int(line_start))
            le = min(len(lines), int(line_end))
            return "\n".join(lines[ls - 1:le])
    raise FileNotFoundError(
        f"Source file '{filename}' not found in any processed_md root"
    )


def resolve_section(doc_id: str, payload: dict) -> dict:
    """Take the Qdrant payload + doc_id and return a unified section
    descriptor:
        {
          section_node_id, heading, source_file,
          line_start_local, line_end_local, section_type,
          full_text, word_count
        }

    Handles both payload shapes:
        v0.4 (new — Tirupathi/JA via kg_builder.py): payload carries
            section_id (kg_node UUID), heading, source_file,
            line_start_local, line_end_local.  → use payload directly,
            slice full_text from source MD file.
        v0.2 (old — Vizag, ingested by step2_sections.py): payload
            carries postgresql_id, section_heading, source_file,
            section_text (truncated). → look up kg_nodes by
            (heading, source_file) for the node_id, then slice from
            source MD file using line_start_local on the node.
    Either way we end up with kg_node-anchored line numbers and the
    real full body from the source file."""
    # New schema fast path
    section_node_id = payload.get("section_id")
    heading      = payload.get("heading")  or payload.get("section_heading")
    source_file  = payload.get("source_file")
    ls_local     = payload.get("line_start_local")
    le_local     = payload.get("line_end_local")
    section_type = payload.get("section_type")

    # If new fields are missing, look up the kg_node Section by
    # (doc_id, heading, source_file) — stable cross-table key.
    if not (section_node_id and ls_local and le_local):
        candidates = rest_get("kg_nodes", {
            "select":    "node_id,properties",
            "doc_id":    f"eq.{doc_id}",
            "node_type": "eq.Section",
        })
        match = None
        for n in candidates:
            p = n["properties"] or {}
            if p.get("heading") == heading and p.get("source_file") == source_file:
                match = n
                break
        if match is None:
            raise RuntimeError(
                f"Could not resolve Qdrant payload to a kg_node Section "
                f"(doc_id={doc_id}, heading={heading!r})"
            )
        section_node_id = match["node_id"]
        mp = match["properties"] or {}
        ls_local      = mp.get("line_start_local") or mp.get("line_start")
        le_local      = mp.get("line_end_local")   or mp.get("line_end")
        source_file   = source_file or mp.get("source_file")
        section_type  = section_type or mp.get("section_type")

    full_text = _slice_source_file(source_file, ls_local, le_local)
    word_count = len(full_text.split())

    return {
        "section_node_id":   section_node_id,
        "heading":           heading,
        "source_file":       source_file,
        "line_start_local":  ls_local,
        "line_end_local":    le_local,
        "section_type":      section_type,
        "full_text":         full_text,
        "word_count":        word_count,
    }


# ── Step 4: LLM percentage-extraction call ────────────────────────────

LLM_SYSTEM = (
    "You are a precise procurement-document fact extractor. "
    "Return JSON only. Quote evidence verbatim from the supplied text. "
    "Never fabricate."
)


def build_pbg_prompt(section_text: str) -> str:
    return (
        "Read the following procurement-clause text.\n\n"
        f"Text:\n\"\"\"\n{section_text}\n\"\"\"\n\n"
        "Question: What PERCENTAGE (of contract value or bid amount) is "
        "specified for Performance Security, Performance Bank Guarantee, "
        "Security Deposit, or PBG?\n\n"
        "Return JSON only:\n"
        "{\n"
        "  \"percentage\": float between 0 and 100 OR null if no percentage is stated,\n"
        "  \"evidence\": exact quote from the text containing the percentage, or empty string,\n"
        "  \"found\": true if a Performance Security / PBG / Security Deposit percentage is stated, false otherwise\n"
        "}\n\n"
        "Rules:\n"
        "- Return ONLY the FIRST and most authoritative percentage that applies to "
        "Performance Security / Security Deposit. Ignore percentages that refer to "
        "EMD, retention money, mobilisation advance, or liquidated damages.\n"
        "- Convert phrases like 'two and a half percent' or 'half percent' to numbers.\n"
        "- If the text mentions Performance Security but no percentage, "
        "set percentage=null, found=false.\n"
        "- Evidence MUST be an exact substring of the supplied text."
    )


# Per-section character cap for the rerank prompt. 4000 chars × 10
# candidates ≈ 40K input chars (~10K tokens) — comfortably inside
# qwen-2.5-72b's 128K context.
#
# IMPORTANT: PBG content is sometimes buried at the TAIL of a section
# (e.g. JA "Penalty for lapses:" — GCC 51.1 PBG sentence at body
# offset 5079 of 5434). Head-only truncation would cut it off.
# We split the cap as ~60% head + ~40% tail so a buried tail clause
# still reaches the LLM.
RERANK_PER_SECTION_CHAR_CAP = 4000


def _truncate_for_rerank(text: str, cap: int = RERANK_PER_SECTION_CHAR_CAP) -> tuple[str, bool]:
    """If `text` ≤ cap, return as-is. Otherwise return head ⊕ tail
    (60/40 split) with an explicit elision marker so the LLM knows
    a middle chunk was dropped."""
    if len(text) <= cap:
        return text, False
    head_len = int(cap * 0.6)
    tail_len = cap - head_len
    head = text[:head_len]
    tail = text[-tail_len:]
    # Prefer paragraph boundaries for cleaner cuts on either side
    last_para_in_head = head.rfind("\n\n", int(head_len * 0.75))
    if last_para_in_head > 0:
        head = head[:last_para_in_head]
    first_para_in_tail = tail.find("\n\n", 0, int(tail_len * 0.25))
    if first_para_in_tail > 0:
        tail = tail[first_para_in_tail + 2:]
    return (
        head
        + "\n\n[... middle of section elided for rerank prompt ...]\n\n"
        + tail
    ), True


def build_pbg_rerank_prompt(candidates: list[dict]) -> str:
    """Stitch top-k candidates into one prompt. The LLM picks the
    correct candidate index AND extracts the percentage in one call,
    so we don't need a separate "is this the right section" pass."""
    blocks = []
    for i, c in enumerate(candidates):
        body, _truncated = _truncate_for_rerank(c["full_text"])
        blocks.append(
            f"--- CANDIDATE {i} ---\n"
            f"heading: {c['heading']}\n"
            f"section_type: {c.get('section_type') or 'unknown'}\n"
            f"cosine_similarity: {c['similarity']:.4f}\n"
            f"text:\n\"\"\"\n{body}\n\"\"\""
        )
    candidates_block = "\n\n".join(blocks)

    return (
        f"You are reading {len(candidates)} candidate sections from a procurement document. "
        "Exactly ONE of them (or none) is the actual Performance Security clause "
        "stating its percentage of bid/contract value.\n\n"
        f"{candidates_block}\n\n"
        "Question: Which candidate states the PERCENTAGE (of contract value or "
        "bid amount) for Performance Security, Performance Bank Guarantee, "
        "Security Deposit, or PBG?\n\n"
        "Return JSON only:\n"
        "{\n"
        "  \"chosen_index\": integer 0..N-1 of the candidate that contains the PBG percentage, OR null if none of them do,\n"
        "  \"percentage\": float between 0 and 100, or null,\n"
        "  \"evidence\": exact verbatim quote from the chosen candidate's text containing the percentage, or empty string,\n"
        "  \"found\": true if a Performance Security / PBG / Security Deposit percentage is stated in any candidate, false otherwise,\n"
        "  \"reasoning\": one short sentence on why this candidate was chosen over the others\n"
        "}\n\n"
        "Selection rules:\n"
        "- The chosen candidate MUST contain a percentage that applies to Performance Security, "
        "Performance Bank Guarantee, Security Deposit, or PBG itself — NOT to EMD, "
        "retention money, mobilisation advance, liquidated damages, or any other instrument.\n"
        "- Common phrasings: \"Performance Security ... equal to X per cent of the bid amount\", "
        "\"Security Deposit at the rate of X% of contract value\", \"PBG of X%\".\n"
        "- A section that defines retention as \"7½% withheld\" or \"2½% on completion\" is "
        "NOT a PBG clause — those are retention money. Do NOT pick it.\n"
        "- A section that mentions liquidated-damages \"at the rate of 0.1% per day of "
        "Performance Security\" is NOT stating the PBG percentage — those are LD rates. "
        "Do NOT pick it.\n"
        "- Convert words like 'two and a half per cent' to 2.5.\n"
        "- Evidence MUST be an exact substring of the chosen candidate's text.\n"
        "- If NO candidate states a PBG percentage (e.g. PBG is given only as a fixed "
        "amount in INR), set chosen_index=null, percentage=null, found=false."
    )


def call_llm(system: str, user: str) -> tuple[str, dict]:
    """Returns (raw_content_string, raw_response_dict). Uses OpenRouter
    via the OpenAI SDK (same path as the tender_type extractor)."""
    from openai import OpenAI
    client = OpenAI(base_url=LLM_BASE_URL.rstrip("/"), api_key=LLM_API_KEY)
    extra_headers = {
        "HTTP-Referer": "https://github.com/konevenkatesh/procureAI",
        "X-Title": "AP Procurement Validator",
    }
    resp = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        temperature=0.0,
        max_tokens=512,
        extra_headers=extra_headers,
    )
    return (resp.choices[0].message.content or ""), resp.model_dump()


def parse_llm_response(raw: str) -> dict:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)
    if not text.startswith("{"):
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            text = m.group(0)
    data = json.loads(text)
    return data


# ── Step 6: Materialise ValidationFinding + VIOLATES_RULE ─────────────

def get_or_create_rule_node(doc_id: str, rule_id: str) -> str:
    """Find or create the RuleNode for the given rule_id under doc_id."""
    existing = rest_get("kg_nodes", {
        "select":    "node_id",
        "doc_id":    f"eq.{doc_id}",
        "node_type": "eq.RuleNode",
        "properties->>rule_id": f"eq.{rule_id}",
    })
    if existing:
        return existing[0]["node_id"]
    # Pull rule metadata from rules
    rule_rows = rest_get("rules", {
        "select":  "rule_id,natural_language,layer,severity,rule_type,pattern_type,typology_code,defeats",
        "rule_id": f"eq.{rule_id}",
    })
    r = rule_rows[0] if rule_rows else {}
    inserted = rest_post("kg_nodes", [{
        "doc_id":    doc_id,
        "node_type": "RuleNode",
        "label":     f"{rule_id}: {(r.get('natural_language') or '')[:90]}",
        "properties": {
            "rule_id":         rule_id,
            "layer":           r.get("layer"),
            "severity":        r.get("severity"),
            "rule_type":       r.get("rule_type"),
            "pattern_type":    r.get("pattern_type"),
            "typology_code":   r.get("typology_code"),
            "defeats":         r.get("defeats") or [],
        },
        "source_ref": f"rules:{rule_id}",
    }])
    return inserted[0]["node_id"]


# ── Main ──────────────────────────────────────────────────────────────

def _delete_prior_tier1_pbg(doc_id: str, rule_id: str) -> tuple[int, int]:
    """Idempotent re-run: remove any Tier-1 PBG VIOLATES_RULE edges and
    ValidationFinding nodes left over from a previous invocation of
    THIS script. Identified via `tier=1` + `rule_id` properties."""
    # Edges first (FK — depends on no FK to nodes for ValidationFinding)
    edges_to_delete = rest_get("kg_edges", {
        "select": "edge_id",
        "doc_id": f"eq.{doc_id}",
        "edge_type": "eq.VIOLATES_RULE",
        "properties->>rule_id": f"eq.{rule_id}",
        "properties->>tier":    "eq.1",
    })
    n_e = 0
    for e in edges_to_delete:
        r = requests.delete(
            f"{REST}/rest/v1/kg_edges?edge_id=eq.{e['edge_id']}",
            headers=H, timeout=30,
        )
        r.raise_for_status()
        n_e += 1

    findings_to_delete = rest_get("kg_nodes", {
        "select": "node_id",
        "doc_id": f"eq.{doc_id}",
        "node_type": "eq.ValidationFinding",
        "properties->>rule_id": f"eq.{rule_id}",
        "properties->>tier":    "eq.1",
    })
    n_f = 0
    for f in findings_to_delete:
        r = requests.delete(
            f"{REST}/rest/v1/kg_nodes?node_id=eq.{f['node_id']}",
            headers=H, timeout=30,
        )
        r.raise_for_status()
        n_f += 1
    return n_f, n_e


def main() -> int:
    timings: dict[str, float] = {}
    print("=" * 76)
    print(f"  Tier-1 PBG-Shortfall check (BGE-M3 + LLM, NO regex)")
    print(f"  doc_id: {DOC_ID}")
    print(f"  rule:   {RULE_ID}    threshold: ≥ {RULE_THRESHOLD_PCT}%")
    print(f"  model:  {LLM_MODEL}")
    print("=" * 76)

    n_f, n_e = _delete_prior_tier1_pbg(DOC_ID, RULE_ID)
    if n_f or n_e:
        print(f"  cleared {n_f} prior Tier-1 finding node(s) and "
              f"{n_e} edge(s) before re-running")

    # Step 0: tight query string targeting PBG-clause AMOUNT language.
    #
    # Earlier we used the AP-FC Article 279 preamble (the rule's
    # text_english). That preamble is OBLIGATION language ("shall be
    # required to give security") which lexically overlaps too many
    # sections (retention, EMD, settlement-of-claims, etc.). The
    # actual PBG clauses in ITB 42 / GCC 51.1 / PCC use AMOUNT
    # language ("furnish the Performance Security for an amount
    # equal to X per cent of the bid amount/contract value, in the
    # shape of bank guarantee").
    #
    # The query below mirrors that wording so cosine puts the real
    # PBG clauses on top instead of the obligation/retention noise.
    query_text = (
        "Performance Security equal to per cent of bid amount "
        "contract value furnish bank guarantee"
    )
    print(f"\n── Query text (PBG-amount tight query) ──")
    print(f"  ({len(query_text)} chars)")
    print(f"  {query_text}")

    # Step 1: BGE-M3 embed
    t0 = time.perf_counter()
    print(f"\n── Step 1: BGE-M3 embed query ──")
    qvec = embed_query(query_text)
    timings["embed"] = time.perf_counter() - t0
    print(f"  vector dim: {len(qvec)}")
    print(f"  wall:       {timings['embed']:.2f}s  (includes first-time model load)")

    # Step 2: Qdrant top-K (FIX B) — section_type filter + tight query.
    # K=10 because for the APCRDA Works template family the actual PBG
    # clause sits at filtered ranks 6–8 (see L13/L17 diagnostics on JA),
    # which top-5 misses. 10 candidates × ≤4000 chars each ≈ 10K tokens
    # in the rerank prompt — safely within qwen-2.5-72b's 128K context.
    K = 10
    t0 = time.perf_counter()
    print(f"\n── Step 2: Qdrant top-{K} (filter doc_id={DOC_ID}, "
          f"section_type ∈ {PBG_SECTION_TYPES}) ──")
    points = qdrant_topk(qvec, DOC_ID, k=K, section_types=PBG_SECTION_TYPES)
    timings["qdrant"] = time.perf_counter() - t0
    print(f"  {len(points)} candidate(s) returned in {timings['qdrant']*1000:.0f}ms:")
    for i, p in enumerate(points):
        pl = p["payload"]
        h  = pl.get("heading") or pl.get("section_heading") or ""
        print(f"    [{i}] cosine={p['score']:.4f}  type={pl.get('section_type','?'):5s}  "
              f"lines={pl.get('line_start_local')}-{pl.get('line_end_local')}  "
              f"{h[:70]}")

    # Step 3: resolve all K candidates (slice source MD per-section)
    t0 = time.perf_counter()
    print(f"\n── Step 3: Resolve all {len(points)} candidates ──")
    candidates: list[dict] = []
    for p in points:
        sec = resolve_section(DOC_ID, p["payload"])
        sec["similarity"] = p["score"]
        candidates.append(sec)
    timings["fetch_section"] = time.perf_counter() - t0
    print(f"  resolved {len(candidates)} sections in {timings['fetch_section']*1000:.0f}ms")

    # Step 4: ONE LLM call — pick the right candidate AND extract %
    t0 = time.perf_counter()
    print(f"\n── Step 4: LLM rerank + extraction (qwen-2.5-72b @ openrouter) ──")
    user_prompt = build_pbg_rerank_prompt(candidates)
    print(f"  prompt size: {len(user_prompt)} chars (~{len(user_prompt)//4} tokens)")
    raw_content, _full_resp = call_llm(LLM_SYSTEM, user_prompt)
    timings["llm"] = time.perf_counter() - t0
    print(f"  wall:        {timings['llm']:.2f}s")
    print(f"\n── Raw LLM JSON ──")
    print(raw_content)

    # Step 5: parse + select the chosen candidate
    parsed = parse_llm_response(raw_content)
    chosen   = parsed.get("chosen_index")
    pct      = parsed.get("percentage")
    found    = bool(parsed.get("found"))
    evidence = (parsed.get("evidence") or "").strip()
    reason   = (parsed.get("reasoning") or "").strip()

    print(f"\n── Parsed ──")
    print(f"  chosen_index : {chosen}")
    print(f"  found        : {found}")
    print(f"  percentage   : {pct}")
    print(f"  reasoning    : {reason[:200]}")
    print(f"  evidence     : {evidence[:300]!r}")

    # Resolve the chosen candidate (or None if LLM picked nothing)
    if chosen is not None and isinstance(chosen, int) and 0 <= chosen < len(candidates):
        section    = candidates[chosen]
        similarity = section["similarity"]
        print(f"  → using candidate [{chosen}]: "
              f"{section['heading'][:60]} (cosine={similarity:.4f})")
    else:
        section    = None
        similarity = None
        print(f"  → no candidate chosen by LLM")

    # Validation: only emit a finding when the LLM both found a value
    # AND that value is below the rule threshold. found=False with
    # null percentage means "section is irrelevant or missing the figure"
    # — leave as no-finding for now (caller can decide what to do with
    # absence-of-evidence).
    is_violation = (
        found and pct is not None and section is not None
        and pct < RULE_THRESHOLD_PCT
    )
    print(f"\n── Decision ──")
    print(f"  threshold:    {RULE_THRESHOLD_PCT}%")
    print(f"  is_violation: {is_violation}")
    if is_violation:
        # Step 6: materialise the finding and the edge
        t0 = time.perf_counter()
        section_node_id = section["section_node_id"]
        rule_node_id = get_or_create_rule_node(DOC_ID, RULE_ID)
        finding = rest_post("kg_nodes", [{
            "doc_id":    DOC_ID,
            "node_type": "ValidationFinding",
            "label":     f"{TYPOLOGY}: PBG = {pct}% (expected ≥ {RULE_THRESHOLD_PCT}%)",
            "properties": {
                "rule_id":          RULE_ID,
                "typology_code":    TYPOLOGY,
                "severity":         SEVERITY,
                "evidence":         evidence,
                "percentage_found": pct,
                "threshold":        RULE_THRESHOLD_PCT,
                "tier":             1,
                "extracted_by":     "bge-m3+llm-rerank:qwen-2.5-72b@openrouter",
                "retrieval_strategy": (
                    f"qdrant_top{K}_section_filter_{'-'.join(PBG_SECTION_TYPES)}"
                    f"_llm_rerank"
                ),
                "rerank_chosen_index": chosen,
                "rerank_reasoning":  reason,
                "section_node_id":  section_node_id,
                "section_heading":  section["heading"],
                "source_file":      section["source_file"],
                "line_start_local": section["line_start_local"],
                "line_end_local":   section["line_end_local"],
                "qdrant_similarity": round(similarity, 4),
                "status":           "OPEN",
                "defeated":         False,
            },
            "source_ref": f"tier1:pbg_check:{RULE_ID}",
        }])[0]
        edge = rest_post("kg_edges", [{
            "doc_id":       DOC_ID,
            "from_node_id": section_node_id,
            "to_node_id":   rule_node_id,
            "edge_type":    "VIOLATES_RULE",
            "weight":       1.0,
            "properties": {
                "rule_id":           RULE_ID,
                "typology":          TYPOLOGY,
                "severity":          SEVERITY,
                "defeated":          False,
                "tier":              1,
                "percentage_found":  pct,
                "threshold":         RULE_THRESHOLD_PCT,
                "evidence":          evidence,
                "qdrant_similarity": round(similarity, 4),
                "finding_node_id":   finding["node_id"],
            },
        }])[0]
        timings["materialise"] = time.perf_counter() - t0
        print(f"  → ValidationFinding {finding['node_id']}")
        print(f"  → VIOLATES_RULE     {edge['edge_id']}  Section→Rule")
        print(f"  materialise wall:   {timings['materialise'] * 1000:.0f}ms")

    # Summary
    print()
    print("=" * 76)
    print("  TIMING SUMMARY")
    print("=" * 76)
    total = sum(timings.values())
    for k, v in timings.items():
        unit = "s" if v >= 1 else "ms"
        val  = v if v >= 1 else v * 1000
        print(f"    {k:18s} {val:8.2f} {unit}")
    print(f"    {'TOTAL':18s} {total:8.2f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
