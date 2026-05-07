"""
scripts/tier1_dlp_check.py

Tier-1 DLP-Period-Short check, BGE-M3 + LLM, NO regex.

THRESHOLD shape (return to PBG/EMD/Bid-Validity/MA machinery after FM
presence-shape). Per the read-first scan:

    AP-GO-084  TenderState=AP AND TenderType IN [Works, EPC]
               WARNING — AP DLP fixed at TWO YEARS (24 months) for both
               original works AND maintenance works.
    MPW-030    TenderType=Works AND ContractType=EPC
               HARD_BLOCK — about LATENT defect period (separate clause)
               and procuring authority's organisational capacity (in-
               house QA expert). NOT a Tier-1 doc-content check; same
               exclusion reasoning as MPW-122 (FM execution-stage).
    CVC-114    TenderType=Goods AND InstallationByVendor=true
               HARD_BLOCK — Goods-only; SKIPs everywhere on AP corpus.

defeats=[] across all three. AP corpus has zero AP-State HARD_BLOCKs
for DLP — AP-GO-084 (WARNING) is the regulatory anchor.

Single firing rule for Tier-1 on this corpus: **AP-GO-084 (24-month
threshold, WARNING)**. Fires on Vizag/JA/HC/Kakinada (Works+AP);
SKIPs on Tirupathi/Vijayawada (PPP).

Pipeline mirrors the threshold-shape typologies (PBG/EMD/MA):
  1. Pick rule via condition_evaluator.
  2. Section filter via DLP_SECTION_ROUTER —
        APCRDA_Works → [NIT, GCC, Forms]
        SBD_Format   → [NIT, GCC, Evaluation, Forms]
        NREDCAP_PPP  → [GCC]    (rule SKIPs; filter retained)
        default      → [NIT, GCC, Forms, Evaluation]
  3. BGE-M3 embed an answer-shaped query.
  4. Qdrant top-K=10 within the filter.
  5. LLM rerank with DLP-specific ignore rules (Performance Bank
     Guarantee validity, Bid Validity, EMD validity, Operations &
     Maintenance period for PPP, Latent Defect period for EPC) and
     structured DLP extraction (presence + months + start-event +
     basis).
  6. L24 evidence-guard hallucination check.
  7. L36 Section-bounded grep + L40 whole-file grep on raw absence.
  8. Apply threshold check — three states per L37/L48:
        COMPLIANT  (rule fires AND dlp_months ≥ 24) → silent (no row).
        WARNING    (rule fires AND dlp_months < 24) → row + edge.
        UNVERIFIED (LLM/L24 fail OR grep promotion) → row, no edge.
        GAP_VIOLATION (rule fires AND no DLP at all in source) → row + edge.

Silent-on-COMPLIANT decision per L48 (the portal infers COMPLIANT
from absence of any of OPEN / UNVERIFIED / GAP_VIOLATION rows).

Tested on judicial_academy_exp_001 first (expected: silent — DLP=24
months, threshold=24, equal).
"""
from __future__ import annotations

import os
import sys
import time
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
from modules.validation.grep_fallback    import (
    grep_source_for_keywords,
    grep_full_source_for_keywords,
)


# ── Constants ─────────────────────────────────────────────────────────

DOC_ID = sys.argv[1] if len(sys.argv) > 1 else "judicial_academy_exp_001"

TYPOLOGY = "DLP-Period-Short"

QDRANT_URL  = os.environ.get("QDRANT_URL", "http://localhost:6333")
COLLECTION  = "tender_sections"

LLM_MODEL = os.environ.get("LLM_MODEL", "qwen/qwen-2.5-72b-instruct")


