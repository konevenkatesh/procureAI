"""
scripts/tier1_jp_check.py

Tier-1 Judicial-Preview-Bypass check, BGE-M3 + LLM, NO regex.

PRESENCE shape with the post-L36 three-state contract (COMPLIANT /
UNVERIFIED / ABSENCE) + L36 grep fallback. AP Government infrastructure
projects of value Rs.100 crore and above must undergo Judicial Preview
by a sitting/former High Court Judge prior to inviting tenders, per
AP-GO-001 and the AP Judicial Preview Act 2019 (predecessor framework
GO Ms No 38/2018). The bid document MUST cite the Judicial Preview
Authority (APJPA) submission / clearance. Absence at the >=100cr gate
is a HARD_BLOCK violation (or ADVISORY when EstimatedValue is
UNKNOWN per L27 downgrade).

Pipeline (same shape as Blacklist post-L36):
  1. Pick rule via condition_evaluator. AP-GO-001 (Works/EPC + 100cr)
     is the canonical primary for Works docs. AP-GO-004 (any AP tender
     + 100cr) is the catch-all for PPP docs (Tirupathi/Vijayawada).
     Vizag (EV=null) gets L27 ADVISORY downgrade.
  2. Section filter via JP_SECTION_ROUTER —
        APCRDA_Works → [NIT, ITB]
        SBD_Format   → [NIT, ITB, Evaluation]    (Kakinada n_gcc=0)
        NREDCAP_PPP  → [NIT, ITB]
        default      → [NIT, ITB]
  3. BGE-M3 embed an answer-shaped query.
  4. Qdrant top-K=10 candidates within the filter.
  5. LLM rerank with JP-specific extraction:
        judicial_preview_cited, preview_authority_named,
        act_or_go_referenced, public_domain_window_specified,
        preview_completion_window_specified, evidence.
     IMPORTANT: prompt explicitly distinguishes "Judicial Preview"
     (framework) from "Judicial Academy" (the procuring entity for
     the JA doc) — they must NOT be conflated.
  6. Hallucination guard (L24).
  7. L36 source-grep fallback on the absence path.
  8. Apply three-way decision (per L35).

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
from modules.validation.text_utils       import smart_truncate
from modules.validation.llm_client       import call_llm, parse_llm_json
from modules.validation.grep_fallback    import grep_source_for_keywords


# ── Constants ─────────────────────────────────────────────────────────

DOC_ID = sys.argv[1] if len(sys.argv) > 1 else "judicial_academy_exp_001"

TYPOLOGY = "Judicial-Preview-Bypass"

QDRANT_URL  = os.environ.get("QDRANT_URL", "http://localhost:6333")
COLLECTION  = "tender_sections"

LLM_MODEL = os.environ.get("LLM_MODEL", "qwen/qwen-2.5-72b-instruct")


# L36 source-grep fallback vocabulary. Phrase-precise — bare
# "judicial" is too noisy because the JA doc is for the Judicial
# Academy itself (procuring entity). Only flag when one of the
# regulator-specific phrases appears.
GREP_FALLBACK_KEYWORDS = [
    "Judicial Preview",
    "APJPA",
    "Judicial Preview Authority",
    "Judicial Preview Act",
    "GO Ms No. 38",
    "GO Ms No 38",
    "G.O. Ms. No. 38",
    "Hon'ble Judge",
    "preview by a sitting",
    "preview by a former",
    "AP Judicial Preview",
]


# Answer-shaped query — mirrors the literal wording of CLAUSE-AP-
# JUDICIAL-PREVIEW-MANDATE-001 / CLAUSE-AP-JUDICIAL-PREVIEW-PROCESS-001
# and the AP-GO-001 / AP-GO-004 rule text.
QUERY_TEXT = (
    "Judicial Preview APJPA Authority Act 2019 GO Ms 38 2018 "
    "infrastructure project Rs 100 crore Hon'ble Judge "
    "sitting High Court former judge tender documents preview"
)


# Rule candidates evaluated via condition_evaluator. Priority order:
#   1. AP-GO-001 — most specific (Works/EPC + 100cr)
#   2. AP-GO-004 — universal-at-100cr catch-all (covers PPP)
#   3. AP-GO-009 — sector-list gate
#   4. AP-GO-006 — public-domain window (HARD_BLOCK at 100cr)
#   5. AP-GO-003 — anti-splitting (Works/EPC, no value gate)
#
# All AP-State, defeats=[] across the typology — knowledge-layer gap,
# no defeasibility wired.
RULE_CANDIDATES = [
    {
        "rule_id":         "AP-GO-001",
        "natural_language": "AP Works/EPC infrastructure projects ≥ 100cr must undergo Judicial Preview by a sitting/former High Court Judge prior to inviting tenders",
        "severity":        "HARD_BLOCK",
        "layer":           "AP-State",
        "shape":           "presence",
    },
    {
        "rule_id":         "AP-GO-004",
        "natural_language": "AP tender of value ≥ 100cr — Government Agency must place all tender-related documents (NIT, BDS, GCC, SCC, technical specs, BOQ) before the Hon'ble Judge",
        "severity":        "HARD_BLOCK",
        "layer":           "AP-State",
        "shape":           "presence",
    },
    {
        "rule_id":         "AP-GO-009",
        "natural_language": "AP Judicial Preview applies to 25 enumerated infrastructure sectors at the 100cr threshold (per Schedule to AP Judicial Preview Act 2019)",
        "severity":        "HARD_BLOCK",
        "layer":           "AP-State",
        "shape":           "presence",
    },
    {
        "rule_id":         "AP-GO-006",
        "natural_language": "AP tender ≥ 100cr — tender documents referred for Judicial Preview must be placed in public domain for one week to invite suggestions before preview decisions",
        "severity":        "HARD_BLOCK",
        "layer":           "AP-State",
        "shape":           "presence",
    },
    {
        "rule_id":         "AP-GO-003",
        "natural_language": "AP infrastructure project shall not be split/segregated to evade the 100cr Judicial Preview threshold (anti-splitting, no value gate)",
        "severity":        "HARD_BLOCK",
        "layer":           "AP-State",
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


# ── Qdrant top-K with section_type filter ─────────────────────────────

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


# ── LLM rerank prompt for Judicial Preview detection ─────────────────

LLM_SYSTEM = (
    "You are a precise procurement-document fact extractor. "
    "Return JSON only. Quote evidence verbatim from the supplied text. "
    "Never fabricate."
)


# Keyword vocabulary for smart_truncate windowing (JP-specific).
JP_TRUNCATE_KEYWORDS = [
    r"Judicial Preview",
    r"\bAPJPA\b",
    r"Judicial Preview Authority",
    r"Judicial Preview Act",
    r"GO Ms No\.? *38",
    r"G\.O\.? Ms\.? No\.? *38",
    r"Hon'?ble Judge",
    r"sitting.*High Court",
    r"former.*High Court",
    r"Rs\.? *100 crore",
    r"100\s*cr\b",
]


def build_jp_rerank_prompt(candidates: list[dict]) -> str:
    blocks = []
    for i, c in enumerate(candidates):
        body = smart_truncate(c["full_text"], window=3000,
                               keywords=JP_TRUNCATE_KEYWORDS)
        blocks.append(
            f"--- CANDIDATE {i} ---\n"
            f"heading: {c['heading']}\n"
            f"section_type: {c.get('section_type') or 'unknown'}\n"
            f"cosine_similarity: {c['similarity']:.4f}\n"
            f"text:\n\"\"\"\n{body}\n\"\"\""
        )
    candidates_block = "\n\n".join(blocks)

    return (
        f"You are reading {len(candidates)} candidate sections from an Andhra Pradesh "
        "infrastructure tender document. Exactly ONE of them (or none) carries the "
        "JUDICIAL PREVIEW citation — the contractual statement that the tender "
        "document HAS BEEN SUBMITTED to (or APPROVED by) the AP Judicial Preview "
        "Authority (APJPA), a body of sitting/former High Court Judges, per the AP "
        "Judicial Preview Act 2019 (predecessor: GO Ms No. 38/2018).\n"
        "\n"
        "AP Government infrastructure projects of value Rs. 100 crore and above "
        "MUST undergo Judicial Preview prior to inviting tenders. The doc must cite "
        "this submission to be compliant.\n"
        "\n"
        "CRITICAL distinction (this corpus has a procuring entity called 'Judicial "
        "Academy' — that is NOT the Judicial Preview framework):\n"
        "  • 'Judicial Academy' / 'A.P. Judicial Academy' — the procuring entity\n"
        "    for the Judicial Academy bid pack. NOT a Judicial Preview citation.\n"
        "  • 'Judicial Preview' / 'APJPA' / 'Judicial Preview Authority' /\n"
        "    'AP Judicial Preview Act' / 'GO Ms No. 38' / 'Hon'ble Judge preview' —\n"
        "    these ARE the regulatory framework. ONLY these count.\n"
        "\n"
        f"{candidates_block}\n\n"
        "Return JSON only:\n"
        "{\n"
        "  \"chosen_index\":                     integer 0..N-1 of the JP-citation candidate, OR null if no candidate cites JP,\n"
        "  \"judicial_preview_cited\":           bool   (TRUE only for the regulatory framework, NOT for 'Judicial Academy' as procuring entity),\n"
        "  \"preview_authority_named\":          bool OR null  (TRUE if doc names APJPA / Judicial Preview Authority / Hon'ble Judge by office),\n"
        "  \"act_or_go_referenced\":             string OR null  (e.g. 'AP Judicial Preview Act 2019', 'GO Ms No. 38/2018', 'GO Ms No. 38'),\n"
        "  \"public_domain_window_specified\":   bool OR null  (TRUE if doc commits to the 7-day pre-bid public-domain window per AP-GO-006),\n"
        "  \"preview_completion_window_specified\": bool OR null  (TRUE if doc commits to the 8-day or 15-day preview windows per AP-GO-007/008),\n"
        "  \"evidence\":                         \"verbatim quote from the chosen candidate's text identifying the JP citation\",\n"
        "  \"found\":                            bool,\n"
        "  \"reasoning\":                        \"one short sentence explaining the choice; if you saw 'Judicial Academy' but not JP framework, say so explicitly\"\n"
        "}\n\n"
        "Selection rules — IGNORE the following content (NOT a Judicial Preview citation):\n"
        "- 'Judicial Academy' references (the procuring entity / project subject) — "
        "  count of references to this name does NOT make judicial_preview_cited true.\n"
        "- General references to 'judicial action' / 'court' / 'litigation' in "
        "  dispute-resolution clauses — that's about contract enforcement, not JP.\n"
        "- 'Honorable High Court' references in jurisdiction / arbitration clauses "
        "  — same; not the JP framework.\n"
        "- AP-GOs OTHER than 38/2018 / Judicial Preview Act 2019 — even if cited, "
        "  they're not JP unless they specifically invoke the JP framework.\n"
        "\n"
        "Selection rules — DO pick if the candidate states:\n"
        "- 'tender documents have been submitted to the Judicial Preview Authority' "
        "  / 'cleared by the APJPA' / 'as per AP Judicial Preview Act 2019'.\n"
        "- 'preview by a sitting/former Judge of the High Court of A.P. per GO Ms "
        "  No. 38/2018' or equivalent.\n"
        "- 'this tender has been placed in the public domain for 7 days as per the "
        "  Judicial Preview Authority requirement' (the public-domain window).\n"
        "- A reference to GO Ms No. 38/2018 OR AP Judicial Preview Act 2019 OR "
        "  'Hon'ble Judge preview' specifically tied to a tender-document review.\n"
        "\n"
        "QUOTE FORMAT — STRICT (per L35):\n"
        "- Return a SINGLE CONTIGUOUS quote from ONE sentence or ONE clause only.\n"
        "- Do NOT stitch multiple paragraphs into one quote.\n"
        "- Do NOT add ellipsis ('...') between lines.\n"
        "- Do NOT paraphrase, summarise, or condense — quote EXACTLY as it appears in the supplied text.\n"
        "- Do NOT introduce or remove markdown formatting (asterisks, underscores, italics) — preserve the source formatting verbatim.\n"
        "- Pick the SHORTEST contiguous span that proves the JP citation; one sentence is usually enough.\n"
        "\n"
        "- If no candidate cites JP, set chosen_index=null, judicial_preview_cited=false, found=false. "
        "  This is the BYPASS-VIOLATION outcome.\n"
        "- Evidence MUST be an exact substring of the chosen candidate's text."
    )


def parse_llm_response(raw: str) -> dict:
    """Thin wrapper kept for in-script readability — actual parsing
    logic lives in modules.validation.llm_client.parse_llm_json (per L35)."""
    return parse_llm_json(raw)


# ── Rule selection via condition_evaluator ────────────────────────────

def fetch_tender_facts(doc_id: str) -> dict:
    """Build the facts dict that condition_evaluator reads.
    Strict LLM-only sources (per L28). Sector is NOT extracted — AP-GO-009's
    sector subterm will resolve as UNKNOWN and trigger L27 ADVISORY downgrade
    on PPP docs where AP-GO-001 SKIPs."""
    rows = rest_get("kg_nodes", {
        "select":    "properties",
        "doc_id":    f"eq.{doc_id}",
        "node_type": "eq.TenderDocument",
    })
    if not rows:
        return {}
    p = rows[0].get("properties") or {}

    facts: dict = {
        "tender_type":     p.get("tender_type"),
        "is_ap_tender":    bool(p.get("is_ap_tender")),
        "TenderType":      p.get("tender_type"),
        "TenderState":     "AndhraPradesh" if p.get("is_ap_tender") else "Other",
    }

    ev_cr = p.get("estimated_value_cr")
    if ev_cr is not None:
        try:
            facts["EstimatedValue"] = float(ev_cr) * 1e7   # crores → rupees
            facts["_estimated_value_cr"] = float(ev_cr)
        except (TypeError, ValueError):
            pass

    return facts


def select_jp_rule(tender_facts: dict) -> dict | None:
    """Pick the highest-priority rule that fires (or fires-as-UNKNOWN
    per L27 downgrade). AP-GO-001 is the canonical primary for Works
    docs ≥ 100cr; AP-GO-004 is the catch-all for PPP."""
    fired: list[dict] = []
    ev_rs = tender_facts.get("EstimatedValue")
    ev_str = (f"{ev_rs:.0f} rs ({tender_facts.get('_estimated_value_cr')} cr)"
              if ev_rs is not None else "UNKNOWN (no LLM extract)")
    print(f"\n  Rule selection — facts: tender_type={tender_facts.get('tender_type')!r}, "
          f"is_ap_tender={tender_facts.get('is_ap_tender')}, "
          f"EstimatedValue={ev_str}")
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
        print(f"    [{rid}] condition={cw!r}  verdict={verdict.value}  defeats={defeats}")
        if verdict == Verdict.FIRE:
            fired.append(dict(cand, defeats=defeats, verdict_origin="FIRE"))
        elif verdict == Verdict.UNKNOWN:
            downgraded = dict(cand, defeats=defeats,
                              severity="ADVISORY",
                              severity_origin=cand["severity"],
                              verdict_origin="UNKNOWN")
            fired.append(downgraded)

    defeated_ids = set()
    for f in fired:
        for victim in (f.get("defeats") or []):
            defeated_ids.add(victim)
    surviving = [f for f in fired if f["rule_id"] not in defeated_ids]
    if not surviving:
        print(f"  → no rule fires for these facts (correct silence — typology N/A on this doc)")
        return None
    chosen = surviving[0]
    note = ""
    if chosen.get("verdict_origin") == "UNKNOWN":
        note = (f"  [severity downgraded from {chosen.get('severity_origin')} → "
                f"ADVISORY because at least one fact was UNKNOWN]")
    print(f"  → selected {chosen['rule_id']} (severity={chosen['severity']}, "
          f"shape={chosen['shape']}){note}")
    return chosen


# ── Idempotent re-run cleanup ─────────────────────────────────────────

def _delete_prior_tier1_jp(doc_id: str) -> tuple[int, int]:
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
    print(f"  Tier-1 Judicial-Preview-Bypass (BGE-M3 + LLM, NO regex)")
    print(f"  doc_id : {DOC_ID}")
    print(f"  model  : {LLM_MODEL}")
    print("=" * 76)

    n_f, n_e = _delete_prior_tier1_jp(DOC_ID)
    if n_f or n_e:
        print(f"  cleared {n_f} prior Tier-1 JP finding node(s) and "
              f"{n_e} edge(s) before re-running")

    # 1. Pick rule via condition_evaluator
    facts = fetch_tender_facts(DOC_ID)
    rule  = select_jp_rule(facts)
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
    print(f"\n── Step 3: LLM rerank + Judicial-Preview detection ──")
    user_prompt = build_jp_rerank_prompt(candidates)
    print(f"  prompt size: {len(user_prompt)} chars (~{len(user_prompt)//4} tokens)")
    raw_content = call_llm(LLM_SYSTEM, user_prompt, max_tokens=900)
    timings["llm"] = time.perf_counter() - t0
    print(f"  wall: {timings['llm']:.2f}s")
    print(f"\n── Raw LLM JSON ──")
    print(raw_content)

    # 7. Parse + select chosen candidate
    parsed = parse_llm_response(raw_content)
    chosen          = parsed.get("chosen_index")
    jp_cited        = bool(parsed.get("judicial_preview_cited"))
    authority_named = parsed.get("preview_authority_named")
    act_go_ref      = parsed.get("act_or_go_referenced")
    pub_domain      = parsed.get("public_domain_window_specified")
    preview_window  = parsed.get("preview_completion_window_specified")
    evidence        = (parsed.get("evidence") or "").strip()
    found           = bool(parsed.get("found"))
    reason          = (parsed.get("reasoning") or "").strip()

    print(f"\n── Parsed ──")
    print(f"  chosen_index                         : {chosen}")
    print(f"  found                                : {found}")
    print(f"  judicial_preview_cited               : {jp_cited}")
    print(f"  preview_authority_named              : {authority_named}")
    print(f"  act_or_go_referenced                 : {act_go_ref!r}")
    print(f"  public_domain_window_specified       : {pub_domain}")
    print(f"  preview_completion_window_specified  : {preview_window}")
    print(f"  reasoning                            : {reason[:200]}")
    print(f"  evidence                             : {evidence[:300]!r}")

    section = None
    similarity = None
    ev_passed = False
    ev_score = 0
    ev_method = "skipped"

    llm_found_clause   = jp_cited and (chosen is not None)
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
                print(f"  L24_FAILED — LLM found JP citation but quote is unverifiable. "
                      f"Routing to UNVERIFIED finding (NOT absence).")
                print(f"    LLM evidence  : {evidence[:200]!r}")
        else:
            print(f"  ⚠ no evidence quote provided — treating as L24-failed (empty)")
            ev_passed = False; ev_score = 0; ev_method = "empty"
    else:
        print(f"  → no candidate chosen by LLM")
        if jp_cited:
            print(f"  ⚠ judicial_preview_cited=True but chosen_index=null — treating as False")
            jp_cited = False
            llm_found_clause = False

    # 8. Three-way decision (per L35) + L36 grep fallback on absence
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
        reason_label = "compliant_judicial_preview_cited"
    elif grep_promoted_to_unverified:
        reason_label = "jp_unverified_grep_fallback_retrieval_gap"
    elif is_unverified:
        reason_label = "jp_unverified_llm_found_quote_failed_l24"
    else:
        reason_label = "judicial_preview_bypass_violation"

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

    # 9. Materialise finding
    t0 = time.perf_counter()
    if section is not None and is_unverified and not grep_promoted_to_unverified:
        section_node_id = section["section_node_id"]
        section_heading = section["heading"]
        source_file     = section["source_file"]
        line_start_local = section["line_start_local"]
        line_end_local   = section["line_end_local"]
        qdrant_similarity = round(similarity, 4) if similarity is not None else None
    else:
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
        evidence  = (f"Judicial Preview citation not found in document "
                     f"after searching {', '.join(section_types)} section types "
                     f"(also exhaustive grep across all matching sections — no "
                     f"keyword hits)")
        print(f"  → ABSENCE finding — LLM rerank empty AND grep fallback empty; "
              f"genuine bypass")
    elif grep_promoted_to_unverified:
        ev_passed = None
        ev_score  = None
        ev_method = "grep_fallback_retrieval_gap"
        evidence  = (f"LLM rerank top-{K} returned no JP candidate, but exhaustive "
                     f"grep across {', '.join(section_types)} found keyword hits "
                     f"in {len(grep_hits)} section(s) — likely retrieval coverage "
                     f"gap. First match: "
                     f"{grep_hits[0]['snippet'][:160] if grep_hits else 'n/a'}")
        print(f"  → UNVERIFIED finding (grep fallback) — retrieval missed "
              f"{len(grep_hits)} section(s) carrying JP keywords")
    elif is_unverified:
        print(f"  → UNVERIFIED finding — LLM identified JP citation but quote "
              f"failed L24 verification (score={ev_score}, method={ev_method})")

    rule_node_id = get_or_create_rule_node(DOC_ID, rule["rule_id"])

    if grep_promoted_to_unverified:
        label = (
            f"{TYPOLOGY}: UNVERIFIED (L36 grep-fallback) — LLM rerank "
            f"missed the citation; exhaustive grep found {len(grep_hits)} "
            f"section(s) with JP keyword hits; requires human review"
        )
    elif is_unverified:
        label = (
            f"{TYPOLOGY}: UNVERIFIED — LLM found JP citation but quote "
            f"failed L24 (score={ev_score}, method={ev_method}); "
            f"requires human review against {(section['heading'][:60] if section else 'TenderDocument')!r}"
        )
    else:
        label = (
            f"{TYPOLOGY}: Judicial Preview citation absent — {rule['rule_id']} "
            f"({rule['severity']}) requires APJPA/JP-Act-2019 citation for this "
            f"AP infrastructure tender"
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
        "rule_id":               rule["rule_id"],
        "typology_code":         TYPOLOGY,
        "severity":              rule["severity"],
        "evidence":              evidence,
        "extraction_path":       "presence",
        "llm_found_clause":      llm_found_clause,
        "judicial_preview_cited":            llm_found_clause,
        "preview_authority_named":           authority_named,
        "act_or_go_referenced":              act_go_ref,
        "public_domain_window_specified":    pub_domain,
        "preview_completion_window_specified": preview_window,
        "rule_shape":            rule["shape"],
        "violation_reason":      reason_label,
        "tier":                  1,
        "extracted_by":          "bge-m3+llm-rerank:qwen-2.5-72b@openrouter",
        "retrieval_strategy": (
            f"qdrant_top{K}_router_{family}_section_filter_"
            f"{'-'.join(section_types)}_llm_rerank+grep_fallback"
        ),
        "doc_family":            family,
        "section_filter":        section_types,
        "rerank_chosen_index":   chosen,
        "rerank_reasoning":      reason,
        "section_node_id":       section_node_id,
        "section_heading":       section_heading,
        "source_file":           source_file,
        "line_start_local":      line_start_local,
        "line_end_local":        line_end_local,
        "qdrant_similarity":     qdrant_similarity,
        # L24 audit fields
        "evidence_in_source":    ev_passed,
        "evidence_verified":     ev_passed,
        "evidence_match_score":  ev_score,
        "evidence_match_method": ev_method,
        # Rule-evaluator inputs
        "estimated_value_cr":          facts.get("_estimated_value_cr"),
        # L27 audit
        "verdict_origin":              rule.get("verdict_origin"),
        "severity_origin":             rule.get("severity_origin"),
        # L35 status / human-review markers
        "status":                     "UNVERIFIED" if is_unverified else "OPEN",
        "requires_human_review":      bool(is_unverified),
        "human_review_reason": (
            f"L36 grep fallback fired: LLM rerank top-{K} did not surface a "
            f"Judicial Preview citation, but exhaustive grep across "
            f"{section_types} found keyword hits in {len(grep_hits)} section(s). "
            f"Reviewer should open the listed sections in grep_audit.hits and "
            f"confirm whether the doc carries a real APJPA / JP Act citation "
            f"(NOT 'Judicial Academy' references)."
            if grep_promoted_to_unverified else
            "LLM found JP citation but evidence quote failed L24 verification "
            f"(score={ev_score}, method={ev_method}). Reviewer should open "
            f"the section above (line_start={line_start_local}, "
            f"line_end={line_end_local}) and confirm the citation."
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
        "source_ref": f"tier1:jp_check:{rule['rule_id']}",
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
                "judicial_preview_cited": False,
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
