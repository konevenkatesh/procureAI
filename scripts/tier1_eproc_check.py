"""
scripts/tier1_eproc_check.py

Tier-1 E-Procurement-Bypass check, BGE-M3 + LLM, NO regex.

PRESENCE shape — same lineage as PVC / IP / LD. The doc MUST mandate
electronic submission of bids via an e-procurement portal. Absence
of an e-procurement mandate (i.e. doc permits paper-only / offline-
only submission) is a HARD_BLOCK violation.

Threshold gates:
    AP        : EstimatedValue >= Rs.1 lakh   (AP-GO-012, all procurement)
    Central   : EstimatedValue >  Rs.2 lakh   (MPS-218 universal threshold)
    AP Goods  : EstimatedValue >  Rs.1 lakh   (AP-GO-010, AP-GO-158)

All 6 corpus docs are well above these thresholds, so the threshold
gate is universally satisfied — the question reduces to "does the
doc mandate e-procurement?"

Pipeline (same shape as LD):
  1. Pick rule via condition_evaluator. AP-GO-012 is the canonical
     primary for our corpus (HARD_BLOCK, fires on every AP doc above
     Rs.1 lakh — which is all of them). On Vizag, EV=null →
     AP-GO-012 condition is UNKNOWN → L27 ADVISORY downgrade.
  2. Section filter via EPROC_SECTION_ROUTER —
        APCRDA_Works → [NIT, ITB]
        SBD_Format   → [NIT, ITB, Evaluation]   (Kakinada n_gcc=0)
        NREDCAP_PPP  → [NIT, ITB]
        default      → [NIT, ITB]
  3. BGE-M3 embed an answer-shaped query.
  4. Qdrant top-K=10 candidates within the filter.
  5. LLM rerank with e-procurement-specific ignore rules:
     - Two-cover sealed bid SYSTEM (legacy AP language alongside
       e-procurement — NOT a bypass by itself).
     - Reverse auction parameters (e-procurement variant, but the
       presence-detector wants the BASE mandate, not the auction
       mechanic).
     - GeM cost-breakup forms (about pricing, not platform mandate).
     - PMC MoU framework agreements (different shape).
  6. Hallucination guard (L24): verify evidence is in the chosen
     section's full_text. Discard on score < 85.
  7. Apply rule check:
        e_procurement_present=True  → compliant (no finding)
        e_procurement_present=False → violation (HARD_BLOCK or
                                       ADVISORY per L27 downgrade)
  8. Materialise ValidationFinding + VIOLATES_RULE with L24 + L29
     audit fields (absence findings get
     evidence_match_method='absence_finding_no_evidence').

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

TYPOLOGY = "E-Procurement-Bypass"

QDRANT_URL  = os.environ.get("QDRANT_URL", "http://localhost:6333")
COLLECTION  = "tender_sections"

LLM_MODEL = os.environ.get("LLM_MODEL", "qwen/qwen-2.5-72b-instruct")


# Answer-shaped query — mirrors the literal wording of e-procurement
# mandate clauses (AP-GO-012, GFR-G-042, MPG-061, MPS-218, MPW-048).
QUERY_TEXT = (
    "e-procurement portal electronic submission digital signature "
    "DSC online bid upload apeprocurement.gov.in CPPP GeM-CPPP "
    "NIC GePNIC mandatory online tender"
)


# Rule candidates evaluated via condition_evaluator. Priority order:
# most specific first.
#
# AP-GO-012 is the canonical AP primary — fires on EVERY AP doc with
# EstimatedValue >= 1 lakh, regardless of TenderType. AP-GO-010 has
# the only defeasibility wiring in the typology (defeats=[GFR-G-042,
# MPG-061, MPS-218, MPW-048]) but only fires on AP Goods (none in
# corpus). Central rules (GFR-G-042, MPG-061, MPS-218, MPW-048) fire
# alongside on AP docs — AP-GO-012 wins by candidate-list priority
# even though there's no formal `defeats` wiring between AP-GO-012
# and the Central rules (knowledge-layer gap to flag).
RULE_CANDIDATES = [
    {
        "rule_id":         "AP-GO-012",
        "natural_language": "AP procurement >= 1 lakh — all works AND material must be invited via e-procurement platform",
        "severity":        "HARD_BLOCK",
        "layer":           "AP-State",
        "shape":           "presence",
    },
    {
        "rule_id":         "AP-GO-010",
        "natural_language": "AP Goods >= 1 lakh — must use e-procurement (defeats Central counterparts)",
        "severity":        "HARD_BLOCK",
        "layer":           "AP-State",
        "shape":           "presence",
    },
    {
        "rule_id":         "AP-GO-158",
        "natural_language": "AP materials/stores > 1 lakh — e-procurement only (cross-ref GO 258/2013, AP Financial Code Art 125)",
        "severity":        "HARD_BLOCK",
        "layer":           "AP-State",
        "shape":           "presence",
    },
    {
        "rule_id":         "MPS-218",
        "natural_language": "Central procurement > 2 lakh — e-procurement mandatory (OM 10/3/2012-PPC + GFR 2017)",
        "severity":        "HARD_BLOCK",
        "layer":           "Central",
        "shape":           "presence",
    },
    {
        "rule_id":         "MPW-048",
        "natural_language": "Central Works — all bids must be received through e-procurement portals (GFR 2017 Rule 160)",
        "severity":        "HARD_BLOCK",
        "layer":           "Central",
        "shape":           "presence",
    },
    {
        "rule_id":         "GFR-G-042",
        "natural_language": "All Central Ministries/Departments — bids must be received via NIC's portal or compliant alternative",
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


# ── LLM rerank prompt for E-Procurement mandate detection ───────────

LLM_SYSTEM = (
    "You are a precise procurement-document fact extractor. "
    "Return JSON only. Quote evidence verbatim from the supplied text. "
    "Never fabricate."
)


# Keyword vocabulary for smart_truncate windowing (e-proc-specific).
# E-procurement mandates often appear as short statements buried in
# longer NIT/ITB blocks — centring the window on the literal phrase
# prevents elision (L26).
EPROC_TRUNCATE_KEYWORDS = [
    r"e-procurement",
    r"e procurement",
    r"eprocurement",
    r"\bE-PROCUREMENT\b",
    r"electronic.*submission",
    r"online.*submission",
    r"digital signature",
    r"\bDSC\b",
    r"apeprocurement\.gov\.in",
    r"\bCPPP\b",
    r"\bGeM\b",
    r"GeM-CPPP",
    r"NIC.*portal",
    r"\bGePNIC\b",
    r"upload.*portal",
    r"online.*tender",
    r"electronic.*bid",
]


def build_eproc_rerank_prompt(candidates: list[dict]) -> str:
    blocks = []
    for i, c in enumerate(candidates):
        body = smart_truncate(c["full_text"], window=3000,
                               keywords=EPROC_TRUNCATE_KEYWORDS)
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
        "Exactly ONE of them (or none) contains the EXPLICIT MANDATE that BIDS MUST "
        "BE SUBMITTED ELECTRONICALLY through an e-procurement portal — Government "
        "of India's CPPP / GeM-CPPP / NIC's GePNIC, OR Andhra Pradesh's "
        "apeprocurement.gov.in, OR an equivalent compliant portal. The mandate "
        "should specify online upload, digital signature (DSC) requirements, "
        "and explicitly NOT permit paper-only / offline-only submission.\n\n"
        f"{candidates_block}\n\n"
        "Question: Does the document MANDATE e-procurement? Identify the candidate "
        "that carries the mandate and report the platform.\n\n"
        "Return JSON only:\n"
        "{\n"
        "  \"chosen_index\":              integer 0..N-1 of the e-procurement-mandate candidate, OR null if no candidate explicitly mandates e-procurement,\n"
        "  \"e_procurement_present\":     bool,\n"
        "  \"platform\":                  string OR null  (e.g. 'apeprocurement.gov.in', 'CPPP', 'GeM-CPPP', 'NIC GePNIC', or null if not specified),\n"
        "  \"digital_signature_required\":bool OR null   (true if DSC is required for online submission),\n"
        "  \"offline_alternative_present\":bool OR null   (true ONLY if the doc EXPLICITLY ALSO permits offline/paper submission as a primary alternative; false if doc mandates online; null if not addressed),\n"
        "  \"go_reference\":              string OR null  (e.g. 'GO Ms No 174', 'GFR Rule 160', 'OM 10/3/2012-PPC'),\n"
        "  \"evidence\":                  \"verbatim quote from the chosen candidate's text identifying the e-procurement mandate\",\n"
        "  \"found\":                     bool,\n"
        "  \"reasoning\":                 \"one short sentence explaining the choice\"\n"
        "}\n\n"
        "QUOTE FORMAT — STRICT (per L35):\n"
        "- Return a SINGLE CONTIGUOUS quote from ONE sentence or ONE clause only.\n"
        "- Do NOT stitch multiple paragraphs into one quote.\n"
        "- Do NOT add ellipsis ('...') between lines.\n"
        "- Do NOT paraphrase, summarise, or condense — quote EXACTLY as it appears in the supplied text.\n"
        "- Do NOT introduce or remove markdown formatting (asterisks, underscores, italics) — preserve the source formatting verbatim.\n"
        "- Pick the SHORTEST contiguous span that proves the mandate; one sentence is usually enough.\n"
        "\n"
        "Selection rules — IGNORE the following content (NOT an e-procurement mandate):\n"
        "- TWO-COVER SEALED BID SYSTEM (legacy AP language describing physical "
        "  envelopes labelled Cover-A / Cover-B / Cover-C). On AP docs this "
        "  often appears ALONGSIDE e-procurement language — by itself it is "
        "  NOT a bypass. Only flag a bypass if the doc has two-cover language "
        "  AND has NO e-procurement language at all.\n"
        "- ELECTRONIC REVERSE AUCTION (eRA) parameters — that's a procurement "
        "  METHOD running ON e-procurement, not the platform mandate itself.\n"
        "- GeM COST BREAKUP forms — those are pricing-side specs, not the "
        "  platform mandate.\n"
        "- PMC MoU / Project Management Consultant framework agreements — "
        "  different contractual instrument, different shape.\n"
        "- LAND BORDER COUNTRIES certificate templates — eligibility shape, "
        "  not a platform mandate.\n"
        "- Generic 'tender' / 'bid' references in retention or evaluation "
        "  clauses (those describe HOW bids are evaluated, not WHERE they are "
        "  submitted).\n"
        "- Bank guarantees / EMD / BG submission instructions (those are "
        "  payment-side, not platform-side).\n"
        "\n"
        "Selection rules — DO pick if the candidate states:\n"
        "- 'Bids shall be submitted electronically via [portal name]' or "
        "  equivalent imperative.\n"
        "- 'apeprocurement.gov.in' / 'eprocure.gov.in' / 'GeM-CPPP' / "
        "  'CPPP (Central Public Procurement Portal)' / 'NIC GePNIC' as the "
        "  designated submission portal.\n"
        "- 'Digital Signature Certificate (DSC) is required' for online uploads.\n"
        "- 'Bid Process shall be conducted by way of E-PROCUREMENT' or "
        "  'tenders shall be invited through the e-procurement platform'.\n"
        "- References to GFR Rule 160 / Rule 159, OM 10/3/2012-PPC dt 9 Jan "
        "  2014, or AP GO Ms 174 / GO 258/2013.\n"
        "\n"
        "- If no candidate explicitly mandates e-procurement, set "
        "  chosen_index=null, e_procurement_present=false, found=false. "
        "  This is the BYPASS-VIOLATION outcome.\n"
        "- Evidence MUST be an exact substring of the chosen candidate's text.\n"
        "- offline_alternative_present should be TRUE ONLY when the doc "
        "  explicitly says paper/offline submission is acceptable AS WELL — "
        "  legacy two-cover language by itself does NOT make this true."
    )


def parse_llm_response(raw: str) -> dict:
    """Thin wrapper kept for in-script readability — the actual
    parsing logic now lives in modules.validation.llm_client per
    L35 (lifted so every typology script benefits from the
    JSON-escape sanitiser without copy-paste)."""
    return parse_llm_json(raw)


# ── Rule selection via condition_evaluator ────────────────────────────

def fetch_tender_facts(doc_id: str) -> dict:
    """Build the facts dict that condition_evaluator reads.

    Strict LLM-only sources (per L28). The Central rules' OrgType /
    BidsReceived / OrgIsCentralOrAttached subterms are NOT extracted
    today; they will resolve as UNKNOWN during evaluation when
    referenced. Most fire on the simpler conditions (AP-GO-012:
    `TenderState=AP AND EstimatedValue>=100000`; MPS-218:
    `EstimatedValue>200000`).
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
            facts["EstimatedValue"] = float(ev_cr) * 1e7   # crores → rupees
            facts["_estimated_value_cr"] = float(ev_cr)
        except (TypeError, ValueError):
            pass

    return facts


