"""
scripts/tier1_blacklist_check.py

Tier-1 Blacklist-Not-Checked check, BGE-M3 + LLM, NO regex.

PRESENCE shape with L35 three-state contract (COMPLIANT / UNVERIFIED
/ ABSENCE). The doc MUST require bidders to declare past debarments
/ blacklistings / sanctions, OR commit the buyer to verifying against
debarment lists. Absence of any blacklist-check requirement is a
HARD_BLOCK violation — it means the procuring entity isn't filtering
out bidders the regulator has already debarred.

Bidder-side declaration (the canonical anchor):
    "Bidder shall declare any previous transgressions of the Code of
     Integrity in the last three years AND any debarment/blacklisting
     by another Procuring Entity. Misrepresentation is grounds for
     disqualification or termination." (MPS-021)

Buyer-side verification (alternative anchor):
    "The Procuring Entity shall verify the bidder against the
     Department of Expenditure debarment list, the World Bank /
     ADB sanctions lists, and any State-level blacklists before
     award."

Either is sufficient. The script returns COMPLIANT if EITHER form is
explicitly present in ITB or Forms.

Pipeline (same shape as PVC / IP / LD / E-Proc post-L35):
  1. Pick rule via condition_evaluator. MPS-021 is the canonical
     primary across the corpus (`TenderType=ANY`, fires on every doc).
     Rule selector also considers MPS-186 (Services), MPW-158 (Works),
     GFR-G-037 (universal), and AP-State rules where applicable.
  2. Section filter via BLACKLIST_SECTION_ROUTER —
        APCRDA_Works → [ITB, Forms]
        SBD_Format   → [ITB, Forms, Evaluation]   (Kakinada n_gcc=0)
        NREDCAP_PPP  → [ITB, Forms]
        default      → [ITB, Forms]
  3. BGE-M3 embed an answer-shaped query.
  4. Qdrant top-K=10 candidates within the filter.
  5. LLM rerank with blacklist-specific ignore rules (general PQ
     disqualification grounds, conflict-of-interest forms,
     allied-firm definitions, AP contractor advance restrictions,
     consultant-COI declarations) and structured extraction
     (presence + form_type + go_reference).
  6. Hallucination guard (L24): verify evidence is in the chosen
     section's full_text. Discard on score < 85.
  7. Apply rule check — three-way branch (per L35):
        is_compliant   = llm_found AND ev_passed → no finding
        is_unverified  = llm_found AND NOT ev_passed → UNVERIFIED finding (no edge)
        is_absence     = NOT llm_found → ABSENCE finding (with edge)

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


# ── Constants ─────────────────────────────────────────────────────────

DOC_ID = sys.argv[1] if len(sys.argv) > 1 else "judicial_academy_exp_001"

TYPOLOGY = "Blacklist-Not-Checked"

QDRANT_URL  = os.environ.get("QDRANT_URL", "http://localhost:6333")
COLLECTION  = "tender_sections"

LLM_MODEL = os.environ.get("LLM_MODEL", "qwen/qwen-2.5-72b-instruct")


# Answer-shaped query — mirrors the literal wording of MPS-021,
# GFR Rule 151, CLAUSE-BLACKLIST-DISCLOSURE-FORM-001, and the
# WB/ADB debarment cross-check anchor.
QUERY_TEXT = (
    "blacklist debarment sanction ineligible disqualification "
    "previous transgression bidder declaration self-certificate "
    "World Bank ADB debarred banned excluded GFR Rule 151 "
    "MPS 2022 holiday listing past performance"
)


# Rule candidates evaluated via condition_evaluator. Priority order:
# most specific first (Works / Services / Goods → universal).
# All except AP-GO-093 / 094 / 098 / 108 are HARD_BLOCK.
# defeats=[] across the typology — knowledge-layer gap, no
# defeasibility wired (same pattern as IP/LD/MA).
RULE_CANDIDATES = [
    {
        "rule_id":         "MPS-021",
        "natural_language": "Bidder must declare past debarments/transgressions in last 3 years; non-disclosure is grounds for disqualification (universal)",
        "severity":        "HARD_BLOCK",
        "layer":           "Central",
        "shape":           "presence",
    },
    {
        "rule_id":         "MPW-158",
        "natural_language": "GFR Rule 151 — Works bidder shall be debarred if convicted under PC Act 1988 or IPC for loss of life/property/public health during contract execution",
        "severity":        "HARD_BLOCK",
        "layer":           "Central",
        "shape":           "presence",
    },
    {
        "rule_id":         "MPS-186",
        "natural_language": "GFR Rule 151 — bidder shall be debarred if convicted under PC Act 1988 or IPC during procurement contract execution",
        "severity":        "HARD_BLOCK",
        "layer":           "Central",
        "shape":           "presence",
    },
    {
        "rule_id":         "GFR-G-037",
        "natural_language": "Bidder convicted under PC Act 1988 or IPC for loss of life/property in procurement contract shall be debarred up to 3 years",
        "severity":        "HARD_BLOCK",
        "layer":           "Central",
        "shape":           "presence",
    },
    {
        "rule_id":         "AP-GO-095",
        "natural_language": "AP contractor must be removed from approved list on multiple performance failures, persistent contract violation, false information, etc.",
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


# ── LLM rerank prompt for Blacklist detection ────────────────────────

LLM_SYSTEM = (
    "You are a precise procurement-document fact extractor. "
    "Return JSON only. Quote evidence verbatim from the supplied text. "
    "Never fabricate."
)


# Keyword vocabulary for smart_truncate windowing (blacklist-specific).
# Blacklist-check requirements often appear as short clauses in long
# ITB blocks; centring the window on the literal phrase prevents
# elision (L26).
BLACKLIST_TRUNCATE_KEYWORDS = [
    r"\bblacklist",
    r"\bdebar",
    r"\bsanction",
    r"\bineligible",
    r"\bineligibility",
    r"holiday listing",
    r"previous transgression",
    r"banned",
    r"convicted",
    r"declaration",
    r"\bself-cert",
    r"World Bank",
    r"\bADB\b",
    r"\bIBRD\b",
    r"\bIDA\b",
    r"GFR.*Rule.*151",
    r"Prevention of Corruption Act",
    r"barred by",
    r"sphere of effective influence",
    r"allied firm",
]


def build_blacklist_rerank_prompt(candidates: list[dict]) -> str:
    blocks = []
    for i, c in enumerate(candidates):
        body = smart_truncate(c["full_text"], window=3000,
                               keywords=BLACKLIST_TRUNCATE_KEYWORDS)
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
        "Exactly ONE of them (or none) carries the BLACKLIST-CHECK REQUIREMENT — "
        "either:\n"
        "  (a) a BIDDER-SIDE DECLARATION REQUIREMENT: bidders MUST declare past "
        "      debarments / blacklistings / sanctions / convictions / previous "
        "      transgressions of the Code of Integrity (typically last 3 years), "
        "      with non-disclosure being grounds for disqualification; OR\n"
        "  (b) a BUYER-SIDE VERIFICATION COMMITMENT: the procuring entity will "
        "      verify the bidder against debarment lists (Department of "
        "      Expenditure, CVC, World Bank, ADB, AP State blacklist, etc.) "
        "      before award; OR\n"
        "  (c) an ELIGIBILITY-BAR clause: the doc explicitly states that bidders "
        "      who are debarred / banned / blacklisted / sanctioned by named "
        "      authorities are NOT eligible to participate in this procurement.\n"
        "\n"
        "Any one of (a) / (b) / (c) is sufficient to count as compliant.\n\n"
        f"{candidates_block}\n\n"
        "Question: Does the document carry a blacklist-check requirement? Identify "
        "the candidate that proves it.\n\n"
        "Return JSON only:\n"
        "{\n"
        "  \"chosen_index\":              integer 0..N-1 of the blacklist-check candidate, OR null if no candidate explicitly carries the requirement,\n"
        "  \"blacklist_check_required\":  bool,\n"
        "  \"check_form\":                one of 'bidder_self_declaration' | 'buyer_verification_commitment' | 'eligibility_bar' | 'multiple' | null,\n"
        "  \"includes_multilateral_lender_check\": bool OR null  (true if WB/ADB/JICA debarment cross-check is included),\n"
        "  \"go_reference\":              string OR null  (e.g. 'GFR Rule 151', 'GO Ms No XX', 'CVC Office Order ZZ'),\n"
        "  \"evidence\":                  \"verbatim quote from the chosen candidate's text identifying the blacklist-check requirement\",\n"
        "  \"found\":                     bool,\n"
        "  \"reasoning\":                 \"one short sentence explaining the choice\"\n"
        "}\n\n"
        "Selection rules — IGNORE the following content (NOT a blacklist-check requirement):\n"
        "- GENERAL PQ DISQUALIFICATION GROUNDS (e.g. 'misleading representations', "
        "  'poor performance in last 5 years', 'abandonment of work') — those are "
        "  performance-history disqualifications, NOT blacklist/debarment checks.\n"
        "- CONFLICT-OF-INTEREST forms or COI declarations for consultants — that's "
        "  a separate eligibility shape (consulting-specific, ITB COI clauses).\n"
        "- ALLIED-FIRM DEFINITIONS alone (CLAUSE-DEBARMENT-ALLIED-001 pattern) — "
        "  the definition is operational; the bidder-check requirement must be "
        "  separately stated.\n"
        "- AP CONTRACTOR ADVANCE restrictions (No advance to contractor without "
        "  special sanction) — different shape.\n"
        "- HOLIDAY LISTING / DEBARMENT GRADES descriptions (Volume-I/Section-2/ITB "
        "  CLAUSE-DEBARMENT-GRADES-001 / CLAUSE-HOLIDAY-LISTING-001) — these "
        "  describe WHAT debarment levels exist, not WHETHER bidders are required "
        "  to declare past debarments. Pick them only if the same clause ALSO "
        "  imposes a present-tense bidder declaration / buyer verification.\n"
        "- INTEGRITY PACT clauses or CIPP code declarations alone — separate "
        "  typology (Missing-Integrity-Pact). The blacklist check is distinct.\n"
        "- SHOW-CAUSE NOTICE FORMATS or DEBARMENT ORDER FORMATS (MPW 2025 "
        "  Annexures 15/16) — operational templates, not bidder requirements.\n"
        "\n"
        "Selection rules — DO pick if the candidate states:\n"
        "- 'Bidder shall declare any previous debarment/blacklisting/conviction/"
        "  transgression by [list of authorities]' (bidder_self_declaration).\n"
        "- 'A bidder shall be debarred if convicted under [PC Act 1988 / IPC / "
        "  any law] for [loss of life / property / public health] during contract "
        "  execution' (eligibility_bar via GFR Rule 151).\n"
        "- 'A bidder that has been sanctioned by [WB/ADB/IBRD/IDA] is NOT "
        "  eligible to participate' (eligibility_bar with multilateral cross-check).\n"
        "- 'Bidders barred by Central/State Govt or any controlled entity OR "
        "  World Bank OR ADB shall be excluded' (CLAUSE-BLACKLIST-DISCLOSURE-"
        "  FORM-001 anchor).\n"
        "- 'The Procuring Entity shall verify the bidder against [debarment "
        "  list/CVC/DoE/WB sanctions list]' (buyer_verification_commitment).\n"
        "- A reference to GFR Rule 151, MPS Para 2.4.5, OM F.1/20/2018-PPD, OR "
        "  AP-GO blacklist orders.\n"
        "\n"
        "QUOTE FORMAT — STRICT (per L35):\n"
        "- Return a SINGLE CONTIGUOUS quote from ONE sentence or ONE clause only.\n"
        "- Do NOT stitch multiple paragraphs into one quote.\n"
        "- Do NOT add ellipsis ('...') between lines.\n"
        "- Do NOT paraphrase, summarise, or condense — quote EXACTLY as it appears in the supplied text.\n"
        "- Do NOT introduce or remove markdown formatting (asterisks, underscores, italics) — preserve the source formatting verbatim.\n"
        "- Pick the SHORTEST contiguous span that proves the requirement; one sentence is usually enough.\n"
        "\n"
        "- If no candidate carries the requirement, set chosen_index=null, "
        "  blacklist_check_required=false, found=false. This is the BYPASS-VIOLATION "
        "  outcome.\n"
        "- Evidence MUST be an exact substring of the chosen candidate's text."
    )


def parse_llm_response(raw: str) -> dict:
    """Thin wrapper kept for in-script readability — actual parsing
    logic lives in modules.validation.llm_client.parse_llm_json
    (per L35 — handles AP markdown's invalid-JSON escapes)."""
    return parse_llm_json(raw)


# ── Rule selection via condition_evaluator ────────────────────────────

def fetch_tender_facts(doc_id: str) -> dict:
    """Build the facts dict that condition_evaluator reads.

    Strict LLM-only sources (per L28). The Central rules' subterms
    (BidderHasConvictionRecord, IntegrityBreachDetermined, etc.) are
    NOT extracted at this stage — they will resolve as UNKNOWN
    during evaluation when referenced. MPS-021 has a clean
    `TenderType=ANY` so it fires on every doc.
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
        "tender_type":     p.get("tender_type"),
        "is_ap_tender":    bool(p.get("is_ap_tender")),
        "TenderType":      p.get("tender_type"),
        "TenderState":     "AndhraPradesh" if p.get("is_ap_tender") else "Other",
    }

    ev_cr = p.get("estimated_value_cr")
    if ev_cr is not None:
        try:
            facts["EstimatedValue"] = float(ev_cr) * 1e7
            facts["_estimated_value_cr"] = float(ev_cr)
        except (TypeError, ValueError):
            pass

    return facts


def select_blacklist_rule(tender_facts: dict) -> dict | None:
    """Pick the highest-priority rule that fires (or fires-as-UNKNOWN
    per L27 downgrade). MPS-021 is the canonical primary across the
    corpus."""
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

def _delete_prior_tier1_blacklist(doc_id: str) -> tuple[int, int]:
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
    print(f"  Tier-1 Blacklist-Not-Checked (BGE-M3 + LLM, NO regex)")
    print(f"  doc_id : {DOC_ID}")
    print(f"  model  : {LLM_MODEL}")
    print("=" * 76)

    n_f, n_e = _delete_prior_tier1_blacklist(DOC_ID)
    if n_f or n_e:
        print(f"  cleared {n_f} prior Tier-1 Blacklist finding node(s) and "
              f"{n_e} edge(s) before re-running")

    # 1. Pick rule via condition_evaluator
    facts = fetch_tender_facts(DOC_ID)
    rule  = select_blacklist_rule(facts)
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
    print(f"\n── Step 3: LLM rerank + Blacklist-check detection ──")
    user_prompt = build_blacklist_rerank_prompt(candidates)
    print(f"  prompt size: {len(user_prompt)} chars (~{len(user_prompt)//4} tokens)")
    raw_content = call_llm(LLM_SYSTEM, user_prompt, max_tokens=900)
    timings["llm"] = time.perf_counter() - t0
    print(f"  wall: {timings['llm']:.2f}s")
    print(f"\n── Raw LLM JSON ──")
    print(raw_content)

    # 7. Parse + select chosen candidate
    parsed = parse_llm_response(raw_content)
    chosen        = parsed.get("chosen_index")
    bl_required   = bool(parsed.get("blacklist_check_required"))
    check_form    = parsed.get("check_form")
    multilateral  = parsed.get("includes_multilateral_lender_check")
    go_reference  = parsed.get("go_reference")
    evidence      = (parsed.get("evidence") or "").strip()
    found         = bool(parsed.get("found"))
    reason        = (parsed.get("reasoning") or "").strip()

    print(f"\n── Parsed ──")
    print(f"  chosen_index               : {chosen}")
    print(f"  found                      : {found}")
    print(f"  blacklist_check_required   : {bl_required}")
    print(f"  check_form                 : {check_form!r}")
    print(f"  includes_multilateral_check: {multilateral}")
    print(f"  go_reference               : {go_reference!r}")
    print(f"  reasoning                  : {reason[:200]}")
    print(f"  evidence                   : {evidence[:300]!r}")

    section = None
    similarity = None
    ev_passed = False
    ev_score = 0
    ev_method = "skipped"

    # L35 three-state contract: track LLM's pre-verification verdict
    # separately from post-verification. A failed L24 quote-
    # verification is NOT the same as an absent clause.
    llm_found_clause   = bl_required and (chosen is not None)
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
        if bl_required:
            print(f"  ⚠ bl_required=True but chosen_index=null — treating as False")
            bl_required = False
            llm_found_clause = False

    # 8. Three-way decision (per L35)
    is_compliant  = llm_found_clause and ev_passed
    is_unverified = llm_found_clause and not ev_passed
    is_absence    = not llm_found_clause

    if is_compliant:
        reason_label = "compliant_blacklist_check_present"
    elif is_unverified:
        reason_label = "blacklist_check_unverified_llm_found_quote_failed_l24"
    else:
        reason_label = "blacklist_check_absent_violation"

    print(f"\n── Decision ──")
    print(f"  rule              : {rule['rule_id']} ({rule['severity']}, shape={rule['shape']})")
    print(f"  llm_found_clause  : {llm_found_clause}")
    print(f"  ev_passed         : {ev_passed}  (score={ev_score}, method={ev_method})")
    print(f"  is_compliant      : {is_compliant}")
    print(f"  is_unverified     : {is_unverified}")
    print(f"  is_absence        : {is_absence}")
    print(f"  reason_label      : {reason_label}")

    if is_compliant:
        return 0

    # 9. Materialise finding (UNVERIFIED or ABSENCE)
    t0 = time.perf_counter()
    if section is not None and is_unverified:
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
        evidence  = (f"Blacklist-check requirement not found in document "
                     f"after searching {', '.join(section_types)} section types")
        print(f"  → ABSENCE finding — skipping evidence_guard "
              f"(no quote to verify)")
    elif is_unverified:
        print(f"  → UNVERIFIED finding — LLM identified clause but quote "
              f"failed L24 verification (score={ev_score}, method={ev_method})")

    rule_node_id = get_or_create_rule_node(DOC_ID, rule["rule_id"])

    if is_unverified:
        label = (
            f"{TYPOLOGY}: UNVERIFIED — LLM found blacklist-check requirement "
            f"but quote failed L24 (score={ev_score}, method={ev_method}); "
            f"requires human review against {(section['heading'][:60] if section else 'TenderDocument')!r}"
        )
    else:
        label = (
            f"{TYPOLOGY}: blacklist-check requirement absent — {rule['rule_id']} "
            f"({rule['severity']}) requires bidder declaration / buyer "
            f"verification of past debarments for this tender"
        )

    finding_props = {
        "rule_id":               rule["rule_id"],
        "typology_code":         TYPOLOGY,
        "severity":              rule["severity"],
        "evidence":              evidence,
        "extraction_path":       "presence",
        "llm_found_clause":      llm_found_clause,
        "blacklist_check_required":           llm_found_clause,    # mirrors LLM verdict (pre-L24)
        "check_form":                         check_form,
        "includes_multilateral_lender_check": multilateral,
        "go_reference":          go_reference,
        "rule_shape":            rule["shape"],
        "violation_reason":      reason_label,
        "tier":                  1,
        "extracted_by":          "bge-m3+llm-rerank:qwen-2.5-72b@openrouter",
        "retrieval_strategy": (
            f"qdrant_top{K}_router_{family}_section_filter_"
            f"{'-'.join(section_types)}_llm_rerank"
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
            "LLM found blacklist-check requirement but evidence quote "
            f"failed L24 verification (score={ev_score}, method={ev_method}). "
            f"Reviewer should open the section above (line_start={line_start_local}, "
            f"line_end={line_end_local}) and confirm the bidder-declaration "
            f"or buyer-verification clause is present in the source text."
            if is_unverified else None
        ),
        "defeated":            False,
    }

    finding = rest_post("kg_nodes", [{
        "doc_id":    DOC_ID,
        "node_type": "ValidationFinding",
        "label":     label,
        "properties": finding_props,
        "source_ref": f"tier1:blacklist_check:{rule['rule_id']}",
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
                "blacklist_check_required": False,
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