# L36/L40 source-grep fallback vocabulary. DLP-specific phrases —
# tightened per L45 grep-vocabulary discipline. Do NOT include broad
# tokens like "warranty" alone (matches OEM AMC clauses) or generic
# "maintenance period" (matches PPP O&M Period in Tirupathi/Vijayawada
# DCAs which are NOT DLP). The phrase list is anchored on the
# regulated terminology: "Defect Liability Period" / "Defects
# Liability Period" / "DLP".
GREP_FALLBACK_KEYWORDS = [
    "Defect Liability Period",
    "Defects Liability Period",
    "Defects Liability",
    "Defect Liability",
    "DLP",
    "defect notification period",
    "defect liability period",   # lowercase variant
    "defects liability period",
    "guarantee period",          # CVC-114 Goods variant; harmless on Works
]


# Dual-query retrieval per L49 (this typology's lesson). Single-query
# top-K consistently surfaces only the GCC framework clause ("The
# Defects Liability Period is the period named in the PCC") at high
# cosines (0.55-0.64) on the AP corpus, while the value-stating
# locations — NIT datasheet rows, Forms bidder declarations, scope-
# of-work lines — score lower (0.44-0.48) because the DLP value is
# one row in a long tabular section or one sentence in a long form.
# A single-query approach silences DLP-Period-Short by-reference on
# every AP Works doc — the framework clause yields dlp_months=null
# every time and the threshold compare never runs.
#
# Two queries, K/2 candidates each, merged + deduped:
#   QUERY_FRAMEWORK — favours the GCC clause-definition style. The
#                     LLM uses this to confirm the regulated DLP
#                     framework is invoked.
#   QUERY_VALUE     — favours the value-stating style (datasheet row,
#                     bidder declaration, scope line, regulatory cite).
#                     The LLM uses this to extract dlp_months for
#                     threshold compare.
QUERY_FRAMEWORK = (
    "Defects Liability Period clause defined in PCC duration from "
    "Completion Date Provisional Certificate Taking Over Engineer in "
    "Charge sub-clause 35.3 GCC SCC"
)
QUERY_VALUE = (
    "Period of Defect Liability Period DLP 24 months from the date of "
    "completion of work I we are accepting defect liability period 24 "
    "Months twenty four months two years APSS clause 28 GO Ms No 8 T R B"
)
# Backwards-compatible alias used by the older single-query banner
# print only — the actual retrieval uses the two queries above.
QUERY_TEXT = QUERY_VALUE


