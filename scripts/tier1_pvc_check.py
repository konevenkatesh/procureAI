"""
scripts/tier1_pvc_check.py

Tier-1 Missing-PVC-Clause check, BGE-M3 + LLM, NO regex.

PRESENCE shape (vs the THRESHOLD shape of PBG/EMD/Bid-Validity):
  - "Does the document contain a Price Variation / Price Adjustment
     clause?" rather than "is value X above/below threshold Y?"
  - LLM extracts pvc_present (bool) + optional formula_breakdown for
    the audit trail; no numeric threshold to compare.
  - Per Decision 5 from the read-first round: presence-only this
    round; the formula-structure check (3-element 10-25%/material/
    labour) is deferred (defeasibility-rich, mostly WARNING-level —
    fits a follow-on round).

Pipeline (same shape as bid-validity):
  1. Pick rule via condition_evaluator on AP-GO-019 (AP-State, HARD_BLOCK,
     condition: TenderState=AP AND TenderType IN [Works, EPC] AND
     EstimatedValue >= 4000000 [rupees] AND OriginalContractPeriodMonths
     >= 6) and MPW-133 (Central, HARD_BLOCK, TenderType=Works AND
     ContractDuration > 18 months).
     Defeasibility: AP-GO-019.defeats=[MPW-133, MPW25-094] — AP Works
     picks AP-GO-019; non-AP Works > 18 months picks MPW-133;
     PPP / Goods / Services / short-duration → no rule fires → SKIP.
  2. Section filter via PVC_SECTION_ROUTER:
        APCRDA_Works  → [GCC, SCC, Specifications]
        SBD_Format    → [GCC, SCC, Evaluation]
        NREDCAP_PPP   → [GCC] (rule SKIPs anyway)
        default       → [GCC, SCC, Specifications]
  3. BGE-M3 embed an answer-shaped query (see QUERY_TEXT below).
  4. Qdrant top-K=10 candidates within the filter.
  5. LLM rerank with PVC-specific ignore rules (scope variation,
     employer-delay escalation, contract extension, force majeure,
     LD) and structured PVC extraction (presence + formula breakdown
     + GO reference).
  6. Hallucination guard (L24): verify evidence is in the chosen
     section's full_text. Discard on score < 85.
  7. Apply rule check:
        pvc_present=True  → compliant (presence shape)
        pvc_present=False → HARD_BLOCK violation (rule fires + no clause)
  8. Materialise ValidationFinding + VIOLATES_RULE with L24 audit
     fields + formula_breakdown for downstream formula-structure
     check (deferred typology).

Tested on vizag_ugss_exp_001 first.
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
from modules.validation.text_utils       import smart_truncate
from modules.validation.llm_client       import call_llm, parse_llm_json
from modules.validation.grep_fallback    import grep_source_for_keywords


# ── Constants ─────────────────────────────────────────────────────────

DOC_ID = sys.argv[1] if len(sys.argv) > 1 else "vizag_ugss_exp_001"

TYPOLOGY = "Missing-PVC-Clause"

QDRANT_URL  = os.environ.get("QDRANT_URL", "http://localhost:6333")
COLLECTION  = "tender_sections"

LLM_MODEL = os.environ.get("LLM_MODEL", "qwen/qwen-2.5-72b-instruct")


# L36 source-grep fallback vocabulary. When the LLM rerank returns
# chosen_index=null (absence), cross-check the full section_filter
# coverage for any of these keywords. Hit → downgrade ABSENCE to
# UNVERIFIED (likely retrieval-coverage gap, not real bypass).
GREP_FALLBACK_KEYWORDS = [
    "price variation",
    "price adjustment",
    "escalation",
    "GO 62",
    "GO 94",
    "PVC",
    "PA formula",
    "fixed element",
    "material element",
    "labour element",
]


# Answer-shaped query — mirrors the literal wording of AP / Central
# Price-Variation clauses (cement/steel/bitumen/CPI-IW/BoCE/GO 62-2021
# and the standard "fixed + material + labour" formula vocabulary).
QUERY_TEXT = (
    "Price Variation Clause Price Adjustment formula "
    "material element labour element fixed percentage "
    "cement steel bitumen escalation GO 62 2021"
)


# Rule candidates evaluated via condition_evaluator. Priority order
# matters — the first FIRE rule (after defeasibility filter) wins.
# AP-GO-019 must come first so AP Works tenders pick it instead of
# MPW-133.
RULE_CANDIDATES = [
    {
        "rule_id":         "AP-GO-019",
        "natural_language": "AP works ≥ Rs.40 lakh AND ≥ 6 months — Price Adjustment clause MUST be present (positive AND negative)",
        "severity":        "HARD_BLOCK",
        "layer":           "AP-State",
        "shape":           "presence",
    },
    {
        "rule_id":         "MPW-133",
        "natural_language": "Central Works > 18 months — Price Variation Clause MUST be present",
        "severity":        "HARD_BLOCK",
        "layer":           "Central",
        "shape":           "presence",
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


# ── LLM rerank prompt for PVC ─────────────────────────────────────────

LLM_SYSTEM = (
    "You are a precise procurement-document fact extractor. "
    "Return JSON only. Quote evidence verbatim from the supplied text. "
    "Never fabricate."
)


# Keyword vocabulary for smart_truncate windowing (PVC-specific).
# Pre-LLM windower keeps the content centred on the PA / PVC mention
# so a buried clause (e.g. inside a long Volume-III GCC block)
# survives truncation. See L26.
PVC_TRUNCATE_KEYWORDS = [
    r"price variation",
    r"price adjustment",
    r"escalation",
    r"fixed element",
    r"material element",
    r"labour element",
    r"\bcement\b",
    r"\bsteel\b",
    r"\bbitumen\b",
    r"cpi-iw",
    r"\bboce\b",
    r"go 62",
    r"go 94",
    r"\bpvc\b",
    r"\bpva\b",
]


def build_pvc_rerank_prompt(candidates: list[dict]) -> str:
    blocks = []
    for i, c in enumerate(candidates):
        body = smart_truncate(c["full_text"], window=3000,
                               keywords=PVC_TRUNCATE_KEYWORDS)
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
        "Exactly ONE of them (or none) is the actual Price Variation / Price "
        "Adjustment clause — the contract provision that allows the contract price "
        "to be adjusted for changes in cost of construction materials, labour, "
        "POL/bitumen, etc. over the life of a long-duration Works contract.\n\n"
        f"{candidates_block}\n\n"
        "Question: Which candidate states the Price Variation / Price Adjustment "
        "clause? Extract its presence and formula structure.\n\n"
        "Return JSON only:\n"
        "{\n"
        "  \"chosen_index\": integer 0..N-1 of the PVC candidate, OR null if no candidate is a PVC clause,\n"
        "  \"pvc_present\":  bool,\n"
        "  \"formula_breakdown\": {\n"
        "    \"fixed_pct\":         float OR null  (the fixed/non-variable element percentage, e.g. 15 for 15%),\n"
        "    \"material_covered\":  list OR null   (materials whose price varies, e.g. ['cement', 'steel', 'bitumen', 'POL']),\n"
        "    \"labour_present\":    bool           (does the formula include a labour component?)\n"
        "  },\n"
        "  \"go_reference\":  string OR null  (referenced AP GO/Central order, e.g. 'GO Ms No 62/2021', 'MPW 2022 Section 6.5.6'),\n"
        "  \"evidence\":     \"verbatim quote from the chosen candidate's text identifying the clause\",\n"
        "  \"found\":        bool,\n"
        "  \"reasoning\":    \"one short sentence explaining the choice\"\n"
        "}\n\n"
        "Selection rules — IGNORE the following content (NOT a PVC clause):\n"
        "- SCOPE variation clauses (additions/omissions to scope of work — e.g. "
        "  'Variations in scope', 'Authority Proposed Variation', 'Change of Scope').\n"
        "- Price escalation due to EMPLOYER DELAY / EXTENSION OF TIME (a different "
        "  remedy clause; not the contract-PA framework).\n"
        "- Contract EXTENSION clauses (period extension, not price adjustment).\n"
        "- FORCE MAJEURE price effects.\n"
        "- LIQUIDATED DAMAGES / PENALTY clauses.\n"
        "- POST-BID STATUTORY CHANGES alone (covered by a separate clause; only "
        "  count it as PVC if combined with material/labour/POL formula).\n"
        "\n"
        "Selection rules — DO pick if the candidate states:\n"
        "- A Price-Adjustment FORMULA with components for material, labour, POL, etc.\n"
        "- An AP-State Price Adjustment clause referencing GO Ms 62/2021 or GO 94/2003.\n"
        "- A Central PVC clause referencing MPW 2022 §6.5.6, MPW 2025 §5.4, or "
        "  the 18-month threshold for Works.\n"
        "- 'Price shall be adjusted on a quarterly basis' / 'CPI-IW for labour' / "
        "  'BoCE rates for steel and cement' / 'fortnightly PSU prices for POL'.\n"
        "- Even if the formula isn't fully spelled out, if the section EXPLICITLY "
        "  invokes Price Variation/Adjustment with at least a material or labour "
        "  reference, treat as pvc_present=true and capture what's there.\n"
        "\n"
        "- If the candidate has a generic 'Variations' clause (scope only, no "
        "  price-adjustment formula or material/labour references), set "
        "  pvc_present=false and chosen_index=null.\n"
        "- Evidence MUST be an exact substring of the chosen candidate's text.\n"
        "- If no candidate is a PVC clause, set chosen_index=null, pvc_present=false, "
        "  found=false.\n"
        "\n"
        "QUOTE FORMAT — STRICT (per L35):\n"
        "- Return a SINGLE CONTIGUOUS quote from ONE sentence or ONE clause only.\n"
        "- Do NOT stitch multiple paragraphs into one quote.\n"
        "- Do NOT add ellipsis ('...') between lines.\n"
        "- Do NOT paraphrase, summarise, or condense — quote EXACTLY as it appears in the supplied text.\n"
        "- Do NOT introduce or remove markdown formatting (asterisks, underscores, italics) — preserve the source formatting verbatim.\n"
        "- Pick the SHORTEST contiguous span that proves the clause is present; one sentence is usually enough."
    )


def parse_llm_response(raw: str) -> dict:
    """Thin wrapper kept for in-script readability — actual parsing
    logic lives in modules.validation.llm_client.parse_llm_json
    (lifted per L35 so every typology script benefits from the
    JSON-escape sanitiser without copy-paste)."""
    return parse_llm_json(raw)


# ── Rule selection via condition_evaluator ────────────────────────────

def fetch_tender_facts(doc_id: str) -> dict:
    """Build the facts dict that condition_evaluator reads.

    Strict LLM-only sources. The regex-classifier fields
    (estimated_value_cr_classified, duration_months_classified, etc.)
    have been deleted from the TenderDocument schema and are NOT
    consulted here. If estimated_value_cr is null (LLM extraction
    didn't run or didn't find a value), `EstimatedValue` is omitted
    from the facts dict — condition_evaluator will return UNKNOWN
    for any condition that references it, and the rule-selection
    code downgrades the rule's severity to ADVISORY for the
    UNKNOWN-fire path.
    """
    rows = rest_get("kg_nodes", {
        "select":    "properties",
        "doc_id":    f"eq.{doc_id}",
        "node_type": "eq.TenderDocument",
    })
    if not rows:
        return {}
    p = rows[0].get("properties") or {}

    facts: dict = {
        "tender_type":  p.get("tender_type"),
        "is_ap_tender": bool(p.get("is_ap_tender")),
        "TenderType":   p.get("tender_type"),
        "TenderState":  "AndhraPradesh" if p.get("is_ap_tender") else "Other",
    }

    # EstimatedValue (rupees) — LLM-extracted only
    ev_cr = p.get("estimated_value_cr")
    if ev_cr is not None:
        try:
            facts["EstimatedValue"] = float(ev_cr) * 1e7   # crores → rupees
            facts["_estimated_value_cr"] = float(ev_cr)
        except (TypeError, ValueError):
            pass

    # Duration in months — LLM-extracted (when tender_facts_extractor
    # extracts it). Regex `duration_months_classified` is no longer
    # consulted. If absent, the condition referencing it returns
    # UNKNOWN.
    dur_mo = p.get("duration_months")
    if dur_mo is not None:
        try:
            dur_mo_i = int(dur_mo)
            facts["OriginalContractPeriodMonths"] = dur_mo_i
            facts["ContractDuration"]             = dur_mo_i   # MPW-133 spelling
        except (TypeError, ValueError):
            pass

    return facts


def select_pvc_rule(tender_facts: dict) -> dict | None:
    fired: list[dict] = []
    ev_rs = tender_facts.get("EstimatedValue")
    ev_str = f"{ev_rs:.0f} rs ({tender_facts.get('_estimated_value_cr')} cr)" if ev_rs is not None else "UNKNOWN (no LLM extract)"
    dur = tender_facts.get("OriginalContractPeriodMonths")
    dur_str = f"{dur}" if dur is not None else "UNKNOWN"
    print(f"\n  Rule selection — facts: tender_type={tender_facts.get('tender_type')!r}, "
          f"is_ap_tender={tender_facts.get('is_ap_tender')}, "
          f"EstimatedValue={ev_str}, DurationMonths={dur_str}")
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
            fired.append(dict(cand, defeats=defeats, verdict_origin="FIRE"))
        elif verdict == Verdict.UNKNOWN:
            # UNKNOWN-fire path — rule's condition couldn't be fully
            # evaluated because at least one fact is missing (typically
            # estimated_value_cr or duration_months). Per the new
            # design, the rule still fires but its severity is
            # downgraded to ADVISORY. Audit trail records the downgrade.
            downgraded = dict(cand, defeats=defeats,
                              severity="ADVISORY",
                              severity_origin=cand["severity"],
                              verdict_origin="UNKNOWN")
            fired.append(downgraded)

    # Defeasibility filter — drop any rule whose rule_id is in another
    # fired rule's defeats list.
    defeated_ids = set()
    for f in fired:
        for victim in (f.get("defeats") or []):
            defeated_ids.add(victim)
    surviving = [f for f in fired if f["rule_id"] not in defeated_ids]
    if not surviving:
        print(f"  → no rule fires for these facts (correct silence — typology N/A on this doc)")
        return None
    chosen = surviving[0]   # priority by candidate-list order
    note = ""
    if chosen.get("verdict_origin") == "UNKNOWN":
        note = (f"  [severity downgraded from {chosen.get('severity_origin')} → "
                f"ADVISORY because at least one fact was UNKNOWN]")
    print(f"  → selected {chosen['rule_id']} (severity={chosen['severity']}, "
          f"shape={chosen['shape']}){note}")
    return chosen


# ── Idempotent re-run cleanup ─────────────────────────────────────────

def _delete_prior_tier1_pvc(doc_id: str) -> tuple[int, int]:
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
    print(f"  Tier-1 Missing-PVC-Clause (BGE-M3 + LLM, NO regex)")
    print(f"  doc_id : {DOC_ID}")
    print(f"  model  : {LLM_MODEL}")
    print("=" * 76)

    n_f, n_e = _delete_prior_tier1_pvc(DOC_ID)
    if n_f or n_e:
        print(f"  cleared {n_f} prior Tier-1 PVC finding node(s) and "
              f"{n_e} edge(s) before re-running")

    # 1. Pick rule via condition_evaluator
    facts = fetch_tender_facts(DOC_ID)
    rule  = select_pvc_rule(facts)
    if rule is None:
        # No rule fires — this is the correct silence path for PPP /
        # short-duration / non-Works tenders. We don't run retrieval at
        # all because there's nothing to check.
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

    # 4. Qdrant top-K
    K = 10
    t0 = time.perf_counter()
    print(f"\n── Step 2: Qdrant top-{K} (family={family}, section_type ∈ {section_types}) ──")
    points = qdrant_topk(qvec, DOC_ID, k=K, section_types=section_types)
    timings["qdrant"] = time.perf_counter() - t0
    print(f"  {len(points)} candidate(s) returned in {timings['qdrant']*1000:.0f}ms:")
    for i, p in enumerate(points):
        pl = p["payload"]
        h  = (pl.get("heading") or pl.get("section_heading") or "")[:60]
        print(f"    [{i}] cosine={p['score']:.4f}  type={pl.get('section_type','?'):14s}  "
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
    print(f"\n── Step 3: LLM rerank + PVC extraction ──")
    user_prompt = build_pvc_rerank_prompt(candidates)
    print(f"  prompt size: {len(user_prompt)} chars (~{len(user_prompt)//4} tokens)")
    raw_content = call_llm(LLM_SYSTEM, user_prompt, max_tokens=900)
    timings["llm"] = time.perf_counter() - t0
    print(f"  wall: {timings['llm']:.2f}s")
    print(f"\n── Raw LLM JSON ──")
    print(raw_content)

    # 7. Parse + select chosen candidate
    parsed = parse_llm_response(raw_content)
    chosen        = parsed.get("chosen_index")
    pvc_present   = bool(parsed.get("pvc_present"))
    formula       = parsed.get("formula_breakdown") or {}
    go_reference  = parsed.get("go_reference")
    evidence      = (parsed.get("evidence") or "").strip()
    found         = bool(parsed.get("found"))
    reason        = (parsed.get("reasoning") or "").strip()

    print(f"\n── Parsed ──")
    print(f"  chosen_index    : {chosen}")
    print(f"  found           : {found}")
    print(f"  pvc_present     : {pvc_present}")
    print(f"  formula.fixed_pct       : {formula.get('fixed_pct')}")
    print(f"  formula.material_covered: {formula.get('material_covered')}")
    print(f"  formula.labour_present  : {formula.get('labour_present')}")
    print(f"  go_reference    : {go_reference!r}")
    print(f"  reasoning       : {reason[:200]}")
    print(f"  evidence        : {evidence[:300]!r}")

    # L35 three-state contract: COMPLIANT / UNVERIFIED / ABSENCE.
    # Track the LLM's pre-verification verdict separately from
    # post-verification. A failed L24 quote-verification is NOT the
    # same as an absent clause — it means we don't have audit-grade
    # evidence yet, not that the document is non-compliant.
    section = None
    similarity = None
    ev_passed = False
    ev_score = 0
    ev_method = "skipped"

    llm_found_clause   = pvc_present and (chosen is not None)
    llm_chose_candidate = chosen is not None and isinstance(chosen, int) and 0 <= chosen < len(candidates)

    if llm_chose_candidate:
        section = candidates[chosen]
        similarity = section["similarity"]
        print(f"  → using candidate [{chosen}]: {section['heading'][:60]} "
              f"(cosine={similarity:.4f})")
        if evidence:
            ev_passed, ev_score, ev_method = verify_evidence_in_section(
                evidence, section["full_text"]
            )
            print(f"  evidence_verified : {ev_passed}  (score={ev_score}, method={ev_method})")
            if not ev_passed:
                print(f"  L24_FAILED — LLM found clause but quote is unverifiable. "
                      f"Routing to UNVERIFIED finding (NOT absence).")
                print(f"    LLM evidence  : {evidence[:200]!r}")
        else:
            print(f"  ⚠ no evidence quote provided — treating as L24-failed (empty)")
            ev_passed = False; ev_score = 0; ev_method = "empty"
    else:
        print(f"  → no candidate chosen by LLM")
        if pvc_present:
            print(f"  ⚠ pvc_present=True but chosen_index=null — treating as False")
            pvc_present = False
            llm_found_clause = False

    # 8. Apply rule check — three-way branch (per L35) + L36 grep fallback:
    #    (a) llm_found_clause AND ev_passed     → compliant, no finding
    #    (b) llm_found_clause AND NOT ev_passed → UNVERIFIED finding (no edge)
    #    (c) NOT llm_found_clause               → grep fallback decides:
    #         - any grep hit → UNVERIFIED-via-grep (no edge)
    #         - no grep hit  → genuine ABSENCE finding (with edge)
    is_compliant   = llm_found_clause and ev_passed
    is_unverified  = llm_found_clause and not ev_passed
    raw_is_absence = not llm_found_clause

    grep_hits: list[dict] = []
    grep_promoted_to_unverified = False
    if raw_is_absence:
        print(f"\n── L36 source-grep fallback (absence path) ──")
        any_hit, grep_hits = grep_source_for_keywords(
            DOC_ID, section_types, GREP_FALLBACK_KEYWORDS,
        )
        print(f"  scanned section_types : {section_types}")
        print(f"  any_hit               : {any_hit}  ({len(grep_hits)} section(s) match)")
        if any_hit:
            for h in grep_hits[:3]:
                print(f"    [{h['section_type']}] {h['heading'][:50]!r:55s} "
                      f"lines={h['line_start_local']}-{h['line_end_local']}  "
                      f"matched={h['keyword_matches'][:3]}")
            if len(grep_hits) > 3:
                print(f"    ... and {len(grep_hits) - 3} more")
            grep_promoted_to_unverified = True
            print(f"  → ABSENCE downgraded to UNVERIFIED — retrieval-coverage gap")

    is_absence    = raw_is_absence and not grep_promoted_to_unverified
    is_unverified = is_unverified or grep_promoted_to_unverified

    if is_compliant:
        reason_label = "compliant_pvc_present"
    elif grep_promoted_to_unverified:
        reason_label = "pvc_unverified_grep_fallback_retrieval_gap"
    elif is_unverified:
        reason_label = "pvc_unverified_llm_found_quote_failed_l24"
    else:
        reason_label = "pvc_absent_violation"

    print(f"\n── Decision ──")
    print(f"  rule              : {rule['rule_id']} ({rule['severity']}, shape={rule['shape']})")
    print(f"  llm_found_clause  : {llm_found_clause}")
    print(f"  ev_passed         : {ev_passed}  (score={ev_score}, method={ev_method})")
    print(f"  raw_is_absence    : {raw_is_absence}")
    print(f"  grep_fallback_hit : {grep_promoted_to_unverified}")
    print(f"  is_compliant      : {is_compliant}")
    print(f"  is_unverified     : {is_unverified}")
    print(f"  is_absence        : {is_absence}")
    print(f"  reason_label      : {reason_label}")

    if is_compliant:
        return 0

    # 9. Materialise finding (UNVERIFIED or ABSENCE).
    # VIOLATES_RULE edge is emitted ONLY for ABSENCE findings —
    # UNVERIFIED findings are NOT violations until a human reviewer
    # confirms (per L35 contract).
    t0 = time.perf_counter()
    if section is not None and is_unverified:
        # Keep the section attribution on the UNVERIFIED finding —
        # the LLM identified WHERE the clause should be; the human
        # reviewer can start there.
        section_node_id = section["section_node_id"]
        section_heading = section["heading"]
        source_file     = section["source_file"]
        line_start_local = section["line_start_local"]
        line_end_local   = section["line_end_local"]
        qdrant_similarity = round(similarity, 4) if similarity is not None else None
    else:
        # Doc-level attribution for ABSENCE.
        td_rows = rest_get("kg_nodes", {
            "select":    "node_id",
            "doc_id":    f"eq.{DOC_ID}",
            "node_type": "eq.TenderDocument",
        })
        section_node_id = td_rows[0]["node_id"] if td_rows else None
        section_heading = None
        source_file     = None
        line_start_local = None
        line_end_local   = None
        qdrant_similarity = None

    if is_absence:
        ev_passed = None
        ev_score  = None
        ev_method = "absence_finding_no_evidence"
        evidence  = ("Price Variation Clause not found in document "
                     f"after searching {', '.join(section_types)} section types "
                     f"(also exhaustive grep across all matching sections — no "
                     f"keyword hits)")
        print(f"  → ABSENCE finding — LLM rerank empty AND grep fallback "
              f"empty; genuine absence")
    elif grep_promoted_to_unverified:
        ev_passed = None
        ev_score  = None
        ev_method = "grep_fallback_retrieval_gap"
        evidence  = (f"LLM rerank top-{K} returned no PVC candidate, but "
                     f"exhaustive grep across {', '.join(section_types)} found "
                     f"keyword hits in {len(grep_hits)} section(s) — likely a "
                     f"retrieval coverage gap. First match: "
                     f"{grep_hits[0]['snippet'][:160] if grep_hits else 'n/a'}")
        print(f"  → UNVERIFIED finding (grep fallback) — retrieval missed "
              f"{len(grep_hits)} section(s) carrying PVC keywords")
    elif is_unverified:
        print(f"  → UNVERIFIED finding — LLM identified clause but quote "
              f"failed L24 verification (score={ev_score}, method={ev_method})")

    rule_node_id = get_or_create_rule_node(DOC_ID, rule["rule_id"])

    if grep_promoted_to_unverified:
        label = (
            f"{TYPOLOGY}: UNVERIFIED (L36 grep-fallback) — LLM rerank "
            f"missed the clause; exhaustive grep found {len(grep_hits)} "
            f"section(s) with PVC keyword hits; requires human review"
        )
    elif is_unverified:
        label = (
            f"{TYPOLOGY}: UNVERIFIED — LLM found PVC clause but quote "
            f"failed L24 (score={ev_score}, method={ev_method}); "
            f"requires human review against {(section['heading'][:60] if section else 'TenderDocument')!r}"
        )
    else:
        label = (
            f"{TYPOLOGY}: PVC clause absent — {rule['rule_id']} "
            f"({rule['severity']}) requires Price Variation / Price Adjustment "
            f"clause for this tender"
        )

    grep_audit = None
    if grep_promoted_to_unverified:
        grep_audit = {
            "scanned_section_types":  section_types,
            "keywords":               GREP_FALLBACK_KEYWORDS,
            "hits_count":             len(grep_hits),
            "hits": [
                {"section_node_id":  h["section_node_id"],
                 "heading":          h["heading"],
                 "section_type":     h["section_type"],
                 "source_file":      h["source_file"],
                 "line_start_local": h["line_start_local"],
                 "line_end_local":   h["line_end_local"],
                 "keyword_matches":  h["keyword_matches"],
                 "snippet":          h["snippet"][:300]}
                for h in grep_hits[:10]
            ],
        }

    finding_props = {
        "rule_id":            rule["rule_id"],
        "typology_code":      TYPOLOGY,
        "severity":           rule["severity"],
        "evidence":           evidence,
        "extraction_path":    "presence",
        "llm_found_clause":   llm_found_clause,
        "pvc_present":        llm_found_clause,    # mirrors LLM verdict (pre-L24)
        "formula_breakdown":  formula,
        "go_reference":       go_reference,
        "rule_shape":         rule["shape"],
        "violation_reason":   reason_label,
        "tier":               1,
        "extracted_by":       "bge-m3+llm-rerank:qwen-2.5-72b@openrouter",
        "retrieval_strategy": (
            f"qdrant_top{K}_router_{family}_section_filter_"
            f"{'-'.join(section_types)}_llm_rerank+grep_fallback"
        ),
        "doc_family":          family,
        "section_filter":      section_types,
        "rerank_chosen_index": chosen,
        "rerank_reasoning":    reason,
        "section_node_id":     section_node_id,
        "section_heading":     section_heading,
        "source_file":         source_file,
        "line_start_local":    line_start_local,
        "line_end_local":      line_end_local,
        "qdrant_similarity":   qdrant_similarity,
        # L24 audit fields
        "evidence_in_source":    ev_passed,
        "evidence_verified":     ev_passed,
        "evidence_match_score":  ev_score,
        "evidence_match_method": ev_method,
        # Rule-evaluator inputs (LLM-extracted; null when extractor returned UNKNOWN)
        "estimated_value_cr":          facts.get("_estimated_value_cr"),
        "duration_months":             facts.get("OriginalContractPeriodMonths"),
        # L35 status / human-review markers
        "status":                     "UNVERIFIED" if is_unverified else "OPEN",
        "requires_human_review":      bool(is_unverified),
        "human_review_reason": (
            f"L36 grep fallback fired: LLM rerank top-{K} did not surface a "
            f"PVC clause, but exhaustive grep across {section_types} found "
            f"keyword hits in {len(grep_hits)} section(s). Reviewer should "
            f"open the listed sections in grep_audit.hits."
            if grep_promoted_to_unverified else
            "LLM found clause but evidence quote failed L24 verification "
            f"(score={ev_score}, method={ev_method}). Reviewer should "
            f"open the section above (line_start={line_start_local}, "
            f"line_end={line_end_local}) and confirm the PVC clause is "
            f"present in the source text."
            if is_unverified else None
        ),
        # L36 grep-fallback audit
        "grep_fallback_audit":         grep_audit,
        "defeated":            False,
    }

    finding = rest_post("kg_nodes", [{
        "doc_id":    DOC_ID,
        "node_type": "ValidationFinding",
        "label":     label,
        "properties": finding_props,
        "source_ref": f"tier1:pvc_check:{rule['rule_id']}",
    }])[0]

    edge = None
    if is_absence:
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
                "extraction_path":      "presence",
                "pvc_present":          False,
                "evidence":             evidence,
                "qdrant_similarity":    qdrant_similarity,
                "violation_reason":     reason_label,
                "doc_family":           family,
                "evidence_match_score":  ev_score,
                "evidence_match_method": ev_method,
                "finding_node_id":      finding["node_id"],
            },
        }])[0]

    timings["materialise"] = time.perf_counter() - t0
    print(f"  → ValidationFinding {finding['node_id']}  "
          f"(status={'UNVERIFIED' if is_unverified else 'OPEN'})")
    if edge is not None:
        print(f"  → VIOLATES_RULE     {edge['edge_id']}  "
              f"{'Section' if section else 'TenderDocument'}→Rule")
    else:
        print(f"  → no VIOLATES_RULE edge "
              f"(UNVERIFIED finding — awaiting human review)")

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
