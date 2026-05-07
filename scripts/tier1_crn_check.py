"""
scripts/tier1_crn_check.py

Tier-1 Criteria-Restriction-Narrow check, BGE-M3 + LLM, NO regex.

PRESENCE shape with anti-pattern detection. The Criteria-Restriction-
Narrow typology has 47 TYPE_1_ACTIONABLE rules but most are
Consultancy/Goods-PQB-specific (SKIP on Works/PPP corpus). The Tier-1-
operationalisable subset:

    MPG-279  TenderType=ANY              HARD_BLOCK
             Article 19(1)(g) — REASONABLE eligibility/PQ criteria;
             cannot exclude eligible bidders for arbitrary or
             capricious reasons. The anchor for "JV ban without
             regulatory justification".

Other anchored rules (out of scope for this typology, in this typology
script — covered by adjacent typologies or excluded):
    MPG-076  HARD_BLOCK ANY  — eligibility criteria specified in
             tender doc. All 6 corpus docs comply trivially.
    MPG-120  HARD_BLOCK ANY  — evaluation only on tender-doc terms;
             meta-quality, not a discrete clause check.
    MPG-129  HARD_BLOCK ANY  — techno-commercial evaluation only on
             tender-doc conditions; meta.
    MPG-078  HARD_BLOCK PQ   — PQ entirely on bidder capability.
    AP-GO-061  WARNING AP    — gov-experience-only requirement
             (regulated, not anti-pattern).
    AP-GO-159  Goods-only    — SKIPs corpus-wide.

Corpus pattern (from read-first):
    JA (APCRDA Works):    BDS L445 "Joint Venture: not allowed" —
                          NO regulatory citation → GAP_VIOLATION
    HC (APCRDA Works):    BDS L299 "Joint Venture: not allowed" —
                          NO regulatory citation → GAP_VIOLATION
    Vizag (UGSS):         JV allowed, max 2 → COMPLIANT silent
    Kakinada (SBD):       JV applicable, max 3, lead 51% → COMPLIANT silent
    Tirupathi (PPP):      Consortium up to 3 (DCA framework) → COMPLIANT silent
    Vijayawada (PPP):     same as Tirupathi → COMPLIANT silent

This is the FOURTH APCRDA Works template gap (JA + HC pair):
    L43 Arbitration §60 Property weakness
    L50 Solvency framework (no Tahsildar / no validity)
    L53 JV ban without justification (this typology)
    Plus L52 ABC formula M=3 in the OTHER pair (Vizag + Kakinada).

Pipeline:
  1. Pick rule (MPG-279 fires on all 6 docs — TenderType=ANY).
  2. Section filter via CRN_SECTION_ROUTER → [NIT, ITB, Forms].
  3. BGE-M3 dual queries (framework + value).
  4. Per-section-type quota retrieval (L49) + grep-seeded supplement
     for JV / Consortium / "not allowed" keywords (L50).
  5. LLM rerank with CRN-specific ignore rules (foreign-bidder ban
     covered by L44 Geographic-Restriction; conflict-of-interest
     bars; multi-bid restriction; sub-contracting limits) and 4-field
     structured extraction.
  6. L24 evidence-guard hallucination check.
  7. L36/L40 grep fallback for absence path.
  8. Decision tree (silent-on-COMPLIANT per L48):
        COMPLIANT silent if:
          - jv_consortium_explicitly_allowed=True, OR
          - jv_consortium_explicitly_banned=True AND
            jv_ban_has_regulatory_citation=True (justified), OR
          - neither allowed nor banned (not mentioned = no restriction).
        GAP_VIOLATION if:
          - jv_consortium_explicitly_banned=True AND
            jv_ban_has_regulatory_citation=False
            (arbitrary exclusion per MPG-279).
        UNVERIFIED if L24 fails OR grep-promoted absence.

Tested on judicial_academy_exp_001 first (expected: GAP_VIOLATION
HARD_BLOCK — APCRDA Works template BDS override "JV not allowed"
without regulatory citation).
"""
from __future__ import annotations

