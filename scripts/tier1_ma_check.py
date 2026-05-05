"""
scripts/tier1_ma_check.py

Tier-1 Mobilisation-Advance-Excess check, BGE-M3 + LLM, NO regex.

THRESHOLD shape, with "absent = compliant" semantics.

Mobilisation Advance is OPTIONAL in Indian procurement (GFR Rule 172
says advance payments are exceptional). When the document DOES state
an MA percentage, it must not exceed the regulated cap:
    AP Works > 1 cr   →  10%  (AP-GO-014, AP-GO-076 — WARNING)
    AP EPC            →   5%  (AP-GO-224 — HARD_BLOCK)
    Central Works     →  10%  (MPW-130 — HARD_BLOCK, MPW 2022 §6.5.3)
    PPP / DBFOT       →   no rule fires → SKIP at rule layer.

Outcomes (vs PVC/IP/LD where absence = violation):
    no MA clause            → compliant, no finding (advance is optional)
    MA clause, pct ≤ cap    → compliant, no finding
    MA clause, pct > cap    → violation, finding emitted

Pipeline (same shape as LD):
  1. Pick rule via condition_evaluator. Each candidate carries a
     `cap_pct` field (5 or 10) used at threshold-compare time.
     UNKNOWN→ADVISORY downgrade per L27 fires on Vizag because
     EstimatedValue is null and AP-GO-014 has `EstimatedValue>1e7`.
  2. Section filter via MA_SECTION_ROUTER —
        APCRDA_Works → [GCC, SCC]
        SBD_Format   → [GCC, SCC, Evaluation]
        NREDCAP_PPP  → [GCC, SCC]
        default      → [GCC, SCC, Specifications]
  3. BGE-M3 embed an answer-shaped query.
  4. Qdrant top-K=10 candidates.
  5. LLM rerank with MA-specific ignore rules (Plant/Machinery
     advance, Secured advance, Supplier-advance, Notice-to-Proceed)
     and structured MA extraction (presence + pct + interest-bearing
     + BG-backed + recovery method + GO reference).
  6. Hallucination guard (L24): verify evidence is in the chosen
     section's full_text. Discard on score < 85.
  7. Apply threshold check:
        ma_clause_present=False                 → compliant
        ma_clause_present=True, pct=null        → compliant (PCC by-ref)
        ma_clause_present=True, pct ≤ cap       → compliant
        ma_clause_present=True, pct > cap       → violation
  8. Materialise ValidationFinding + VIOLATES_RULE only on violation.
     L29 absence-finding marker is NOT used here (absence has no row
     to mark in this typology).

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
from modules.validation.llm_client       import call_llm


# ── Constants ─────────────────────────────────────────────────────────

DOC_ID = sys.argv[1] if len(sys.argv) > 1 else "judicial_academy_exp_001"

TYPOLOGY = "Mobilisation-Advance-Excess"

QDRANT_URL  = os.environ.get("QDRANT_URL", "http://localhost:6333")
COLLECTION  = "tender_sections"

LLM_MODEL = os.environ.get("LLM_MODEL", "qwen/qwen-2.5-72b-instruct")


# Answer-shaped query — mirrors the literal wording of MPW 2022 §6.5.3
# (Central Works baseline) and AP-GO-094/057 wording.
QUERY_TEXT = (
    "Mobilisation Advance mobilization advance 10% contract value "
    "Bank Guarantee interest bearing recovery instalments "
    "GCC SCC GO 94/2003 GO 57/2024 MPW 6.5.3"
)


# Rule candidates evaluated via condition_evaluator. Priority order:
# most specific first (AP EPC 5% cap → AP Works 10% cap → Central
# Works 10% cap). All carry a `cap_pct` for threshold-compare.
# defeats=[] across the typology — knowledge-layer gap, no
# defeasibility wired (same pattern as IP/LD).
RULE_CANDIDATES = [
    {
        "rule_id":         "AP-GO-224",
        "natural_language": "AP EPC contracts allow Mobilisation Advance not exceeding 5% of contract value",
        "severity":        "HARD_BLOCK",
        "layer":           "AP-State",
        "shape":           "threshold",
        "cap_pct":         5.0,
    },
    {
        "rule_id":         "AP-GO-014",
        "natural_language": "AP Works > 1 cr — Mobilization Advance up to 10% of contract value (GO 94/2003 baseline)",
        "severity":        "WARNING",
        "layer":           "AP-State",
        "shape":           "threshold",
        "cap_pct":         10.0,
    },
    {
        "rule_id":         "AP-GO-076",
        "natural_language": "AP Works > 1 cr — Mobilization Advance up to 10% (forms baseline restored by GO 57/2024)",
        "severity":        "WARNING",
        "layer":           "AP-State",
        "shape":           "threshold",
        "cap_pct":         10.0,
    },
    {
        "rule_id":         "MPW-130",
        "natural_language": "Central Works — Mobilisation Advance at 10% of contract price, interest-bearing, BG-backed (MPW 2022 §6.5.3)",
        "severity":        "HARD_BLOCK",
        "layer":           "Central",
        "shape":           "threshold",
        "cap_pct":         10.0,
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


# ── LLM rerank prompt for Mobilisation Advance ───────────────────────

LLM_SYSTEM = (
    "You are a precise procurement-document fact extractor. "
    "Return JSON only. Quote evidence verbatim from the supplied text. "
    "Never fabricate."
)


# Keyword vocabulary for smart_truncate windowing (MA-specific).
# MA percentage rows are short and easily elided in long GCC/SCC
# blocks — centring the window on the literal phrase prevents
# elision (L26).
MA_TRUNCATE_KEYWORDS = [
    r"mobilisation advance",
    r"mobilization advance",
    r"\bMA\b",
    r"mobilization advance against bank guarantee",
    r"interest-bearing advance",
    r"interest bearing advance",
    r"\bGO 94\b",
    r"\bGO 57\b",
    r"MPW.*6\.5\.3",
    r"up to.*ten percent",
    r"up to.*10\s*%",
    r"up to.*5\s*%",
    r"up to.*five percent",
    r"recoverable from contractor",
    r"interest-bearing",
]


def build_ma_rerank_prompt(candidates: list[dict]) -> str:
    blocks = []
    for i, c in enumerate(candidates):
        body = smart_truncate(c["full_text"], window=3000,
                               keywords=MA_TRUNCATE_KEYWORDS)
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
        "Exactly ONE of them (or none) contains the MOBILISATION ADVANCE clause "
        "— the contract provision that allows the contractor to receive an "
        "INTEREST-BEARING advance (typically up to 10% of contract value) "
        "against an UNCONDITIONAL Bank Guarantee, EXCLUSIVELY for mobilisation "
        "costs (site setup, staff deployment, initial equipment), recoverable "
        "from running bills as work progresses.\n\n"
        f"{candidates_block}\n\n"
        "Question: Which candidate states the Mobilisation Advance clause? "
        "Extract its presence, percentage, interest-bearing flag, and BG-backed flag.\n\n"
        "Return JSON only:\n"
        "{\n"
        "  \"chosen_index\":              integer 0..N-1 of the MA candidate, OR null if no candidate is an MA clause,\n"
        "  \"ma_clause_present\":         bool,\n"
        "  \"mobilisation_advance_pct\":  float OR null  (recovery rate, e.g. 10.0 for 10% of contract value; null if framework invoked but % is in PCC/SCC),\n"
        "  \"interest_bearing\":          bool OR null   (true if interest-bearing per the clause; null if not specified),\n"
        "  \"bg_backed\":                 bool OR null   (true if MA requires unconditional Bank Guarantee),\n"
        "  \"recovery_method\":           string OR null (e.g. 'instalments from running bills', 'two equal instalments', null if not specified),\n"
        "  \"go_reference\":              string OR null (e.g. 'MPW 2022 §6.5.3', 'GO 94/2003', 'GO 57/2024'),\n"
        "  \"evidence\":                  \"verbatim quote from the chosen candidate's text identifying the MA clause\",\n"
        "  \"found\":                     bool,\n"
        "  \"reasoning\":                 \"one short sentence explaining the choice\"\n"
        "}\n\n"
        "Selection rules — IGNORE the following content (NOT a Mobilisation Advance clause):\n"
        "- PLANT / MACHINERY / SHUTTERING ADVANCE (typically 5% additional on "
        "  new equipment hypothecated to Government — MPW-131 territory). "
        "  This is a SEPARATE advance category, NOT Mobilisation Advance.\n"
        "- SECURED ADVANCE / MATERIAL ADVANCE against materials brought to site "
        "  (typically up to 75% of invoice — MPW-132 territory). DIFFERENT shape.\n"
        "- SUPPLIER ADVANCE PAYMENT under GFR Rule 172 (30%/40% limits to "
        "  private firms / PSUs for Goods/Services). DIFFERENT shape — applies "
        "  to Goods supply contracts, not Works Mobilisation Advance.\n"
        "- NOTICE TO PROCEED / mobilisation period / commencement clauses — "
        "  these describe WHEN mobilisation BEGINS, not the advance payment.\n"
        "- INSURANCE during advance dispatch / LC clauses — adjacent topic.\n"
        "- PRE-PAID ELECTRICITY SMART METER advance — sector-specific.\n"
        "- Generic 'advance' references in retention or running-bill clauses "
        "  (those describe RECOVERY adjustment, not the MA clause itself).\n"
        "\n"
        "Selection rules — DO pick if the candidate states:\n"
        "- 'Mobilisation Advance' / 'Mobilization Advance' / 'MA' as a clause heading.\n"
        "- A stated percentage of contract value as INTEREST-BEARING advance "
        "  EXCLUSIVELY for MOBILISATION COSTS (site setup, mobilisation of "
        "  staff/equipment).\n"
        "- Reference to 'unconditional Bank Guarantee' as security for the advance.\n"
        "- Reference to MPW 2022 §6.5.3, GO 94/2003, or GO 57/2024.\n"
        "- A clause that says 'advance payable up to X% against BG, recovered "
        "  from interim bills' or equivalent.\n"
        "- Even when the % is a PCC/SCC placeholder (e.g. '{{ma_pct}}%' or "
        "  'as stated in PCC'), if the framework is EXPLICITLY invoked, set "
        "  ma_clause_present=true and mobilisation_advance_pct=null (capture "
        "  the framework reference in go_reference).\n"
        "\n"
        "- If the candidate has only force-majeure / scope-variation / EOT / "
        "  retention / supplier-advance language without a Works Mobilisation "
        "  Advance specifically, set ma_clause_present=false and chosen_index=null.\n"
        "- Evidence MUST be an exact substring of the chosen candidate's text.\n"
        "- If no candidate is an MA clause, set chosen_index=null, "
        "  ma_clause_present=false, found=false."
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
    """Build the facts dict that condition_evaluator reads.

    Strict LLM-only sources (per L28). Same shape as PVC/IP/LD facts.
    `MobilizationAdvanceProvided` and `MobilisationAdvancePaid` flags
    that some MA rules gate on are NOT extracted at this stage —
    those would require a pre-pass to detect MA-clause presence
    before rule selection runs. We let the rule selector treat those
    subterms as UNKNOWN (per L27 downgrade) when they appear.
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