def select_eproc_rule(tender_facts: dict) -> dict | None:
    """Pick the highest-priority rule that fires (or fires-as-UNKNOWN
    per L27 downgrade). All AP corpus docs are well above the Rs.1
    lakh threshold, so AP-GO-012 is the canonical primary."""
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

    # Defeasibility filter — AP-GO-010 defeats GFR-G-042, MPG-061,
    # MPS-218, MPW-048 when it fires (only on AP Goods). For Works
    # and PPP corpus docs AP-GO-010 doesn't fire so no defeats apply,
    # but the wiring is still respected.
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

def _delete_prior_tier1_eproc(doc_id: str) -> tuple[int, int]:
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
    print(f"  Tier-1 E-Procurement-Bypass (BGE-M3 + LLM, NO regex)")
    print(f"  doc_id : {DOC_ID}")
    print(f"  model  : {LLM_MODEL}")
    print("=" * 76)

    n_f, n_e = _delete_prior_tier1_eproc(DOC_ID)
    if n_f or n_e:
        print(f"  cleared {n_f} prior Tier-1 E-Proc finding node(s) and "
              f"{n_e} edge(s) before re-running")

    # 1. Pick rule via condition_evaluator
    facts = fetch_tender_facts(DOC_ID)
    rule  = select_eproc_rule(facts)
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
    print(f"\n── Step 3: LLM rerank + e-procurement mandate detection ──")
    user_prompt = build_eproc_rerank_prompt(candidates)
    print(f"  prompt size: {len(user_prompt)} chars (~{len(user_prompt)//4} tokens)")
    raw_content = call_llm(LLM_SYSTEM, user_prompt, max_tokens=900)
    timings["llm"] = time.perf_counter() - t0
    print(f"  wall: {timings['llm']:.2f}s")
    print(f"\n── Raw LLM JSON ──")
    print(raw_content)

    # 7. Parse + select chosen candidate
    parsed = parse_llm_response(raw_content)
    chosen        = parsed.get("chosen_index")
    eproc_present = bool(parsed.get("e_procurement_present"))
    platform      = parsed.get("platform")
    dsc_required  = parsed.get("digital_signature_required")
    offline_alt   = parsed.get("offline_alternative_present")
    go_reference  = parsed.get("go_reference")
    evidence      = (parsed.get("evidence") or "").strip()
    found         = bool(parsed.get("found"))
    reason        = (parsed.get("reasoning") or "").strip()

    print(f"\n── Parsed ──")
    print(f"  chosen_index               : {chosen}")
    print(f"  found                      : {found}")
    print(f"  e_procurement_present      : {eproc_present}")
    print(f"  platform                   : {platform!r}")
    print(f"  digital_signature_required : {dsc_required}")
    print(f"  offline_alternative_present: {offline_alt}")
    print(f"  go_reference               : {go_reference!r}")
    print(f"  reasoning                  : {reason[:200]}")
    print(f"  evidence                   : {evidence[:300]!r}")

    section = None
    similarity = None
    ev_passed = False
    ev_score = 0
    ev_method = "skipped"

    # L35 contract: track the LLM's pre-verification verdict separately
    # from the post-verification eproc_present. The LLM's `found` /
    # `e_procurement_present` may report TRUE even when the L24
    # evidence guard fails — that is NOT the same as "clause absent".
    # It's "LLM identified a clause but the quote it returned cannot
    # be verified against the source." That third state needs an
    # UNVERIFIED finding, not an absence finding.
    llm_found_clause   = bool(parsed.get("e_procurement_present")) and (chosen is not None)
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
            if ev_passed:
                # L24 PASS — LLM verdict is verified end-to-end.
                pass
            else:
                # L24 FAIL — LLM said clause is present but the quote
                # it returned doesn't match the picked section. Keep
                # `eproc_present` as the LLM said (True) but also
                # mark `evidence_unverified=True` so the materialise
                # branch below can route to the UNVERIFIED path.
                print(f"  L24_FAILED — LLM found clause but quote is unverifiable. "
                      f"Routing to UNVERIFIED finding (NOT absence).")
                print(f"    LLM evidence  : {evidence[:200]!r}")
        else:
            print(f"  ⚠ no evidence quote provided — treating as L24-failed (empty)")
            ev_passed = False; ev_score = 0; ev_method = "empty"
    else:
        print(f"  → no candidate chosen by LLM")
        if eproc_present:
            print(f"  ⚠ eproc_present=True but chosen_index=null — treating as False")
            eproc_present = False
            llm_found_clause = False

    # 8. Apply rule check — three-way branch (per L35):
    #    (a) llm_found_clause AND ev_passed     → compliant, no finding
    #    (b) llm_found_clause AND NOT ev_passed → UNVERIFIED finding (no edge)
    #    (c) NOT llm_found_clause               → absence finding (with edge)
    is_compliant   = llm_found_clause and ev_passed
    is_unverified  = llm_found_clause and not ev_passed
    is_absence     = not llm_found_clause

    if is_compliant:
        reason_label = "compliant_e_procurement_present"
    elif is_unverified:
        reason_label = "e_procurement_unverified_llm_found_quote_failed_l24"
    else:
        reason_label = "e_procurement_bypass_violation"

    print(f"\n── Decision ──")
    print(f"  rule              : {rule['rule_id']} ({rule['severity']}, shape={rule['shape']})")
    print(f"  llm_found_clause  : {llm_found_clause}")
    print(f"  ev_passed         : {ev_passed}  (score={ev_score}, method={ev_method})")
    print(f"  is_compliant      : {is_compliant}")
    print(f"  is_unverified     : {is_unverified}")
    print(f"  is_absence        : {is_absence}")
    print(f"  reason_label      : {reason_label}")

    if is_compliant:
        # No finding emitted (compliant docs are implicit "no row").
        return 0

    # 9. Materialise finding (UNVERIFIED or ABSENCE).
    # The VIOLATES_RULE edge is emitted ONLY for ABSENCE findings —
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

    # L29: ABSENCE findings skip evidence_guard semantics; keep the
    # search-trace description as evidence.
    if is_absence:
        ev_passed = None
        ev_score  = None
        ev_method = "absence_finding_no_evidence"
        evidence  = (f"E-procurement mandate not found in document "
                     f"after searching {', '.join(section_types)} section types")
        print(f"  → ABSENCE finding — skipping evidence_guard "
              f"(no quote to verify)")
    elif is_unverified:
        # L35 UNVERIFIED — the LLM found the clause and quoted text
        # but L24 partial_ratio < 85. Keep the LLM's evidence quote
        # in the finding so a human reviewer can compare against the
        # picked section's text. ev_score / ev_method already record
        # the L24 outcome.
        print(f"  → UNVERIFIED finding — LLM identified clause but quote "
              f"failed L24 verification (score={ev_score}, method={ev_method})")

    rule_node_id = get_or_create_rule_node(DOC_ID, rule["rule_id"])

    if is_unverified:
        label = (
            f"{TYPOLOGY}: UNVERIFIED — LLM found e-procurement mandate "
            f"but quote failed L24 (score={ev_score}, method={ev_method}); "
            f"requires human review against {(section['heading'][:60] if section else 'TenderDocument')!r}"
        )
    else:
        label = (
            f"{TYPOLOGY}: e-procurement mandate absent — {rule['rule_id']} "
            f"({rule['severity']}) requires bids to be submitted via "
            f"e-procurement portal for this tender"
        )

    finding_props = {
        "rule_id":               rule["rule_id"],
        "typology_code":         TYPOLOGY,
        "severity":              rule["severity"],
        "evidence":              evidence,
        "extraction_path":       "presence",
        "llm_found_clause":      llm_found_clause,
        "e_procurement_present": llm_found_clause,    # mirrors LLM verdict (pre-L24)
        "platform":              platform,
        "digital_signature_required":  dsc_required,
        "offline_alternative_present": offline_alt,
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
        "status":                      "UNVERIFIED" if is_unverified else "OPEN",
        "requires_human_review":       bool(is_unverified),
        "human_review_reason": (
            "LLM found clause but evidence quote failed L24 verification "
            f"(score={ev_score}, method={ev_method}). Reviewer should "
            f"open the section above (line_start={line_start_local}, "
            f"line_end={line_end_local}) and confirm the e-procurement "
            f"mandate is present in the source text."
            if is_unverified else None
        ),
        "defeated":            False,
    }

    finding = rest_post("kg_nodes", [{
        "doc_id":    DOC_ID,
        "node_type": "ValidationFinding",
        "label":     label,
        "properties": finding_props,
        "source_ref": f"tier1:eproc_check:{rule['rule_id']}",
    }])[0]

    # VIOLATES_RULE edge ONLY for ABSENCE findings (per L35 contract).
    # UNVERIFIED findings are NOT violations until a human reviewer
    # confirms — they live as ValidationFinding nodes only, with
    # status=UNVERIFIED and requires_human_review=true.
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
                "e_procurement_present": False,
                "platform":             platform,
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
