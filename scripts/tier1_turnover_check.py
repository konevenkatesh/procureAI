"""
scripts/tier1_turnover_check.py

Tier-1 Turnover-Threshold-Excess check, BGE-M3 + LLM, NO regex.

THRESHOLD shape with optional-clause semantics. Two PQ shapes coexist
in AP procurement:
  (a) Bid-Capacity formula (3AN-B / 2AN-B) — formulaic; no fixed INR
      figure to test against the cap. COMPLIANT outcome (correct
      formula approach).
  (b) Fixed-INR turnover floor (e.g. "avg turnover ≥ INR 128.75 cr") —
      tested against the ≤ 2× annual contract value cap (CVC-028).

Operative cap: PQ turnover requirement should not exceed 2× of annual
contract value. Above 2× = bidder-pool restriction = excess violation.
multiple_of_annual = pq_turnover_cr / (estimated_value_cr / tenure_years).

Pipeline (post-L37 four-state contract):
  1. Pick rule via condition_evaluator. CVC-028 (Works/Civil/Electrical,
     WARNING) is the canonical primary — its threshold IS the operative
     constraint. MPG-255 (TenderType=ANY, HARD_BLOCK) catches PPP. UNKNOWN
     subterms (WorkType, PrequalificationApplied) trigger L27 ADVISORY
     downgrade — honest path when we can't fully resolve the gate.
  2. Section filter via TURNOVER_SECTION_ROUTER —
        APCRDA_Works → [NIT, ITB]
        SBD_Format   → [NIT, Evaluation]
        NREDCAP_PPP  → [NIT, ITB]
        default      → [NIT, ITB, Evaluation]
  3. BGE-M3 embed an answer-shaped query.
  4. Qdrant top-K=10 candidates within the filter.
  5. LLM rerank with 4-field extraction:
        pq_type ∈ {"fixed_inr", "bid_capacity_formula", "not_found"},
        pq_turnover_cr (float | null),
        tenure_years   (int   | null),  # extracted INLINE per user spec
        formula_multiplier (int | null),
        evidence (verbatim quote),
        found (bool).
  6. Hallucination guard (L24).
  7. L36 source-grep fallback on the not_found path.
  8. Apply outcome logic per user spec:
        bid_capacity_formula           → COMPLIANT (no finding)
        fixed_inr & multiple ≤ 2.0     → COMPLIANT (no finding)
        fixed_inr & multiple > 2.0     → WARNING violation
        not_found  & grep hit          → UNVERIFIED (with grep audit)
        not_found  & no grep           → ABSENCE (rare; would mean no PQ
                                          machinery at all — typology-
                                          irrelevant doc)

Tested on judicial_academy_exp_001 first (expected: bid_capacity_formula,
COMPLIANT, no finding).
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
from modules.validation.grep_fallback    import grep_source_for_keywords


# ── Constants ─────────────────────────────────────────────────────────

DOC_ID = sys.argv[1] if len(sys.argv) > 1 else "judicial_academy_exp_001"

TYPOLOGY = "Turnover-Threshold-Excess"

QDRANT_URL  = os.environ.get("QDRANT_URL", "http://localhost:6333")
COLLECTION  = "tender_sections"

LLM_MODEL = os.environ.get("LLM_MODEL", "qwen/qwen-2.5-72b-instruct")

# Operative cap (CVC-028 / MPW-039): PQ turnover requirement should
# not exceed 2× annual contract value (multiplier ≤ 2). Above this =
# bidder-pool restriction = Turnover-Threshold-Excess violation.
MULTIPLE_OF_ANNUAL_CAP = 2.0

# Default tenure used ONLY as a defensive backstop when LLM doesn't
# supply tenure_years AND the doc is identified as PPP (NREDCAP-style
# 5-year PPA contract is the corpus default). Captured in audit fields
# as `tenure_years_source = "default_ppp_5yr"` so reviewer sees it.
PPP_DEFAULT_TENURE_YEARS = 5


# L36 source-grep fallback vocabulary — phrases per user spec.
# Per L36 these are exhaustively scanned across the FULL section_filter
# coverage (not just LLM top-K).
GREP_FALLBACK_KEYWORDS = [
    "annual turnover",
    "average annual turnover",
    "bid capacity",
    "Statement I",
    "Statement X",
    "financial capacity",
    "net worth",
    "turnover",
]


# Answer-shaped query — mirrors the literal wording of the PQ clause
# templates (CLAUSE-PQ-FINANCIAL-001, CLAUSE-WORKS-PQ-TURNOVER-001,
# CLAUSE-AVAILABLE-BID-CAPACITY-001) and the CVC-028 / MPG-255 / MPW-039
# rule text.
QUERY_TEXT = (
    "Pre-qualification financial capacity average annual turnover "
    "three years bidder Rs crore minimum threshold net worth "
    "Available Bid Capacity formula 2AN B 3AN B Statement X "
    "Annual Financial turnover Statement I"
)


# Rule candidates evaluated via condition_evaluator. Priority order:
#   1. CVC-028  — Civil/Electrical Works, WARNING, the operative ≤2× cap
#   2. MPG-255  — Universal-PQ catch-all (TenderType=ANY), HARD_BLOCK
#   3. MPW-039  — Works PQB multiplier-of-2, HARD_BLOCK
#   4. MPS-204  — Non-Consultancy Services 30% floor, HARD_BLOCK
#
# AP-GO-092 (HARD_BLOCK contractor-class match) is OUT OF SCOPE per
# user decision — different shape (registration class vs ECV-band match)
# better treated as Tier-2 typology candidate.
RULE_CANDIDATES = [
    {
        "rule_id":         "CVC-028",
        "natural_language": "Civil/Electrical Works PQ avg 3-yr turnover ≥ 30% of estimated cost AND ≤ 2× annual contract value (CVC norm); above 2× = restrictive",
        "severity":        "WARNING",
        "layer":           "CVC",
        "shape":           "threshold",
    },
    {
        "rule_id":         "MPG-255",
        "natural_language": "PQ Criterion 3 (Financial Standing) — avg 3-yr turnover ≥ BDS-stated INR threshold; net worth shall NOT be negative",
        "severity":        "HARD_BLOCK",
        "layer":           "Central",
        "shape":           "threshold",
    },
    {
        "rule_id":         "MPW-039",
        "natural_language": "Works PQ Criterion 1 — minimum annual construction value calibrated to multiplier-of-2 of annual projected expenditure; above multiplier 2 = artificially high turnover threshold",
        "severity":        "HARD_BLOCK",
        "layer":           "Central",
        "shape":           "threshold",
    },
    {
        "rule_id":         "MPS-204",
        "natural_language": "Non-Consultancy Services Financial Capability — avg 3-yr turnover ≥ 30% of estimated cost; liquid assets ≥ BDS amount",
        "severity":        "HARD_BLOCK",
        "layer":           "Central",
        "shape":           "threshold",
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


# ── LLM rerank prompt for PQ turnover detection ──────────────────────

LLM_SYSTEM = (
    "You are a precise procurement-document fact extractor. "
    "Return JSON only. Quote evidence verbatim from the supplied text. "
    "Never fabricate."
)


# Keyword vocabulary for smart_truncate windowing — tightened to ONLY
# the patterns that uniquely anchor PQ-financial content. Earlier
# revisions included `Statement\s*[IX]\b`, `\bnet\s*worth\b` and
# "last X years" — those matched early rows of the Eligibility &
# Qualification Criteria table in HC's Section III (line 477–582,
# 13K chars), which pulled smart_truncate's anchor up to char ~1000
# while the actual bid-capacity formula sat at char ~8700. The LLM
# never saw the formula and reported not_found.
#
# The patterns kept here are formula-specific or threshold-figure
# specific. The threshold-figure keywords ("INR Nnn crore", "Rs Nn",
# "Nnn.nn crore") will only match the late paragraph that states
# the actual avg-turnover floor — exactly where we want the window
# anchored.
TURNOVER_TRUNCATE_KEYWORDS = [
    r"available\s+bid\s+capacity",
    r"assessed\s+available\s+bid\s+capacity",
    r"\(\s*A\s*\*\s*N\s*\*\s*2\s*[-–]\s*B\s*\)",
    r"\(\s*3\s*\*?\s*A\s*\*?\s*N\s*[-–]\s*B\s*\)",
    r"\(\s*3\s*A\s*N\s*[-–]\s*B\s*\)",
    r"\bA\s*\*\s*N\s*\*\s*2\s*[-–]\s*B\b",
    r"average\s+annual\s+turnover",
    r"average\s+Turnover\s+of\s+at\s+least",
    r"\d+\.\d+\s*crore",
    r"INR\s*\d+\.?\d*\s*crore",
    r"Rs\.?\s*\d+\.?\d*\s*crore",
    r"Annual\s+Financial\s+turnover",
]


def build_turnover_rerank_prompt(candidates: list[dict]) -> str:
    # PQ Eligibility & Qualification Criteria sections in SBD-style
    # Works tenders (HC, JA) are LARGE markdown tables — 13–15K chars
    # per section. The bid-capacity formula often sits 60–80% through
    # the section, past smart_truncate's head/tail anchors at the
    # default 3000-char window. Bumping to 6000 keeps formula + table
    # head visible without blowing the prompt budget (10 candidates ×
    # 6000 chars ≈ 60K chars ≈ 15K tokens — well under qwen-2.5-72b).
    blocks = []
    for i, c in enumerate(candidates):
        body = smart_truncate(c["full_text"], window=6000,
                              keywords=TURNOVER_TRUNCATE_KEYWORDS)
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
        "Andhra Pradesh procurement tender document. Exactly ONE of "
        "them (or none) carries the PRE-QUALIFICATION FINANCIAL "
        "CAPACITY criterion — the contractual statement of how a "
        "bidder's financial standing is tested.\n"
        "\n"
        "Two PQ-financial shapes are valid in this corpus:\n"
        "  (A) FIXED INR TURNOVER — an explicit minimum average "
        "annual turnover stated in rupees (e.g. 'Bidder shall have "
        "an average Turnover of at least INR 128.75 crore in the 3 "
        "Accounting Years preceding the Bid Due Date'). Sometimes "
        "paired with a Net Worth floor.\n"
        "  (B) BID-CAPACITY FORMULA — a formulaic capacity test using "
        "past turnover as input (e.g. 'Available Bid Capacity = "
        "(2*A*N - B)' or '(3AN - B)' where A is max one-year "
        "executed value, N is contract tenure in years, B is current "
        "commitments). NO single fixed INR threshold; the formula "
        "approach is the test.\n"
        "\n"
        f"{candidates_block}\n\n"
        "Return JSON only:\n"
        "{\n"
        "  \"chosen_index\":         integer 0..N-1 of the PQ-financial candidate, OR null if no candidate carries a PQ financial criterion,\n"
        "  \"pq_type\":              \"fixed_inr\" | \"bid_capacity_formula\" | \"not_found\",\n"
        "  \"pq_turnover_cr\":       float (in crore) OR null  — only when pq_type='fixed_inr'; the stated minimum average annual turnover figure,\n"
        "  \"tenure_years\":         integer OR null  — the project tenure / contract period in years (extract from the SAME doc; e.g. '5 (five) years' → 5; for fixed_inr docs the tenure usually appears in the project-overview / Datasheet table; for formula docs the tenure is the N variable),\n"
        "  \"formula_multiplier\":   integer OR null  — only when pq_type='bid_capacity_formula'; the multiplier coefficient (2 or 3) in the formula (A*N*M-B),\n"
        "  \"evidence\":             \"verbatim quote (single contiguous span) — the line(s) that state the PQ financial criterion and (for fixed_inr) the rupee figure\",\n"
        "  \"found\":                bool,\n"
        "  \"reasoning\":            \"one short sentence explaining the choice; if no PQ-financial criterion is in any candidate, say so explicitly\"\n"
        "}\n\n"
        "Selection rules — IGNORE the following content (NOT a PQ financial criterion):\n"
        "- Similar-works experience requirements (e.g. 'completed similar works of value ≥ X% of estimated value') — these are EXPERIENCE criteria, not turnover.\n"
        "- Plant and equipment requirements (cranes, excavators, key items of machinery).\n"
        "- Personnel / key-staff requirements (Project Manager qualifications, engineer counts).\n"
        "- Contractor REGISTRATION CLASS requirements (Special Class, Class-I, Class-II) — that's a separate registration-vs-ECV-class typology.\n"
        "- EMD / Earnest Money Deposit amounts.\n"
        "- Performance Security percentages.\n"
        "- Bid validity periods.\n"
        "- General eligibility conditions (GST registration, PAN, blacklist self-declaration).\n"
        "- Litigation history bars.\n"
        "\n"
        "Selection rules — DO pick if the candidate states:\n"
        "- An explicit minimum average annual turnover figure in INR / Rs / crore / lakh (this is FIXED_INR).\n"
        "- A bid-capacity formula like (A*N*2-B) or (3AN-B) with definitions of A, N, B (this is BID_CAPACITY_FORMULA).\n"
        "- A Statement-I 'Annual Financial turnover' submission requirement that REFERENCES a threshold figure (FIXED_INR) — but if Statement-I is purely a blank submission template with no threshold figure stated, treat it as informational and prefer a candidate that has the actual threshold or formula.\n"
        "\n"
        "QUOTE FORMAT — STRICT (per L35):\n"
        "- Return a SINGLE CONTIGUOUS quote from ONE sentence, ONE clause, or ONE table row. Do NOT stitch multiple paragraphs.\n"
        "- Do NOT add ellipsis ('...') between lines.\n"
        "- Do NOT paraphrase, summarise, or condense — quote EXACTLY as it appears in the supplied text.\n"
        "- Do NOT introduce or remove markdown formatting (asterisks, underscores, italics, table-cell pipes) — preserve the source formatting verbatim.\n"
        "- Pick the SHORTEST contiguous span that proves the criterion; one sentence is usually enough.\n"
        "\n"
        "- If no candidate carries a PQ-financial criterion, set chosen_index=null, pq_type=\"not_found\", found=false. The L36 grep fallback will then take over.\n"
        "- Evidence MUST be an exact substring of the chosen candidate's text."
    )


def parse_llm_response(raw: str) -> dict:
    """Thin wrapper kept for in-script readability — actual parsing
    logic lives in modules.validation.llm_client.parse_llm_json (per L35)."""
    return parse_llm_json(raw)


# ── Rule selection via condition_evaluator ────────────────────────────

def fetch_tender_facts(doc_id: str) -> dict:
    """Build the facts dict that condition_evaluator reads.
    Strict LLM-only sources (per L28). WorkType / PQTurnoverCriterion /
    PQB / PrequalificationApplied / ServiceCategory are NOT extracted —
    those subterms will resolve as UNKNOWN and trigger L27 ADVISORY
    downgrade where applicable."""
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


def select_turnover_rule(tender_facts: dict) -> dict | None:
    """Pick the highest-priority rule that fires (or fires-as-UNKNOWN
    per L27 downgrade). CVC-028 first because its threshold IS the
    operative ≤2× cap; MPG-255 catches universal-PQ on PPP."""
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

def _delete_prior_tier1_turnover(doc_id: str) -> tuple[int, int]:
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
    print(f"  Tier-1 Turnover-Threshold-Excess (BGE-M3 + LLM, NO regex)")
    print(f"  doc_id : {DOC_ID}")
    print(f"  model  : {LLM_MODEL}")
    print(f"  cap    : multiple_of_annual ≤ {MULTIPLE_OF_ANNUAL_CAP:.1f}×")
    print("=" * 76)

    n_f, n_e = _delete_prior_tier1_turnover(DOC_ID)
    if n_f or n_e:
        print(f"  cleared {n_f} prior Tier-1 Turnover finding node(s) and "
              f"{n_e} edge(s) before re-running")

    # 1. Pick rule via condition_evaluator
    facts = fetch_tender_facts(DOC_ID)
    rule  = select_turnover_rule(facts)
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
    print(f"\n── Step 3: LLM rerank + PQ-turnover detection ──")
    user_prompt = build_turnover_rerank_prompt(candidates)
    print(f"  prompt size: {len(user_prompt)} chars (~{len(user_prompt)//4} tokens)")
    raw_content = call_llm(LLM_SYSTEM, user_prompt, max_tokens=900)
    timings["llm"] = time.perf_counter() - t0
    print(f"  wall: {timings['llm']:.2f}s")
    print(f"\n── Raw LLM JSON ──")
    print(raw_content)

    # 7. Parse + select chosen candidate
    parsed = parse_llm_response(raw_content)
    chosen           = parsed.get("chosen_index")
    pq_type          = (parsed.get("pq_type") or "not_found").strip().lower()
    pq_turnover_cr   = parsed.get("pq_turnover_cr")
    tenure_years     = parsed.get("tenure_years")
    formula_mult     = parsed.get("formula_multiplier")
    evidence         = (parsed.get("evidence") or "").strip()
    found            = bool(parsed.get("found"))
    reason           = (parsed.get("reasoning") or "").strip()

    # Coerce types
    try:
        pq_turnover_cr = float(pq_turnover_cr) if pq_turnover_cr is not None else None
    except (TypeError, ValueError):
        pq_turnover_cr = None
    try:
        tenure_years = int(tenure_years) if tenure_years is not None else None
    except (TypeError, ValueError):
        tenure_years = None
    try:
        formula_mult = int(formula_mult) if formula_mult is not None else None
    except (TypeError, ValueError):
        formula_mult = None

    print(f"\n── Parsed ──")
    print(f"  chosen_index           : {chosen}")
    print(f"  found                  : {found}")
    print(f"  pq_type                : {pq_type!r}")
    print(f"  pq_turnover_cr         : {pq_turnover_cr}")
    print(f"  tenure_years           : {tenure_years}")
    print(f"  formula_multiplier     : {formula_mult}")
    print(f"  reasoning              : {reason[:200]}")
    print(f"  evidence               : {evidence[:300]!r}")

    section = None
    similarity = None
    ev_passed = False
    ev_score = 0
    ev_method = "skipped"

    llm_chose_candidate = (chosen is not None and isinstance(chosen, int)
                           and 0 <= chosen < len(candidates))
    llm_found_clause   = found and pq_type in ("fixed_inr", "bid_capacity_formula") and llm_chose_candidate

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
                print(f"  L24_FAILED — LLM found PQ criterion but quote is unverifiable. "
                      f"Routing to UNVERIFIED finding.")
                print(f"    LLM evidence  : {evidence[:200]!r}")
        else:
            print(f"  ⚠ no evidence quote provided — treating as L24-failed (empty)")
            ev_passed = False; ev_score = 0; ev_method = "empty"
    else:
        print(f"  → no candidate chosen by LLM (pq_type={pq_type!r})")
        if pq_type in ("fixed_inr", "bid_capacity_formula") and not llm_chose_candidate:
            print(f"  ⚠ pq_type={pq_type!r} but chosen_index=null — treating as not_found")
            pq_type = "not_found"
            llm_found_clause = False

    # 8. Threshold compute (only meaningful for fixed_inr) ────────────
    ecv_cr = facts.get("_estimated_value_cr")
    annual_contract_value_cr: float | None = None
    multiple_of_annual: float | None = None
    tenure_source: str | None = None

    # If LLM didn't extract tenure, fall back to PPP default for PPP docs.
    if tenure_years is None and pq_type == "fixed_inr":
        if facts.get("tender_type") == "PPP":
            tenure_years = PPP_DEFAULT_TENURE_YEARS
            tenure_source = "default_ppp_5yr"
            print(f"  ⚠ tenure_years missing — falling back to PPP default "
                  f"({PPP_DEFAULT_TENURE_YEARS}yr) for tender_type=PPP")
        else:
            tenure_source = "missing_no_compute"
    elif tenure_years is not None:
        tenure_source = "llm_extracted"

    if (pq_type == "fixed_inr" and pq_turnover_cr is not None
            and ecv_cr is not None and tenure_years and tenure_years > 0):
        annual_contract_value_cr = ecv_cr / tenure_years
        if annual_contract_value_cr > 0:
            multiple_of_annual = pq_turnover_cr / annual_contract_value_cr
        print(f"\n── Threshold compute ──")
        print(f"  ECV                       : {ecv_cr:.2f} cr")
        print(f"  tenure_years              : {tenure_years} ({tenure_source})")
        print(f"  annual_contract_value     : {annual_contract_value_cr:.2f} cr")
        print(f"  pq_turnover_required      : {pq_turnover_cr:.2f} cr")
        print(f"  multiple_of_annual        : {multiple_of_annual:.3f}× "
              f"(cap = {MULTIPLE_OF_ANNUAL_CAP:.1f}×)")
    elif pq_type == "fixed_inr":
        print(f"\n── Threshold compute SKIPPED ──")
        print(f"  ECV                  : {ecv_cr}")
        print(f"  tenure_years         : {tenure_years}")
        print(f"  pq_turnover_cr       : {pq_turnover_cr}")
        print(f"  → cannot compute multiple_of_annual; routing to UNVERIFIED")

    # 9. Outcome decision per user spec + L36 grep fallback on not_found
    is_compliant_formula        = (pq_type == "bid_capacity_formula" and ev_passed)
    is_compliant_within_cap     = (pq_type == "fixed_inr" and ev_passed
                                   and multiple_of_annual is not None
                                   and multiple_of_annual <= MULTIPLE_OF_ANNUAL_CAP)
    is_violation_excess         = (pq_type == "fixed_inr" and ev_passed
                                   and multiple_of_annual is not None
                                   and multiple_of_annual > MULTIPLE_OF_ANNUAL_CAP)
    # UNVERIFIED branches:
    is_unverified_l24_fail      = (pq_type in ("fixed_inr", "bid_capacity_formula")
                                   and llm_chose_candidate and not ev_passed)
    is_unverified_no_compute    = (pq_type == "fixed_inr" and ev_passed
                                   and multiple_of_annual is None)

    raw_is_absence = (pq_type == "not_found") or (not llm_chose_candidate
                                                   and pq_type not in ("fixed_inr",
                                                                        "bid_capacity_formula"))
    grep_hits: list[dict] = []
    grep_promoted_to_unverified = False
    if raw_is_absence:
        print(f"\n── L36 source-grep fallback (not_found path) ──")
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
            print(f"  → not_found downgraded to UNVERIFIED — retrieval-coverage gap")

    is_absence    = raw_is_absence and not grep_promoted_to_unverified
    is_unverified = (is_unverified_l24_fail or is_unverified_no_compute
                     or grep_promoted_to_unverified)
    is_compliant  = is_compliant_formula or is_compliant_within_cap

    # Pick reason label
    if is_compliant_formula:
        reason_label = (f"compliant_bid_capacity_formula"
                        f"_multiplier_{formula_mult or '?'}")
    elif is_compliant_within_cap:
        reason_label = (f"compliant_pq_turnover_{pq_turnover_cr:.2f}cr_within_"
                        f"{MULTIPLE_OF_ANNUAL_CAP:.1f}x_annual"
                        f"_multiple_{multiple_of_annual:.3f}")
    elif is_violation_excess:
        reason_label = (f"turnover_threshold_excess_pq_{pq_turnover_cr:.2f}cr_"
                        f"vs_annual_{annual_contract_value_cr:.2f}cr_"
                        f"multiple_{multiple_of_annual:.3f}x_above_cap_"
                        f"{MULTIPLE_OF_ANNUAL_CAP:.1f}x")
    elif grep_promoted_to_unverified:
        reason_label = "turnover_unverified_grep_fallback_retrieval_gap"
    elif is_unverified_l24_fail:
        reason_label = "turnover_unverified_llm_quote_failed_l24"
    elif is_unverified_no_compute:
        reason_label = (f"turnover_unverified_cannot_compute_multiple_"
                        f"missing_ecv_or_tenure")
    else:
        reason_label = "turnover_pq_criterion_absent"

    print(f"\n── Decision ──")
    print(f"  rule                       : {rule['rule_id']} "
          f"({rule['severity']}, shape={rule['shape']})")
    print(f"  pq_type                    : {pq_type}")
    print(f"  llm_found_clause           : {llm_found_clause}")
    print(f"  ev_passed                  : {ev_passed}  (score={ev_score}, method={ev_method})")
    print(f"  multiple_of_annual         : {multiple_of_annual}")
    print(f"  is_compliant_formula       : {is_compliant_formula}")
    print(f"  is_compliant_within_cap    : {is_compliant_within_cap}")
    print(f"  is_violation_excess        : {is_violation_excess}")
    print(f"  is_unverified              : {is_unverified}")
    print(f"  is_absence                 : {is_absence}")
    print(f"  reason_label               : {reason_label}")

    # COMPLIANT branches return early — no finding emitted (per
    # threshold-shape with optional-clause semantics like Mobilisation
    # Advance: absence/within-cap = correct, no row).
    if is_compliant:
        return 0

    # 10. Materialise finding (violation, UNVERIFIED, or ABSENCE)
    t0 = time.perf_counter()
    if section is not None and (is_violation_excess or is_unverified_l24_fail
                                or is_unverified_no_compute):
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
        evidence  = (f"PQ financial criterion not found in document after "
                     f"searching {', '.join(section_types)} section types "
                     f"(also exhaustive grep across all matching sections — "
                     f"no keyword hits)")
        print(f"  → ABSENCE finding — LLM rerank empty AND grep fallback empty")
    elif grep_promoted_to_unverified:
        ev_passed = None
        ev_score  = None
        ev_method = "grep_fallback_retrieval_gap"
        evidence  = (f"LLM rerank top-{K} returned no PQ-financial candidate, "
                     f"but exhaustive grep across {', '.join(section_types)} "
                     f"found keyword hits in {len(grep_hits)} section(s) — "
                     f"likely retrieval coverage gap. First match: "
                     f"{grep_hits[0]['snippet'][:160] if grep_hits else 'n/a'}")
        print(f"  → UNVERIFIED finding (grep fallback) — retrieval missed "
              f"{len(grep_hits)} section(s) carrying turnover keywords")
    elif is_unverified_l24_fail:
        print(f"  → UNVERIFIED finding — LLM identified PQ criterion but "
              f"quote failed L24 verification (score={ev_score}, "
              f"method={ev_method})")
    elif is_unverified_no_compute:
        print(f"  → UNVERIFIED finding — LLM extracted fixed_inr threshold "
              f"but missing ECV or tenure to compute multiple_of_annual")

    rule_node_id = get_or_create_rule_node(DOC_ID, rule["rule_id"])

    if is_violation_excess:
        label = (
            f"{TYPOLOGY}: PQ turnover ₹{pq_turnover_cr:.2f}cr requires "
            f"{multiple_of_annual:.2f}× annual contract value "
            f"(₹{annual_contract_value_cr:.2f}cr/yr) — exceeds "
            f"{MULTIPLE_OF_ANNUAL_CAP:.1f}× cap per {rule['rule_id']} "
            f"({rule['severity']})"
        )
    elif grep_promoted_to_unverified:
        label = (
            f"{TYPOLOGY}: UNVERIFIED (L36 grep-fallback) — LLM rerank "
            f"missed the PQ criterion; exhaustive grep found "
            f"{len(grep_hits)} section(s) with turnover-keyword hits; "
            f"requires human review"
        )
    elif is_unverified_l24_fail:
        label = (
            f"{TYPOLOGY}: UNVERIFIED — LLM found PQ {pq_type} criterion "
            f"but quote failed L24 (score={ev_score}, method={ev_method}); "
            f"requires human review against "
            f"{(section['heading'][:60] if section else 'TenderDocument')!r}"
        )
    elif is_unverified_no_compute:
        label = (
            f"{TYPOLOGY}: UNVERIFIED — LLM extracted PQ turnover "
            f"₹{pq_turnover_cr}cr but cannot compute multiple_of_annual "
            f"(ECV={ecv_cr}, tenure_years={tenure_years}); requires human review"
        )
    else:
        label = (
            f"{TYPOLOGY}: PQ financial criterion absent — {rule['rule_id']} "
            f"({rule['severity']}) requires turnover floor or bid-capacity "
            f"formula in this {facts.get('tender_type', 'unknown')} tender"
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
        "rule_id":                  rule["rule_id"],
        "typology_code":            TYPOLOGY,
        "severity":                 rule["severity"],
        "evidence":                 evidence,
        "extraction_path":          "threshold",
        "llm_found_clause":         llm_found_clause,
        "pq_type":                  pq_type,
        "pq_turnover_cr":           pq_turnover_cr,
        "tenure_years":             tenure_years,
        "tenure_years_source":      tenure_source,
        "formula_multiplier":       formula_mult,
        "annual_contract_value_cr": annual_contract_value_cr,
        "multiple_of_annual":       multiple_of_annual,
        "multiple_of_annual_cap":   MULTIPLE_OF_ANNUAL_CAP,
        "rule_shape":               rule["shape"],
        "violation_reason":         reason_label,
        "tier":                     1,
        "extracted_by":             "bge-m3+llm-rerank:qwen-2.5-72b@openrouter",
        "retrieval_strategy": (
            f"qdrant_top{K}_router_{family}_section_filter_"
            f"{'-'.join(section_types)}_llm_rerank+grep_fallback"
        ),
        "doc_family":               family,
        "section_filter":           section_types,
        "rerank_chosen_index":      chosen,
        "rerank_reasoning":         reason,
        "section_node_id":          section_node_id,
        "section_heading":          section_heading,
        "source_file":              source_file,
        "line_start_local":         line_start_local,
        "line_end_local":           line_end_local,
        "qdrant_similarity":        qdrant_similarity,
        # L24 audit fields
        "evidence_in_source":       ev_passed,
        "evidence_verified":        ev_passed,
        "evidence_match_score":     ev_score,
        "evidence_match_method":    ev_method,
        # Rule-evaluator inputs
        "estimated_value_cr":       facts.get("_estimated_value_cr"),
        # L27 audit
        "verdict_origin":           rule.get("verdict_origin"),
        "severity_origin":          rule.get("severity_origin"),
        # L35 status / human-review markers
        "status":                   "UNVERIFIED" if is_unverified else "OPEN",
        "requires_human_review":    bool(is_unverified),
        "human_review_reason": (
            f"L36 grep fallback fired: LLM rerank top-{K} did not surface a "
            f"PQ-financial criterion, but exhaustive grep across "
            f"{section_types} found turnover/bid-capacity keyword hits in "
            f"{len(grep_hits)} section(s). Reviewer should open the listed "
            f"sections in grep_audit.hits and confirm the PQ shape "
            f"(fixed_inr vs bid_capacity_formula) and threshold."
            if grep_promoted_to_unverified else
            f"LLM found {pq_type} PQ criterion but evidence quote failed "
            f"L24 verification (score={ev_score}, method={ev_method}). "
            f"Reviewer should open the section above (line_start="
            f"{line_start_local}, line_end={line_end_local}) and confirm."
            if is_unverified_l24_fail else
            f"LLM extracted PQ turnover ₹{pq_turnover_cr}cr but missing "
            f"facts to compute multiple_of_annual (ECV={ecv_cr}, "
            f"tenure_years={tenure_years}). Reviewer should fill the "
            f"missing inputs and re-run the threshold compute."
            if is_unverified_no_compute else None
        ),
        # L36 grep-fallback audit
        "grep_fallback_audit":      grep_audit,
        "defeated":                 False,
    }

    finding = rest_post("kg_nodes", [{
        "doc_id":    DOC_ID,
        "node_type": "ValidationFinding",
        "label":     label,
        "properties": finding_props,
        "source_ref": f"tier1:turnover_check:{rule['rule_id']}",
    }])[0]

    # Edge: only emitted on OPEN findings (violations + ABSENCE).
    # UNVERIFIED findings have NO VIOLATES_RULE edge per L37 four-state.
    edge = None
    if is_violation_excess or is_absence:
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
                "pq_type":              pq_type,
                "pq_turnover_cr":       pq_turnover_cr,
                "multiple_of_annual":   multiple_of_annual,
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
