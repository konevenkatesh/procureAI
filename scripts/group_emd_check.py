"""
scripts/group_emd_check.py — Approach B (group-wise fact extraction)

Single BGE-M3 retrieval surfaces the ITB-financial section. ONE LLM call
extracts THREE related facts in one shot:
    1. EMD / Bid Security
    2. Bid validity period (days)
    3. Contractor class required

Facts are stored in the `fact_sheets` table (doc_id, fact_group,
extracted_facts JSONB, section attribution, similarity, extracted_by).

Then we run the EMD rule check against the extracted EMD facts and
materialise a ValidationFinding with the IDENTICAL structure as
Approach A so the comparison is apples-to-apples.

The other two facts (bid_validity_days, contractor_class) ride along
as 'bonus' — they're extracted in the same call but not yet checked
against rules tonight.
"""
from __future__ import annotations

import os
import sys
import time
import json
import re
import requests
from pathlib import Path

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from builder.config import settings
from modules.validator.condition_evaluator import evaluate as evaluate_when, Verdict


# ── Constants ─────────────────────────────────────────────────────────

DOC_ID = sys.argv[1] if len(sys.argv) > 1 else "vizag_ugss_exp_001"

FACT_GROUP = "ITB_financial"
TYPOLOGY   = "EMD-Shortfall"

QDRANT_URL  = os.environ.get("QDRANT_URL", "http://localhost:6333")
COLLECTION  = "tender_sections"

LLM_BASE_URL = os.environ["LLM_BASE_URL"]
LLM_API_KEY  = os.environ["LLM_API_KEY"]
LLM_MODEL    = os.environ["LLM_MODEL"]


# Same section-type filter as Approach A — keeps comparison fair.
ITB_FINANCIAL_SECTION_TYPES = ["ITB", "NIT", "GCC", "PCC", "SCC", "Evaluation"]

# Group query mentions all three facts so BGE-M3 retrieves a section
# that hosts them together (typically the Bid Data Sheet / Notice
# Inviting Tender / "Information for Bidders" preamble).
GROUP_QUERY_TEXT = (
    "bid security earnest money deposit period of bid validity days "
    "contractor class registration qualification estimated contract value"
)


RULE_CANDIDATES = [
    {
        "rule_id":         "AP-GO-050",
        "natural_language": "AP Works two-stage EMD: 1% at issue + 1.5% at agreement = 2.5% total",
        "severity":        "ADVISORY",
        "layer":           "AP-State",
        "shape":           "ap_two_stage",
        "target_total_pct": 2.5,
        "stage1_pct":      1.0,
        "stage2_pct":      1.5,
    },
    {
        "rule_id":         "GFR-G-049",
        "natural_language": "GFR Rule 170: Bid Security 2% to 5% of estimated value",
        "severity":        "HARD_BLOCK",
        "layer":           "Central",
        "shape":           "range",
        "min_pct":         2.0,
        "max_pct":         5.0,
    },
]


# ── Supabase REST helpers ─────────────────────────────────────────────

REST = settings.supabase_rest_url
H = {"apikey": settings.supabase_anon_key,
     "Authorization": f"Bearer {settings.supabase_anon_key}"}


def rest_get(path, params=None):
    r = requests.get(f"{REST}/rest/v1/{path}", params=params or {}, headers=H, timeout=30)
    r.raise_for_status()
    return r.json()


