"""
scripts/tier1_emd_check.py

Tier-1 EMD-Shortfall check, BGE-M3 + LLM, NO regex.

Built from the start with the shared modules:
  - modules.validation.evidence_guard.verify_evidence_in_section (L24)
  - modules.validation.section_router.family_for_doc_with_filter

Pipeline:
  1. Pick the section_type filter via the document family router.
     APCRDA Works → [NIT, ITB]; SBD Format → [Evaluation, ITB];
     NREDCAP PPP → [NIT, Forms]; default → [NIT, ITB, Evaluation].
  2. BGE-M3 embed an answer-shaped query for EMD wording.
  3. Qdrant top-10 candidates within the filter.
  4. LLM rerank — pick the single EMD candidate AND extract
     percentage / two-stage breakdown / fixed amount in one call.
     Explicit ignore rules in the prompt: retention money, PBG,
     liquidated damages, EMD forfeiture, bid-security validity clauses.
  5. Hallucination guard: verify the LLM evidence quote is in the
     chosen-candidate's full_text. Discard on score < 85.
  6. Rule selection via condition_evaluator on AP-GO-050 (AP Works,
     ADVISORY, 2.5% two-stage) and GFR-G-049 (TenderType=ANY,
     2% ≤ EMD ≤ 5%, HARD_BLOCK). AP-GO-050 defeats GFR-G-049 etc.
     for AP Works tenders — defeasibility is wired in the rules table.
  7. Apply the rule's check shape:
        ap_two_stage : violation if total != 2.5% OR two_stage=False
        range        : violation if total < 2% OR total > 5%
  8. Materialise ValidationFinding + VIOLATES_RULE with L24 audit
     fields from the start (evidence_in_source / evidence_verified /
     evidence_match_score / evidence_match_method).

Tested on judicial_academy_exp_001 first.
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
from modules.validation.evidence_guard import verify_evidence_in_section
from modules.validation.section_router import family_for_doc_with_filter


# ── Constants ─────────────────────────────────────────────────────────

DOC_ID = sys.argv[1] if len(sys.argv) > 1 else "judicial_academy_exp_001"

TYPOLOGY = "EMD-Shortfall"

QDRANT_URL  = os.environ.get("QDRANT_URL", "http://localhost:6333")
COLLECTION  = "tender_sections"

LLM_BASE_URL = os.environ["LLM_BASE_URL"]
LLM_API_KEY  = os.environ["LLM_API_KEY"]
LLM_MODEL    = os.environ["LLM_MODEL"]


# Answer-shaped query: matches the literal wording AP Works tenders
# use ("EMD ... 1% of the Estimated Contract Value (ECV) ... GO.MS.
# No.94 ... two stages: at issue + at agreement"). Per L12, the query
# should sound like the answer, not the rule.
QUERY_TEXT = (
    "Earnest Money Deposit bid security amounting per cent "
    "Estimated Contract Value ECV GO.MS tender document issue "
    "agreement stage"
)


# Rule candidates we evaluate via condition_evaluator. The rules table
# already wires AP-GO-050.defeats = ['MPW-079', 'MPW25-052', 'GFR-G-049',
# 'CVC-047'], so for AP Works tenders the AP rule wins; for PPP /
# non-AP, GFR-G-049 fires alone.
RULE_CANDIDATES = [
    {
        "rule_id":         "AP-GO-050",
        "natural_language": "AP Works two-stage EMD: 1% at issue + 1.5% at agreement = 2.5% total",
        "severity":        "ADVISORY",      # Decision 4: carry from rule
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
        f"{REST}/rest/v1/{path}",
        json=body,
        headers={**H, "Content-Type": "application/json", "Prefer": "return=representation"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def rest_delete(path, params=None):
    r = requests.delete(f"{REST}/rest/v1/{path}", params=params or {}, headers=H, timeout=30)
    r.raise_for_status()


# ── BGE-M3 embed ──────────────────────────────────────────────────────

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


# ── Qdrant top-K with section_type filter (filter from the router) ──

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
        raise RuntimeError(
            f"No Qdrant points for doc_id={doc_id} (section_types={section_types})"
        )
    return pts


# ── Resolve payload → kg_node Section + slice full_text ──────────────

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


# ── LLM rerank prompt for EMD ─────────────────────────────────────────

LLM_SYSTEM = (
    "You are a precise procurement-document fact extractor. "
    "Return JSON only. Quote evidence verbatim from the supplied text. "
    "Never fabricate."
)

RERANK_PER_SECTION_CHAR_CAP = 4000


def _truncate_for_rerank(text: str, cap: int = RERANK_PER_SECTION_CHAR_CAP) -> tuple[str, bool]:
    """Head 60% + tail 40% — keeps clauses buried at the end of long
    sections reachable by the LLM (mirrors the PBG truncator)."""
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
        head
        + "\n\n[... middle of section elided for rerank prompt ...]\n\n"
        + tail
    ), True


def build_emd_rerank_prompt(candidates: list[dict]) -> str:
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
        "Exactly ONE of them (or none) is the actual Bid Security / Earnest "
        "Money Deposit (EMD) clause stating the EMD percentage of estimated "
        "contract value or the EMD as a fixed INR amount.\n\n"
        f"{candidates_block}\n\n"
        "Question: Which candidate states the Bid Security / EMD value, and "
        "what does it say?\n\n"
        "Return JSON only:\n"
        "{\n"
        "  \"chosen_index\": integer 0..N-1 of the EMD candidate, OR null if no candidate states EMD,\n"
        "  \"total_pct\":   float OR null   (total EMD percentage of estimated contract value, e.g. 1.0, 2.5, 5.0),\n"
        "  \"stage1_pct\":  float OR null   (AP-style 'paid at issue of tender documents'; null if not two-stage),\n"
        "  \"stage2_pct\":  float OR null   (AP-style 'paid at agreement / by successful bidder'; null if not two-stage),\n"
        "  \"two_stage\":   bool            (true ONLY if document explicitly describes BOTH at-issue and at-agreement payments),\n"
        "  \"amount_cr\":   float OR null   (EMD as a fixed INR amount in crores; '50 lakh' → 0.5; '1.255 crore' → 1.255),\n"
        "  \"evidence\":    \"verbatim quote from the chosen candidate's text stating EMD\",\n"
        "  \"found\":       bool,\n"
        "  \"reasoning\":   \"one short sentence explaining the choice\"\n"
        "}\n\n"
        "Selection rules — IGNORE the following content (not EMD):\n"
        "- Performance Security / Performance Bank Guarantee / PBG percentages "
        "  (a different instrument; we already have a separate check for that).\n"
        "- Retention money (e.g. 7½% withheld, 2½% on completion).\n"
        "- Liquidated-damages percentages (e.g. 0.1% per day of contract value).\n"
        "- Earnest Money FORFEITURE / FALL-BACK clauses (these describe what "
        "  happens to EMD on default — not the EMD value itself).\n"
        "- Bid Security / EMD VALIDITY clauses (period the BG must remain "
        "  valid — not the EMD percentage/amount).\n"
        "\n"
        "Selection rules — DO pick if the candidate states:\n"
        "- 'EMD shall be paid at X% of the estimated contract value' "
        "  → total_pct=X, two_stage=false.\n"
        "- 'Bid Security amounting to X% of the Estimated Contract Value' "
        "  → total_pct=X, two_stage=false.\n"
        "- AP two-stage example: 'EMD collected in two slabs: 1% at issue of "
        "  tender documents + 1.5% at agreement, total 2.5%' → "
        "  total_pct=2.5, stage1_pct=1.0, stage2_pct=1.5, two_stage=true.\n"
        "- 'X% of the Estimated Contract Value (ECV) Rs.Y' → "
        "  total_pct=X, amount_cr=Y/100 (Rs.1,25,50,000 = 1.255 crore).\n"
        "\n"
        "- Always normalise amounts to crores. 1 crore = 100 lakh.\n"
        "- Evidence MUST be an exact substring of the chosen candidate's text.\n"
        "- If no candidate states an EMD value, set chosen_index=null, "
        "total_pct=null, amount_cr=null, found=false."
    )


def call_llm(system: str, user: str) -> tuple[str, dict]:
    """Same retry semantics as scripts/tier1_pbg_check.py::call_llm
    (one retry on empty `resp.choices`, then raise). The transient was
    observed mid-batch on Vizag — upstream returned 200 with no
    choices, crashing `resp.choices[0]`. With the retry, the same
    payload usually succeeds 2 seconds later."""
    from openai import OpenAI
    client = OpenAI(base_url=LLM_BASE_URL.rstrip("/"), api_key=LLM_API_KEY)
    extra_headers = {
        "HTTP-Referer": "https://github.com/konevenkatesh/procureAI",
        "X-Title": "AP Procurement Validator",
    }
    kwargs = dict(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        temperature=0.0,
        max_tokens=700,
        extra_headers=extra_headers,
    )
    resp = client.chat.completions.create(**kwargs)
    if not resp.choices or len(resp.choices) == 0:
        print("  [call_llm] empty choices on first attempt — retrying once after 2s")
        time.sleep(2)
        resp = client.chat.completions.create(**kwargs)
        if not resp.choices or len(resp.choices) == 0:
            raise RuntimeError("OpenRouter empty choices (after retry)")
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


# ── Rule selection via condition_evaluator ────────────────────────────

def fetch_tender_facts(doc_id: str) -> dict:
    rows = rest_get("kg_nodes", {
        "select":    "properties",
        "doc_id":    f"eq.{doc_id}",
        "node_type": "eq.TenderDocument",
    })
    if not rows:
        return {}
    p = rows[0].get("properties") or {}
    return {
        "tender_type":         p.get("tender_type"),
        "is_ap_tender":        bool(p.get("is_ap_tender")),
        "TenderType":          p.get("tender_type"),
        "TenderState":         "AndhraPradesh" if p.get("is_ap_tender") else "Other",
        # GFR-G-049's condition is `BidSecurityRequired=true`. For Works /
        # EPC / Goods / PPP bid security is mandatory under AP and GFR
        # alike; only Consultancy under GFR Rule 170 typically waives it.
        "BidSecurityRequired": True,
        "EstimatedValue":      p.get("estimated_value_cr") or p.get("estimated_value_classified") or 0,
    }


def select_emd_rule(tender_facts: dict) -> dict | None:
    """Iterate RULE_CANDIDATES, evaluate each rule's condition_when
    (read from the rules table) against tender_facts. Apply
    defeasibility — drop any rule that another fired rule defeats.
    Return the first surviving FIRE rule (highest priority by
    candidate order)."""
    fired: list[dict] = []
    print(f"\n  Rule selection — facts: tender_type={tender_facts.get('tender_type')!r}, "
          f"is_ap_tender={tender_facts.get('is_ap_tender')}")
    for cand in RULE_CANDIDATES:
        rid = cand["rule_id"]
        rule_rows = rest_get("rules", {
            "select":  "rule_id,condition_when,defeats",
            "rule_id": f"eq.{rid}",
        })
        if not rule_rows:
            print(f"    [{rid}] not found in rules table")
            continue
        cw = rule_rows[0].get("condition_when") or ""
        verdict = evaluate_when(cw, tender_facts).verdict
        defeats = rule_rows[0].get("defeats") or []
        print(f"    [{rid}] condition_when={cw!r}  verdict={verdict.value}")
        if verdict == Verdict.FIRE:
            fired.append(dict(cand, defeats=defeats))

    defeated_ids = set()
    for f in fired:
        for victim in (f.get("defeats") or []):
            defeated_ids.add(victim)
    surviving = [f for f in fired if f["rule_id"] not in defeated_ids]
    if not surviving:
        print(f"  → no rule fires for these facts")
        return None
    chosen = surviving[0]
    print(f"  → selected {chosen['rule_id']} (defeated_in_chain: {sorted(defeated_ids)})")
    return chosen


def evaluate_emd_against_rule(rule: dict, total_pct: float | None,
                              two_stage: bool) -> tuple[bool, str]:
    """Pure function. Returns (is_violation, reason_label)."""
    if total_pct is None:
        return False, "no_percentage_extracted"

    if rule["shape"] == "ap_two_stage":
        target = float(rule["target_total_pct"])
        if abs(total_pct - target) > 0.01:
            return True, (
                f"total_pct={total_pct} != target={target} "
                f"(AP-GO-050 expects {target}%)"
            )
        if not two_stage:
            return True, (
                f"total_pct={total_pct} matches target but two-stage "
                f"payment (1% at issue + 1.5% at agreement) NOT detected"
            )
        return False, "compliant_two_stage"

    if rule["shape"] == "range":
        lo = float(rule["min_pct"]); hi = float(rule["max_pct"])
        if total_pct < lo: return True, f"total_pct={total_pct} below min={lo}"
        if total_pct > hi: return True, f"total_pct={total_pct} above max={hi}"
        return False, "compliant_range"

    raise ValueError(f"unknown rule shape: {rule['shape']}")


# ── Idempotent re-run cleanup ─────────────────────────────────────────

def _delete_prior_tier1_emd(doc_id: str) -> tuple[int, int]:
    edges = rest_get("kg_edges", {
        "select": "edge_id",
        "doc_id": f"eq.{doc_id}",
        "edge_type": "eq.VIOLATES_RULE",
        "properties->>typology": f"eq.{TYPOLOGY}",
        "properties->>tier":     "eq.1",
    })
    n_e = 0
    for e in edges:
        rest_delete("kg_edges", {"edge_id": f"eq.{e['edge_id']}"}); n_e += 1
    findings = rest_get("kg_nodes", {
        "select": "node_id",
        "doc_id": f"eq.{doc_id}",
        "node_type": "eq.ValidationFinding",
        "properties->>typology_code": f"eq.{TYPOLOGY}",
        "properties->>tier":          "eq.1",
    })
    n_f = 0
    for f in findings:
        rest_delete("kg_nodes", {"node_id": f"eq.{f['node_id']}"}); n_f += 1
    return n_f, n_e


def get_or_create_rule_node(doc_id: str, rule_id: str) -> str:
    existing = rest_get("kg_nodes", {
        "select":    "node_id",
        "doc_id":    f"eq.{doc_id}",
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

def main() -> int:
    timings: dict[str, float] = {}
    t_start = time.perf_counter()

    print("=" * 76)
    print(f"  Tier-1 EMD-Shortfall (BGE-M3 + LLM, NO regex)")
    print(f"  doc_id : {DOC_ID}")
    print(f"  model  : {LLM_MODEL}")
    print("=" * 76)

    # 0. Cleanup any prior Tier-1 EMD finding for this doc
    n_f, n_e = _delete_prior_tier1_emd(DOC_ID)
    if n_f or n_e:
        print(f"  cleared {n_f} prior Tier-1 EMD finding node(s) and "
              f"{n_e} edge(s) before re-running")

    # 1. Pick rule via condition_evaluator
    facts = fetch_tender_facts(DOC_ID)
    rule  = select_emd_rule(facts)
    if rule is None:
        return 0

    # 2. Family + section_type filter (router)
    family, section_types = family_for_doc_with_filter(DOC_ID, TYPOLOGY)
    print(f"\n── Document family / retrieval filter ──")
    print(f"  family         : {family}")
    print(f"  section_types  : {section_types}")

    # 3. BGE-M3 embed
    print(f"\n── Query (answer-shaped) ──")
    print(f"  ({len(QUERY_TEXT)} chars)")
    print(f"  {QUERY_TEXT}")
    t0 = time.perf_counter()
    qvec = embed_query(QUERY_TEXT)
    timings["embed"] = time.perf_counter() - t0
    print(f"\n── Step 1: BGE-M3 embed ── vec_dim={len(qvec)}  wall={timings['embed']:.2f}s")

    # 4. Qdrant top-10 within the family-specific filter
    K = 10
    t0 = time.perf_counter()
    print(f"\n── Step 2: Qdrant top-{K} (family={family}, section_type ∈ {section_types}) ──")
    points = qdrant_topk(qvec, DOC_ID, k=K, section_types=section_types)
    timings["qdrant"] = time.perf_counter() - t0
    print(f"  {len(points)} candidate(s) returned in {timings['qdrant']*1000:.0f}ms:")
    for i, p in enumerate(points):
        pl = p["payload"]
        h  = (pl.get("heading") or pl.get("section_heading") or "")[:60]
        print(f"    [{i}] cosine={p['score']:.4f}  type={pl.get('section_type','?'):5s}  "
              f"lines={pl.get('line_start_local')}-{pl.get('line_end_local')}  {h}")

    # 5. Resolve all candidates
    t0 = time.perf_counter()
    candidates = []
    for p in points:
        sec = resolve_section(DOC_ID, p["payload"])
        sec["similarity"] = p["score"]
        candidates.append(sec)
    timings["fetch_section"] = time.perf_counter() - t0

    # 6. LLM rerank + extraction
    t0 = time.perf_counter()
    print(f"\n── Step 3: LLM rerank + EMD extraction ──")
    user_prompt = build_emd_rerank_prompt(candidates)
    print(f"  prompt size: {len(user_prompt)} chars (~{len(user_prompt)//4} tokens)")
    raw_content, _ = call_llm(LLM_SYSTEM, user_prompt)
    timings["llm"] = time.perf_counter() - t0
    print(f"  wall: {timings['llm']:.2f}s")
    print(f"\n── Raw LLM JSON ──")
    print(raw_content)

    # 7. Parse + select chosen candidate
    parsed = parse_llm_response(raw_content)
    chosen     = parsed.get("chosen_index")
    total_pct  = parsed.get("total_pct")
    stage1_pct = parsed.get("stage1_pct")
    stage2_pct = parsed.get("stage2_pct")
    two_stage  = bool(parsed.get("two_stage"))
    amount_cr  = parsed.get("amount_cr")
    evidence   = (parsed.get("evidence") or "").strip()
    found      = bool(parsed.get("found"))
    reason     = (parsed.get("reasoning") or "").strip()

    print(f"\n── Parsed ──")
    print(f"  chosen_index : {chosen}")
    print(f"  found        : {found}")
    print(f"  total_pct    : {total_pct}")
    print(f"  stage1_pct   : {stage1_pct}")
    print(f"  stage2_pct   : {stage2_pct}")
    print(f"  two_stage    : {two_stage}")
    print(f"  amount_cr    : {amount_cr}")
    print(f"  reasoning    : {reason[:200]}")
    print(f"  evidence     : {evidence[:300]!r}")

    if chosen is None or not found:
        print(f"\n  → no EMD candidate identified; no finding emitted")
        return 0

    if not isinstance(chosen, int) or not (0 <= chosen < len(candidates)):
        print(f"  → chosen_index out of range; no finding emitted")
        return 0

    section = candidates[chosen]
    similarity = section["similarity"]
    print(f"  → using candidate [{chosen}]: {section['heading'][:60]} "
          f"(cosine={similarity:.4f})")

    # 8. Hallucination guard (L24) — verify evidence is in the section
    ev_passed, ev_score, ev_method = verify_evidence_in_section(
        evidence, section["full_text"]
    )
    print(f"  evidence_verified : {ev_passed}  (score={ev_score}, method={ev_method})")
    if not ev_passed:
        print(f"  HALLUCINATION_DETECTED — discarding extraction.")
        print(f"    LLM evidence  : {evidence[:200]!r}")
        print(f"    section first : {section['full_text'][:200]!r}")
        return 0

    # 9. Apply rule check
    is_violation, reason_label = evaluate_emd_against_rule(rule, total_pct, two_stage)
    print(f"\n── Decision ──")
    print(f"  rule         : {rule['rule_id']} ({rule['severity']}, shape={rule['shape']})")
    print(f"  reason_label : {reason_label}")
    print(f"  is_violation : {is_violation}")

    if not is_violation:
        # No finding for compliant — same shape as PBG (only emit on
        # violation; ground-truth-of-no-violation is implicit in "no
        # finding row exists"). For EMD we may want to emit a
        # PASS-recorded ValidationFinding later for completeness, but
        # match the PBG pattern tonight.
        return 0

    # 10. Materialise finding + edge
    t0 = time.perf_counter()
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
            "evidence":           evidence,
            "extraction_path":    "percentage" if total_pct is not None else "amount",
            "total_pct":          total_pct,
            "stage1_pct":         stage1_pct,
            "stage2_pct":         stage2_pct,
            "two_stage":          two_stage,
            "amount_cr":          amount_cr,
            "rule_shape":         rule["shape"],
            "rule_target_total":  rule.get("target_total_pct"),
            "rule_min_pct":       rule.get("min_pct"),
            "rule_max_pct":       rule.get("max_pct"),
            "violation_reason":   reason_label,
            "tier":               1,
            "extracted_by":       "bge-m3+llm-rerank:qwen-2.5-72b@openrouter",
            "retrieval_strategy": (
                f"qdrant_top{K}_router_{family}_section_filter_"
                f"{'-'.join(section_types)}_llm_rerank"
            ),
            "doc_family":          family,
            "section_filter":      section_types,
            "rerank_chosen_index": chosen,
            "rerank_reasoning":    reason,
            "section_node_id":     section_node_id,
            "section_heading":     section["heading"],
            "source_file":         section["source_file"],
            "line_start_local":    section["line_start_local"],
            "line_end_local":      section["line_end_local"],
            "qdrant_similarity":   round(similarity, 4),
            # L24 audit fields
            "evidence_in_source":    ev_passed,
            "evidence_verified":     ev_passed,
            "evidence_match_score":  ev_score,
            "evidence_match_method": ev_method,
            "status":              "OPEN",
            "defeated":            False,
        },
        "source_ref": f"tier1:emd_check:{rule['rule_id']}",
    }])[0]

    edge = rest_post("kg_edges", [{
        "doc_id":       DOC_ID,
        "from_node_id": section_node_id,
        "to_node_id":   rule_node_id,
        "edge_type":    "VIOLATES_RULE",
        "weight":       1.0,
        "properties": {
            "rule_id":            rule["rule_id"],
            "typology":           TYPOLOGY,
            "severity":           rule["severity"],
            "defeated":           False,
            "tier":               1,
            "total_pct":          total_pct,
            "stage1_pct":         stage1_pct,
            "stage2_pct":         stage2_pct,
            "two_stage":          two_stage,
            "amount_cr":          amount_cr,
            "evidence":           evidence,
            "qdrant_similarity":  round(similarity, 4),
            "violation_reason":   reason_label,
            "doc_family":         family,
            "evidence_match_score":  ev_score,
            "evidence_match_method": ev_method,
            "finding_node_id":    finding["node_id"],
        },
    }])[0]
    timings["materialise"] = time.perf_counter() - t0
    print(f"  → ValidationFinding {finding['node_id']}")
    print(f"  → VIOLATES_RULE     {edge['edge_id']}  Section→Rule")

    # Summary
    timings["total_wall"] = time.perf_counter() - t_start
    print()
    print("=" * 76)
    print("  TIMING SUMMARY")
    print("=" * 76)
    for k, v in timings.items():
        unit = "s" if v >= 1 else "ms"
        val  = v if v >= 1 else v * 1000
        print(f"    {k:18s} {val:8.2f} {unit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