import os
import sys
import time
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
from modules.validation.grep_fallback    import (
    grep_source_for_keywords,
    grep_full_source_for_keywords,
)


# ── Constants ─────────────────────────────────────────────────────────

DOC_ID = sys.argv[1] if len(sys.argv) > 1 else "judicial_academy_exp_001"

TYPOLOGY = "Criteria-Restriction-Narrow"

QDRANT_URL  = os.environ.get("QDRANT_URL", "http://localhost:6333")
COLLECTION  = "tender_sections"

LLM_MODEL = os.environ.get("LLM_MODEL", "qwen/qwen-2.5-72b-instruct")


GREP_FALLBACK_KEYWORDS = [
    "joint venture",
    "Joint Venture",
    "consortium",
    "Consortium",
    "\bJV\b",
    "\bSPV\b",
    "not allowed",
    "not permitted",
    "Joint Venture: not allowed",
    "JV: not allowed",
    "Eligible Bidders",
    "Bidder Eligibility",
]


QUERY_FRAMEWORK = (
    "Eligible Bidders Joint Venture JV Consortium SPV maximum number "
    "of members lead member partner ITB 4.1 BDS bidder eligibility "
    "private entity firm registered legally enforceable"
)
QUERY_VALUE = (
    "Joint Venture not allowed not permitted JV not allowed maximum "
    "members NA Consortium maximum 3 Companies Lead Member 26 percent "
    "Equity Contribution joint bidding agreement"
)
QUERY_TEXT = QUERY_VALUE


