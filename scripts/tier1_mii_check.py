"""
scripts/tier1_mii_check.py

Tier-1 MakeInIndia-LCC-Missing check, BGE-M3 + LLM, NO regex.

PRESENCE-shape (absence = violation) typology following the L38
JP-Bypass + L37 four-state contract pattern. Public Procurement
(Preference to Make in India) Order 2017 — issued under GFR Rule
153(iii), latest revision via DPIIT OM No. P-45021/2/2017-PP(BE-II)
dated 16-Sep-2020 — is APPLICABLE to procurement of GOODS, WORKS,
and SERVICES (per MPW-002 + MPS-182 + MPG-022). Every Indian
Government tender for Works above the threshold MUST include:
  • NIT/ITB citation of the Order
  • Class-I (>=50% local content) / Class-II (>=20%) / Non-local
    classification definitions
  • Purchase preference rules (Class-I priority over Class-II
    + Non-local)
  • Bidder Local Content self-certification template (in Forms)
  • Multiple-bidder award rules where applicable

Absence at the bidding-document layer is a HARD_BLOCK violation per
MPW-002 (Works) or MPS-182 (Services / catch-all). No AP-State
variant exists for Works/PPP — AP-GO-137/148/149 are Goods-only and
defeat MPG-020/022 only in that context. So the typology emits a
single-finding shape with NO informational markers (unlike L43
Arbitration / L44 Geographic-Restriction).

Pipeline (post-L44 four-state contract + Method 3 evidence guard):
  1. Pick rule via condition_evaluator. MPW-002 (TenderType=Works,
     HARD_BLOCK) for Works docs; MPS-182 (TenderType=ANY,
     HARD_BLOCK) as catch-all for PPP. Both clean FIRE — no L27
     downgrade.
  2. Section filter via MII_SECTION_ROUTER — all 5 section types
     for every family, since MII content spans NIT (Order citation),
     ITB (definitions), Datasheet (PQ relaxation), Evaluation
     (purchase preference), Forms (LC self-certification).
  3. BGE-M3 embed an answer-shaped query.
  4. Qdrant top-K=10 candidates within the filter.
  5. LLM rerank with 8-field MII extraction.
  6. L24 evidence guard (Method 3 longest-sentence available per L44).
  7. L36 → L40 grep fallback chain on absence path.
  8. Apply three-state decision (per L35).

Predicted corpus result: 6/6 ABSENCE → 6 OPEN HARD_BLOCK findings.
This is the third systemic-absence pattern after JP-Bypass (L38)
and Integrity-Pact (L30).

Tested on vizag_ugss_exp_001 first.
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

DOC_ID = sys.argv[1] if len(sys.argv) > 1 else "vizag_ugss_exp_001"

TYPOLOGY = "MakeInIndia-LCC-Missing"

QDRANT_URL  = os.environ.get("QDRANT_URL", "http://localhost:6333")
COLLECTION  = "tender_sections"

LLM_MODEL = os.environ.get("LLM_MODEL", "qwen/qwen-2.5-72b-instruct")


# L36 source-grep fallback vocabulary. Phrase-precise — bare
# "indigenous" / "purchase preference" / "local content" / "price
# preference" are too broad (Vizag's Scope of Work uses "indigenous
# equipment" in a logistics context, "purchase preference" can refer
# to lowest-cost evaluation rules, "local content" can refer to
# locally-sourced materials in scope, etc.). Only PPP-MII-specific
# phrases here. Vizag-test confirmed the broad keywords produce
# false-positive UNVERIFIED — the systemic absence is genuine.
GREP_FALLBACK_KEYWORDS = [
    "Make in India",
    "Make-in-India",
    "PPP-MII",
    "PPP MII",
    "Public Procurement (Preference to Make in India)",
    "Preference to Make in India",
    "GFR Rule 153",
    "Rule 153(iii)",
    "DPIIT",
    "Class-I local supplier",
    "Class-II local supplier",
    "P-45021",
    "16.09.2020",
    "16-09-2020",
]


# Answer-shaped query — mirrors the literal wording of the canonical
# clause templates (CLAUSE-MAKE-IN-INDIA-PPP2017-001, CLAUSE-MAKE-IN-
# INDIA-001, CLAUSE-MII-PURCHASE-PREFERENCE-001, CLAUSE-MAKE-IN-INDIA-
# VERIF-001) and the rule text.
QUERY_TEXT = (
    "Make in India PPP-MII Order 2017 local content Class-I local "
    "supplier Class-II local supplier purchase preference GFR Rule 153 "
    "DPIIT P-45021 16-09-2020 eligibility criteria self-certification "
    "indigenous procurement preference bidder declaration"
)


# Rule candidates evaluated via condition_evaluator. Priority:
#   1. MPW-002  — TenderType=Works HARD_BLOCK (canonical for Works)
#   2. MPS-182  — TenderType=ANY HARD_BLOCK (catch-all for PPP)
#
# Both clean FIRE — no L27 downgrade. AP-State variants
# (AP-GO-137/148/149) are Goods-only and SKIP on Works/PPP →
# excluded from candidates entirely.
RULE_CANDIDATES = [
    {
        "rule_id":          "MPW-002",
        "natural_language": "Per MPW 2022, Make-in-India (PPP-MII Order 2017, latest revision via DPIIT OM No. P-45021/2/2017-PP(BE-II) dt 16.09.2020) is APPLICABLE to procurement of WORKS (in addition to Goods and Services). Class-I local supplier ≥50%, Class-II ≥20% and <50%, Non-local <20%.",
        "severity":         "HARD_BLOCK",
        "layer":            "Central",
        "shape":            "presence",
    },
    {
        "rule_id":          "MPS-182",
        "natural_language": "PPP-MII Order 2017 (under GFR Rule 153(iii)) is applicable to procurement of GOODS, WORKS and SERVICES. Defines L1, Class-I (>=50% local content), Class-II (>=20% and <50%), Non-local (<20%).",
        "severity":         "HARD_BLOCK",
        "layer":            "Central",
        "shape":            "presence",
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


# ── LLM rerank prompt for MII detection ──────────────────────────────

LLM_SYSTEM = (
    "You are a precise procurement-document fact extractor. "
    "Return JSON only. Quote evidence verbatim from the supplied text. "
    "Never fabricate."
)


# Anchor-keyword discipline (per L39): only patterns that uniquely
# identify Make-in-India / PPP-MII content. The Order's vocabulary
# (Class-I local supplier, DPIIT, GFR 153) is distinctive enough
# that broad matches don't introduce ambiguity.
MII_TRUNCATE_KEYWORDS = [
    r"Make[\s\-]?in[\s\-]?India",
    r"\bMII\b",
    r"PPP[\s\-]?MII",
    r"Public\s+Procurement\s+\(?\s*Preference\s+to\s+Make\s+in\s+India\)?",
    r"Order\s+2017",
    r"GFR\s+Rule\s+153",
    r"Rule\s+153\(iii\)",
    r"\bDPIIT\b",
    r"P-?45021",
    r"16\.09\.2020",
    r"16-09-2020",
    r"Class[\s\-]?I\s+local\s+supplier",
    r"Class[\s\-]?II\s+local\s+supplier",
    r"local\s+content",
    r"purchase\s+preference",
    r"price\s+preference",
    r"\bindigenous\b",
]


def build_mii_rerank_prompt(candidates: list[dict]) -> str:
    blocks = []
    for i, c in enumerate(candidates):
        body = smart_truncate(c["full_text"], window=6000,
                              keywords=MII_TRUNCATE_KEYWORDS)
        blocks.append(
            f"--- CANDIDATE {i} ---\n"
            f"heading: {c['heading']}\n"
            f"section_type: {c.get('section_type') or 'unknown'}\n"
            f"cosine_similarity: {c['similarity']:.4f}\n"
            f"text:\n\"\"\"\n{body}\n\"\"\""
        )
    candidates_block = "\n\n".join(blocks)

    return (
        f"You are reading {len(candidates)} candidate sections from an "
        "Indian / Andhra Pradesh procurement tender document. Exactly "
        "ONE of them (or none) carries the MAKE-IN-INDIA / PPP-MII "
        "Order 2017 framework — the contractual citation of the "
        "Public Procurement (Preference to Make in India) Order 2017 "
        "issued under GFR Rule 153(iii) (latest revision per DPIIT "
        "OM No. P-45021/2/2017-PP(BE-II) dated 16.09.2020).\n"
        "\n"
        "Compliance with the Order requires the doc to carry MULTIPLE "
        "elements distributed across NIT / ITB / Datasheet / "
        "Evaluation / Forms — extract whichever you find:\n"
        "  • PPP-MII Order 2017 / GFR Rule 153(iii) / DPIIT OM "
        "citation (typically NIT)\n"
        "  • Class-I (>=50% local content) / Class-II (>=20%) / "
        "Non-local (<20%) classification definitions (typically ITB)\n"
        "  • Purchase preference rules — Class-I priority over "
        "Class-II + Non-local (typically Evaluation)\n"
        "  • Multiple-bidder award rules / 50:50 split (Evaluation)\n"
        "  • Bidder Local Content self-certification template "
        "(typically Forms)\n"
        "  • PQ relaxation for Make-in-India bidders (Datasheet)\n"
        "\n"
        f"{candidates_block}\n\n"
        "Return JSON only:\n"
        "{\n"
        "  \"chosen_index\":                              integer 0..N-1 of the most relevant MII candidate, OR null if no candidate carries any of the elements above,\n"
        "  \"ppp_mii_order_citation_present\":            bool   (TRUE if doc cites PPP-MII Order 2017 / GFR Rule 153(iii) / DPIIT OM 16.09.2020),\n"
        "  \"mii_classification_defined\":                bool   (TRUE if Class-I/Class-II/Non-local definitions appear with the >=50% / >=20% thresholds),\n"
        "  \"mii_purchase_preference_present\":           bool   (TRUE if Class-I priority over Class-II + Non-local is stated),\n"
        "  \"local_content_self_certification_required\": bool   (TRUE if doc requires bidder self-certification of LC percentage),\n"
        "  \"multiple_bidder_award_rules_present\":       bool   (TRUE if multiple-bidder award rules / 50:50 split appears),\n"
        "  \"pq_relaxation_for_mii_bidders_present\":     bool   (TRUE if PQ relaxation for MII bidders is stated),\n"
        "  \"ap_state_price_preference_present\":         bool   (TRUE if AP-State price preference for indigenous / AP-products / Tungabhadra Steel is mentioned — informational, Goods-only relevance),\n"
        "  \"evidence\":                                  \"verbatim quote (single contiguous span) — the line(s) most relevant to the strongest MII signal you found\",\n"
        "  \"found\":                                     bool,\n"
        "  \"reasoning\":                                 \"one short sentence explaining the choice\"\n"
        "}\n\n"
        "Selection rules — IGNORE the following content (NOT MII):\n"
        "- Generic 'Indian nationality' or land-border-country eligibility clauses (those are Geographic-Restriction territory).\n"
        "- AP Special Class Civil registration with GoAP per GO Ms No 94 (that's Eligibility-Class-Mismatch + Geographic-Restriction territory; NOT MII).\n"
        "- Foreign-bidder exclusion clauses without LC framework (NOT MII).\n"
        "- Generic 'preference' language about evaluation order (e.g. lowest-cost, technical-quality) — only count when it's framed as Make-in-India / Class-I / local-content preference.\n"
        "- Make-in-India brand / programme references in scope-of-work that describe the project's national-importance framing (e.g. 'Smart City under Atmanirbhar Bharat') — those are project-narrative, not procurement-eligibility MII.\n"
        "\n"
        "Selection rules — DO pick if the candidate states:\n"
        "- 'This procurement is governed by the Public Procurement (Preference to Make in India) Order 2017'.\n"
        "- 'Class-I local supplier means a supplier whose item offered has local content >=50%'.\n"
        "- 'Class-I local supplier shall get purchase preference over Class-II and Non-local'.\n"
        "- 'The bidder shall submit a self-certification of percentage of local content per the format in Annexure X' (LC verification).\n"
        "- A reference to GFR Rule 153(iii), DPIIT, or DPIIT OM No. P-45021/2/2017-PP(BE-II).\n"
        "- AP-State price preference clauses (CSP/Tungabhadra) — set ap_state_price_preference_present=true; these are informational and do NOT defeat the Central PPP-MII requirement on Works/PPP.\n"
        "\n"
        "QUOTE FORMAT — STRICT (per L35):\n"
        "- Return a SINGLE CONTIGUOUS quote from ONE sentence, ONE clause, or ONE table row. Do NOT stitch multiple paragraphs.\n"
        "- Do NOT add ellipsis ('...') between lines.\n"
        "- Do NOT paraphrase, summarise, or condense — quote EXACTLY as it appears in the supplied text.\n"
        "- Do NOT introduce or remove markdown formatting.\n"
        "- Pick the SHORTEST contiguous span that proves the strongest MII signal you found. Maximum 2 consecutive sentences. The other booleans speak for the other signals.\n"
        "\n"
        "- If no candidate carries the PPP-MII / Make-in-India framework, set chosen_index=null, found=false, all booleans=false. The L36 grep fallback will then take over.\n"
        "- Evidence MUST be an exact substring of the chosen candidate's text — verifiable byte-for-byte against the source markdown.\n"
        "\n"
        "EXPECTED OUTCOME ON THIS CORPUS: an upfront grep across all 6 doc markdowns returned ZERO hits for the MII vocabulary (Make in India / PPP-MII / Class-I local / DPIIT / etc). The expected result is `found=false` and all booleans=false. This is the third systemic-absence pattern after JP-Bypass and Integrity-Pact. If you find ANY of the elements above, take care to ground your evidence quote — false-positives here would surprise the corpus run."
    )


def parse_llm_response(raw: str) -> dict:
    """Thin wrapper kept for in-script readability — actual parsing
    logic lives in modules.validation.llm_client.parse_llm_json (per L35)."""
    return parse_llm_json(raw)


# ── Rule selection via condition_evaluator ────────────────────────────

def fetch_tender_facts(doc_id: str) -> dict:
    """Build the facts dict that condition_evaluator reads."""
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


def select_mii_rule(tender_facts: dict) -> dict | None:
    """Pick the highest-priority rule that fires. MPW-002 for Works
    docs (Vizag/JA/HC/Kakinada). MPS-182 for non-Works (PPP catch-all).
    Both clean FIRE on their condition_when — no L27 downgrade
    expected on this corpus."""
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

    if not fired:
        print(f"  → no rule fires for these facts (correct silence — typology N/A on this doc)")
        return None
    chosen = fired[0]
    note = ""
    if chosen.get("verdict_origin") == "UNKNOWN":
        note = (f"  [severity downgraded from {chosen.get('severity_origin')} → "
                f"ADVISORY because at least one fact was UNKNOWN]")
    print(f"  → selected {chosen['rule_id']} (severity={chosen['severity']}, "
          f"shape={chosen['shape']}){note}")
    return chosen


# ── Idempotent re-run cleanup ─────────────────────────────────────────

def _delete_prior_tier1_mii(doc_id: str) -> tuple[int, int]:
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
    print(f"  Tier-1 MakeInIndia-LCC-Missing (BGE-M3 + LLM, NO regex)")
    print(f"  doc_id : {DOC_ID}")
    print(f"  model  : {LLM_MODEL}")
    print(f"  shape  : presence (absence = HARD_BLOCK violation)")
    print("=" * 76)

    n_f, n_e = _delete_prior_tier1_mii(DOC_ID)
    if n_f or n_e:
        print(f"  cleared {n_f} prior Tier-1 MII finding node(s) and "
              f"{n_e} edge(s) before re-running")

    # 1. Pick rule via condition_evaluator
    facts = fetch_tender_facts(DOC_ID)
    rule  = select_mii_rule(facts)
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
    print(f"\n── Step 3: LLM rerank + Make-in-India detection ──")
    user_prompt = build_mii_rerank_prompt(candidates)
    print(f"  prompt size: {len(user_prompt)} chars (~{len(user_prompt)//4} tokens)")
    raw_content = call_llm(LLM_SYSTEM, user_prompt, max_tokens=900)
    timings["llm"] = time.perf_counter() - t0
    print(f"  wall: {timings['llm']:.2f}s")
    print(f"\n── Raw LLM JSON ──")
    print(raw_content)

    # 7. Parse + select chosen candidate
    parsed = parse_llm_response(raw_content)
    chosen           = parsed.get("chosen_index")
    order_cited      = bool(parsed.get("ppp_mii_order_citation_present"))
    classifn_defined = bool(parsed.get("mii_classification_defined"))
    purch_pref       = bool(parsed.get("mii_purchase_preference_present"))
    lc_self_cert     = bool(parsed.get("local_content_self_certification_required"))
    multi_bidder     = bool(parsed.get("multiple_bidder_award_rules_present"))
    pq_relaxation    = bool(parsed.get("pq_relaxation_for_mii_bidders_present"))
    ap_price_pref    = bool(parsed.get("ap_state_price_preference_present"))
    evidence         = (parsed.get("evidence") or "").strip()
    found            = bool(parsed.get("found"))
    reason           = (parsed.get("reasoning") or "").strip()

    # Compliance signal: the doc must AT MINIMUM cite the Order AND
    # define the classification — the two anchor signals.
    # Anything less is partial / absent.
    mii_compliant_minimum = order_cited and classifn_defined

    print(f"\n── Parsed ──")
    print(f"  chosen_index                                : {chosen}")
    print(f"  found                                       : {found}")
    print(f"  ppp_mii_order_citation_present              : {order_cited}      ← anchor")
    print(f"  mii_classification_defined                  : {classifn_defined} ← anchor")
    print(f"  mii_purchase_preference_present             : {purch_pref}")
    print(f"  local_content_self_certification_required   : {lc_self_cert}")
    print(f"  multiple_bidder_award_rules_present         : {multi_bidder}")
    print(f"  pq_relaxation_for_mii_bidders_present       : {pq_relaxation}")
    print(f"  ap_state_price_preference_present           : {ap_price_pref}    (informational, Goods-only)")
    print(f"  reasoning                                   : {reason[:200]}")
    print(f"  evidence                                    : {evidence[:300]!r}")

    section = None
    similarity = None
    ev_passed = False
    ev_score = 0
    ev_method = "skipped"

    llm_chose_candidate = (chosen is not None and isinstance(chosen, int)
                           and 0 <= chosen < len(candidates))
    llm_found_clause   = found and mii_compliant_minimum and llm_chose_candidate

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
                print(f"  L24_FAILED — LLM found MII signal but quote unverifiable. "
                      f"Routing to UNVERIFIED finding.")
        else:
            print(f"  ⚠ no evidence quote provided — treating as L24-failed (empty)")
            ev_passed = False; ev_score = 0; ev_method = "empty"
    else:
        print(f"  → no candidate chosen by LLM (correct on this corpus per upfront grep)")

    # 8. Three-state decision (per L35) + L36 grep + L40 whole-file fallback
    is_compliant   = (mii_compliant_minimum and llm_chose_candidate and ev_passed)
    is_unverified  = (mii_compliant_minimum and llm_chose_candidate and not ev_passed)
    raw_is_absence = not (mii_compliant_minimum and llm_chose_candidate)

    grep_hits: list[dict] = []
    full_grep_hits: list[dict] = []
    grep_promoted_to_unverified = False
    full_grep_promoted = False
    kg_coverage_gap = False
    if raw_is_absence or (llm_chose_candidate and not ev_passed):
        if raw_is_absence:
            print(f"\n── L36 source-grep fallback (absence path) ──")
        else:
            print(f"\n── L36 source-grep fallback (L24-fail path) ──")
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
            if raw_is_absence:
                grep_promoted_to_unverified = True
                print(f"  → ABSENCE downgraded to UNVERIFIED — retrieval-coverage gap")
        else:
            # L40 whole-file fallback
            print(f"\n── L40 whole-file grep (Tier-2 fallback) ──")
            any_full, full_grep_hits = grep_full_source_for_keywords(
                DOC_ID, GREP_FALLBACK_KEYWORDS,
            )
            print(f"  whole-file any_hit    : {any_full}  "
                  f"({len(full_grep_hits)} match line(s))")
            for h in full_grep_hits[:3]:
                gap = "GAP" if h["kg_coverage_gap"] else "in-section"
                print(f"    [{gap}] {h['source_file'][:38]:40s} "
                      f"L{h['line_no']:<5d}  matched={h['keyword_matches']}")
            if len(full_grep_hits) > 3:
                print(f"    ... and {len(full_grep_hits) - 3} more")
            if any_full:
                kg_coverage_gap = any(h["kg_coverage_gap"] for h in full_grep_hits)
                if raw_is_absence or kg_coverage_gap:
                    full_grep_promoted = True
                    print(f"  → "
                          f"{'absence' if raw_is_absence else 'L24-fail'} "
                          f"downgraded to UNVERIFIED — "
                          f"{'kg_coverage_gap' if kg_coverage_gap else 'whole-file-only'} hit")

    is_absence    = raw_is_absence and not grep_promoted_to_unverified and not full_grep_promoted
    is_unverified = is_unverified or grep_promoted_to_unverified or full_grep_promoted

    if is_compliant:
        reason_label = "compliant_ppp_mii_order_citation_and_classification_present"
    elif grep_promoted_to_unverified:
        reason_label = "mii_unverified_grep_fallback_retrieval_gap"
    elif full_grep_promoted:
        reason_label = ("mii_unverified_kg_coverage_gap"
                        if kg_coverage_gap
                        else "mii_unverified_whole_file_grep_only")
    elif is_unverified:
        reason_label = "mii_unverified_llm_quote_failed_l24"
    else:
        reason_label = "ppp_mii_order_2017_absent"

    print(f"\n── Decision ──")
    print(f"  rule              : {rule['rule_id']} ({rule['severity']}, shape={rule['shape']})")
    print(f"  llm_found_clause  : {llm_found_clause}")
    print(f"  ev_passed         : {ev_passed}  (score={ev_score}, method={ev_method})")
    print(f"  raw_is_absence    : {raw_is_absence}")
    print(f"  grep_fallback_hit : {grep_promoted_to_unverified or full_grep_promoted}")
    print(f"  is_compliant      : {is_compliant}")
    print(f"  is_unverified     : {is_unverified}")
    print(f"  is_absence        : {is_absence}")
    print(f"  reason_label      : {reason_label}")

    if is_compliant:
        return 0

    # 9. Materialise finding
    t0 = time.perf_counter()
    if section is not None and is_unverified and not (grep_promoted_to_unverified or full_grep_promoted):
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
        ev_passed_out = None
        ev_score_out  = None
        ev_method_out = "absence_finding_no_evidence"
        evidence_out  = (f"PPP-MII Order 2017 / Make-in-India framework not found "
                         f"in document after BGE-M3 retrieval, L36 Section-bounded "
                         f"grep, and L40 whole-file grep on "
                         f"{', '.join(section_types)}. Per MPW-002 / MPS-182, "
                         f"every Indian Government Works/Services tender must "
                         f"include the Order's classification (Class-I >=50% / "
                         f"Class-II >=20% / Non-local) + purchase preference + "
                         f"bidder LC self-certification.")
        print(f"  → ABSENCE finding — LLM rerank empty AND grep fallbacks empty; "
              f"genuine systemic absence")
    elif grep_promoted_to_unverified:
        ev_passed_out = None
        ev_score_out  = None
        ev_method_out = "grep_fallback_retrieval_gap"
        evidence_out  = (f"LLM rerank top-{K} returned no MII candidate, but "
                         f"exhaustive grep across {', '.join(section_types)} "
                         f"found keyword hits in {len(grep_hits)} section(s). "
                         f"First match: "
                         f"{grep_hits[0]['snippet'][:160] if grep_hits else 'n/a'}")
        print(f"  → UNVERIFIED finding (grep fallback) — retrieval missed "
              f"{len(grep_hits)} section(s) with MII keywords")
    elif full_grep_promoted:
        ev_passed_out = None
        ev_score_out  = None
        ev_method_out = ("whole_file_grep_kg_coverage_gap"
                         if kg_coverage_gap else "whole_file_grep_match")
        first = full_grep_hits[0] if full_grep_hits else None
        evidence_out  = (f"LLM rerank, Section-bounded grep BOTH empty but "
                         f"whole-file grep found {len(full_grep_hits)} match "
                         f"line(s) — "
                         f"{'KG-coverage GAP detected' if kg_coverage_gap else 'whole-file only hit'}. "
                         f"First match: {first['source_file']}:L{first['line_no']} "
                         f"{first['snippet'][:120] if first else 'n/a'}")
        print(f"  → UNVERIFIED finding (whole-file fallback)")
    elif is_unverified:
        ev_passed_out = ev_passed
        ev_score_out  = ev_score
        ev_method_out = ev_method
        evidence_out  = evidence
        print(f"  → UNVERIFIED finding — LLM identified MII signal but quote "
              f"failed L24 (score={ev_score}, method={ev_method})")
    else:
        ev_passed_out = ev_passed
        ev_score_out  = ev_score
        ev_method_out = ev_method
        evidence_out  = evidence

    rule_node_id = get_or_create_rule_node(DOC_ID, rule["rule_id"])

    if is_absence:
        label = (
            f"{TYPOLOGY}: PPP-MII Order 2017 / Make-in-India framework absent "
            f"— {rule['rule_id']} ({rule['severity']}) requires this "
            f"{facts.get('tender_type', 'unknown')} tender to carry the Order's "
            f"classification + purchase preference + bidder LC self-certification "
            f"(per MPW-002 / MPS-182 / MPG-022 / DPIIT OM 16.09.2020)"
        )
    elif grep_promoted_to_unverified:
        label = (
            f"{TYPOLOGY}: UNVERIFIED (L36 grep-fallback) — LLM rerank "
            f"missed the MII clause; exhaustive grep found {len(grep_hits)} "
            f"section(s) with MII keyword hits; requires human review"
        )
    elif full_grep_promoted:
        label = (
            f"{TYPOLOGY}: UNVERIFIED (L40 whole-file fallback) — "
            f"{'KG-coverage gap' if kg_coverage_gap else 'whole-file only'}; "
            f"{len(full_grep_hits)} match line(s)"
        )
    elif is_unverified:
        label = (
            f"{TYPOLOGY}: UNVERIFIED — LLM found MII signal but quote "
            f"failed L24 (score={ev_score}, method={ev_method}); requires "
            f"human review against "
            f"{(section['heading'][:60] if section else 'TenderDocument')!r}"
        )
    else:
        label = ""

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
            "hits": (
                [{"section_node_id":  h["section_node_id"],
                  "heading":          h["heading"],
                  "section_type":     h["section_type"],
                  "source_file":      h["source_file"],
                  "line_start_local": h["line_start_local"],
                  "line_end_local":   h["line_end_local"],
                  "keyword_matches":  h["keyword_matches"],
                  "snippet":          h["snippet"][:300]}
                 for h in grep_hits[:10]]
                if grep_promoted_to_unverified else
                [{"source_file":      h["source_file"],
                  "line_no":          h["line_no"],
                  "kg_coverage_gap":  h["kg_coverage_gap"],
                  "covering_section": h["covering_section"],
                  "keyword_matches":  h["keyword_matches"],
                  "snippet":          h["snippet"][:300]}
                 for h in full_grep_hits[:10]]
            ),
        }

    finding_props = {
        "rule_id":               rule["rule_id"],
        "typology_code":         TYPOLOGY,
        "severity":              rule["severity"],
        "evidence":              evidence_out,
        "extraction_path":       "presence",
        "llm_found_clause":      llm_found_clause,
        # Multi-field LLM extraction snapshot
        "ppp_mii_order_citation_present":            order_cited,
        "mii_classification_defined":                classifn_defined,
        "mii_purchase_preference_present":           purch_pref,
        "local_content_self_certification_required": lc_self_cert,
        "multiple_bidder_award_rules_present":       multi_bidder,
        "pq_relaxation_for_mii_bidders_present":     pq_relaxation,
        "ap_state_price_preference_present":         ap_price_pref,
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
        "evidence_in_source":    ev_passed_out,
        "evidence_verified":     ev_passed_out,
        "evidence_match_score":  ev_score_out,
        "evidence_match_method": ev_method_out,
        # Rule-evaluator inputs
        "estimated_value_cr":          facts.get("_estimated_value_cr"),
        # L27 audit
        "verdict_origin":              rule.get("verdict_origin"),
        "severity_origin":             rule.get("severity_origin"),
        # L35 status / human-review markers
        "status":                     "UNVERIFIED" if is_unverified else "OPEN",
        "requires_human_review":      bool(is_unverified),
        "human_review_reason": (
            f"L36 grep fallback fired: LLM rerank top-{K} did not surface MII, "
            f"but exhaustive grep across {section_types} found keyword hits in "
            f"{len(grep_hits)} section(s). Reviewer should open the listed "
            f"sections in grep_audit.hits and confirm the MII framework."
            if grep_promoted_to_unverified else
            f"L40 whole-file fallback: LLM rerank AND Section-bounded grep both "
            f"empty, but whole-file grep found {len(full_grep_hits)} match "
            f"line(s). "
            f"{'kg_coverage_gap=TRUE — reviewer should re-run kg_builder.' if kg_coverage_gap else 'Whole-file only hits — reviewer should verify retrieval coverage.'}"
            if full_grep_promoted else
            f"LLM identified MII signal but quote failed L24 (score={ev_score}, "
            f"method={ev_method}). Reviewer should open the section above and confirm."
            if is_unverified else None
        ),
        # L36/L40 grep-fallback audit
        "grep_fallback_audit":         grep_audit,
        "defeated":            False,
    }

    finding = rest_post("kg_nodes", [{
        "doc_id":    DOC_ID,
        "node_type": "ValidationFinding",
        "label":     label,
        "properties": finding_props,
        "source_ref": f"tier1:mii_check:{rule['rule_id']}",
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