# Rule candidates evaluated via condition_evaluator. Priority order:
# AP-GO-084 (only Tier-1 doc-content check); MPW-030 + CVC-114
# excluded from RULE_CANDIDATES because they're operational/Goods
# (see header docstring). Unlike MA's 4-rule list, DLP collapses
# cleanly to one rule on this corpus.
RULE_CANDIDATES = [
    {
        "rule_id":          "AP-GO-084",
        "natural_language": "AP Works/EPC Defect Liability Period fixed at 2 years (24 months) for both original works and maintenance works",
        "severity":         "WARNING",
        "layer":            "AP-State",
        "shape":            "threshold",
        "threshold_months": 24,
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


# ── LLM rerank prompt for DLP ─────────────────────────────────────────

LLM_SYSTEM = (
    "You are a precise procurement-document fact extractor. "
    "Return JSON only. Quote evidence verbatim from the supplied text. "
    "Never fabricate."
)


# Keyword vocabulary for smart_truncate windowing (DLP-specific). DLP
# values are short numerics inside long datasheet/GCC blocks — centring
# the window on the literal phrase prevents elision (L26).
DLP_TRUNCATE_KEYWORDS = [
    r"defect.{0,10}liability.{0,30}period",
    r"defects.{0,10}liability.{0,30}period",
    r"\bDLP\b",
    r"\b24\s*months?",
    r"\b12\s*months?",
    r"\b6\s*months?",
    r"\bone\s*\(?\s*1\s*\)?\s*year",
    r"\btwo\s*\(?\s*2\s*\)?\s*years?",
    r"\btwenty.?four\s*months?",
    r"\bsix\s*months?",
    r"defect notification period",
    r"completion of work",
    r"provisional certificate",
    r"taking over",
    r"\bGO\s*Ms\s*No\s*8\b",
    r"APSS.{0,20}clause\s*28",
    r"AP-GO-084",
]


def build_dlp_rerank_prompt(candidates: list[dict]) -> str:
    blocks = []
    for i, c in enumerate(candidates):
        body = smart_truncate(c["full_text"], window=3000,
                               keywords=DLP_TRUNCATE_KEYWORDS)
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
        "Exactly ONE of them (or none) states the DEFECT LIABILITY PERIOD (DLP) — "
        "the duration after work-completion during which the contractor remains "
        "responsible for fixing defects/deficiencies at their own cost. The DLP "
        "is typically expressed in MONTHS (e.g. 24 months, 12 months) or YEARS "
        "(e.g. 2 years, 1 year), and starts from a defined event (Completion / "
        "Provisional Certificate / Taking-Over / Handing-Over).\n\n"
        f"{candidates_block}\n\n"
        "Question: Which candidate states the Defect Liability Period and its "
        "duration? Extract presence + duration in months + start-event + basis.\n\n"
        "Return JSON only:\n"
        "{\n"
        "  \"chosen_index\":         integer 0..N-1 of the DLP candidate, OR null if no candidate states a DLP duration,\n"
        "  \"dlp_clause_present\":   bool,\n"
        "  \"dlp_months\":           integer OR null  (24 for '24 months' or '2 years'; 12 for '12 months' or '1 year'; 6 for '6 months'; null if duration unstated),\n"
        "  \"dlp_starts_from\":      string OR null  (e.g. 'completion of work', 'Provisional Certificate', 'Taking-Over Certificate', 'Handing-Over'),\n"
        "  \"dlp_basis\":            string OR null  (regulatory/contract anchor, e.g. 'AP-GO-084', 'GO Ms No 8 T(R&B) 2003', 'APSS Clause 28', 'GCC §35.3'),\n"
        "  \"evidence\":             \"verbatim quote from the chosen candidate's text identifying the DLP duration\",\n"
        "  \"found\":                bool,\n"
        "  \"reasoning\":            \"one short sentence explaining the choice\"\n"
        "}\n\n"
        "Selection rules — IGNORE the following content (NOT the DLP duration):\n"
        "- PERFORMANCE BANK GUARANTEE validity windows (the BG must remain valid "
        "  beyond DLP, but the BG-validity period is NOT the DLP itself; the DLP "
        "  is what the BG-validity is anchored to).\n"
        "- BID VALIDITY / BID SECURITY / EMD VALIDITY periods (totally different "
        "  shape; bid-stage windows, not contract-execution windows).\n"
        "- OPERATIONS & MAINTENANCE PERIOD for PPP / DBFOT contracts (the O&M "
        "  Period spans the entire Concession Period post-COD; it REPLACES the "
        "  DLP concept rather than instantiating it). Skip these candidates.\n"
        "- LATENT DEFECT PERIOD for EPC contracts (a separate clause beyond "
        "  the DLP — MPW-030; not the DLP itself).\n"
        "- DEFECTS LIABILITY CERTIFICATE issuance procedure (the DLC is issued "
        "  AT THE END of the DLP — the DLC clause references the DLP duration "
        "  by name but doesn't usually state it; pick the clause that states the "
        "  DURATION, not the certificate procedure).\n"
        "- CONTRACT PERIOD / CONTRACTUAL DURATION (the time to complete the work, "
        "  separate from the post-completion defect-liability window).\n"
        "- WARRANTY periods for Goods-only contracts where the manufacturer "
        "  warranty is distinct from the works DLP (CVC-114; not in scope).\n"
        "- OEM AMC consent letters / OEM extended-warranty clauses (these are "
        "  separate from the works DLP).\n"
        "- INSURANCE periods (Contractor's All Risk insurance from Start Date to "
        "  end of DLP — references DLP but doesn't state its duration).\n"
        "- EARNEST MONEY DEPOSIT validity window (e.g. '28 days from expiry of "
        "  DLP') — this references DLP but isn't the DLP.\n"
        "\n"
        "Selection rules — DO pick if the candidate states:\n"
        "- A datasheet row 'Period of Defect Liability Period (DLP) | 24 months' "
        "  or similar tabular statement of the duration.\n"
        "- A clause definition '(v) The Defects Liability Period is the period "
        "  named in the PCC pursuant to Sub-Clause 35.3 and calculated from the "
        "  Completion Date' COMBINED with a PCC/SCC line stating the actual "
        "  duration — pick the PCC/SCC line if available; otherwise the GCC "
        "  reference is acceptable with dlp_months=null.\n"
        "- A bidder declaration 'I/We hereby declare that I am/we are accepting "
        "  for the defect liability period as 24 Months' (Forms-shape).\n"
        "- A scope-of-work line '...including 2 years defect liability period...' "
        "  (Vol-II Specifications-shape).\n"
        "- A regulatory cite 'The defect liability period of contract in terms "
        "  of GO Ms No 8, T(R&B) department dated 08-01-2003 is twenty four "
        "  months after completion of work' — extract dlp_months=24, dlp_basis="
        "  the GO citation.\n"
        "- An override declaration 'as 24 Months instead of 6 months under "
        "  clause 28 of APSS' — Kakinada-shape; extract dlp_months=24 (the "
        "  override governs over the APSS baseline).\n"
        "- Even if the duration is stamped as a PCC/SCC placeholder ('as stated "
        "  in PCC'), if the section EXPLICITLY invokes the DLP framework with a "
        "  GCC reference, treat as dlp_clause_present=true with dlp_months=null "
        "  and capture the framework reference.\n"
        "\n"
        "Number-extraction rules:\n"
        "- Convert years to months: '2 years' → 24, '1 year' → 12, '6 months' "
        "  stays 6.\n"
        "- Word-form numbers: 'twenty-four months' → 24, 'twelve months' → 12.\n"
        "- If the candidate states multiple DLPs (e.g. main works 24 months, "
        "  maintenance works 12 months), extract the MAIN works DLP into "
        "  dlp_months and note the variation in reasoning.\n"
        "- If the doc states an override (e.g. 'instead of 6 months under APSS'), "
        "  the override governs — extract the override duration.\n"
        "\n"
        "- Evidence MUST be an exact substring of the chosen candidate's text.\n"
        "- If no candidate states a DLP duration, set chosen_index=null, "
        "  dlp_clause_present=false, dlp_months=null, found=false.\n"
        "\n"
        "QUOTE FORMAT — STRICT (per L35):\n"
        "- Return a SINGLE CONTIGUOUS quote from ONE sentence or ONE clause only.\n"
        "- Do NOT stitch multiple paragraphs into one quote.\n"
        "- Do NOT add ellipsis ('...') between lines.\n"
        "- Do NOT paraphrase, summarise, or condense — quote EXACTLY as it appears in the supplied text.\n"
        "- Do NOT introduce or remove markdown formatting (asterisks, underscores, italics, backslash-escapes) — preserve the source formatting verbatim.\n"
        "- Pick the SHORTEST contiguous span that proves the duration; one sentence (or one tabular row) is usually enough."
    )


def parse_llm_response(raw: str) -> dict:
    return parse_llm_json(raw)


# ── Rule selection via condition_evaluator ────────────────────────────

def fetch_tender_facts(doc_id: str) -> dict:
    """Build the facts dict that condition_evaluator reads.

    Strict LLM-only sources (per L28). ContractType is NOT extracted
    today — relevant only to MPW-030 which is excluded from RULE_
    CANDIDATES anyway, so the omission doesn't affect rule selection.
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
        # ContractType not extracted; MPW-030 excluded upstream.
        # InstallationByVendor not extracted; CVC-114 SKIPs on Goods.
    }

    ev_cr = p.get("estimated_value_cr")
    if ev_cr is not None:
        try:
            facts["EstimatedValue"] = float(ev_cr) * 1e7   # crores → rupees
            facts["_estimated_value_cr"] = float(ev_cr)
        except (TypeError, ValueError):
            pass

    return facts


def select_dlp_rule(tender_facts: dict) -> dict | None:
    """Pick the highest-priority rule that fires (or fires-as-UNKNOWN
    per L27 downgrade). Returns None only when every rule's condition
    evaluates to SKIP."""
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

    # Defeasibility filter (no rule defeats anything in this typology)
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
          f"shape={chosen['shape']}, threshold={chosen['threshold_months']}mo){note}")
    return chosen


# ── Idempotent re-run cleanup ─────────────────────────────────────────

def _delete_prior_tier1_dlp(doc_id: str) -> tuple[int, int]:
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
    print(f"  Tier-1 DLP-Period-Short (BGE-M3 + LLM, NO regex)")
    print(f"  doc_id : {DOC_ID}")
    print(f"  model  : {LLM_MODEL}")
    print("=" * 76)

    n_f, n_e = _delete_prior_tier1_dlp(DOC_ID)
    if n_f or n_e:
        print(f"  cleared {n_f} prior Tier-1 DLP finding node(s) and "
              f"{n_e} edge(s) before re-running")

    # 1. Pick rule via condition_evaluator
    facts = fetch_tender_facts(DOC_ID)
    rule  = select_dlp_rule(facts)
    if rule is None:
        return 0

    # 2. Family + section_type filter (router)
    family, section_types = family_for_doc_with_filter(DOC_ID, TYPOLOGY)
    print(f"\n── Document family / retrieval filter ──")
    print(f"  family         : {family}")
    print(f"  section_types  : {section_types}")

    # 3. BGE-M3 embed (dual query — framework + value)
    print(f"\n── Query 1/2 (framework, answer-shaped) ──")
    print(f"  ({len(QUERY_FRAMEWORK)} chars) {QUERY_FRAMEWORK}")
    print(f"\n── Query 2/2 (value, answer-shaped) ──")
    print(f"  ({len(QUERY_VALUE)} chars) {QUERY_VALUE}")
    t0 = time.perf_counter()
    qvec_fw = embed_query(QUERY_FRAMEWORK)
    qvec_val = embed_query(QUERY_VALUE)
    timings["embed"] = time.perf_counter() - t0
    print(f"\n── Step 1: BGE-M3 embed (×2) ── "
          f"vec_dim={len(qvec_fw)}  wall={timings['embed']:.2f}s")

    # 4. Per-section-type quota retrieval per L49.
    # Single-query / dual-query top-K both consistently return all-GCC
    # candidates on this typology (NIT datasheet rows and Forms bidder
    # declarations score 0.44-0.49 cosine, while compact GCC clauses
    # score 0.55-0.69). Without explicit quotas, the LLM never sees
    # the value-stating sections and the threshold compare degenerates
    # to "framework present, by-reference, default compliant".
    #
    # Per-section-type retrieval: take top-K_FW from GCC-style filter
    # using QUERY_FRAMEWORK, plus top-K_VAL from each non-GCC type
    # using QUERY_VALUE. Merge by Qdrant point id, dedupe, return top
    # K_MERGED to LLM.
    K_FW     = 5   # GCC framework candidates
    K_VAL    = 3   # value-stating candidates per non-GCC type
    K_MERGED = 12  # post-merge cap fed into the LLM rerank
    t0 = time.perf_counter()

    points_fw: list[dict] = []
    if "GCC" in section_types:
        try:
            points_fw = qdrant_topk(qvec_fw, DOC_ID, k=K_FW, section_types=["GCC"])
        except RuntimeError:
            # SBD_Format docs (e.g. Kakinada) have n_gcc=0 — no GCC
            # sections in Qdrant. The value pool below picks up the
            # slack from Evaluation / Forms / NIT.
            points_fw = []

    value_types = [t for t in section_types if t != "GCC"]
    points_val: list[dict] = []
    val_breakdown: list[tuple[str, int]] = []
    for st in value_types:
        try:
            pts = qdrant_topk(qvec_val, DOC_ID, k=K_VAL, section_types=[st])
            points_val.extend(pts)
            val_breakdown.append((st, len(pts)))
        except RuntimeError:
            # No points for this section_type on this doc — skip.
            val_breakdown.append((st, 0))

    # Merge by Qdrant point id, keep max cosine (best lens wins).
    by_id: dict = {}
    for p in points_fw + points_val:
        pid = p["id"]
        if pid not in by_id or p["score"] > by_id[pid]["score"]:
            by_id[pid] = p
    merged = sorted(by_id.values(), key=lambda x: x["score"], reverse=True)
    points = merged[:K_MERGED]
    K = len(points)
    timings["qdrant"] = time.perf_counter() - t0

    val_str = ", ".join(f"{t}:{n}" for t, n in val_breakdown) or "(none)"
    print(f"\n── Step 2: per-section-type quota retrieval "
          f"(family={family}) ──")
    print(f"  {len(points_fw)} GCC framework (top-{K_FW}) + "
          f"value pool [{val_str}] (top-{K_VAL} each) → "
          f"{len(merged)} merged → top-{K} fed to LLM "
          f"(in {timings['qdrant']*1000:.0f}ms total):")
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
    print(f"\n── Step 3: LLM rerank + DLP extraction ──")
    user_prompt = build_dlp_rerank_prompt(candidates)
    print(f"  prompt size: {len(user_prompt)} chars (~{len(user_prompt)//4} tokens)")
    raw_content = call_llm(LLM_SYSTEM, user_prompt, max_tokens=900)
    timings["llm"] = time.perf_counter() - t0
    print(f"  wall: {timings['llm']:.2f}s")
    print(f"\n── Raw LLM JSON ──")
    print(raw_content)

    # 7. Parse + select chosen candidate
    parsed = parse_llm_response(raw_content)
    chosen          = parsed.get("chosen_index")
    dlp_present     = bool(parsed.get("dlp_clause_present"))
    dlp_months      = parsed.get("dlp_months")
    dlp_starts_from = parsed.get("dlp_starts_from")
    dlp_basis       = parsed.get("dlp_basis")
    evidence        = (parsed.get("evidence") or "").strip()
    found           = bool(parsed.get("found"))
    reason          = (parsed.get("reasoning") or "").strip()

    print(f"\n── Parsed ──")
    print(f"  chosen_index        : {chosen}")
    print(f"  found               : {found}")
    print(f"  dlp_clause_present  : {dlp_present}")
    print(f"  dlp_months          : {dlp_months}")
    print(f"  dlp_starts_from     : {dlp_starts_from!r}")
    print(f"  dlp_basis           : {dlp_basis!r}")
    print(f"  reasoning           : {reason[:200]}")
    print(f"  evidence            : {evidence[:300]!r}")

    section = None
    similarity = None
    ev_passed = False
    ev_score = 0
    ev_method = "skipped"

    llm_found_clause   = dlp_present and (chosen is not None)
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
        if dlp_present:
            print(f"  ⚠ dlp_present=True but chosen_index=null — treating as False")
            dlp_present = False
            llm_found_clause = False

    # 8. Threshold check + grep fallback
    threshold_months = int(rule["threshold_months"])

    is_compliant_l24 = llm_found_clause and ev_passed
    is_unverified_l24 = llm_found_clause and not ev_passed
    raw_is_absence    = not llm_found_clause

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

    is_absence    = raw_is_absence and not grep_promoted_to_unverified and not full_grep_promoted
    is_unverified = is_unverified_l24 or grep_promoted_to_unverified or full_grep_promoted

    # Threshold compare on the L24-verified-present path
    is_threshold_short = False
    if is_compliant_l24:
        if dlp_months is None:
            # Framework invoked but no months stated. Per the prompt
            # contract, the LLM should set dlp_months=null when the
            # value is by-reference (PCC/SCC placeholder). Treat as
            # silent compliant — would need PCC/SCC verification to
            # escalate, which is execution-stage.
            reason_label = "compliant_clause_present_no_months_stated"
        elif int(dlp_months) < threshold_months:
            is_threshold_short = True
            reason_label = (f"dlp_{int(dlp_months)}mo_below_threshold_"
                            f"{threshold_months}mo")
        else:
            reason_label = (f"compliant_dlp_{int(dlp_months)}mo_meets_threshold_"
                            f"{threshold_months}mo")
    elif grep_promoted_to_unverified:
        reason_label = "dlp_unverified_grep_fallback_retrieval_gap"
    elif full_grep_promoted:
        reason_label = ("dlp_unverified_kg_coverage_gap"
                        if kg_coverage_gap
                        else "dlp_unverified_whole_file_grep_only")
    elif is_unverified_l24:
        reason_label = "dlp_unverified_llm_quote_failed_l24"
    else:
        # Absence + grep fallbacks both empty.
        reason_label = "dlp_clause_absent_violation"

    print(f"\n── Decision ──")
    print(f"  rule              : {rule['rule_id']} ({rule['severity']}, threshold={threshold_months}mo)")
    print(f"  llm_found_clause  : {llm_found_clause}")
    print(f"  ev_passed         : {ev_passed}  (score={ev_score}, method={ev_method})")
    print(f"  dlp_months        : {dlp_months}")
    print(f"  raw_is_absence    : {raw_is_absence}")
    print(f"  grep_fallback_hit : {grep_promoted_to_unverified or full_grep_promoted}")
    print(f"  is_compliant_l24  : {is_compliant_l24}")
    print(f"  is_threshold_short: {is_threshold_short}")
    print(f"  is_unverified     : {is_unverified}")
    print(f"  is_absence        : {is_absence}")
    print(f"  reason_label      : {reason_label}")

    # COMPLIANT (rule fires AND L24 passes AND threshold met) → silent.
    # No row, no edge — per L48 (portal infers COMPLIANT from absence
    # of any other state row).
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

    # 9. Materialise finding (THRESHOLD-SHORT, UNVERIFIED, or GAP_VIOLATION).
    # VIOLATES_RULE edge is emitted on THRESHOLD-SHORT and on
    # GAP_VIOLATION (is_absence). UNVERIFIED gets row but no edge.
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
        evidence_out  = (f"Defect Liability Period clause not found in document "
                         f"after BGE-M3 retrieval, L36 Section-bounded grep, and "
                         f"L40 whole-file grep across {', '.join(section_types)}. "
                         f"Per AP-GO-084 (WARNING), AP Works/EPC contracts must "
                         f"state the DLP duration (regulated at 24 months / 2 "
                         f"years for both original works and maintenance works).")
        print(f"  → GAP_VIOLATION finding — LLM rerank empty AND grep fallbacks "
              f"empty; genuine absence")
    elif grep_promoted_to_unverified:
        ev_passed_out = None
        ev_score_out  = None
        ev_method_out = "grep_fallback_retrieval_gap"
        evidence_out  = (f"LLM rerank top-{K} returned no DLP candidate, but "
                         f"exhaustive grep across {', '.join(section_types)} "
                         f"found keyword hits in {len(grep_hits)} section(s). "
                         f"First match: "
                         f"{grep_hits[0]['snippet'][:160] if grep_hits else 'n/a'}")
        print(f"  → UNVERIFIED finding (L36 grep fallback) — retrieval missed "
              f"{len(grep_hits)} section(s) with DLP keywords")
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
        print(f"  → UNVERIFIED finding (L40 whole-file fallback)")
    elif is_unverified_l24:
        ev_passed_out = ev_passed
        ev_score_out  = ev_score
        ev_method_out = ev_method
        evidence_out  = evidence
        print(f"  → UNVERIFIED finding — LLM identified DLP signal but quote "
              f"failed L24 (score={ev_score}, method={ev_method})")
    else:
        # THRESHOLD-SHORT path
        ev_passed_out = ev_passed
        ev_score_out  = ev_score
        ev_method_out = ev_method
        evidence_out  = evidence
        print(f"  → THRESHOLD-SHORT finding — DLP {dlp_months}mo < "
              f"threshold {threshold_months}mo; emitting WARNING row + edge")

    rule_node_id = get_or_create_rule_node(DOC_ID, rule["rule_id"])

    if is_threshold_short:
        label = (
            f"{TYPOLOGY}: DLP {int(dlp_months)} months below threshold "
            f"{threshold_months} months — {rule['rule_id']} ({rule['severity']}) "
            f"requires DLP ≥ {threshold_months}mo for AP Works/EPC"
        )
    elif is_absence:
        label = (
            f"{TYPOLOGY}: Defect Liability Period clause absent — "
            f"{rule['rule_id']} ({rule['severity']}) requires AP Works/EPC "
            f"contracts to state DLP ≥ {threshold_months}mo"
        )
    elif grep_promoted_to_unverified:
        label = (
            f"{TYPOLOGY}: UNVERIFIED (L36 grep-fallback) — LLM rerank "
            f"missed the DLP clause; exhaustive grep found {len(grep_hits)} "
            f"section(s) with DLP keyword hits; requires human review"
        )
    elif full_grep_promoted:
        label = (
            f"{TYPOLOGY}: UNVERIFIED (L40 whole-file fallback) — "
            f"{'KG-coverage gap' if kg_coverage_gap else 'whole-file only'}; "
            f"{len(full_grep_hits)} match line(s)"
        )
    else:
        label = (
            f"{TYPOLOGY}: UNVERIFIED — LLM found DLP clause but quote "
            f"failed L24 (score={ev_score}, method={ev_method}); requires "
            f"human review against "
            f"{(section['heading'][:60] if section else 'TenderDocument')!r}"
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
        "extraction_path":       "threshold",
        "llm_found_clause":      llm_found_clause,
        # Multi-field DLP extraction snapshot
        "dlp_clause_present":    llm_found_clause,
        "dlp_months":            (int(dlp_months) if dlp_months is not None else None),
        "dlp_threshold_months":  threshold_months,
        "dlp_starts_from":       dlp_starts_from,
        "dlp_basis":             dlp_basis,
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
            f"L36 grep fallback fired: LLM rerank top-{K} did not surface DLP, "
            f"but exhaustive grep across {section_types} found keyword hits in "
            f"{len(grep_hits)} section(s). Reviewer should open the listed "
            f"sections in grep_audit.hits and confirm the DLP clause."
            if grep_promoted_to_unverified else
            f"L40 whole-file fallback: LLM rerank AND Section-bounded grep both "
            f"empty, but whole-file grep found {len(full_grep_hits)} match "
            f"line(s). "
            f"{'kg_coverage_gap=TRUE — reviewer should re-run kg_builder.' if kg_coverage_gap else 'Whole-file only hits — reviewer should verify retrieval coverage.'}"
            if full_grep_promoted else
            f"LLM identified DLP signal but quote failed L24 (score={ev_score}, "
            f"method={ev_method}). Reviewer should open the section above and confirm."
            if is_unverified_l24 else None
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
        "source_ref": f"tier1:dlp_check:{rule['rule_id']}",
    }])[0]

    edge = None
    if is_threshold_short or is_absence:
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
                "dlp_clause_present":   llm_found_clause,
                "dlp_months":           (int(dlp_months) if dlp_months is not None else None),
                "dlp_threshold_months": threshold_months,
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