def select_ma_rule(tender_facts: dict) -> dict | None:
    """Pick the highest-priority rule that fires (or fires-as-UNKNOWN
    per L27 downgrade). Returns None when every rule's condition
    evaluates to SKIP (correct-silence path for PPP/DBFOT)."""
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
        print(f"    [{rid}] cap={cand['cap_pct']}%  condition={cw!r}  verdict={verdict.value}  defeats={defeats}")
        if verdict == Verdict.FIRE:
            fired.append(dict(cand, defeats=defeats, verdict_origin="FIRE"))
        elif verdict == Verdict.UNKNOWN:
            downgraded = dict(cand, defeats=defeats,
                              severity="ADVISORY",
                              severity_origin=cand["severity"],
                              verdict_origin="UNKNOWN")
            fired.append(downgraded)

    # Defeasibility filter (no rule defeats anything in this typology
    # today, but kept for symmetry with PVC/IP/LD).
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
          f"shape={chosen['shape']}, cap={chosen['cap_pct']}%){note}")
    return chosen


# ── Idempotent re-run cleanup ─────────────────────────────────────────

def _delete_prior_tier1_ma(doc_id: str) -> tuple[int, int]:
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
    print(f"  Tier-1 Mobilisation-Advance-Excess (BGE-M3 + LLM, NO regex)")
    print(f"  doc_id : {DOC_ID}")
    print(f"  model  : {LLM_MODEL}")
    print("=" * 76)

    n_f, n_e = _delete_prior_tier1_ma(DOC_ID)
    if n_f or n_e:
        print(f"  cleared {n_f} prior Tier-1 MA finding node(s) and "
              f"{n_e} edge(s) before re-running")

    # 1. Pick rule via condition_evaluator
    facts = fetch_tender_facts(DOC_ID)
    rule  = select_ma_rule(facts)
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
    print(f"\n── Step 3: LLM rerank + MA extraction ──")
    user_prompt = build_ma_rerank_prompt(candidates)
    print(f"  prompt size: {len(user_prompt)} chars (~{len(user_prompt)//4} tokens)")
    raw_content = call_llm(LLM_SYSTEM, user_prompt, max_tokens=900)
    timings["llm"] = time.perf_counter() - t0
    print(f"  wall: {timings['llm']:.2f}s")
    print(f"\n── Raw LLM JSON ──")
    print(raw_content)

    # 7. Parse + select chosen candidate
    parsed = parse_llm_response(raw_content)
    chosen        = parsed.get("chosen_index")
    ma_present    = bool(parsed.get("ma_clause_present"))
    ma_pct        = parsed.get("mobilisation_advance_pct")
    interest_brg  = parsed.get("interest_bearing")
    bg_backed     = parsed.get("bg_backed")
    recovery      = parsed.get("recovery_method")
    go_reference  = parsed.get("go_reference")
    evidence      = (parsed.get("evidence") or "").strip()
    found         = bool(parsed.get("found"))
    reason        = (parsed.get("reasoning") or "").strip()

    print(f"\n── Parsed ──")
    print(f"  chosen_index             : {chosen}")
    print(f"  found                    : {found}")
    print(f"  ma_clause_present        : {ma_present}")
    print(f"  mobilisation_advance_pct : {ma_pct}")
    print(f"  interest_bearing         : {interest_brg}")
    print(f"  bg_backed                : {bg_backed}")
    print(f"  recovery_method          : {recovery!r}")
    print(f"  go_reference             : {go_reference!r}")
    print(f"  reasoning                : {reason[:200]}")
    print(f"  evidence                 : {evidence[:300]!r}")

    section = None
    similarity = None
    ev_passed = False
    ev_score = 0
    ev_method = "skipped"

    if chosen is not None and isinstance(chosen, int) and 0 <= chosen < len(candidates):
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
                print(f"  HALLUCINATION_DETECTED — discarding extraction.")
                print(f"    LLM evidence  : {evidence[:200]!r}")
                section = None
                ma_present = False
                ma_pct = None
        else:
            print(f"  ⚠ no evidence quote provided — treating as not-verified")
            ev_passed = False; ev_score = 0; ev_method = "empty"
            ma_present = False
            ma_pct = None
            section = None
    else:
        print(f"  → no candidate chosen by LLM")
        if ma_present:
            print(f"  ⚠ ma_present=True but chosen_index=null — treating as False")
            ma_present = False
            ma_pct = None

    # 8. Apply threshold check (the new logic, vs LD's presence-only path)
    cap_pct = float(rule["cap_pct"])
    if not ma_present:
        # MA is OPTIONAL in Indian procurement — no clause = compliant.
        is_violation = False
        reason_label = "compliant_no_ma_clause"
    elif ma_pct is None:
        # Framework invoked but % is in PCC/SCC. Audit field captures
        # the by-reference status; treat as compliant (would need
        # PCC/SCC verification to escalate).
        is_violation = False
        reason_label = "compliant_clause_present_no_pct_stated"
    elif float(ma_pct) > cap_pct:
        is_violation = True
        reason_label = f"ma_pct_{ma_pct}_exceeds_cap_{cap_pct}"
    else:
        is_violation = False
        reason_label = f"compliant_ma_pct_{ma_pct}_within_cap_{cap_pct}"

    print(f"\n── Decision ──")
    print(f"  rule           : {rule['rule_id']} ({rule['severity']}, shape={rule['shape']}, cap={cap_pct}%)")
    print(f"  ma_present     : {ma_present}")
    print(f"  ma_pct         : {ma_pct}")
    print(f"  reason_label   : {reason_label}")
    print(f"  is_violation   : {is_violation}")

    if not is_violation:
        return 0

    # 9. Materialise finding + edge
    t0 = time.perf_counter()
    if section is not None:
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

    rule_node_id = get_or_create_rule_node(DOC_ID, rule["rule_id"])

    label = (
        f"{TYPOLOGY}: Mobilisation Advance {ma_pct}% exceeds cap {cap_pct}% — "
        f"{rule['rule_id']} ({rule['severity']}) caps MA at {cap_pct}% "
        f"of contract value for this tender"
    )

    finding_props = {
        "rule_id":               rule["rule_id"],
        "typology_code":         TYPOLOGY,
        "severity":              rule["severity"],
        "evidence":              evidence,
        "extraction_path":       "threshold",
        "ma_clause_present":     ma_present,
        "mobilisation_advance_pct": ma_pct,
        "ma_cap_pct":            cap_pct,
        "interest_bearing":      interest_brg,
        "bg_backed":             bg_backed,
        "recovery_method":       recovery,
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
        "status":              "OPEN",
        "defeated":            False,
    }

    finding = rest_post("kg_nodes", [{
        "doc_id":    DOC_ID,
        "node_type": "ValidationFinding",
        "label":     label,
        "properties": finding_props,
        "source_ref": f"tier1:ma_check:{rule['rule_id']}",
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
            "extraction_path":      "threshold",
            "ma_clause_present":    ma_present,
            "mobilisation_advance_pct": ma_pct,
            "ma_cap_pct":           cap_pct,
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
    print(f"  → ValidationFinding {finding['node_id']}")
    print(f"  → VIOLATES_RULE     {edge['edge_id']}  "
          f"{'Section' if section else 'TenderDocument'}→Rule")

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