def rest_post(path, body):
    r = requests.post(
        f"{REST}/rest/v1/{path}", json=body,
        headers={**H, "Content-Type": "application/json", "Prefer": "return=representation"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def rest_delete(path, params=None):
    r = requests.delete(f"{REST}/rest/v1/{path}", params=params or {}, headers=H, timeout=30)
    r.raise_for_status()


# ── Step 1: BGE-M3 query vector ───────────────────────────────────────

def embed_query(text: str) -> list[float]:
    cache = getattr(embed_query, "_model", None)
    if cache is None:
        from sentence_transformers import SentenceTransformer
        m = SentenceTransformer("BAAI/bge-m3")
        m.max_seq_length = 1024
        embed_query._model = m
        cache = m
    vec = cache.encode(text, normalize_embeddings=True, show_progress_bar=False)
    return vec.tolist()


# ── Step 2: Qdrant top-K with section_type filter ─────────────────────

def qdrant_topk(query_vec: list[float], doc_id: str, k: int,
                section_types: list[str]) -> list[dict]:
    body = {
        "query":  query_vec,
        "limit":  k,
        "with_payload": True,
        "filter": {
            "must": [
                {"key": "doc_id",       "match": {"value": doc_id}},
                {"key": "section_type", "match": {"any":   list(section_types)}},
            ],
        },
    }
    r = requests.post(
        f"{QDRANT_URL}/collections/{COLLECTION}/points/query",
        json=body, timeout=30,
    )
    r.raise_for_status()
    pts = r.json()["result"]["points"]
    if not pts:
        raise RuntimeError(f"No Qdrant points for doc_id={doc_id}")
    return pts


# ── Step 3: resolve payload → kg_node Section + full_text ────────────

PROCESSED_MD_ROOTS = (
    REPO / "source_documents" / "e_procurement" / "processed_md",
    REPO / "source_documents" / "sample_tenders" / "processed_md",
)


def _slice_source_file(filename: str, ls: int, le: int) -> str:
    for root in PROCESSED_MD_ROOTS:
        p = root / filename
        if p.exists():
            lines = p.read_text(encoding="utf-8").splitlines()
            ls_i = max(1, int(ls))
            le_i = min(len(lines), int(le))
            return "\n".join(lines[ls_i - 1:le_i])
    raise FileNotFoundError(filename)


def resolve_section(doc_id: str, payload: dict) -> dict:
    section_node_id = payload.get("section_id")
    heading      = payload.get("heading")  or payload.get("section_heading")
    source_file  = payload.get("source_file")
    ls_local     = payload.get("line_start_local")
    le_local     = payload.get("line_end_local")
    section_type = payload.get("section_type")
    if not (section_node_id and ls_local and le_local):
        cands = rest_get("kg_nodes", {
            "select":    "node_id,properties",
            "doc_id":    f"eq.{doc_id}",
            "node_type": "eq.Section",
        })
        match = None
        for n in cands:
            p = n["properties"] or {}
            if p.get("heading") == heading and p.get("source_file") == source_file:
                match = n
                break
        if match is None:
            raise RuntimeError(f"can't resolve heading={heading!r}")
        section_node_id = match["node_id"]
        mp = match["properties"] or {}
        ls_local      = mp.get("line_start_local") or mp.get("line_start")
        le_local      = mp.get("line_end_local")   or mp.get("line_end")
        source_file   = source_file or mp.get("source_file")
        section_type  = section_type or mp.get("section_type")
    full_text = _slice_source_file(source_file, ls_local, le_local)
    return {
        "section_node_id":   section_node_id,
        "heading":           heading,
        "source_file":       source_file,
        "line_start_local":  ls_local,
        "line_end_local":    le_local,
        "section_type":      section_type,
        "full_text":         full_text,
        "word_count":        len(full_text.split()),
    }


# ── Step 4: ONE LLM call extracts THREE related facts ─────────────────

LLM_SYSTEM = (
    "You are a precise procurement-document fact extractor. "
    "Return JSON only. Quote evidence verbatim from the supplied text. "
    "Never fabricate."
)

RERANK_PER_SECTION_CHAR_CAP = 4000


def _truncate_for_rerank(text: str, cap: int = RERANK_PER_SECTION_CHAR_CAP) -> tuple[str, bool]:
    if len(text) <= cap:
        return text, False
    head_len = int(cap * 0.6)
    tail_len = cap - head_len
    head = text[:head_len]
    tail = text[-tail_len:]
    last_para_in_head = head.rfind("\n\n", int(head_len * 0.75))
    if last_para_in_head > 0:
        head = head[:last_para_in_head]
    first_para_in_tail = tail.find("\n\n", 0, int(tail_len * 0.25))
    if first_para_in_tail > 0:
        tail = tail[first_para_in_tail + 2:]
    return (
        head + "\n\n[... middle of section elided ...]\n\n" + tail,
        True,
    )


def build_group_prompt(candidates: list[dict]) -> str:
    blocks = []
    for i, c in enumerate(candidates):
        body, _t = _truncate_for_rerank(c["full_text"])
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
        "These should be from the bidder-information / Bid Data Sheet / Notice "
        "Inviting Tender region. Pick the candidate that is the primary "
        "bidder-information section (the one stating EMD, bid validity, "
        "contractor class together — typically a single table or 'Information "
        "to Tenderers'). If multiple candidates are relevant, prefer the one "
        "with the most of the three facts.\n\n"
        f"{candidates_block}\n\n"
        "Extract three related facts from the chosen candidate (and other "
        "candidates if needed for evidence — but ALWAYS prefer the one you "
        "chose). Return JSON only:\n"
        "{\n"
        "  \"chosen_index\": integer 0..N-1 (primary section),\n"
        "  \"emd\": {\n"
        "    \"total_pct\":   float OR null   (total EMD percentage of estimated contract value),\n"
        "    \"stage1_pct\":  float OR null   (AP-style 'paid at issue of tender documents'),\n"
        "    \"stage2_pct\":  float OR null   (AP-style 'paid at agreement / by successful bidder'),\n"
        "    \"two_stage\":   bool            (true ONLY if document explicitly describes BOTH stages),\n"
        "    \"amount_cr\":   float OR null   (EMD as a fixed amount in crores),\n"
        "    \"evidence\":    \"verbatim quote that states EMD\",\n"
        "    \"line\":        int OR null    (line number in the section if visible),\n"
        "    \"found\":       bool\n"
        "  },\n"
        "  \"bid_validity\": {\n"
        "    \"days\":      int OR null      (period of bid validity in DAYS — e.g. '3 months' → 90),\n"
        "    \"evidence\":  \"verbatim quote\",\n"
        "    \"found\":     bool\n"
        "  },\n"
        "  \"contractor_class\": {\n"
        "    \"class\":     string OR null   (e.g. 'Class-I', 'Special Class', 'Class-IA Civil'),\n"
        "    \"evidence\":  \"verbatim quote\",\n"
        "    \"found\":     bool\n"
        "  }\n"
        "}\n\n"
        "Selection rules:\n"
        "- 'EMD shall be paid at 1% of the estimated contract value' → "
        "  emd.total_pct=1.0, emd.two_stage=false.\n"
        "- AP two-stage example: 'EMD: 1% at issue + 1.5% at agreement = 2.5% total' → "
        "  emd.total_pct=2.5, emd.stage1_pct=1.0, emd.stage2_pct=1.5, emd.two_stage=true.\n"
        "- IGNORE Performance Security / PBG percentages — those are a different "
        "instrument. IGNORE retention money. IGNORE Additional Security beyond DLP.\n"
        "- Convert all amounts to crores. 1 crore = 100 lakh. 'Rs.50 lakh' → 0.5.\n"
        "- Bid validity in months → days (1 month = 30 days conventional).\n"
        "- contractor_class: report exact wording from the document. If absent, found=false.\n"
        "- Evidence MUST be an exact substring of the chosen candidate's text.\n"
        "- If a fact is not stated in the chosen candidate, set found=false for that fact "
        "AND its other fields to null."
    )


def call_llm(system: str, user: str) -> tuple[str, dict]:
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
        max_tokens=900,                    # group prompt produces longer JSON than single-fact
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
    return json.loads(text)


# ── Step 5: rule selection (same as Approach A) ───────────────────────

def fetch_tender_facts(doc_id: str) -> dict:
    rows = rest_get("kg_nodes", {
        "select": "properties", "doc_id": f"eq.{doc_id}",
        "node_type": "eq.TenderDocument",
    })
    if not rows:
        return {}
    p = rows[0].get("properties") or {}
    return {
        "tender_type":      p.get("tender_type"),
        "is_ap_tender":     bool(p.get("is_ap_tender")),
        "TenderType":       p.get("tender_type"),
        "TenderState":      "AndhraPradesh" if p.get("is_ap_tender") else "Other",
        "BidSecurityRequired": True,
        "EstimatedValue":   p.get("estimated_value_cr") or 0,
    }


def select_emd_rule(tender_facts: dict) -> dict | None:
    fired: list[dict] = []
    print(f"\n  Rule selection — facts: tender_type={tender_facts.get('tender_type')!r}, "
          f"is_ap_tender={tender_facts.get('is_ap_tender')}")
    for cand in RULE_CANDIDATES:
        rid = cand["rule_id"]
        rule_rows = rest_get("rules", {
            "select": "rule_id,condition_when,defeats", "rule_id": f"eq.{rid}",
        })
        if not rule_rows:
            continue
        cw = rule_rows[0].get("condition_when") or ""
        verdict = evaluate_when(cw, tender_facts).verdict
        defeats = rule_rows[0].get("defeats") or []
        print(f"    [{rid}] cond={cw!r}  verdict={verdict.value}")
        if verdict == Verdict.FIRE:
            fired.append(dict(cand, defeats=defeats))
    defeated_ids = set()
    for f in fired:
        for victim in (f.get("defeats") or []):
            defeated_ids.add(victim)
    surviving = [f for f in fired if f["rule_id"] not in defeated_ids]
    return surviving[0] if surviving else None


def evaluate_emd_against_rule(rule: dict, total_pct: float | None,
                              two_stage: bool) -> tuple[bool, str]:
    if total_pct is None:
        return False, "no_percentage_extracted"
    if rule["shape"] == "ap_two_stage":
        target = float(rule["target_total_pct"])
        if abs(total_pct - target) > 0.01:
            return True, f"total_pct={total_pct} != target={target}"
        if not two_stage:
            return True, "single_stage_when_ap_two_stage_required"
        return False, "compliant_two_stage"
    elif rule["shape"] == "range":
        lo = float(rule["min_pct"]); hi = float(rule["max_pct"])
        if total_pct < lo: return True, f"total_pct={total_pct} below min={lo}"
        if total_pct > hi: return True, f"total_pct={total_pct} above max={hi}"
        return False, "compliant_range"
    raise ValueError(f"unknown shape: {rule['shape']}")


# ── Idempotent re-run cleanup ─────────────────────────────────────────

def _delete_prior_tier1_emd_approach_b(doc_id: str) -> tuple[int, int, int]:
    """Delete prior fact_sheets row + ValidationFinding + edge for this
    doc_id+fact_group so re-runs are idempotent."""
    fs_rows = rest_get("fact_sheets", {
        "select": "id", "doc_id": f"eq.{doc_id}",
        "fact_group": f"eq.{FACT_GROUP}",
    })
    n_fs = 0
    for r in fs_rows:
        rest_delete("fact_sheets", {"id": f"eq.{r['id']}"}); n_fs += 1

    edges = rest_get("kg_edges", {
        "select": "edge_id", "doc_id": f"eq.{doc_id}",
        "edge_type": "eq.VIOLATES_RULE",
        "properties->>typology": f"eq.{TYPOLOGY}",
        "properties->>approach": "eq.B_group_extraction",
    })
    n_e = 0
    for e in edges:
        rest_delete("kg_edges", {"edge_id": f"eq.{e['edge_id']}"}); n_e += 1

    findings = rest_get("kg_nodes", {
        "select": "node_id", "doc_id": f"eq.{doc_id}",
        "node_type": "eq.ValidationFinding",
        "properties->>typology_code": f"eq.{TYPOLOGY}",
        "properties->>approach":      "eq.B_group_extraction",
    })
    n_f = 0
    for f in findings:
        rest_delete("kg_nodes", {"node_id": f"eq.{f['node_id']}"}); n_f += 1

    return n_fs, n_f, n_e


def get_or_create_rule_node(doc_id: str, rule_id: str) -> str:
    existing = rest_get("kg_nodes", {
        "select":    "node_id", "doc_id": f"eq.{doc_id}",
        "node_type": "eq.RuleNode",
        "properties->>rule_id": f"eq.{rule_id}",
    })
    if existing:
        return existing[0]["node_id"]
    rule_rows = rest_get("rules", {
        "select":  "rule_id,natural_language,layer,severity,rule_type,pattern_type,typology_code,defeats",
        "rule_id": f"eq.{rule_id}",
    })
    r = rule_rows[0] if rule_rows else {}
    inserted = rest_post("kg_nodes", [{
        "doc_id": doc_id, "node_type": "RuleNode",
        "label": f"{rule_id}: {(r.get('natural_language') or '')[:90]}",
        "properties": {
            "rule_id": rule_id,
            "layer": r.get("layer"), "severity": r.get("severity"),
            "rule_type": r.get("rule_type"), "pattern_type": r.get("pattern_type"),
            "typology_code": r.get("typology_code"),
            "defeats": r.get("defeats") or [],
        },
        "source_ref": f"rules:{rule_id}",
    }])
    return inserted[0]["node_id"]


# ── Main ──────────────────────────────────────────────────────────────

def main() -> int:
    timings: dict[str, float] = {}
    t_start = time.perf_counter()

    print("=" * 76)
    print(f"  Group EMD Check (Approach B — group-wise fact extraction)")
    print(f"  doc_id:     {DOC_ID}")
    print(f"  fact_group: {FACT_GROUP}")
    print(f"  model:      {LLM_MODEL}")
    print("=" * 76)

    n_fs, n_f, n_e = _delete_prior_tier1_emd_approach_b(DOC_ID)
    if n_fs or n_f or n_e:
        print(f"  cleared {n_fs} prior fact_sheet row(s), "
              f"{n_f} prior finding(s), {n_e} prior edge(s)")

    facts = fetch_tender_facts(DOC_ID)
    rule  = select_emd_rule(facts)
    if rule is None:
        print(f"  → no EMD rule fires for these facts")
        return 0

    print(f"\n── Group query (one retrieval, three facts) ──")
    print(f"  ({len(GROUP_QUERY_TEXT)} chars)")
    print(f"  {GROUP_QUERY_TEXT}")

    # 1. BGE-M3 embed
    t0 = time.perf_counter()
    qvec = embed_query(GROUP_QUERY_TEXT)
    timings["embed"] = time.perf_counter() - t0
    print(f"\n── Step 1: BGE-M3 embed ── {timings['embed']:.2f}s")

    # 2. Qdrant top-10
    K = 10
    t0 = time.perf_counter()
    points = qdrant_topk(qvec, DOC_ID, k=K, section_types=ITB_FINANCIAL_SECTION_TYPES)
    timings["qdrant"] = time.perf_counter() - t0
    print(f"\n── Step 2: Qdrant top-{K} (filter section_type ∈ {ITB_FINANCIAL_SECTION_TYPES}) "
          f"── {timings['qdrant']*1000:.0f}ms")
    for i, p in enumerate(points):
        pl = p["payload"]
        h = pl.get("heading") or pl.get("section_heading") or ""
        print(f"    [{i}] cosine={p['score']:.4f}  type={pl.get('section_type','?'):5s}  "
              f"lines={pl.get('line_start_local')}-{pl.get('line_end_local')}  "
              f"{h[:70]}")

    # 3. Resolve all candidates
    t0 = time.perf_counter()
    candidates = []
    for p in points:
        sec = resolve_section(DOC_ID, p["payload"])
        sec["similarity"] = p["score"]
        candidates.append(sec)
    timings["fetch_section"] = time.perf_counter() - t0

    # 4. ONE LLM call for all three facts
    t0 = time.perf_counter()
    print(f"\n── Step 3: ONE LLM call for EMD + bid_validity + contractor_class ──")
    user_prompt = build_group_prompt(candidates)
    print(f"  prompt size: {len(user_prompt)} chars (~{len(user_prompt)//4} tokens)")
    raw_content, _ = call_llm(LLM_SYSTEM, user_prompt)
    timings["llm"] = time.perf_counter() - t0
    print(f"  wall:        {timings['llm']:.2f}s")
    print(f"\n── Raw LLM JSON (group) ──")
    print(raw_content)

    # 5. Parse
    parsed = parse_llm_response(raw_content)
    chosen   = parsed.get("chosen_index")
    emd      = parsed.get("emd") or {}
    validity = parsed.get("bid_validity") or {}
    klass    = parsed.get("contractor_class") or {}

    print(f"\n── Parsed (group facts) ──")
    print(f"  chosen_index : {chosen}")
    print(f"  EMD")
    print(f"    total_pct  : {emd.get('total_pct')}")
    print(f"    stage1_pct : {emd.get('stage1_pct')}")
    print(f"    stage2_pct : {emd.get('stage2_pct')}")
    print(f"    two_stage  : {emd.get('two_stage')}")
    print(f"    amount_cr  : {emd.get('amount_cr')}")
    print(f"    found      : {emd.get('found')}")
    print(f"    evidence   : {(emd.get('evidence') or '')[:200]!r}")
    print(f"  bid_validity")
    print(f"    days       : {validity.get('days')}")
    print(f"    evidence   : {(validity.get('evidence') or '')[:120]!r}")
    print(f"  contractor_class")
    print(f"    class      : {klass.get('class')}")
    print(f"    evidence   : {(klass.get('evidence') or '')[:120]!r}")

    if chosen is None or not isinstance(chosen, int) or not (0 <= chosen < len(candidates)):
        print(f"\n  → no candidate selected; nothing to write")
        return 0

    section = candidates[chosen]
    similarity = section["similarity"]

    # 6. Save fact_sheets row
    t0 = time.perf_counter()
    fs_row = rest_post("fact_sheets", [{
        "doc_id":           DOC_ID,
        "fact_group":       FACT_GROUP,
        "extracted_facts":  {"emd": emd, "bid_validity": validity, "contractor_class": klass},
        "section_heading":  section["heading"],
        "source_file":      section["source_file"],
        "line_start":       int(section["line_start_local"]),
        "line_end":         int(section["line_end_local"]),
        "qdrant_similarity": float(round(similarity, 4)),
        "extracted_by":     "bge-m3+llm-group:qwen-2.5-72b@openrouter",
    }])[0]
    timings["fact_sheet_write"] = time.perf_counter() - t0
    print(f"\n  → fact_sheets row {fs_row['id']} written  ({timings['fact_sheet_write']*1000:.0f}ms)")

    # 7. Apply rule check on EMD subset
    total_pct  = emd.get("total_pct")
    two_stage  = bool(emd.get("two_stage"))
    is_violation, reason_label = evaluate_emd_against_rule(rule, total_pct, two_stage)
    print(f"\n── Decision ──")
    print(f"  rule          : {rule['rule_id']} ({rule['severity']}, shape={rule['shape']})")
    print(f"  reason        : {reason_label}")
    print(f"  is_violation  : {is_violation}")

    if is_violation:
        section_node_id = section["section_node_id"]
        rule_node_id    = get_or_create_rule_node(DOC_ID, rule["rule_id"])

        if rule["shape"] == "ap_two_stage":
            label = (
                f"{TYPOLOGY}: EMD = {total_pct}% (expected {rule['target_total_pct']}% "
                f"two-stage 1%+1.5%); reason: {reason_label}"
            )
        else:
            label = (
                f"{TYPOLOGY}: EMD = {total_pct}% (expected {rule['min_pct']}%-"
                f"{rule['max_pct']}%); reason: {reason_label}"
            )

        finding = rest_post("kg_nodes", [{
            "doc_id":    DOC_ID,
            "node_type": "ValidationFinding",
            "label":     label,
            "properties": {
                "rule_id":            rule["rule_id"],
                "typology_code":      TYPOLOGY,
                "severity":           rule["severity"],
                "evidence":           emd.get("evidence") or "",
                "extraction_path":    "percentage" if total_pct is not None else "amount",
                "total_pct":          total_pct,
                "stage1_pct":         emd.get("stage1_pct"),
                "stage2_pct":         emd.get("stage2_pct"),
                "two_stage":          two_stage,
                "amount_cr":          emd.get("amount_cr"),
                "rule_shape":         rule["shape"],
                "rule_target_total":  rule.get("target_total_pct"),
                "rule_min_pct":       rule.get("min_pct"),
                "rule_max_pct":       rule.get("max_pct"),
                "violation_reason":   reason_label,
                "tier":               1,
                "extracted_by":       "bge-m3+llm-group:qwen-2.5-72b@openrouter",
                "retrieval_strategy":
                    f"qdrant_top{K}_section_filter_group_extraction",
                "rerank_chosen_index": chosen,
                "section_node_id":    section_node_id,
                "section_heading":    section["heading"],
                "source_file":        section["source_file"],
                "line_start_local":   section["line_start_local"],
                "line_end_local":     section["line_end_local"],
                "qdrant_similarity":  round(similarity, 4),
                "approach":           "B_group_extraction",
                "fact_sheet_id":      fs_row["id"],
                "status":             "OPEN",
                "defeated":           False,
            },
            "source_ref": f"tier1:group_emd:{rule['rule_id']}",
        }])[0]

        edge = rest_post("kg_edges", [{
            "doc_id":       DOC_ID,
            "from_node_id": section_node_id,
            "to_node_id":   rule_node_id,
            "edge_type":    "VIOLATES_RULE",
            "weight":       1.0,
            "properties": {
                "rule_id":           rule["rule_id"],
                "typology":          TYPOLOGY,
                "severity":          rule["severity"],
                "defeated":          False,
                "tier":              1,
                "total_pct":         total_pct,
                "evidence":          emd.get("evidence") or "",
                "qdrant_similarity": round(similarity, 4),
                "violation_reason":  reason_label,
                "approach":          "B_group_extraction",
                "fact_sheet_id":     fs_row["id"],
                "finding_node_id":   finding["node_id"],
            },
        }])[0]
        print(f"  → ValidationFinding {finding['node_id']}")
        print(f"  → VIOLATES_RULE     {edge['edge_id']}  Section→Rule")

    # Summary
    timings["total_wall"] = time.perf_counter() - t_start
    print()
    print("=" * 76)
    print("  TIMING SUMMARY (Approach B)")
    print("=" * 76)
    for k, v in timings.items():
        unit = "s" if v >= 1 else "ms"
        val  = v if v >= 1 else v * 1000
        print(f"    {k:18s} {val:8.2f} {unit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
