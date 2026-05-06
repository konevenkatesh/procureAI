"""
scripts/tier1_class_mismatch_check.py

Tier-1 Eligibility-Class-Mismatch check, BGE-M3 + LLM, NO regex.

THRESHOLD shape with optional-clause semantics + L36 grep fallback.
AP-GO-092 (HARD_BLOCK) sets contractor monetary tendering limits per
GO 8/2003 / Memo 36/2003 / GO Ms No 94/2003:

   Special   ECV >  Rs.10 cr            (int=6)
   Class-I   Rs.2 cr  < ECV <= Rs.10 cr (int=5)
   Class-II  Rs.1 cr  < ECV <= Rs.2 cr  (int=4)
   Class-III Rs.50 L  < ECV <= Rs.1 cr  (int=3)
   Class-IV  Rs.10 L  < ECV <= Rs.50 L  (int=2)
   Class-V             ECV <= Rs.10 L   (int=1)

The DOCUMENT-side test: does the doc's "Eligible Class of Bidders"
text admit ONLY contractors whose registered class can legally
tender for this ECV band? If the doc's lowest-admitted class has a
monetary ceiling BELOW the ECV, that's a HARD_BLOCK violation —
the doc's eligibility text admits bidders whose registration class
makes them legally ineligible at evaluation per AP-GO-092.

Two corner cases:
- VAGUE: doc says "appropriate eligible class as per G.O.Ms.No.94"
  without naming the class. Defers to rule but doesn't enforce a
  floor — ADVISORY-UNDERSPECIFIED.
- "& above" / "or higher" breadth: e.g. "Class-I & above" admits
  {Class-I, Special}. The COMPLIANCE FLOOR is the LOWEST admitted
  class. If that floor < band_required, fire HARD_BLOCK.

Pipeline (post-L37 four-state contract):
  1. Pick rule via condition_evaluator. AP-GO-092 SKIPs on PPP →
     correct silence on Tirupathi/Vijayawada.
  2. Section filter via ELIGIBILITY_CLASS_SECTION_ROUTER —
        APCRDA_Works → [NIT, ITB]
        SBD_Format   → [NIT, Evaluation]
        NREDCAP_PPP  → [] (defensive; rule SKIPs first)
        default      → [NIT, ITB, Evaluation]
  3. BGE-M3 embed an answer-shaped query.
  4. Qdrant top-K=10 candidates within the filter.
  5. LLM rerank with class-extraction:
        required_class ∈ {Special, Class-I..V, vague, not_found},
        class_breadth ∈ {exact, and_above, or_higher, or_equivalent},
        go_reference (text), evidence (verbatim), found (bool).
  6. Hallucination guard (L24).
  7. L36 source-grep fallback on the not_found path.
  8. Apply outcome logic:
        required_class = "vague"     → ADVISORY-UNDERSPECIFIED finding
        required_class = "not_found" → grep fallback → UNVERIFIED or ABSENCE
        Both required_class AND ECV known:
          band_required = ECV → class lookup
          required_class_int = scale lookup
          breadth ∈ {and_above, or_higher} → floor = required_class_int
          breadth = exact                  → floor = required_class_int
          floor >= band_required → COMPLIANT (no finding)
          floor <  band_required → HARD_BLOCK violation

Tested on kakinada_pkg11_exp_001 first (expected: required_class=
Class-I, breadth=and_above, ECV=152.78cr → band_required=Special(6),
Class-I(5) < 6 → HARD_BLOCK).
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

DOC_ID = sys.argv[1] if len(sys.argv) > 1 else "kakinada_pkg11_exp_001"

TYPOLOGY = "Eligibility-Class-Mismatch"

QDRANT_URL  = os.environ.get("QDRANT_URL", "http://localhost:6333")
COLLECTION  = "tender_sections"

LLM_MODEL = os.environ.get("LLM_MODEL", "qwen/qwen-2.5-72b-instruct")


# Canonical class scale per user decision (typology-13 read-first review).
# Higher integer = higher class = legally permitted to tender for higher
# monetary bands. The compliance test compares the doc-stated FLOOR
# against the band_required derived from ECV.
CLASS_SCALE: dict[str, int] = {
    "Special":  6,
    "Class-I":  5,
    "Class-II": 4,
    "Class-III": 3,
    "Class-IV": 2,
    "Class-V":  1,
}

# Reverse for log / label output.
CLASS_NAME_BY_INT: dict[int, str] = {v: k for k, v in CLASS_SCALE.items()}


def band_required_for_ecv_cr(ecv_cr: float) -> tuple[str, int]:
    """AP-GO-092 / GO 8/2003 monetary band → required class.
    Returns (class_name, class_int). Boundaries inclusive on the high
    side per GO 8/2003 wording ('Class-I Rs.2-10 cr' = 2 < ECV ≤ 10)."""
    if ecv_cr is None:
        raise ValueError("ECV cannot be None for band lookup")
    if ecv_cr > 10.0:
        return ("Special",  CLASS_SCALE["Special"])
    if ecv_cr > 2.0:
        return ("Class-I",  CLASS_SCALE["Class-I"])
    if ecv_cr > 1.0:
        return ("Class-II", CLASS_SCALE["Class-II"])
    if ecv_cr > 0.5:
        return ("Class-III", CLASS_SCALE["Class-III"])
    if ecv_cr > 0.1:
        return ("Class-IV", CLASS_SCALE["Class-IV"])
    return ("Class-V", CLASS_SCALE["Class-V"])


# L36 source-grep fallback vocabulary. Phrase-precise; bare "class"
# is too noisy (it appears in "first-class", "world class", etc.).
GREP_FALLBACK_KEYWORDS = [
    "Special Class",
    "Class I",
    "Class-I",
    "Class II",
    "Class-II",
    "Class III",
    "Class-III",
    "Class IV",
    "Class-IV",
    "Class V",
    "Class-V",
    "Eligible Class",
    "Class of Bidders",
    "Category of Registration",
    "Civil Registration",
    "Civil Contractors",
    "GO Ms No.94",
    "GO Ms No 94",
    "GO.MS. No.94",
    "G.O.Ms.No.94",
    "G.O Ms.No.94",
    "G.O. Ms. No. 94",
    "registration class",
    "registered contractor",
]


# Answer-shaped query — mirrors the literal wording of the doc's
# "Eligible Class of Bidders" / "Category of Registration" headers
# and the AP-GO-092 rule text.
QUERY_TEXT = (
    "Eligible Class of Bidders Special Class Civil registration "
    "Government of Andhra Pradesh GO Ms No 94 contractor class "
    "Category of Registration Civil Contractors monetary limit "
    "tendering class I II III IV V"
)


# Rule candidates. AP-GO-092 is the canonical primary HARD_BLOCK
# (verification: "Check ECV against bidder's registered class limits;
# reject if class is below ECV band"). The other 3 rules in the
# cluster (AP-GO-059 / 065 / 099) are subsidiary and not built tonight
# per user-approved scope.
RULE_CANDIDATES = [
    {
        "rule_id":         "AP-GO-092",
        "natural_language": "AP Civil Works contractor monetary tendering limits by class (per GO 8/2003): Special > Rs.10 cr; Class-I Rs.2-10 cr; Class-II Rs.1-2 cr; Class-III Rs.50 lakh-1 cr; Class-IV Rs.10-50 lakh; Class-V up to Rs.10 lakh",
        "severity":        "HARD_BLOCK",
        "layer":           "AP-State",
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
    if not section_types:
        raise RuntimeError(
            f"Empty section_types passed to Qdrant; rule selector should "
            f"have exited before this call (doc_id={doc_id})"
        )
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


# ── LLM rerank prompt for class-of-bidders extraction ────────────────

LLM_SYSTEM = (
    "You are a precise procurement-document fact extractor. "
    "Return JSON only. Quote evidence verbatim from the supplied text. "
    "Never fabricate."
)


# Tightened anchor vocabulary per L39 lesson — only patterns that
# uniquely identify the class-of-bidders text. Bare "class" is too
# noisy (matches "first-class", "world class", etc.); class-name
# patterns (Special / Class-I / etc.) and the GO Ms No 94 reference
# are the right anchors.
CLASS_TRUNCATE_KEYWORDS = [
    r"Special\s+Class",
    r"Class[\s\-]?I\b",
    r"Class[\s\-]?II\b",
    r"Class[\s\-]?III\b",
    r"Class[\s\-]?IV\b",
    r"Class[\s\-]?V\b",
    r"Eligible\s+Class\s+of\s+Bidders",
    r"Category\s+of\s+Registration",
    r"Civil\s+Registration",
    r"Civil\s+Contractors",
    r"G\.?O\.?\s*Ms\.?\s*No\.?\s*94",
    r"GO\s*Ms\.?\s*No\.?\s*94",
    r"appropriate\s+(eligible\s+)?class",
]


def build_class_rerank_prompt(candidates: list[dict]) -> str:
    blocks = []
    for i, c in enumerate(candidates):
        body = smart_truncate(c["full_text"], window=6000,
                              keywords=CLASS_TRUNCATE_KEYWORDS)
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
        "Andhra Pradesh civil-works procurement tender document. "
        "Exactly ONE of them (or none) carries the ELIGIBLE-CLASS-OF-"
        "BIDDERS criterion — the contractual statement of which "
        "Government-of-AP contractor registration class(es) are "
        "permitted to bid for this tender, per GO Ms No 94/2003 "
        "(also referenced as G.O.Ms.No.94 / GO.MS. No.94 / similar).\n"
        "\n"
        "AP contractor classes (highest to lowest):\n"
        "  • Special Class — unrestricted ECV\n"
        "  • Class-I       — ECV up to Rs.10 cr\n"
        "  • Class-II      — ECV up to Rs.2 cr\n"
        "  • Class-III     — ECV up to Rs.1 cr\n"
        "  • Class-IV      — ECV up to Rs.50 lakh\n"
        "  • Class-V       — ECV up to Rs.10 lakh\n"
        "\n"
        f"{candidates_block}\n\n"
        "Return JSON only:\n"
        "{\n"
        "  \"chosen_index\":     integer 0..N-1 of the eligibility-class candidate, OR null if no candidate carries this criterion,\n"
        "  \"required_class\":   one of \"Special\" | \"Class-I\" | \"Class-II\" | \"Class-III\" | \"Class-IV\" | \"Class-V\" | \"vague\" | \"not_found\"\n"
        "                       (\"vague\" means doc says something like 'appropriate eligible class as per G.O.Ms.No.94' without naming a specific class),\n"
        "  \"class_breadth\":    one of \"exact\" | \"and_above\" | \"or_higher\" | \"or_equivalent\" | null\n"
        "                       (use \"and_above\" for 'Class-I & above', 'Class I and above' etc.;\n"
        "                       \"or_higher\" for 'Class-I or higher';\n"
        "                       \"or_equivalent\" for 'Class-I or equivalent';\n"
        "                       \"exact\" for 'Special Class Civil registration' with no breadth qualifier;\n"
        "                       null when required_class is 'vague' or 'not_found'),\n"
        "  \"go_reference\":     string OR null  (the GO citation if present, e.g. 'GO Ms No. 94 dated 01-07-2003'),\n"
        "  \"evidence\":         \"verbatim quote (single contiguous span) — the line(s) that state the eligible class\",\n"
        "  \"found\":            bool,\n"
        "  \"reasoning\":        \"one short sentence explaining the choice\"\n"
        "}\n\n"
        "Selection rules — IGNORE the following content (NOT a class-of-bidders criterion):\n"
        "- Sub-contractor class references ('proposed Sub-Contractor gets eligibility from suitable class') — that's downstream sub-contracting, not the bidder eligibility floor.\n"
        "- Past-experience certificate value thresholds (those test bidder financials, not class).\n"
        "- General registration requirements (GST / PAN / EPF / electrical license) — those are NOT contractor-class.\n"
        "- Available Bid Capacity formula (turnover-based, separate typology).\n"
        "- 'Class' references in JV / Consortium clauses describing partner-class eligibility — only count if it states the bidder/lead's required class.\n"
        "\n"
        "Selection rules — DO pick if the candidate states:\n"
        "- 'Eligible Class of Bidders: ... Special Class Civil registration ...' (FIXED class).\n"
        "- 'Category of Registration: Class-I Civil & above' (CLASS + breadth).\n"
        "- 'Civil Contractors having registrations with Government of Andhra Pradesh in appropriate eligible class as per the G.O.Ms.No.94' (VAGUE — defers to rule, no specific class named).\n"
        "- A BDS / ITB-rewrite line like 'ITB 4.1 ... The Bidder should have a Special Class Civil registration ...'.\n"
        "\n"
        "QUOTE FORMAT — STRICT (per L35):\n"
        "- Return a SINGLE CONTIGUOUS quote from ONE sentence, ONE clause, or ONE table row. Do NOT stitch multiple paragraphs.\n"
        "- Do NOT add ellipsis ('...') between lines.\n"
        "- Do NOT paraphrase, summarise, or condense — quote EXACTLY as it appears in the supplied text.\n"
        "- Do NOT introduce or remove markdown formatting (asterisks, underscores, italics, table-cell pipes) — preserve the source formatting verbatim.\n"
        "- Pick the SHORTEST contiguous span that proves the criterion; one sentence or one table-row entry is usually enough.\n"
        "\n"
        "- If no candidate carries an eligibility-class criterion, set chosen_index=null, required_class=\"not_found\", found=false. The L36 grep fallback will then take over.\n"
        "- Evidence MUST be an exact substring of the chosen candidate's text."
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


def select_class_match_rule(tender_facts: dict) -> dict | None:
    """Pick the rule that fires (or fires-as-UNKNOWN per L27 downgrade).
    AP-GO-092 SKIPs on TenderType=PPP — correct silence on PPP DCAs."""
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

def _delete_prior_tier1_class(doc_id: str) -> tuple[int, int]:
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
    print(f"  Tier-1 Eligibility-Class-Mismatch (BGE-M3 + LLM, NO regex)")
    print(f"  doc_id : {DOC_ID}")
    print(f"  model  : {LLM_MODEL}")
    print(f"  scale  : Special=6, Class-I=5, II=4, III=3, IV=2, V=1")
    print("=" * 76)

    n_f, n_e = _delete_prior_tier1_class(DOC_ID)
    if n_f or n_e:
        print(f"  cleared {n_f} prior Tier-1 Class-Mismatch finding node(s) "
              f"and {n_e} edge(s) before re-running")

    # 1. Pick rule via condition_evaluator
    facts = fetch_tender_facts(DOC_ID)
    rule  = select_class_match_rule(facts)
    if rule is None:
        return 0

    # 2. Family + section_type filter (router)
    family, section_types = family_for_doc_with_filter(DOC_ID, TYPOLOGY)
    print(f"\n── Document family / retrieval filter ──")
    print(f"  family         : {family}")
    print(f"  section_types  : {section_types}")
    if not section_types:
        # Defensive — should not reach here because rule selector
        # SKIPped on PPP. If it does, exit cleanly.
        print(f"  → empty section_filter (typology N/A on this family); silent exit")
        return 0

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
    print(f"\n── Step 3: LLM rerank + class-of-bidders extraction ──")
    user_prompt = build_class_rerank_prompt(candidates)
    print(f"  prompt size: {len(user_prompt)} chars (~{len(user_prompt)//4} tokens)")
    raw_content = call_llm(LLM_SYSTEM, user_prompt, max_tokens=900)
    timings["llm"] = time.perf_counter() - t0
    print(f"  wall: {timings['llm']:.2f}s")
    print(f"\n── Raw LLM JSON ──")
    print(raw_content)

    # 7. Parse + select chosen candidate
    parsed = parse_llm_response(raw_content)
    chosen          = parsed.get("chosen_index")
    required_class  = (parsed.get("required_class") or "not_found").strip()
    class_breadth   = parsed.get("class_breadth")
    go_reference    = parsed.get("go_reference")
    evidence        = (parsed.get("evidence") or "").strip()
    found           = bool(parsed.get("found"))
    reason          = (parsed.get("reasoning") or "").strip()

    # Normalise required_class to canonical scale or sentinels.
    canonical_classes = set(CLASS_SCALE.keys()) | {"vague", "not_found"}
    if required_class not in canonical_classes:
        # Try to map common variants (e.g. "Class I" → "Class-I")
        rc_norm = required_class.replace(" ", "-").strip()
        if rc_norm in CLASS_SCALE:
            required_class = rc_norm
        else:
            print(f"  ⚠ unrecognised required_class={required_class!r} — "
                  f"treating as 'not_found'")
            required_class = "not_found"

    if class_breadth is not None:
        class_breadth = str(class_breadth).strip().lower()

    print(f"\n── Parsed ──")
    print(f"  chosen_index           : {chosen}")
    print(f"  found                  : {found}")
    print(f"  required_class         : {required_class!r}")
    print(f"  class_breadth          : {class_breadth!r}")
    print(f"  go_reference           : {go_reference!r}")
    print(f"  reasoning              : {reason[:200]}")
    print(f"  evidence               : {evidence[:300]!r}")

    section = None
    similarity = None
    ev_passed = False
    ev_score = 0
    ev_method = "skipped"

    llm_chose_candidate = (chosen is not None and isinstance(chosen, int)
                           and 0 <= chosen < len(candidates))
    llm_found_clause   = (found and required_class != "not_found"
                          and llm_chose_candidate)

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
                print(f"  L24_FAILED — LLM found class clause but quote is unverifiable. "
                      f"Routing to UNVERIFIED finding.")
                print(f"    LLM evidence  : {evidence[:200]!r}")
        else:
            print(f"  ⚠ no evidence quote provided — treating as L24-failed (empty)")
            ev_passed = False; ev_score = 0; ev_method = "empty"
    else:
        print(f"  → no candidate chosen by LLM (required_class={required_class!r})")
        if required_class != "not_found" and not llm_chose_candidate:
            print(f"  ⚠ required_class={required_class!r} but chosen_index=null — "
                  f"treating as not_found")
            required_class = "not_found"
            llm_found_clause = False

    # 8. Threshold compute (band + class-int compare) ─────────────────
    ecv_cr = facts.get("_estimated_value_cr")
    band_required_name: str | None = None
    band_required_int:  int | None = None
    floor_class_int:    int | None = None
    if ecv_cr is not None:
        band_required_name, band_required_int = band_required_for_ecv_cr(ecv_cr)
        print(f"\n── Band lookup ──")
        print(f"  ECV               : {ecv_cr:.2f} cr")
        print(f"  band_required     : {band_required_name} (int={band_required_int})")
    else:
        print(f"\n── Band lookup SKIPPED (ECV unknown) ──")

    if required_class in CLASS_SCALE:
        floor_class_int = CLASS_SCALE[required_class]
        # "and_above" / "or_higher" / "or_equivalent" all mean the FLOOR
        # is the named class — higher classes also admitted, but lower
        # ones are NOT. So the compliance test compares the floor.
        # "exact" means only the named class is admitted; same compare
        # against band_required.
        breadth_label = class_breadth or "exact"
        print(f"  required_class    : {required_class} (int={floor_class_int})")
        print(f"  class_breadth     : {breadth_label!r}")
        print(f"  floor_class_int   : {floor_class_int}")

    # 9. Outcome decision per user spec + L36 grep fallback on not_found
    is_compliant            = False
    is_violation_mismatch   = False
    is_advisory_vague       = False
    is_unverified_l24_fail  = False
    is_unverified_no_band   = False

    if required_class == "vague" and ev_passed:
        is_advisory_vague = True
    elif (required_class in CLASS_SCALE and ev_passed
          and band_required_int is not None and floor_class_int is not None):
        if floor_class_int >= band_required_int:
            is_compliant = True
        else:
            is_violation_mismatch = True
    elif (required_class in CLASS_SCALE and ev_passed
          and band_required_int is None):
        # ECV unknown — can't compute band_required. UNVERIFIED.
        is_unverified_no_band = True
    elif (required_class != "not_found" and llm_chose_candidate
          and not ev_passed):
        is_unverified_l24_fail = True

    raw_is_absence = (required_class == "not_found"
                      or (not llm_chose_candidate
                          and required_class not in CLASS_SCALE
                          and required_class != "vague"))

    grep_hits: list[dict] = []
    full_grep_hits: list[dict] = []
    grep_promoted_to_unverified = False
    full_grep_promoted = False        # True when whole-file grep finds a hit beyond Section coverage
    kg_coverage_gap = False
    # Run L36 → L40 fallback chain on EITHER absence OR L24-fail.
    # L24-fail (hallucinated quote) is a hint that the LLM didn't see
    # the real text in the candidates it was shown — possibly because
    # it's in a Section the retrieval missed (L36 catches) OR in a
    # source range no Section covers (L40 catches as kg_coverage_gap).
    run_fallback_chain = raw_is_absence or is_unverified_l24_fail
    if run_fallback_chain:
        if raw_is_absence:
            print(f"\n── L36 source-grep fallback (not_found path) ──")
        else:
            print(f"\n── L36 source-grep fallback (L24-fail path — hallucinated quote check) ──")
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
            # On absence path, L36 hits → UNVERIFIED-retrieval-gap.
            # On L24-fail path, L36 hits → keep the L24-fail label
            # (the hallucinated quote was caught; the candidate did
            # contain class-keywords elsewhere).
            if raw_is_absence:
                grep_promoted_to_unverified = True
                print(f"  → not_found downgraded to UNVERIFIED — retrieval-coverage gap")
            else:
                print(f"  → L24-fail path: keeping is_unverified_l24_fail label "
                      f"(L36 hits noted in audit but not promoting)")
        else:
            # L40 — Tier-2 whole-file fallback. When Section-bounded grep
            # is empty, escalate to scanning each source markdown file
            # entirely (not bounded by Section ranges). Distinguishes a
            # genuine source absence from a kg_builder section-coverage
            # gap. Hits with kg_coverage_gap=True are KG-defect signals.
            print(f"\n── L40 whole-file grep (Tier-2 fallback) ──")
            any_full, full_grep_hits = grep_full_source_for_keywords(
                DOC_ID, GREP_FALLBACK_KEYWORDS,
            )
            print(f"  whole-file any_hit    : {any_full}  "
                  f"({len(full_grep_hits)} match line(s) across source files)")
            for h in full_grep_hits[:3]:
                gap = "GAP" if h["kg_coverage_gap"] else "in-section"
                print(f"    [{gap}] {h['source_file'][:38]:40s} "
                      f"L{h['line_no']:<5d}  matched={h['keyword_matches']}")
            if len(full_grep_hits) > 3:
                print(f"    ... and {len(full_grep_hits) - 3} more")
            if any_full:
                kg_coverage_gap = any(h["kg_coverage_gap"] for h in full_grep_hits)
                # Promote on absence path always; on L24-fail path,
                # promote only when the whole-file hit reveals a
                # kg_coverage_gap (which is the more meaningful signal
                # than "L24 caught a hallucination").
                if raw_is_absence or kg_coverage_gap:
                    full_grep_promoted = True
                    # On L24-fail+kg_coverage_gap path, override the
                    # L24-fail label with the kg_coverage_gap label —
                    # it's more informative for the reviewer.
                    if is_unverified_l24_fail and kg_coverage_gap:
                        is_unverified_l24_fail = False
                    print(f"  → "
                          f"{'absence' if raw_is_absence else 'L24-fail'} "
                          f"downgraded to UNVERIFIED — "
                          f"{'kg_coverage_gap' if kg_coverage_gap else 'whole-file-only'} "
                          f"hit; reviewer should re-build KG and re-run")

    is_absence    = raw_is_absence and not grep_promoted_to_unverified and not full_grep_promoted
    is_unverified = (is_unverified_l24_fail or is_unverified_no_band
                     or grep_promoted_to_unverified
                     or full_grep_promoted)

    # Pick reason label
    if is_compliant:
        reason_label = (f"compliant_{required_class.lower()}_meets_band_"
                        f"{band_required_name.lower()}")
    elif is_violation_mismatch:
        reason_label = (f"class_mismatch_doc_admits_{required_class.lower()}_"
                        f"{(class_breadth or 'exact')}_vs_band_required_"
                        f"{band_required_name.lower()}_floor_{floor_class_int}_"
                        f"vs_required_{band_required_int}")
    elif is_advisory_vague:
        reason_label = "class_underspecified_doc_defers_to_rule_no_specific_class"
    elif grep_promoted_to_unverified:
        reason_label = "class_unverified_grep_fallback_retrieval_gap"
    elif full_grep_promoted:
        reason_label = ("class_unverified_kg_coverage_gap"
                        if kg_coverage_gap
                        else "class_unverified_whole_file_grep_only")
    elif is_unverified_l24_fail:
        reason_label = "class_unverified_llm_quote_failed_l24"
    elif is_unverified_no_band:
        reason_label = "class_unverified_cannot_compute_band_ecv_unknown"
    else:
        reason_label = "class_eligibility_text_absent"

    print(f"\n── Decision ──")
    print(f"  rule                       : {rule['rule_id']} "
          f"({rule['severity']}, shape={rule['shape']})")
    print(f"  required_class             : {required_class}")
    print(f"  band_required              : {band_required_name}")
    print(f"  is_compliant               : {is_compliant}")
    print(f"  is_violation_mismatch      : {is_violation_mismatch}")
    print(f"  is_advisory_vague          : {is_advisory_vague}")
    print(f"  is_unverified              : {is_unverified}")
    print(f"  is_absence                 : {is_absence}")
    print(f"  reason_label               : {reason_label}")

    # COMPLIANT branch returns early — no finding emitted.
    if is_compliant:
        return 0

    # 10. Materialise finding (violation, advisory, UNVERIFIED, or ABSENCE)
    t0 = time.perf_counter()
    if section is not None and (is_violation_mismatch or is_advisory_vague
                                or is_unverified_l24_fail
                                or is_unverified_no_band):
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
        evidence  = (f"Eligible Class of Bidders criterion not found in "
                     f"document after searching {', '.join(section_types)} "
                     f"section types (also exhaustive grep across all "
                     f"matching sections — no keyword hits)")
        print(f"  → ABSENCE finding — LLM rerank empty AND grep fallback empty")
    elif grep_promoted_to_unverified:
        ev_passed = None
        ev_score  = None
        ev_method = "grep_fallback_retrieval_gap"
        evidence  = (f"LLM rerank top-{K} returned no class-of-bidders "
                     f"candidate, but exhaustive grep across "
                     f"{', '.join(section_types)} found keyword hits in "
                     f"{len(grep_hits)} section(s). First match: "
                     f"{grep_hits[0]['snippet'][:160] if grep_hits else 'n/a'}")
        print(f"  → UNVERIFIED finding (grep fallback) — retrieval missed "
              f"{len(grep_hits)} section(s) carrying class keywords")
    elif full_grep_promoted:
        ev_passed = None
        ev_score  = None
        ev_method = ("whole_file_grep_kg_coverage_gap"
                     if kg_coverage_gap else "whole_file_grep_match")
        first = full_grep_hits[0] if full_grep_hits else None
        evidence  = (f"LLM rerank, Section-bounded grep BOTH empty but "
                     f"whole-file grep found {len(full_grep_hits)} match "
                     f"line(s) — "
                     f"{'KG-coverage GAP detected' if kg_coverage_gap else 'whole-file only hit'}. "
                     f"First match: "
                     f"{first['source_file']}:L{first['line_no']} "
                     f"{first['snippet'][:120] if first else 'n/a'}")
        print(f"  → UNVERIFIED finding (whole-file fallback) — "
              f"{'KG-coverage gap' if kg_coverage_gap else 'whole-file-only hit'}; "
              f"{len(full_grep_hits)} match line(s)")
    elif is_unverified_l24_fail:
        print(f"  → UNVERIFIED finding — LLM identified class clause but "
              f"quote failed L24 verification (score={ev_score}, "
              f"method={ev_method})")
    elif is_unverified_no_band:
        print(f"  → UNVERIFIED finding — LLM extracted required_class="
              f"{required_class} but missing ECV to compute band_required")

    rule_node_id = get_or_create_rule_node(DOC_ID, rule["rule_id"])

    # Severity for the finding: HARD_BLOCK from AP-GO-092 unless L27
    # downgraded to ADVISORY. Vague-clause findings carry a separate
    # ADVISORY severity (under-specified, not a class-mismatch).
    if is_advisory_vague:
        finding_severity = "ADVISORY"
    elif is_violation_mismatch:
        finding_severity = rule["severity"]    # HARD_BLOCK (or ADVISORY if L27)
    elif is_unverified or is_absence:
        finding_severity = rule["severity"]
    else:
        finding_severity = rule["severity"]

    if is_violation_mismatch:
        breadth_str = f" {class_breadth}" if class_breadth and class_breadth != "exact" else ""
        label = (
            f"{TYPOLOGY}: doc admits {required_class}{breadth_str} bidders "
            f"(floor int={floor_class_int}) but ECV ₹{ecv_cr:.2f}cr requires "
            f"{band_required_name} class (int={band_required_int}) per AP-GO-092 "
            f"({rule['severity']})"
        )
    elif is_advisory_vague:
        label = (
            f"{TYPOLOGY}: doc UNDERSPECIFIED — eligibility text defers to "
            f"GO Ms No 94 without naming the required class for ECV "
            f"₹{ecv_cr if ecv_cr is not None else 'unknown'}cr; reviewer "
            f"should require explicit class for audit clarity"
        )
    elif grep_promoted_to_unverified:
        label = (
            f"{TYPOLOGY}: UNVERIFIED (L36 grep-fallback) — LLM rerank "
            f"missed the class clause; exhaustive grep found "
            f"{len(grep_hits)} section(s) with class-keyword hits; "
            f"requires human review"
        )
    elif full_grep_promoted:
        label = (
            f"{TYPOLOGY}: UNVERIFIED (L40 whole-file fallback) — "
            f"{'KG-coverage gap detected — class clause exists in source but no Section node covers the line; reviewer should re-build KG' if kg_coverage_gap else 'whole-file grep matched but no Section-bounded hit'}; "
            f"{len(full_grep_hits)} match line(s) found"
        )
    elif is_unverified_l24_fail:
        label = (
            f"{TYPOLOGY}: UNVERIFIED — LLM found {required_class} class "
            f"clause but quote failed L24 (score={ev_score}, "
            f"method={ev_method}); requires human review against "
            f"{(section['heading'][:60] if section else 'TenderDocument')!r}"
        )
    elif is_unverified_no_band:
        label = (
            f"{TYPOLOGY}: UNVERIFIED — doc demands {required_class} class "
            f"but ECV unknown so band-required cannot be computed; "
            f"reviewer should fill ECV and re-run"
        )
    else:
        label = (
            f"{TYPOLOGY}: Eligible-Class-of-Bidders criterion absent — "
            f"{rule['rule_id']} ({rule['severity']}) requires this AP "
            f"Works tender to name the contractor class admitted to bid"
        )

    grep_audit = None
    if grep_promoted_to_unverified:
        grep_audit = {
            "tier":                   "L36_section_bounded",
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
    elif full_grep_promoted:
        grep_audit = {
            "tier":                   "L40_whole_file",
            "scanned_section_types":  section_types,
            "keywords":               GREP_FALLBACK_KEYWORDS,
            "section_bounded_empty":  True,
            "kg_coverage_gap":        kg_coverage_gap,
            "hits_count":             len(full_grep_hits),
            "hits": [
                {"source_file":       h["source_file"],
                 "line_no":           h["line_no"],
                 "kg_coverage_gap":   h["kg_coverage_gap"],
                 "covering_section":  h["covering_section"],
                 "keyword_matches":   h["keyword_matches"],
                 "snippet":           h["snippet"][:300]}
                for h in full_grep_hits[:10]
            ],
        }

    finding_props = {
        "rule_id":                  rule["rule_id"],
        "typology_code":            TYPOLOGY,
        "severity":                 finding_severity,
        "evidence":                 evidence,
        "extraction_path":          "threshold",
        "llm_found_clause":         llm_found_clause,
        "required_class":           required_class,
        "required_class_int":       floor_class_int,
        "class_breadth":            class_breadth,
        "go_reference":             go_reference,
        "band_required_name":       band_required_name,
        "band_required_int":        band_required_int,
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
        "estimated_value_cr":       ecv_cr,
        # L27 audit
        "verdict_origin":           rule.get("verdict_origin"),
        "severity_origin":          rule.get("severity_origin"),
        # L35 status / human-review markers
        "status":                   "UNVERIFIED" if is_unverified else "OPEN",
        "requires_human_review":    bool(is_unverified),
        "human_review_reason": (
            f"L36 grep fallback fired: LLM rerank top-{K} did not surface "
            f"a class-of-bidders clause, but exhaustive grep across "
            f"{section_types} found class-keyword hits in {len(grep_hits)} "
            f"section(s). Reviewer should open the listed sections in "
            f"grep_audit.hits and confirm the demanded class against "
            f"AP-GO-092 band for ECV ₹{ecv_cr}cr."
            if grep_promoted_to_unverified else
            f"L40 whole-file fallback fired: LLM rerank AND Section-bounded "
            f"grep both empty, but whole-file grep found "
            f"{len(full_grep_hits)} match line(s). "
            f"{'kg_coverage_gap=TRUE — at least one match line is NOT covered by any Section node; the kg_builder did not index this region. Reviewer should re-run kg_builder on this doc and re-check.' if kg_coverage_gap else 'All matches fall inside Section ranges yet Section-bounded grep returned empty — likely a keyword-vocabulary mismatch worth reviewing.'} "
            f"First match: {full_grep_hits[0]['source_file']}:L{full_grep_hits[0]['line_no']}."
            if full_grep_promoted else
            f"LLM found {required_class} class clause but evidence quote "
            f"failed L24 verification (score={ev_score}, method={ev_method}). "
            f"Reviewer should open the section above (line_start="
            f"{line_start_local}, line_end={line_end_local}) and confirm."
            if is_unverified_l24_fail else
            f"LLM extracted required_class={required_class} but ECV unknown, "
            f"so band-required can't be computed. Fill ECV and re-run."
            if is_unverified_no_band else None
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
        "source_ref": f"tier1:class_mismatch_check:{rule['rule_id']}",
    }])[0]

    # Edge: only emitted on OPEN findings (violations + advisory_vague + ABSENCE).
    # UNVERIFIED findings have NO VIOLATES_RULE edge per L37 four-state.
    edge = None
    if is_violation_mismatch or is_advisory_vague or is_absence:
        edge = rest_post("kg_edges", [{
            "doc_id":       DOC_ID,
            "from_node_id": section_node_id,
            "to_node_id":   rule_node_id,
            "edge_type":    "VIOLATES_RULE",
            "weight":       1.0,
            "properties": {
                "rule_id":              rule["rule_id"],
                "typology":             TYPOLOGY,
                "severity":             finding_severity,
                "defeated":             False,
                "tier":                 1,
                "extraction_path":      "threshold",
                "required_class":       required_class,
                "required_class_int":   floor_class_int,
                "band_required_name":   band_required_name,
                "band_required_int":    band_required_int,
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
          f"(status={'UNVERIFIED' if is_unverified else 'OPEN'}, "
          f"severity={finding_severity})")
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
