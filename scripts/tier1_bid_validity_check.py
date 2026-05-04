"""
scripts/tier1_bid_validity_check.py

Tier-1 Bid-Validity-Short check, BGE-M3 + LLM, NO regex.

Same shape as scripts/tier1_emd_check.py:
    1. Section filter from the document family router
       (BID_VALIDITY_SECTION_ROUTER per L25-spec).
    2. BGE-M3 embed an answer-shaped query for bid-validity wording.
    3. Qdrant top-10 candidates within the filter.
    4. LLM rerank — pick the single bid-validity candidate AND
       extract days/months/weeks + days_normalised in one call.
       Explicit ignore rules: bid-security validity, bank-guarantee
       validity, contract period, DLP period, warranty period.
    5. Hallucination guard (L24): verify evidence is in the chosen
       section's full_text. Discard on score < 85.
    6. Rule selection via condition_evaluator on AP-GO-067 (AP Works
       only, WARNING, threshold 90 days) and MPG-073 (TenderType=ANY,
       HARD_BLOCK, threshold 75 days OTE).
       Defeasibility: AP-GO-067 wins on AP Works by candidate-list
       priority; the AP rule's `defeats=[MPW-066]` is preserved.
       MPW25-050 IS NOT in AP-GO-067's defeats array — knowledge
       layer gap. We detect when this gap fires (AP Works + MPW25-050
       FIREs) and record it on the finding as `defeasibility_gap`.
    7. Apply the rule's check shape:
         min_days : violation if days_normalised < min_days
       (Decision 5: only shortfall, not too-long. CVC-075 range top
        bound is out of scope for this typology.)
    8. Materialise ValidationFinding + VIOLATES_RULE with L24 audit
       fields from the start.

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
from modules.validation.evidence_guard   import verify_evidence_in_section
from modules.validation.section_router   import family_for_doc_with_filter
from modules.validation.llm_client       import call_llm


# ── Constants ─────────────────────────────────────────────────────────

DOC_ID = sys.argv[1] if len(sys.argv) > 1 else "judicial_academy_exp_001"

TYPOLOGY = "Bid-Validity-Short"

QDRANT_URL  = os.environ.get("QDRANT_URL", "http://localhost:6333")
COLLECTION  = "tender_sections"

# Kept for the run-banner printout only; the actual call goes through
# modules.validation.llm_client which reads the same env var.
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen/qwen-2.5-72b-instruct")


# Answer-shaped query — mirrors the literal wording AP/Central tender
# docs use for bid-validity ("Bids shall remain valid for X days from
# the bid submission deadline / opening of tenders"). Per L12, the
# query should sound like the answer, not the rule.
QUERY_TEXT = (
    "bid validity period days bids shall remain valid "
    "from date of submission opening tender"
)


# Rule candidates evaluated via condition_evaluator. Order is
# priority order: the first FIRE rule (after defeasibility filter)
# wins. AP-GO-067 must come first so AP Works tenders pick it
# instead of MPG-073.
RULE_CANDIDATES = [
    {
        "rule_id":         "AP-GO-067",
        "natural_language": "AP works tenders shall be valid for 1, 2, or 3 months as specified",
        "severity":        "WARNING",     # Decision 3 — carry from rule, no escalation
        "layer":           "AP-State",
        "shape":           "min_days",
        "min_days":        90,            # Decision 2 — rule's natural_language, not the 180-day clause-template default
        "threshold_basis": "AP-GO-067 §1, 'valid for 1, 2, or 3 months' — upper bound 3 months × 30 = 90 days",
    },
    {
        "rule_id":         "MPG-073",
        "natural_language": "GFR Goods/General: bid validity normally 75 days OTE / 90 days GTE",
        "severity":        "HARD_BLOCK",
        "layer":           "Central",
        "shape":           "min_days",
        "min_days":        75,            # Decision 4 — OTE default; GTE noted in audit
        "threshold_basis": "MPG-073 — 75 days OTE (default) / 90 days GTE (procurement-mode unknown)",
    },
]

# Rules that fire on AP Works but are NOT in AP-GO-067's defeats
# array — knowledge-layer wiring gap. Recorded on the finding so a
# downstream review can decide whether to extend the defeats wiring.
# (See L23 / L24 for similar deferred-typology / honest-gap framing.)
DEFEASIBILITY_GAP_RULES = ["MPW25-050"]


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


# ── LLM rerank prompt for Bid-Validity ────────────────────────────────

LLM_SYSTEM = (
    "You are a precise procurement-document fact extractor. "
    "Return JSON only. Quote evidence verbatim from the supplied text. "
    "Never fabricate."
)

RERANK_PER_SECTION_CHAR_CAP = 4000


def _truncate_for_rerank(text: str, cap: int = RERANK_PER_SECTION_CHAR_CAP) -> tuple[str, bool]:
    """Head 60% + tail 40% truncator — kept for back-compat with PBG/EMD.
    Bid-validity uses `smart_truncate` instead because the answer
    sometimes lives in the elided middle of a long ITB-rewrite block
    (JA's section 464-542 is 13K chars; "NINETY (90) days" sits at
    offset 11,527 — between head end 2400 and tail start 11,682)."""
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


# Keyword-aware windowing — anchor the truncation window on the
# earliest occurrence of any bid-validity keyword in the section.
# When a section is long (>3000 chars) and the validity wording is
# buried in the middle, head+tail truncation drops it; this slides
# the window to where the answer actually is.
SMART_TRUNCATE_KEYWORDS = [
    r"bid validity",
    r"bids shall remain valid",
    r"validity period",
    r"remain valid for",
    # Spelled-out day counts (cover 30/60/90/120/180 day common values)
    r"\bninety\b", r"\bsixty\b", r"\bthirty\b", r"\beighty\b",
    r"one hundred twenty", r"hundred eighty",
    # Patterns: "validity ... days" / "days ... validity" within ~50 chars
    r"validity[^.]{0,50}days",
    r"days[^.]{0,50}validity",
]


def smart_truncate(text: str, window: int = 3000) -> str:
    """Centre a `window`-sized slice on the earliest keyword hit in the
    section. If no keyword matches, fall back to head+tail (2400/1600)
    so the LLM still sees both ends.

    Window=3000 with K=15 candidates ≈ 45K chars in the rerank prompt
    (well within qwen-72b's 128K context).
    """
    if len(text) <= window:
        return text

    text_lower = text.lower()
    earliest = len(text)
    for kw in SMART_TRUNCATE_KEYWORDS:
        m = re.search(kw, text_lower)
        if m and m.start() < earliest:
            earliest = m.start()

    if earliest < len(text):
        # Centre the window on the earliest keyword hit
        half = window // 2
        start = max(0, earliest - half)
        end   = min(len(text), earliest + half)
        prefix = "[... section start elided ...]\n\n" if start > 0 else ""
        suffix = "\n\n[... section end elided ...]"     if end < len(text) else ""
        return prefix + text[start:end] + suffix

    # No keyword hit — keep both ends
    return text[:2400] + "\n\n[... middle of section elided ...]\n\n" + text[-1600:]


def build_validity_rerank_prompt(candidates: list[dict]) -> str:
    blocks = []
    for i, c in enumerate(candidates):
        body = smart_truncate(c["full_text"])
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
        "Exactly ONE of them (or none) is the BID VALIDITY clause — the period during "
        "which a submitted bid (offer) must remain open and binding on the bidder, "
        "starting from the bid submission deadline.\n\n"
        f"{candidates_block}\n\n"
        "Question: Which candidate states the BID VALIDITY period (the bid/tender "
        "validity, NOT any other validity)?\n\n"
        "Return JSON only:\n"
        "{\n"
        "  \"chosen_index\":     integer 0..N-1 of the bid-validity candidate, OR null if no candidate states it,\n"
        "  \"validity_days\":    integer OR null  (validity period stated in DAYS, e.g. 90, 120, 180),\n"
        "  \"validity_months\":  integer OR null  (validity period stated in MONTHS, e.g. 1, 2, 3, 6),\n"
        "  \"validity_weeks\":   integer OR null  (validity period stated in WEEKS, rare),\n"
        "  \"days_normalised\":  integer OR null  (normalised to days; see normalisation rule below),\n"
        "  \"evidence\":         \"verbatim quote from the chosen candidate stating the bid validity\",\n"
        "  \"found\":            bool,\n"
        "  \"reasoning\":        \"one short sentence explaining the choice\"\n"
        "}\n\n"
        "Normalisation rule for days_normalised:\n"
        "  days_normalised = validity_days, OR\n"
        "                    validity_months × 30, OR\n"
        "                    validity_weeks × 7\n"
        "  (one of the three must be set; days_normalised = whichever is non-null × its multiplier)\n"
        "\n"
        "Selection rules — IGNORE the following content (NOT bid validity):\n"
        "- Bid SECURITY validity (the period the bank guarantee / EMD bond must remain "
        "  valid — typically 180 days from bid submission). This is a DIFFERENT validity.\n"
        "- BANK GUARANTEE validity, INSURANCE SURETY BOND validity, EBG validity.\n"
        "- CONTRACT PERIOD / Concession Period (the time to execute the work, post-award).\n"
        "- DEFECTS LIABILITY PERIOD (DLP) / DEFECT LIABILITY PERIOD.\n"
        "- WARRANTY PERIOD / GUARANTEE PERIOD.\n"
        "- Any validity that is not the period during which the BID/TENDER itself "
        "  remains open after submission.\n"
        "\n"
        "Selection rules — DO pick if the candidate states:\n"
        "- 'Bids shall remain valid for X days from the date of bid submission/opening' → "
        "  validity_days=X, days_normalised=X.\n"
        "- 'Bid validity: X days' / 'Period of validity of bids: X days' → "
        "  validity_days=X.\n"
        "- 'The Bidder agrees to keep the offer in this tender valid for a period of X "
        "  month(s)' → validity_months=X, days_normalised=X*30.\n"
        "- 'The bid validity period shall be NINETY (90) days' → validity_days=90.\n"
        "- 'Validity of bids: 6 months' → validity_months=6, days_normalised=180.\n"
        "\n"
        "- Evidence MUST be an exact substring of the chosen candidate's text.\n"
        "- If no candidate states a bid-validity period (only the rule references it "
        "  abstractly), set chosen_index=null, all numeric fields=null, found=false."
    )


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
        "tender_type":      p.get("tender_type"),
        "is_ap_tender":     bool(p.get("is_ap_tender")),
        "TenderType":       p.get("tender_type"),
        "TenderState":      "AndhraPradesh" if p.get("is_ap_tender") else "Other",
        "EstimatedValue":   p.get("estimated_value_cr") or p.get("estimated_value_classified") or 0,
    }


def select_validity_rule(tender_facts: dict) -> tuple[dict | None, list[str]]:
    """Iterate RULE_CANDIDATES in priority order, evaluate condition_when
    against tender_facts, return (chosen_rule, defeasibility_gaps).

    defeasibility_gaps: rules from DEFEASIBILITY_GAP_RULES that ALSO
    fire on the same facts and are NOT in the chosen rule's defeats
    array — the audit trail records this so a downstream knowledge-layer
    review can extend the defeats wiring.
    """
    fired: list[dict] = []
    print(f"\n  Rule selection — facts: tender_type={tender_facts.get('tender_type')!r}, "
          f"is_ap_tender={tender_facts.get('is_ap_tender')}")
    for cand in RULE_CANDIDATES:
        rid = cand["rule_id"]
        rows = rest_get("rules", {
            "select":  "rule_id,condition_when,defeats",
            "rule_id": f"eq.{rid}",
        })
        if not rows:
            print(f"    [{rid}] not found in rules table")
            continue
        cw = rows[0].get("condition_when") or ""
        verdict = evaluate_when(cw, tender_facts).verdict
        defeats = rows[0].get("defeats") or []
        print(f"    [{rid}] condition_when={cw!r}  verdict={verdict.value}  defeats={defeats}")
        if verdict == Verdict.FIRE:
            fired.append(dict(cand, defeats=defeats))

    # Defeasibility filter — drop any rule whose rule_id is in another
    # fired rule's defeats list (mirrors the EMD pattern).
    defeated_ids = set()
    for f in fired:
        for victim in (f.get("defeats") or []):
            defeated_ids.add(victim)
    surviving = [f for f in fired if f["rule_id"] not in defeated_ids]
    if not surviving:
        print(f"  → no rule fires for these facts")
        return None, []
    chosen = surviving[0]   # priority by candidate-list order

    # Detect knowledge-layer defeasibility gaps — rules that should
    # arguably be defeated by the AP-state rule but aren't in its
    # defeats array. Flag, don't fail.
    gaps: list[str] = []
    if chosen["rule_id"] == "AP-GO-067":
        for rid in DEFEASIBILITY_GAP_RULES:
            rows = rest_get("rules", {
                "select":  "rule_id,condition_when",
                "rule_id": f"eq.{rid}",
            })
            if not rows:
                continue
            cw = rows[0].get("condition_when") or ""
            v  = evaluate_when(cw, tender_facts).verdict
            if v == Verdict.FIRE:
                gaps.append(rid)

    if gaps:
        print(f"  ⚠ defeasibility gap: AP-GO-067 chosen but {gaps} also FIRE "
              f"(not in AP-GO-067.defeats; recording on finding)")

    print(f"  → selected {chosen['rule_id']} "
          f"(severity={chosen['severity']}, threshold={chosen['min_days']} days)")
    return chosen, gaps


def evaluate_validity_against_rule(rule: dict,
                                   days_normalised: int | None) -> tuple[bool, str]:
    """Pure function. Returns (is_violation, reason_label). Decision 5:
    only flag too-short, not too-long."""
    if days_normalised is None:
        return False, "no_days_extracted"
    if rule["shape"] == "min_days":
        threshold = int(rule["min_days"])
        if days_normalised < threshold:
            return True, (
                f"days_normalised={days_normalised} below min={threshold} "
                f"({rule['rule_id']} {rule['threshold_basis']})"
            )
        return False, "compliant"
    raise ValueError(f"unknown rule shape: {rule['shape']}")


# ── Idempotent re-run cleanup ─────────────────────────────────────────

def _delete_prior_tier1_validity(doc_id: str) -> tuple[int, int]:
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
    print(f"  Tier-1 Bid-Validity-Short (BGE-M3 + LLM, NO regex)")
    print(f"  doc_id : {DOC_ID}")
    print(f"  model  : {LLM_MODEL}")
    print("=" * 76)

    n_f, n_e = _delete_prior_tier1_validity(DOC_ID)
    if n_f or n_e:
        print(f"  cleared {n_f} prior Tier-1 Bid-Validity finding node(s) and "
              f"{n_e} edge(s) before re-running")

    # 1. Pick rule via condition_evaluator
    facts = fetch_tender_facts(DOC_ID)
    rule, defeasibility_gap = select_validity_rule(facts)
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

    # 4. Qdrant top-15 — bid-validity values are short (~20 chars
    # in a wider section) and JA's "NINETY (90) days" anchor sat at
    # rank 13 in the [ITB, NIT] filter pool. K=15 catches it without
    # changing the query/prompt/router. (PBG/EMD use K=10; this
    # typology runs slightly higher.)
    K = 15
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
    print(f"\n── Step 3: LLM rerank + bid-validity extraction ──")
    user_prompt = build_validity_rerank_prompt(candidates)
    print(f"  prompt size: {len(user_prompt)} chars (~{len(user_prompt)//4} tokens)")
    raw_content = call_llm(LLM_SYSTEM, user_prompt, max_tokens=700)
    timings["llm"] = time.perf_counter() - t0
    print(f"  wall: {timings['llm']:.2f}s")
    print(f"\n── Raw LLM JSON ──")
    print(raw_content)

    # 7. Parse + select chosen candidate
    parsed = parse_llm_response(raw_content)
    chosen          = parsed.get("chosen_index")
    validity_days   = parsed.get("validity_days")
    validity_months = parsed.get("validity_months")
    validity_weeks  = parsed.get("validity_weeks")
    days_normalised = parsed.get("days_normalised")
    evidence        = (parsed.get("evidence") or "").strip()
    found           = bool(parsed.get("found"))
    reason          = (parsed.get("reasoning") or "").strip()

    print(f"\n── Parsed ──")
    print(f"  chosen_index    : {chosen}")
    print(f"  found           : {found}")
    print(f"  validity_days   : {validity_days}")
    print(f"  validity_months : {validity_months}")
    print(f"  validity_weeks  : {validity_weeks}")
    print(f"  days_normalised : {days_normalised}")
    print(f"  reasoning       : {reason[:200]}")
    print(f"  evidence        : {evidence[:300]!r}")

    if chosen is None or not found:
        print(f"\n  → no bid-validity candidate identified; no finding emitted")
        return 0

    if not isinstance(chosen, int) or not (0 <= chosen < len(candidates)):
        print(f"  → chosen_index out of range; no finding emitted")
        return 0

    section = candidates[chosen]
    similarity = section["similarity"]
    print(f"  → using candidate [{chosen}]: {section['heading'][:60]} "
          f"(cosine={similarity:.4f})")

    # 8. Hallucination guard (L24)
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
    is_violation, reason_label = evaluate_validity_against_rule(rule, days_normalised)
    print(f"\n── Decision ──")
    print(f"  rule           : {rule['rule_id']} ({rule['severity']}, "
          f"shape={rule['shape']}, threshold={rule['min_days']} days)")
    print(f"  days_normalised: {days_normalised}")
    print(f"  reason_label   : {reason_label}")
    print(f"  is_violation   : {is_violation}")
    if defeasibility_gap:
        print(f"  defeasibility_gap : {defeasibility_gap}")

    if not is_violation:
        # Decision 5: only emit on shortfall. Compliant case is implicit
        # "no finding row" — same shape as PBG/EMD compliance.
        return 0

    # 10. Materialise finding + edge
    t0 = time.perf_counter()
    section_node_id = section["section_node_id"]
    rule_node_id    = get_or_create_rule_node(DOC_ID, rule["rule_id"])

    label = (
        f"{TYPOLOGY}: bid validity = {days_normalised} days "
        f"(expected ≥ {rule['min_days']} days per {rule['rule_id']}); "
        f"reason: {reason_label}"
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
            "extraction_path":    "days",
            "validity_days":      validity_days,
            "validity_months":    validity_months,
            "validity_weeks":     validity_weeks,
            "days_normalised":    days_normalised,
            "rule_shape":         rule["shape"],
            "rule_min_days":      rule["min_days"],
            "rule_threshold_basis": rule["threshold_basis"],
            "violation_reason":   reason_label,
            # Defeasibility audit (L25-style)
            "defeasibility_gap":  defeasibility_gap,    # rules that fire but aren't in chosen rule's defeats
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
        "source_ref": f"tier1:bid_validity_check:{rule['rule_id']}",
    }])[0]

    edge = rest_post("kg_edges", [{
        "doc_id":       DOC_ID,
        "from_node_id": section_node_id,
        "to_node_id":   rule_node_id,
        "edge_type":    "VIOLATES_RULE",
        "weight":       1.0,
        "properties": {
            "rule_id":              rule["rule_id"],
            "typology":             TYPOLOGY,
            "severity":             rule["severity"],
            "defeated":             False,
            "tier":                 1,
            "days_normalised":      days_normalised,
            "rule_min_days":        rule["min_days"],
            "evidence":             evidence,
            "qdrant_similarity":    round(similarity, 4),
            "violation_reason":     reason_label,
            "doc_family":           family,
            "defeasibility_gap":    defeasibility_gap,
            "evidence_match_score":  ev_score,
            "evidence_match_method": ev_method,
            "finding_node_id":      finding["node_id"],
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