RULE_CANDIDATES = [
    {
        "rule_id":          "MPG-279",
        "natural_language": "Article 19(1)(g) requires REASONABLE eligibility/pre-qualification criteria; the procurement organisation cannot exclude eligible bidders for arbitrary or capricious reasons",
        "severity":         "HARD_BLOCK",
        "layer":            "Central",
        "shape":            "presence_anti_pattern",
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


# ── LLM rerank prompt for JV/Consortium restriction extraction ───────

LLM_SYSTEM = (
    "You are a precise procurement-document fact extractor. "
    "Return JSON only. Quote evidence verbatim from the supplied text. "
    "Never fabricate."
)


CRN_TRUNCATE_KEYWORDS = [
    r"joint\s+venture",
    r"\bJV\b",
    r"consortium",
    r"\bSPV\b",
    r"eligible\s+bidders",
    r"bidder\s+eligibility",
    r"not\s+allowed",
    r"not\s+permitted",
    r"max(?:imum)?\s+(?:number\s+of\s+)?members",
    r"lead\s+(?:member|partner|bidder)",
    r"ITB\s+4\.1",
    r"BDS\s+ITB",
    r"\bGO\s*Ms\s*No\b",
    r"\bAP-GO-\d+\b",
]


def build_crn_rerank_prompt(candidates: list[dict]) -> str:
    blocks = []
    for i, c in enumerate(candidates):
        body = smart_truncate(c["full_text"], window=3000,
                               keywords=CRN_TRUNCATE_KEYWORDS)
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
        "Extract the bidding document's JOINT VENTURE / CONSORTIUM ELIGIBILITY "
        "RULE — does the doc allow bidders to participate as a JV/Consortium, "
        "or does it BAN JV/Consortium participation, and if banned, is there a "
        "regulatory citation justifying the ban?\n\n"
        "The regulatory backdrop: per MPG-279 (Article 19(1)(g)), procuring "
        "entities must adopt REASONABLE eligibility criteria and cannot exclude "
        "bidders for arbitrary or capricious reasons. Banning JVs without "
        "regulatory justification is an arbitrary exclusion.\n\n"
        f"{candidates_block}\n\n"
        "Question: Across ALL candidates, what does the bidding doc say about "
        "JV/Consortium eligibility? Pick the SINGLE BEST candidate (or null) "
        "for the verbatim evidence quote, but evaluate the boolean extractions "
        "against the FULL evidence visible in any candidate. If the doc has a "
        "BDS override flipping the standard JV-permitted framework to 'not "
        "allowed', the BDS override is what counts (most-specific wins).\n\n"
        "Return JSON only:\n"
        "{\n"
        "  \"chosen_index\":                          integer 0..N-1 of the best candidate, OR null if no candidate states JV/Consortium eligibility,\n"
        "  \"jv_consortium_explicitly_allowed\":      bool   (TRUE if doc explicitly allows JV / Consortium participation, even with constraints like max-members),\n"
        "  \"jv_consortium_explicitly_banned\":       bool   (TRUE if doc explicitly states 'Joint Venture: not allowed' / 'JV: not permitted' / 'No JV/Consortium' / equivalent — typically in BDS rewrite of ITB 4.1(a)),\n"
        "  \"jv_ban_has_regulatory_citation\":        bool   (TRUE only if the ban is accompanied by a specific regulatory citation — 'per AP-GO-X' / 'per GO Ms No Y' / 'per MPW Z' / similar; FALSE if the ban stands alone),\n"
        "  \"jv_max_members\":                        integer OR null  (e.g. 2 for 'max 2 members', 3 for 'up to 3 Companies'; null if not stated),\n"
        "  \"jv_lead_member_threshold_pct\":          float OR null  (e.g. 51.0 for '51%' / 26.0 for '26% Equity Contribution'; null if not stated),\n"
        "  \"evidence\":                              \"verbatim quote from the chosen candidate's text identifying the JV/Consortium eligibility statement\",\n"
        "  \"found\":                                 bool,\n"
        "  \"reasoning\":                             \"one short sentence explaining the choice and the determination\"\n"
        "}\n\n"
        "Selection rules — IGNORE the following content (NOT the JV/Consortium eligibility rule):\n"
        "- FOREIGN-BIDDER bans / Land-Border-Country restrictions (covered by "
        "  the Geographic-Restriction typology — separate concern).\n"
        "- CONFLICT OF INTEREST clauses (ITB 4.2 — separate eligibility bar).\n"
        "- MULTI-BID RESTRICTION ('one bid per bidder/JV-member' — separate).\n"
        "- BID SECURITY / EMD requirements.\n"
        "- BID VALIDITY periods.\n"
        "- DEBARMENT / Blacklisting clauses (covered by Blacklist-Not-Checked).\n"
        "- WORLD BANK / ADB DEBARMENT clauses.\n"
        "- SUB-CONTRACTING limits (separate clause).\n"
        "\n"
        "Selection rules — DO pick if the candidate states:\n"
        "- A standard ITB §4.1 'Eligible Bidders' clause permitting JV with sub-"
        "  points (a-h) on max-members, lead-member, joint-and-several "
        "  liability — pick this if it's the dominant statement.\n"
        "- A BDS rewrite at ITB 4.1(a) with 'Joint Venture: not allowed' / "
        "  'Joint Venture: allowed, max N members' — pick the BDS override "
        "  preferentially (it's the operative rule).\n"
        "- A PPP/DCA Definitions section stating 'Consortium = up to N "
        "  Companies' with Lead Member equity threshold — pick if this is the "
        "  dominant JV/Consortium framing for the doc.\n"
        "- An SBD clause stating 'Joint Venture is Applicable. A maximum of N "
        "  bidders allowed... lead bidder shall have X%'.\n"
        "\n"
        "Boolean-extraction rules:\n"
        "- jv_consortium_explicitly_allowed is TRUE if the doc has any "
        "  affirmative permission for JV/Consortium (even with constraints).\n"
        "- jv_consortium_explicitly_banned is TRUE only if the doc has "
        "  EXPLICIT prohibitive language ('not allowed', 'not permitted', 'No "
        "  JV', 'Joint Venture is not applicable'). The BDS override pattern "
        "  ('If JV allowed, maximum number: NA' combined with 'Joint Venture: "
        "  not allowed') counts as banned.\n"
        "- If the doc's standard ITB §4.1 allows JV BUT a BDS override flips "
        "  it to 'not allowed', set jv_consortium_explicitly_banned=true and "
        "  jv_consortium_explicitly_allowed=false. The BDS override is the "
        "  operative rule.\n"
        "- jv_ban_has_regulatory_citation is TRUE only when an explicit "
        "  regulatory anchor is cited alongside the ban ('per GO Ms No X dt "
        "  Y' / 'per APSS clause Z' / 'per MPW 2022 §A.B.C'). Generic 'as per "
        "  procuring authority's discretion' / 'as decided by employer' do NOT "
        "  count as citations.\n"
        "\n"
        "- Evidence MUST be an exact substring of the chosen candidate's text.\n"
        "- If no candidate states JV/Consortium eligibility, set chosen_index=null, "
        "  all booleans=false, found=false.\n"
        "\n"
        "QUOTE FORMAT — STRICT (per L35):\n"
        "- Return a SINGLE CONTIGUOUS quote from ONE sentence or ONE clause only.\n"
        "- Do NOT stitch multiple paragraphs into one quote.\n"
        "- Do NOT add ellipsis ('...') between lines.\n"
        "- Do NOT paraphrase, summarise, or condense — quote EXACTLY as it appears in the supplied text.\n"
        "- Do NOT introduce or remove markdown formatting (asterisks, underscores, italics, backslash-escapes) — preserve the source formatting verbatim.\n"
        "- Pick the SHORTEST contiguous span that proves the JV/Consortium status; one sentence or one row is usually enough."
    )


def parse_llm_response(raw: str) -> dict:
    return parse_llm_json(raw)


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


def select_crn_rule(tender_facts: dict) -> dict | None:
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

def _delete_prior_tier1_crn(doc_id: str) -> tuple[int, int]:
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
    print(f"  Tier-1 Criteria-Restriction-Narrow (BGE-M3 + LLM, NO regex)")
    print(f"  doc_id : {DOC_ID}")
    print(f"  model  : {LLM_MODEL}")
    print("=" * 76)

    n_f, n_e = _delete_prior_tier1_crn(DOC_ID)
    if n_f or n_e:
        print(f"  cleared {n_f} prior Tier-1 CRN finding node(s) and "
              f"{n_e} edge(s) before re-running")

    facts = fetch_tender_facts(DOC_ID)
    rule  = select_crn_rule(facts)
    if rule is None:
        return 0

    family, section_types = family_for_doc_with_filter(DOC_ID, TYPOLOGY)
    print(f"\n── Document family / retrieval filter ──")
    print(f"  family         : {family}")
    print(f"  section_types  : {section_types}")

    print(f"\n── Query 1/2 (framework, answer-shaped) ──")
    print(f"  ({len(QUERY_FRAMEWORK)} chars) {QUERY_FRAMEWORK}")
    print(f"\n── Query 2/2 (value, answer-shaped) ──")
    print(f"  ({len(QUERY_VALUE)} chars) {QUERY_VALUE}")
    t0 = time.perf_counter()
    qvec_fw  = embed_query(QUERY_FRAMEWORK)
    qvec_val = embed_query(QUERY_VALUE)
    timings["embed"] = time.perf_counter() - t0
    print(f"\n── Step 1: BGE-M3 embed (×2) ── "
          f"vec_dim={len(qvec_fw)}  wall={timings['embed']:.2f}s")

    K_FW     = 4
    K_VAL    = 3
    K_MERGED = 14
    t0 = time.perf_counter()

    fw_filter = [t for t in section_types if t in ("ITB", "NIT")]
    if not fw_filter:
        fw_filter = section_types[:1]
    points_fw: list[dict] = []
    try:
        points_fw = qdrant_topk(qvec_fw, DOC_ID, k=K_FW, section_types=fw_filter)
    except RuntimeError:
        points_fw = []

    points_val: list[dict] = []
    val_breakdown: list[tuple[str, int]] = []
    for st in section_types:
        try:
            pts = qdrant_topk(qvec_val, DOC_ID, k=K_VAL, section_types=[st])
            points_val.extend(pts)
            val_breakdown.append((st, len(pts)))
        except RuntimeError:
            val_breakdown.append((st, 0))

    by_id: dict = {}
    for p in points_fw + points_val:
        pid = p["id"]
        if pid not in by_id or p["score"] > by_id[pid]["score"]:
            by_id[pid] = p

    SEED_KEYWORDS = ["Joint Venture", "joint venture", "Consortium",
                     "JV: not allowed", "Joint Venture: not allowed"]
    _, seed_hits = grep_source_for_keywords(
        DOC_ID, section_types, SEED_KEYWORDS,
    )
    seeded_section_ids = {h["section_node_id"] for h in seed_hits}
    n_seeded_added = 0
    if seeded_section_ids:
        for sid in seeded_section_ids:
            already_in = any(
                (p["payload"].get("section_id") == sid) for p in by_id.values()
            )
            if already_in:
                continue
            sec_rows = rest_get("kg_nodes", {
                "select":  "node_id,properties",
                "node_id": f"eq.{sid}",
            })
            if not sec_rows:
                continue
            mp = sec_rows[0].get("properties") or {}
            seeded_pt = {
                "id":      f"seeded:{sid}",
                "score":   0.0,
                "payload": {
                    "section_id":       sid,
                    "heading":          mp.get("heading"),
                    "section_type":     mp.get("section_type"),
                    "source_file":      mp.get("source_file"),
                    "line_start_local": mp.get("line_start_local") or mp.get("line_start"),
                    "line_end_local":   mp.get("line_end_local")   or mp.get("line_end"),
                },
            }
            by_id[seeded_pt["id"]] = seeded_pt
            n_seeded_added += 1

    merged = sorted(by_id.values(), key=lambda x: x["score"], reverse=True)
    points = merged[:K_MERGED]
    K = len(points)
    timings["qdrant"] = time.perf_counter() - t0

    val_str = ", ".join(f"{t}:{n}" for t, n in val_breakdown) or "(none)"
    print(f"\n── Step 2: per-section-type quota + L50 grep-seeded supplement (family={family}) ──")
    print(f"  framework lens [{','.join(fw_filter)}] (top-{K_FW}) → {len(points_fw)} pts")
    print(f"  value lens [{val_str}] (top-{K_VAL} per type) → {len(points_val)} pts")
    print(f"  L50 grep-seeded {SEED_KEYWORDS} → "
          f"{len(seeded_section_ids)} matching section(s), "
          f"{n_seeded_added} new (deduped)")
    print(f"  → {len(merged)} merged → top-{K} fed to LLM "
          f"(in {timings['qdrant']*1000:.0f}ms total):")
    for i, p in enumerate(points):
        pl = p["payload"]
        h  = (pl.get("heading") or pl.get("section_heading") or "")[:60]
        print(f"    [{i}] cosine={p['score']:.4f}  type={pl.get('section_type','?'):14s}  "
              f"lines={pl.get('line_start_local')}-{pl.get('line_end_local')}  {h}")

    t0 = time.perf_counter()
    candidates = []
    for p in points:
        sec = resolve_section(DOC_ID, p["payload"])
        sec["similarity"] = p["score"]
        candidates.append(sec)
    timings["fetch_section"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    print(f"\n── Step 3: LLM rerank + JV/Consortium extraction ──")
    user_prompt = build_crn_rerank_prompt(candidates)
    print(f"  prompt size: {len(user_prompt)} chars (~{len(user_prompt)//4} tokens)")
    raw_content = call_llm(LLM_SYSTEM, user_prompt, max_tokens=900)
    timings["llm"] = time.perf_counter() - t0
    print(f"  wall: {timings['llm']:.2f}s")
    print(f"\n── Raw LLM JSON ──")
    print(raw_content)

    parsed = parse_llm_response(raw_content)
    chosen          = parsed.get("chosen_index")
    jv_allowed      = bool(parsed.get("jv_consortium_explicitly_allowed"))
    jv_banned       = bool(parsed.get("jv_consortium_explicitly_banned"))
    jv_cited        = bool(parsed.get("jv_ban_has_regulatory_citation"))
    jv_max          = parsed.get("jv_max_members")
    jv_lead_pct     = parsed.get("jv_lead_member_threshold_pct")
    evidence        = (parsed.get("evidence") or "").strip()
    found           = bool(parsed.get("found"))
    reason          = (parsed.get("reasoning") or "").strip()

    print(f"\n── Parsed ──")
    print(f"  chosen_index                          : {chosen}")
    print(f"  found                                 : {found}")
    print(f"  jv_consortium_explicitly_allowed      : {jv_allowed}")
    print(f"  jv_consortium_explicitly_banned       : {jv_banned}")
    print(f"  jv_ban_has_regulatory_citation        : {jv_cited}")
    print(f"  jv_max_members                        : {jv_max}")
    print(f"  jv_lead_member_threshold_pct          : {jv_lead_pct}")
    print(f"  reasoning                             : {reason[:200]}")
    print(f"  evidence                              : {evidence[:300]!r}")

    section = None
    similarity = None
    ev_passed = False
    ev_score = 0
    ev_method = "skipped"

    llm_found_signal = (chosen is not None) and (jv_allowed or jv_banned)
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
                print(f"  L24_FAILED — LLM extracted booleans but quote unverifiable.")
                print(f"    LLM evidence  : {evidence[:200]!r}")
        else:
            print(f"  ⚠ no evidence quote provided — treating as L24-failed (empty)")
            ev_passed = False; ev_score = 0; ev_method = "empty"
    else:
        print(f"  → no candidate chosen by LLM")

    # Decision tree (silent-on-COMPLIANT per L48):
    #   COMPLIANT silent if:
    #     - jv_allowed=True (allowed in some form), OR
    #     - jv_banned=True AND jv_cited=True (justified ban), OR
    #     - neither (not mentioned = no restriction).
    #   GAP_VIOLATION if:
    #     - jv_banned=True AND jv_cited=False (arbitrary ban).
    is_arbitrary_ban = jv_banned and not jv_cited

    is_compliant_l24  = llm_chose_candidate and ev_passed and not is_arbitrary_ban
    is_unverified_l24 = llm_chose_candidate and (not ev_passed) and llm_found_signal
    raw_is_absence    = (not llm_found_signal)
    is_gap_violation_pre_grep = (
        llm_chose_candidate and ev_passed and is_arbitrary_ban
    )

    grep_hits: list[dict] = []
    full_grep_hits: list[dict] = []
    grep_promoted_to_unverified = False
    full_grep_promoted = False
    kg_coverage_gap = False

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
        else:
            print(f"\n── L40 whole-file grep (Tier-2 fallback) ──")
            any_full, full_grep_hits = grep_full_source_for_keywords(
                DOC_ID, GREP_FALLBACK_KEYWORDS,
            )
            print(f"  whole-file any_hit    : {any_full}  "
                  f"({len(full_grep_hits)} match line(s))")
            if any_full:
                kg_coverage_gap = any(h["kg_coverage_gap"] for h in full_grep_hits)
                full_grep_promoted = True
                print(f"  → absence downgraded to UNVERIFIED")

    is_absence       = raw_is_absence and not grep_promoted_to_unverified and not full_grep_promoted
    is_unverified    = is_unverified_l24 or grep_promoted_to_unverified or full_grep_promoted
    is_gap_violation = is_gap_violation_pre_grep  # no edge for absence here — JV not mentioned IS compliant

    if is_compliant_l24:
        if jv_allowed:
            reason_label = (f"compliant_jv_allowed_max_{jv_max}_lead_{jv_lead_pct}"
                            if jv_max is not None else "compliant_jv_allowed")
        elif jv_banned and jv_cited:
            reason_label = "compliant_jv_banned_with_regulatory_citation"
        else:
            reason_label = "compliant_jv_eligibility_unrestricted"
    elif is_gap_violation_pre_grep:
        reason_label = "jv_consortium_banned_without_regulatory_citation"
    elif is_absence:
        # JV/Consortium not mentioned at all in the doc — no restriction, compliant.
        reason_label = "compliant_jv_eligibility_not_restricted"
    elif grep_promoted_to_unverified:
        reason_label = "crn_unverified_grep_fallback_retrieval_gap"
    elif full_grep_promoted:
        reason_label = ("crn_unverified_kg_coverage_gap"
                        if kg_coverage_gap
                        else "crn_unverified_whole_file_grep_only")
    elif is_unverified_l24:
        reason_label = "crn_unverified_llm_quote_failed_l24"
    else:
        reason_label = "crn_indeterminate"

    print(f"\n── Decision ──")
    print(f"  rule              : {rule['rule_id']} ({rule['severity']}, shape={rule['shape']})")
    print(f"  llm_found_signal  : {llm_found_signal}")
    print(f"  ev_passed         : {ev_passed}  (score={ev_score}, method={ev_method})")
    print(f"  jv_allowed        : {jv_allowed}")
    print(f"  jv_banned         : {jv_banned}")
    print(f"  jv_cited          : {jv_cited}")
    print(f"  is_arbitrary_ban  : {is_arbitrary_ban}")
    print(f"  is_compliant_l24  : {is_compliant_l24}")
    print(f"  is_gap_violation  : {is_gap_violation}")
    print(f"  is_unverified     : {is_unverified}")
    print(f"  is_absence        : {is_absence}")
    print(f"  reason_label      : {reason_label}")

    # COMPLIANT (or absence-treated-as-compliant) → silent
    if is_compliant_l24 or is_absence:
        print(f"\n  → COMPLIANT — no row, no edge emitted "
              f"(positive signal derived from absence of finding)")
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

    # Materialise (GAP_VIOLATION or UNVERIFIED)
    t0 = time.perf_counter()
    if section is not None and (is_gap_violation_pre_grep or is_unverified_l24) and not (
        grep_promoted_to_unverified or full_grep_promoted
    ):
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

    if is_gap_violation_pre_grep:
        ev_passed_out = ev_passed
        ev_score_out  = ev_score
        ev_method_out = ev_method
        evidence_out  = (f"{evidence}  [Restriction without justification: "
                         f"doc bans JV/Consortium without citing a "
                         f"regulatory authority — arbitrary exclusion per "
                         f"MPG-279 (Article 19(1)(g))]")
        print(f"  → GAP_VIOLATION finding — JV/Consortium banned without regulatory citation")
    elif grep_promoted_to_unverified:
        ev_passed_out = None
        ev_score_out  = None
        ev_method_out = "grep_fallback_retrieval_gap"
        evidence_out  = (f"LLM rerank top-{K} returned no JV/Consortium signal, "
                         f"but exhaustive grep across {', '.join(section_types)} "
                         f"found keyword hits in {len(grep_hits)} section(s).")
    elif full_grep_promoted:
        ev_passed_out = None
        ev_score_out  = None
        ev_method_out = ("whole_file_grep_kg_coverage_gap"
                         if kg_coverage_gap else "whole_file_grep_match")
        evidence_out  = f"L40 whole-file grep — {len(full_grep_hits)} match(es)"
    elif is_unverified_l24:
        ev_passed_out = ev_passed
        ev_score_out  = ev_score
        ev_method_out = ev_method
        evidence_out  = evidence
    else:
        ev_passed_out = ev_passed
        ev_score_out  = ev_score
        ev_method_out = ev_method
        evidence_out  = evidence

    rule_node_id = get_or_create_rule_node(DOC_ID, rule["rule_id"])

    if is_gap_violation_pre_grep:
        label = (
            f"{TYPOLOGY}: JV/Consortium banned without regulatory citation "
            f"— {rule['rule_id']} ({rule['severity']}) prohibits arbitrary "
            f"exclusion of eligible bidders (Article 19(1)(g))"
        )
    elif grep_promoted_to_unverified:
        label = (
            f"{TYPOLOGY}: UNVERIFIED (L36 grep-fallback) — LLM rerank "
            f"missed the JV signal; exhaustive grep found {len(grep_hits)} "
            f"section(s) with JV/Consortium keyword hits"
        )
    elif full_grep_promoted:
        label = (
            f"{TYPOLOGY}: UNVERIFIED (L40 whole-file fallback) — "
            f"{'KG-coverage gap' if kg_coverage_gap else 'whole-file only'}"
        )
    else:
        label = (
            f"{TYPOLOGY}: UNVERIFIED — LLM found JV signal but quote failed L24"
        )

    grep_audit = None
    if grep_promoted_to_unverified or full_grep_promoted:
        grep_audit = {
            "tier": ("L36_section_bounded" if grep_promoted_to_unverified
                     else "L40_whole_file"),
            "scanned_section_types": section_types,
            "keywords": GREP_FALLBACK_KEYWORDS,
            "kg_coverage_gap": kg_coverage_gap,
            "hits_count": (len(grep_hits) if grep_promoted_to_unverified
                           else len(full_grep_hits)),
        }

    finding_props = {
        "rule_id":               rule["rule_id"],
        "typology_code":         TYPOLOGY,
        "severity":              rule["severity"],
        "evidence":              evidence_out,
        "extraction_path":       "presence_anti_pattern",
        "llm_found_signal":      llm_found_signal,
        "jv_consortium_explicitly_allowed":  jv_allowed,
        "jv_consortium_explicitly_banned":   jv_banned,
        "jv_ban_has_regulatory_citation":    jv_cited,
        "jv_max_members":        jv_max,
        "jv_lead_member_threshold_pct": jv_lead_pct,
        "is_arbitrary_ban":      is_arbitrary_ban,
        "rule_shape":            rule["shape"],
        "violation_reason":      reason_label,
        "tier":                  1,
        "extracted_by":          "bge-m3+llm-rerank:qwen-2.5-72b@openrouter",
        "retrieval_strategy": (
            f"qdrant_top{K}_router_{family}_section_filter_"
            f"{'-'.join(section_types)}_per_type_quota+grep_seeded+grep_fallback"
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
        "evidence_in_source":    ev_passed_out,
        "evidence_verified":     ev_passed_out,
        "evidence_match_score":  ev_score_out,
        "evidence_match_method": ev_method_out,
        "estimated_value_cr":          facts.get("_estimated_value_cr"),
        "verdict_origin":              rule.get("verdict_origin"),
        "severity_origin":             rule.get("severity_origin"),
        "status":                     "UNVERIFIED" if is_unverified else "OPEN",
        "requires_human_review":      bool(is_unverified),
        "grep_fallback_audit":         grep_audit,
        "defeated":            False,
    }

    finding = rest_post("kg_nodes", [{
        "doc_id":    DOC_ID,
        "node_type": "ValidationFinding",
        "label":     label,
        "properties": finding_props,
        "source_ref": f"tier1:crn_check:{rule['rule_id']}",
    }])[0]

    edge = None
    if is_gap_violation:
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
                "extraction_path":      "presence_anti_pattern",
                "jv_consortium_explicitly_banned":  jv_banned,
                "jv_ban_has_regulatory_citation":   jv_cited,
                "is_arbitrary_ban":     is_arbitrary_ban,
                "evidence":             evidence_out,
                "qdrant_similarity":    qdrant_similarity,
                "violation_reason":     reason_label,
                "doc_family":           family,
                "evidence_match_score":  ev_score_out,
                "evidence_match_method": ev_method_out,
                "finding_node_id":      finding["node_id"],
            },
        }])[0]

    timings["materialise"] = time.perf_counter() - t0
    print(f"\n  → ValidationFinding {finding['node_id']}  "
          f"(status={'UNVERIFIED' if is_unverified else 'OPEN'})")
    if edge is not None:
        print(f"  → VIOLATES_RULE     {edge['edge_id']}  "
              f"{'Section' if section else 'TenderDocument'}→Rule")
    else:
        print(f"  → no VIOLATES_RULE edge "
              f"(UNVERIFIED finding — awaiting human review)")

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
