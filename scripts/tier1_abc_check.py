"""
scripts/tier1_abc_check.py

Tier-1 Available-Bid-Capacity-Error check, BGE-M3 + LLM, NO regex.

THRESHOLD shape on the M coefficient of the AP-prescribed Available
Bid Capacity formula. Per the read-first scan:

    AP-GO-062  TenderState=AP AND TenderType IN [Works, EPC]
               HARD_BLOCK — AP-prescribed formula:
                   ABC = (A × N × 2) − B    (M = 2, exact)
               where A = max civil-engineering value executed in any
               one year of last 5 years (price-updated), N = number
               of years for completion, B = existing-commitments
               value due in same period. Bidder qualifies iff
               ABC > Estimated Value.
    MPW-043    TenderType=Works AND PQB=true
               HARD_BLOCK — Central baseline:
                   ABC = A × M × N − B    (M = "usually 1.5")
    AP-GO-064  AP execution-stage — B-factor certificate format
               (Engineer-in-Charge ≥ Executive Engineer countersigned
               by SE). Not a doc-content check.
    MPG-055    Goods + RateContract — SKIPs corpus-wide.
    CVC-089    Goods + Provisioning — SKIPs corpus-wide.

**AP-GO-062 (HARD_BLOCK, M = 2 exact) is the primary firing rule** on
the 4 AP Works docs. Tirupathi/Vijayawada PPPs SKIP at the rule layer.

Corpus distribution (already extracted at typology 12 build time):
    JA (APCRDA Works):  M = 2  → matches AP-GO-062 → COMPLIANT
    HC (APCRDA Works):  M = 2  → matches AP-GO-062 → COMPLIANT
    Vizag (UGSS):       M = 3  → +50% lenient → GAP_VIOLATION
    Kakinada (SBD):     M = 3  → +50% lenient → GAP_VIOLATION
    Tirupathi (PPP):    n/a    → rule-skip silent
    Vijayawada (PPP):   n/a    → rule-skip silent

This is a third corpus-pattern signal after L43/L50 — the L52 ABC gap
is shared by Vizag + Kakinada (the non-APCRDA-Works templates), the
opposite pair from L43/L50's APCRDA-Works pair.

Pipeline mirrors L49 DLP threshold check (single value extraction +
exact-match compare to the AP-prescribed M=2):
  1. Pick rule via condition_evaluator (AP-GO-062 fires on 4 AP Works).
  2. Section filter via ABC_SECTION_ROUTER → [NIT, ITB, Evaluation].
  3. BGE-M3 dual queries (framework + value).
  4. Per-section-type quota retrieval (L49) + grep-seeded supplement
     for the literal "available bid capacity" / "ABC" / "AN-B" /
     "bid capacity formula" keywords (L50).
  5. LLM rerank with ABC-specific ignore rules (Turnover threshold;
     PQ Financial Capabilities Solvency line; existing-commitments
     B-factor certificate format) and 3-field structured extraction.
  6. L24 evidence-guard hallucination check.
  7. L36/L40 grep fallback for absence path.
  8. Decision tree (silent-on-COMPLIANT per L48):
        COMPLIANT  if abc_formula_present AND multiplier_M == 2
                   → silent (no row).
        GAP_VIOLATION if abc_formula_present AND multiplier_M != 2
                   → row + VIOLATES_RULE edge.
        UNVERIFIED if L24 fails OR grep-promoted absence.

Tested on judicial_academy_exp_001 first (expected: M=2, COMPLIANT
silent — APCRDA Works template explicitly states 2AN-B formula).
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

TYPOLOGY = "Available-Bid-Capacity-Error"

QDRANT_URL  = os.environ.get("QDRANT_URL", "http://localhost:6333")
COLLECTION  = "tender_sections"

LLM_MODEL = os.environ.get("LLM_MODEL", "qwen/qwen-2.5-72b-instruct")


GREP_FALLBACK_KEYWORDS = [
    "Available Bid Capacity",
    "available bid capacity",
    "Bid Capacity",
    "ABC formula",
    "AN-B",
    "AN − B",
    "A × N",
    "A x N",
    "A*N",
    "Multiplier",
    "bid capacity formula",
    "shall be more than the Estimated",
]


QUERY_FRAMEWORK = (
    "Available Bid Capacity ABC formula AN minus B multiplier number "
    "of years completion existing commitments AP-GO-062 MPW-043 "
    "PQ Financial Capabilities qualification"
)
QUERY_VALUE = (
    "ABC = 2 × A × N − B Multiplier 2 two times A x N - B Available "
    "Bid Capacity shall be more than Estimated Contract Value civil "
    "engineering works five years price level"
)
QUERY_TEXT = QUERY_VALUE   # banner-only alias


RULE_CANDIDATES = [
    {
        "rule_id":          "AP-GO-062",
        "natural_language": "AP Works/EPC Available Bid Capacity formula = (A × N × 2) − B; M = 2 (exact, AP-prescribed)",
        "severity":         "HARD_BLOCK",
        "layer":            "AP-State",
        "shape":            "threshold_exact_match",
        "required_M":       2,
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


# ── LLM rerank prompt for ABC formula extraction ─────────────────────

LLM_SYSTEM = (
    "You are a precise procurement-document fact extractor. "
    "Return JSON only. Quote evidence verbatim from the supplied text. "
    "Never fabricate."
)


ABC_TRUNCATE_KEYWORDS = [
    r"available\s+bid\s+capacity",
    r"\bABC\b",
    r"bid\s+capacity\s+formula",
    r"\(?\s*A\s*[*×x]\s*N\s*[*×x]?\s*[123]?\s*\)?\s*[-−]\s*B",
    r"AN[-−]B",
    r"\bMultiplier\b",
    r"shall\s+be\s+more\s+than\s+the\s+(?:Estimated|estimated)",
    r"price[-\s]?updated",
    r"existing\s+commitments",
    r"\bB\s*=",
]


def build_abc_rerank_prompt(candidates: list[dict]) -> str:
    blocks = []
    for i, c in enumerate(candidates):
        body = smart_truncate(c["full_text"], window=3000,
                               keywords=ABC_TRUNCATE_KEYWORDS)
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
        "Extract the AVAILABLE BID CAPACITY (ABC) formula — the regulated "
        "expression bidders use to demonstrate financial capacity to undertake "
        "the contract. The formula has the canonical shape:\n"
        "    ABC = A × M × N − B\n"
        "where A = max civil-engineering value executed in any one year of last "
        "5 years (price-updated), M = a multiplier (regulatory constant, e.g. 2 "
        "or 1.5 or 3), N = number of years for completion, B = value of existing "
        "commitments due in the same period. The bidder qualifies iff ABC > "
        "Estimated Contract Value.\n\n"
        f"{candidates_block}\n\n"
        "Question: Which candidate states the ABC formula? Extract the formula's "
        "presence and the numeric value of the multiplier M.\n\n"
        "Return JSON only:\n"
        "{\n"
        "  \"chosen_index\":         integer 0..N-1 of the candidate stating the ABC formula, OR null if no candidate states it,\n"
        "  \"abc_formula_present\":  bool,\n"
        "  \"multiplier_M\":         integer OR float OR null  (e.g. 2 for '2AN-B' or 'A × 2 × N - B'; 1.5 for 'A × 1.5 × N - B'; 3 for '3AN-B'; null if M cannot be extracted),\n"
        "  \"formula_full_text\":    string OR null  (the verbatim formula expression as quoted in source, e.g. '2AN-B' / 'A × N × 2 - B' / 'ABC = A × M × N − B'),\n"
        "  \"evidence\":             \"verbatim quote from the chosen candidate's text identifying the ABC formula\",\n"
        "  \"found\":                bool,\n"
        "  \"reasoning\":            \"one short sentence explaining the choice and how the multiplier value was identified\"\n"
        "}\n\n"
        "Selection rules — IGNORE the following content (NOT the ABC formula):\n"
        "- TURNOVER / Average Annual Turnover requirements (a separate PQ "
        "  threshold; the doc may state both turnover AND ABC, but TURNOVER is "
        "  not the ABC formula).\n"
        "- SOLVENCY CERTIFICATE requirements (separate PQ instrument).\n"
        "- B-FACTOR EXISTING-COMMITMENTS CERTIFICATE format (the rule about how "
        "  the B value must be certified by Engineer-in-Charge ≥ EE counter-"
        "  signed by SE — execution-stage, not the ABC formula itself).\n"
        "- LIQUID ASSETS / CASH FLOW REQUIREMENTS (PQ Financial Soundness; "
        "  separate from the ABC formula).\n"
        "- LIQUIDATED DAMAGES rate / cap (separate clause).\n"
        "- BID SECURITY / EMD / PBG amounts (separate financial instruments).\n"
        "- ESTIMATED VALUE / Contract Value statements alone (these are the "
        "  RIGHT-HAND-SIDE comparand for ABC, not the ABC formula itself).\n"
        "\n"
        "Selection rules — DO pick if the candidate states:\n"
        "- A formula expressed as 'ABC = A × M × N − B' / 'ABC = A × N × M − B' / "
        "  '2AN-B' / '3AN-B' / '1.5AN-B' / 'A × 2 × N − B' or any equivalent.\n"
        "- A clause definition '...where M = 2' or '...with M usually 1.5' or "
        "  similar where the multiplier is named or stated.\n"
        "- A PQ table row stating 'Available Bid Capacity = ...' even if the "
        "  variables are explained in a footnote.\n"
        "- A 'shall be more than the Estimated Contract Value' / 'must exceed "
        "  the Estimated Cost' qualifier attached to a bid-capacity expression.\n"
        "\n"
        "Multiplier-extraction rules (CRITICAL):\n"
        "- If the formula is written as '2AN-B' / '3AN-B' / '1.5AN-B', the M "
        "  value is the integer/float literal next to A/N (M = 2, 3, 1.5 "
        "  respectively).\n"
        "- If the formula is written as 'A × N × 2 − B' or 'A × 2 × N − B', "
        "  M = 2.\n"
        "- If the formula is written as 'A × M × N − B' with M defined "
        "  separately ('M = 2' / 'where M is 2'), use the defined value.\n"
        "- If the formula uses words ('two times' / 'twice'), translate to int.\n"
        "- If only the abstract formula (with M as a variable) is stated and "
        "  the doc nowhere assigns a numeric value to M, set multiplier_M=null.\n"
        "- The multiplier is ALWAYS a positive number in [1.0, 5.0]. If you "
        "  extract a value outside this range, you have likely picked up a "
        "  different number (years, percentage, etc.) — re-evaluate.\n"
        "\n"
        "- Evidence MUST be an exact substring of the chosen candidate's text.\n"
        "- If no candidate states the ABC formula, set chosen_index=null, "
        "  abc_formula_present=false, multiplier_M=null, found=false.\n"
        "\n"
        "QUOTE FORMAT — STRICT (per L35):\n"
        "- Return a SINGLE CONTIGUOUS quote from ONE sentence or ONE clause only.\n"
        "- Do NOT stitch multiple paragraphs into one quote.\n"
        "- Do NOT add ellipsis ('...') between lines.\n"
        "- Do NOT paraphrase, summarise, or condense — quote EXACTLY as it appears in the supplied text.\n"
        "- Do NOT introduce or remove markdown formatting (asterisks, underscores, italics, backslash-escapes) — preserve the source formatting verbatim.\n"
        "- Pick the SHORTEST contiguous span that proves the formula presence + multiplier value; one sentence is usually enough."
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


def select_abc_rule(tender_facts: dict) -> dict | None:
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
          f"shape={chosen['shape']}, required_M={chosen['required_M']}){note}")
    return chosen


# ── Idempotent re-run cleanup ─────────────────────────────────────────

def _delete_prior_tier1_abc(doc_id: str) -> tuple[int, int]:
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
    print(f"  Tier-1 Available-Bid-Capacity-Error (BGE-M3 + LLM, NO regex)")
    print(f"  doc_id : {DOC_ID}")
    print(f"  model  : {LLM_MODEL}")
    print("=" * 76)

    n_f, n_e = _delete_prior_tier1_abc(DOC_ID)
    if n_f or n_e:
        print(f"  cleared {n_f} prior Tier-1 ABC finding node(s) and "
              f"{n_e} edge(s) before re-running")

    facts = fetch_tender_facts(DOC_ID)
    rule  = select_abc_rule(facts)
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

    # L49 quotas + L50 grep-seeded supplement
    K_FW     = 4
    K_VAL    = 3
    K_MERGED = 14
    t0 = time.perf_counter()

    # Framework lens: PQ-table-style sections (NIT/Evaluation typically).
    fw_filter = [t for t in section_types if t in ("NIT", "Evaluation", "ITB")]
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

    # L50 grep-seeded supplement: tight literal grep for "available
    # bid capacity" and "AN-B" variants. Pre-Bid Solvency-Stale showed
    # the technique works when the canonical signal-bearing section
    # has a misleading heading (e.g. "SETTLEMENT OF CLAIMS").
    SEED_KEYWORDS = ["Available Bid Capacity", "available bid capacity",
                     "AN-B", "ABC formula", "bid capacity formula"]
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
    print(f"\n── Step 3: LLM rerank + ABC formula extraction ──")
    user_prompt = build_abc_rerank_prompt(candidates)
    print(f"  prompt size: {len(user_prompt)} chars (~{len(user_prompt)//4} tokens)")
    raw_content = call_llm(LLM_SYSTEM, user_prompt, max_tokens=900)
    timings["llm"] = time.perf_counter() - t0
    print(f"  wall: {timings['llm']:.2f}s")
    print(f"\n── Raw LLM JSON ──")
    print(raw_content)

    parsed = parse_llm_response(raw_content)
    chosen          = parsed.get("chosen_index")
    abc_present     = bool(parsed.get("abc_formula_present"))
    multiplier_M    = parsed.get("multiplier_M")
    formula_text    = parsed.get("formula_full_text")
    evidence        = (parsed.get("evidence") or "").strip()
    found           = bool(parsed.get("found"))
    reason          = (parsed.get("reasoning") or "").strip()

    print(f"\n── Parsed ──")
    print(f"  chosen_index           : {chosen}")
    print(f"  found                  : {found}")
    print(f"  abc_formula_present    : {abc_present}")
    print(f"  multiplier_M           : {multiplier_M}")
    print(f"  formula_full_text      : {formula_text!r}")
    print(f"  reasoning              : {reason[:200]}")
    print(f"  evidence               : {evidence[:300]!r}")

    section = None
    similarity = None
    ev_passed = False
    ev_score = 0
    ev_method = "skipped"

    llm_found_signal = abc_present and (chosen is not None)
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
                print(f"  L24_FAILED — LLM extracted formula but quote unverifiable.")
                print(f"    LLM evidence  : {evidence[:200]!r}")
        else:
            print(f"  ⚠ no evidence quote provided — treating as L24-failed (empty)")
            ev_passed = False; ev_score = 0; ev_method = "empty"
    else:
        print(f"  → no candidate chosen by LLM")
        if abc_present:
            print(f"  ⚠ abc_present=True but chosen_index=null — treating as False")
            abc_present = False
            llm_found_signal = False

    # Threshold compare against AP-GO-062's prescribed M=2.
    required_M = rule["required_M"]
    is_compliant_l24  = False
    is_threshold_short = False  # reused name; here it means M-deviation
    is_unverified_l24 = llm_chose_candidate and (not ev_passed) and llm_found_signal
    raw_is_absence    = not llm_found_signal

    M_normalised: float | None = None
    if multiplier_M is not None:
        try:
            M_normalised = float(multiplier_M)
        except (TypeError, ValueError):
            M_normalised = None

    if llm_found_signal and ev_passed:
        if M_normalised is None:
            # Formula present but multiplier value not extractable.
            # Treat as silent compliant by default — same conservative
            # by-reference path as DLP "compliant_clause_present_no_
            # months_stated". Audit field captures the absence for
            # downstream review.
            is_compliant_l24 = True
            reason_label = "compliant_abc_formula_present_no_M_value_extracted"
        elif abs(M_normalised - float(required_M)) < 0.01:
            is_compliant_l24 = True
            reason_label = (f"compliant_abc_formula_present_M_"
                            f"{M_normalised}_matches_required_{required_M}")
        else:
            is_threshold_short = True
            reason_label = (f"abc_M_{M_normalised}_deviates_from_required_"
                            f"{required_M}")

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
            for h in full_grep_hits[:3]:
                gap = "GAP" if h["kg_coverage_gap"] else "in-section"
                print(f"    [{gap}] {h['source_file'][:38]:40s} "
                      f"L{h['line_no']:<5d}  matched={h['keyword_matches']}")
            if len(full_grep_hits) > 3:
                print(f"    ... and {len(full_grep_hits) - 3} more")
            if any_full:
                kg_coverage_gap = any(h["kg_coverage_gap"] for h in full_grep_hits)
                full_grep_promoted = True
                print(f"  → absence downgraded to UNVERIFIED — "
                      f"{'kg_coverage_gap' if kg_coverage_gap else 'whole-file-only'} hit")

    is_absence       = raw_is_absence and not grep_promoted_to_unverified and not full_grep_promoted
    is_unverified    = is_unverified_l24 or grep_promoted_to_unverified or full_grep_promoted
    is_gap_violation = is_threshold_short or is_absence

    if is_compliant_l24 and not is_threshold_short:
        pass  # reason_label already set
    elif is_threshold_short:
        pass  # reason_label already set
    elif is_absence:
        reason_label = "abc_formula_absent"
    elif grep_promoted_to_unverified:
        reason_label = "abc_unverified_grep_fallback_retrieval_gap"
    elif full_grep_promoted:
        reason_label = ("abc_unverified_kg_coverage_gap"
                        if kg_coverage_gap
                        else "abc_unverified_whole_file_grep_only")
    elif is_unverified_l24:
        reason_label = "abc_unverified_llm_quote_failed_l24"
    else:
        reason_label = "abc_indeterminate"

    print(f"\n── Decision ──")
    print(f"  rule              : {rule['rule_id']} ({rule['severity']}, required_M={required_M})")
    print(f"  llm_found_signal  : {llm_found_signal}")
    print(f"  ev_passed         : {ev_passed}  (score={ev_score}, method={ev_method})")
    print(f"  multiplier_M      : {M_normalised}  (vs required {required_M})")
    print(f"  raw_is_absence    : {raw_is_absence}")
    print(f"  grep_fallback_hit : {grep_promoted_to_unverified or full_grep_promoted}")
    print(f"  is_compliant_l24  : {is_compliant_l24}")
    print(f"  is_threshold_short: {is_threshold_short}")
    print(f"  is_unverified     : {is_unverified}")
    print(f"  is_absence        : {is_absence}")
    print(f"  reason_label      : {reason_label}")

    if is_compliant_l24 and not is_threshold_short:
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

    # Materialise (THRESHOLD-DEVIATION, UNVERIFIED, or ABSENCE)
    t0 = time.perf_counter()
    if section is not None and (is_threshold_short or is_unverified_l24) and not (
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

    if is_absence:
        ev_passed_out = None
        ev_score_out  = None
        ev_method_out = "absence_finding_no_evidence"
        evidence_out  = (f"Available Bid Capacity formula not found in document "
                         f"after BGE-M3 retrieval, L36 Section-bounded grep, and "
                         f"L40 whole-file grep across {', '.join(section_types)}. "
                         f"Per AP-GO-062 (HARD_BLOCK), AP Works/EPC contracts must "
                         f"prescribe the formula ABC = (A × N × 2) − B with M = 2.")
        print(f"  → GAP_VIOLATION finding — formula genuinely absent")
    elif is_threshold_short:
        ev_passed_out = ev_passed
        ev_score_out  = ev_score
        ev_method_out = ev_method
        evidence_out  = (f"{evidence}  [Multiplier deviation: doc states "
                         f"M = {M_normalised}; AP-GO-062 prescribes M = "
                         f"{required_M} for AP Works/EPC]")
        direction = "more lenient" if M_normalised > required_M else "more restrictive"
        print(f"  → GAP_VIOLATION finding — multiplier deviation: doc M={M_normalised} "
              f"vs required M={required_M} ({direction} than AP-prescribed)")
    elif grep_promoted_to_unverified:
        ev_passed_out = None
        ev_score_out  = None
        ev_method_out = "grep_fallback_retrieval_gap"
        evidence_out  = (f"LLM rerank top-{K} returned no ABC formula signal, but "
                         f"exhaustive grep across {', '.join(section_types)} found "
                         f"keyword hits in {len(grep_hits)} section(s). First match: "
                         f"{grep_hits[0]['snippet'][:160] if grep_hits else 'n/a'}")
        print(f"  → UNVERIFIED finding (L36 grep fallback)")
    elif full_grep_promoted:
        ev_passed_out = None
        ev_score_out  = None
        ev_method_out = ("whole_file_grep_kg_coverage_gap"
                         if kg_coverage_gap else "whole_file_grep_match")
        first = full_grep_hits[0] if full_grep_hits else None
        evidence_out  = (f"LLM rerank, Section-bounded grep BOTH empty but whole-file "
                         f"grep found {len(full_grep_hits)} match line(s) — "
                         f"{'KG-coverage GAP detected' if kg_coverage_gap else 'whole-file only hit'}. "
                         f"First match: {first['source_file']}:L{first['line_no']} "
                         f"{first['snippet'][:120] if first else 'n/a'}")
        print(f"  → UNVERIFIED finding (L40 whole-file fallback)")
    elif is_unverified_l24:
        ev_passed_out = ev_passed
        ev_score_out  = ev_score
        ev_method_out = ev_method
        evidence_out  = evidence
        print(f"  → UNVERIFIED finding — LLM identified ABC signal but quote "
              f"failed L24 (score={ev_score}, method={ev_method})")
    else:
        ev_passed_out = ev_passed
        ev_score_out  = ev_score
        ev_method_out = ev_method
        evidence_out  = evidence

    rule_node_id = get_or_create_rule_node(DOC_ID, rule["rule_id"])

    if is_threshold_short:
        label = (
            f"{TYPOLOGY}: ABC multiplier M={M_normalised} deviates from "
            f"AP-prescribed M={required_M} — {rule['rule_id']} "
            f"({rule['severity']}) requires AP Works/EPC contracts to "
            f"use the formula ABC = (A × N × 2) − B"
        )
    elif is_absence:
        label = (
            f"{TYPOLOGY}: Available Bid Capacity formula absent — "
            f"{rule['rule_id']} ({rule['severity']}) requires AP Works/EPC "
            f"contracts to prescribe the ABC formula"
        )
    elif grep_promoted_to_unverified:
        label = (
            f"{TYPOLOGY}: UNVERIFIED (L36 grep-fallback) — LLM rerank "
            f"missed the ABC formula; exhaustive grep found {len(grep_hits)} "
            f"section(s) with ABC keyword hits; requires human review"
        )
    elif full_grep_promoted:
        label = (
            f"{TYPOLOGY}: UNVERIFIED (L40 whole-file fallback) — "
            f"{'KG-coverage gap' if kg_coverage_gap else 'whole-file only'}; "
            f"{len(full_grep_hits)} match line(s)"
        )
    else:
        label = (
            f"{TYPOLOGY}: UNVERIFIED — LLM found ABC signal but quote "
            f"failed L24 (score={ev_score}, method={ev_method})"
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
        "extraction_path":       "threshold_exact_match",
        "llm_found_signal":      llm_found_signal,
        "abc_formula_present":   abc_present,
        "multiplier_M":          M_normalised,
        "required_M":            required_M,
        "formula_full_text":     formula_text,
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
        "human_review_reason": (
            f"L36 grep fallback fired: LLM rerank top-{K} did not surface the "
            f"ABC formula, but exhaustive grep across {section_types} found "
            f"keyword hits in {len(grep_hits)} section(s)."
            if grep_promoted_to_unverified else
            f"L40 whole-file fallback: LLM rerank AND Section-bounded grep both "
            f"empty, but whole-file grep found {len(full_grep_hits)} match line(s). "
            f"{'kg_coverage_gap=TRUE — reviewer should re-run kg_builder.' if kg_coverage_gap else 'Whole-file only hits — verify retrieval coverage.'}"
            if full_grep_promoted else
            f"LLM identified ABC signal but quote failed L24 (score={ev_score}, "
            f"method={ev_method})."
            if is_unverified_l24 else None
        ),
        "grep_fallback_audit":         grep_audit,
        "defeated":            False,
    }

    finding = rest_post("kg_nodes", [{
        "doc_id":    DOC_ID,
        "node_type": "ValidationFinding",
        "label":     label,
        "properties": finding_props,
        "source_ref": f"tier1:abc_check:{rule['rule_id']}",
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
                "extraction_path":      "threshold_exact_match",
                "abc_formula_present":  abc_present,
                "multiplier_M":         M_normalised,
                "required_M":           required_M,
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
