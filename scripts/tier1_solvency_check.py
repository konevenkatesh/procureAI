"""
scripts/tier1_solvency_check.py

Tier-1 Solvency-Stale check, BGE-M3 + LLM, NO regex.

PRESENCE shape with multi-field framework extraction. Per the read-
first scan:

    AP-GO-089  TenderState=AP AND TenderType IN [Works, EPC]
               HARD_BLOCK — Solvency Certificate fixed at 10% of class
               minimum, issued by Tahsildar (Revenue Dept officer not
               below Tahsildar rank) OR Scheduled Bank, valid for ONE
               YEAR from date of issue.
    AP-GO-103  same when                                     WARNING
               Prescribed proforma — Tahsildar Annexure V(a) OR
               Scheduled Bank Annexure V(b); free-form NOT acceptable.
    AP-GO-106  same when                                     HARD_BLOCK
               Partnership-change protocol — fresh certificate within
               1 month of substitution. Execution-stage in practice;
               not a typical doc-content check.
    MPW25-028  TenderType=Works AND PQB=true                 HARD_BLOCK
               PQ Financial Soundness — liquid assets / cash flow.
               COMPLIANT in all 4 AP Works docs (each cites the
               liquid-assets/credit-facility framework). Audited but
               not the firing rule.

The typology name suggests an execution-stage staleness check ("is
the bidder's submitted certificate stale?"). The rules under this
code are actually doc-content prescriptions: the bidding doc must
state the regulated solvency framework (Tahsildar OR Bank, 1-year
validity, threshold amount). Same shape as MII's DPIIT Order
"the bidding doc must state the framework even though the order
applies regardless of citation".

**AP-GO-089 (HARD_BLOCK) is the primary firing rule** on the 4 AP
Works docs. Tirupathi/Vijayawada PPPs SKIP at the rule layer.

Pipeline mirrors L49 DLP (per-section-type quota retrieval needed
because Forms-housed Annexure V proformas score below dense PQ-
table NIT/Evaluation candidates):
  1. Pick rule via condition_evaluator.
  2. Section filter via SOLVENCY_SECTION_ROUTER.
  3. BGE-M3 dual queries (framework + value).
  4. Per-section-type quota retrieval (K_FW=4 from NIT/Evaluation
     framework, K_VAL=3 from each remaining type to surface Forms
     proformas).
  5. LLM rerank with Solvency-specific ignore rules (EMD/PBG bank
     guarantees, contract-period validity, partnership-change
     protocol — separate concerns).
  6. L24 evidence-guard hallucination check.
  7. L36/L40 grep fallback for absence path.
  8. Decision tree per the user-confirmed contract:
        COMPLIANT  if (bank_option OR tahsildar_option)
                      AND validity_one_year_stated
                   → silent (no row).
        GAP_VIOLATION if neither option present OR validity absent
                   → row + VIOLATES_RULE edge.
        UNVERIFIED if L24 fails OR grep-promoted absence.

Tested on judicial_academy_exp_001 first (expected: GAP_VIOLATION
HARD_BLOCK — APCRDA Works template drops Tahsildar option and
silently omits the 1-year validity rule).
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

TYPOLOGY = "Solvency-Stale"

QDRANT_URL  = os.environ.get("QDRANT_URL", "http://localhost:6333")
COLLECTION  = "tender_sections"

LLM_MODEL = os.environ.get("LLM_MODEL", "qwen/qwen-2.5-72b-instruct")


# L36/L40 source-grep fallback vocabulary. User-supplied at typology-20
# build time. Includes both the regulated terminology (Tahsildar /
# Annexure V / GO MS Nos) and the framework variants seen in the
# corpus (CA Net Worth certificate, "not earlier than ONE YEAR").
GREP_FALLBACK_KEYWORDS = [
    "solvency certificate",
    "solvency",
    "tahsildar",
    "nationalised bank",
    "nationalized bank",
    "scheduled bank",
    "mandal revenue",
    "one year",
    "validity",
    "annexure v",
    "GO MS No 129",
    "GO MS No 63",
]


# Dual-query retrieval per L49. Single-query top-K consistently
# surfaces only the dense PQ Financial Capabilities tables (NIT /
# Evaluation) and misses the Forms-housed Annexure V proformas
# (Mandal Revenue Officer form L3225 + Bank form L3239 in Kakinada
# — diluted across long form blocks).
QUERY_FRAMEWORK = (
    "Solvency Certificate Nationalised Scheduled Bank Tahsildar "
    "Mandal Revenue Officer Liquid assets credit facility certificate "
    "issued not older than one year from date of issue valid"
)
QUERY_VALUE = (
    "Form of Solvency Certificate Annexure V Bank proforma Mandal "
    "Revenue Officer Tahsildar GO MS No 129 GO MS No 63 partnership "
    "change ten percent monetary limit class registration"
)
QUERY_TEXT = QUERY_VALUE   # banner-only alias


# Rule candidates evaluated via condition_evaluator. AP-GO-089 is the
# primary HARD_BLOCK; AP-GO-103 (proforma WARNING) and AP-GO-106
# (partnership-change protocol HARD_BLOCK) excluded from the
# RULE_CANDIDATES list because they're either covered by AP-GO-089's
# wider framework check (proforma) or execution-stage (partnership).
# MPW25-028 is COMPLIANT in all 4 AP Works docs (each cites the
# liquid-assets / credit-facility framework) and excluded to avoid
# double-firing.
RULE_CANDIDATES = [
    {
        "rule_id":          "AP-GO-089",
        "natural_language": "Solvency Certificate for AP contractor registration fixed at 10% of minimum monetary limit of registered class — issued by Tahsildar OR Scheduled Bank, valid 1 year from date of issue",
        "severity":         "HARD_BLOCK",
        "layer":            "AP-State",
        "shape":            "presence_multi_field",
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


# ── LLM rerank prompt for Solvency framework ─────────────────────────

LLM_SYSTEM = (
    "You are a precise procurement-document fact extractor. "
    "Return JSON only. Quote evidence verbatim from the supplied text. "
    "Never fabricate."
)


# Keyword vocabulary for smart_truncate windowing (Solvency-specific).
SOLVENCY_TRUNCATE_KEYWORDS = [
    r"solvency.{0,20}certificate",
    r"\bsolvency\b",
    r"tahsildar",
    r"mandal\s+revenue\s+officer",
    r"nationalis\w+\s+bank",
    r"scheduled\s+bank",
    r"annexure\s+V",
    r"liquid\s+assets",
    r"credit\s+facilit",
    r"net\s+worth",
    r"\bone\s+year\b",
    r"not\s+older\s+than\s+one\s+year",
    r"not\s+earlier\s+than\s+ONE\s+YEAR",
    r"GO\s*MS\s*No\.?\s*129",
    r"GO\s*MS\s*No\.?\s*63",
    r"GO\s*MS\s*No\.?\s*94",
    r"10\s*%.{0,40}monetary",
    r"ten\s+percent.{0,40}monetary",
]


def build_solvency_rerank_prompt(candidates: list[dict]) -> str:
    blocks = []
    for i, c in enumerate(candidates):
        body = smart_truncate(c["full_text"], window=3000,
                               keywords=SOLVENCY_TRUNCATE_KEYWORDS)
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
        "Extract the bidding document's SOLVENCY CERTIFICATE framework — the "
        "regulated requirement that bidders submit a solvency certificate at "
        "PQ stage to demonstrate financial capacity. Per AP-GO-089, the regulated "
        "framework is: certificate = 10% of minimum monetary limit of registered "
        "class; issued by Tahsildar (Revenue Dept officer not below Tahsildar "
        "rank) OR Scheduled Bank; valid 1 YEAR from date of issue.\n\n"
        f"{candidates_block}\n\n"
        "Question: Across ALL candidates above, what does the bidding doc say "
        "about the solvency-certificate framework? Pick the SINGLE BEST candidate "
        "(or null) for the verbatim evidence quote, but evaluate the boolean "
        "extractions against the FULL evidence visible in any candidate.\n\n"
        "Return JSON only:\n"
        "{\n"
        "  \"chosen_index\":               integer 0..N-1 of the best evidence-quote candidate, OR null if no candidate states the solvency framework,\n"
        "  \"tahsildar_option_present\":   bool   (TRUE if the doc explicitly accepts solvency certificates issued by 'Tahsildar' / 'Mandal Revenue Officer' / 'Officer of the Revenue Department of rank not below Tahsildar'),\n"
        "  \"bank_option_present\":        bool   (TRUE if the doc explicitly accepts solvency certificates from 'Nationalised Bank' / 'Scheduled Bank' / 'Schedule Commercial Bank' / 'Indian Nationalised Banks'),\n"
        "  \"validity_one_year_stated\":   bool   (TRUE if the doc states the certificate must be 'not older than 1 year' / 'not earlier than ONE YEAR' / 'valid 1 year from date of issue' / equivalent),\n"
        "  \"annexure_va_form_attached\":  bool   (TRUE if the doc includes a proforma titled 'FORM OF SOLVENCY CERTIFICATES BY MANDAL REVENUE OFFICER' / 'Annexure V(a)' / Tahsildar-side form),\n"
        "  \"annexure_vb_form_attached\":  bool   (TRUE if the doc includes a proforma titled 'FORM OF SOLVENCY CERTIFICATE BY BANKS' / 'Annexure V(b)' / Bank-side form),\n"
        "  \"solvency_threshold_amount\":  string OR null  (e.g. 'Rs.20.92 Cr.', 'Rs.73 Cr.', 'INR18,37,70,608', or whatever the doc states; null if no monetary threshold is given),\n"
        "  \"regulatory_citation\":        string OR null  (e.g. 'GO MS No 129 dt 05-10-2015', 'GO MS No 63 dt 13-11-2025', 'AP-GO-089', or null if no GO/regulation cited),\n"
        "  \"evidence\":                   \"verbatim quote from the chosen candidate's text identifying the solvency-certificate framework\",\n"
        "  \"found\":                      bool,\n"
        "  \"reasoning\":                  \"one short sentence explaining the choice\"\n"
        "}\n\n"
        "Selection rules — IGNORE the following content (NOT a solvency-certificate framework):\n"
        "- EMD / Bid Security / Earnest Money Deposit Bank Guarantees (a separate "
        "  bid-stage instrument; references 'Scheduled Bank' but NOT solvency).\n"
        "- PERFORMANCE BANK GUARANTEE / Performance Security clauses (post-award; "
        "  references 'Nationalised/Scheduled Bank' but NOT solvency).\n"
        "- LETTER OF CREDIT / payment-security instruments in PPP DCAs (Authority-"
        "  to-Concessionaire LC; not bidder solvency).\n"
        "- INSURANCE clauses (CAR/CWR insurance from Nationalised banks; separate).\n"
        "- BG VALIDITY / Bid Validity windows (separate threshold typology).\n"
        "- TURNOVER PQ requirements (separate financial-capacity instrument; "
        "  turnover is annual revenue, solvency is current-period liquid worth).\n"
        "- PARTNERSHIP-CHANGE protocol clauses (AP-GO-106 — execution-stage; not "
        "  the solvency-framework prescription).\n"
        "- INSOLVENCY / Bankruptcy disqualification clauses (Insolvency and "
        "  Bankruptcy Code 2016 references; eligibility-side, not solvency-cert).\n"
        "\n"
        "Selection rules — DO pick if the candidate states:\n"
        "- A PQ Financial Capabilities table row 'Liquid assets / credit "
        "  facilities / Solvency certificates from any Nationalised/Scheduled "
        "  Bank for not less than Rs.X Cr.' (NIT-shape).\n"
        "- A solvency-certificate paragraph 'Solvency Certificate from any Indian "
        "  Nationalised / Scheduled Banks ... and certificate not older than 1 "
        "  year from Banks' (Vizag Vol-I shape).\n"
        "- A multi-option clause 'Solvency certificates from Nationalised/"
        "  Scheduled Commercial Banks in the prescribed proforma OR Solvency "
        "  Certificate Obtained from the Officer of the Revenue Department of "
        "  the rank not below the Tahsildar / Net worth certificate issued by "
        "  Charted Account as per GO MS No 129 dt 05-10-2015' (Kakinada SBD shape).\n"
        "- A proforma form titled 'FORM OF SOLVENCY CERTIFICATES BY MANDAL "
        "  REVENUE OFFICER' or 'FORM OF SOLVENCY CERTIFICATE BY BANKS' "
        "  (Forms-shape Annexure V).\n"
        "\n"
        "Boolean-extraction rules:\n"
        "- tahsildar_option_present requires an EXPLICIT mention. 'Mandal "
        "  Revenue Officer', 'Officer of Revenue Department of rank not below "
        "  Tahsildar', 'Tahsildar' all count. Generic 'Government officer' or "
        "  'Authorised authority' does NOT count.\n"
        "- bank_option_present requires 'Nationalised Bank' / 'Scheduled Bank' / "
        "  'Schedule Commercial Bank' / 'Bank' in the solvency context. "
        "  References to 'Bank' in EMD/PBG/Insurance contexts do NOT count.\n"
        "- validity_one_year_stated requires an EXPLICIT statement that the "
        "  certificate must not be older than 1 year. 'within last 12 months', "
        "  'not earlier than ONE YEAR from last date of submission', 'certificate "
        "  not older than 1 year from Banks', 'valid for one year from date of "
        "  issue' all count. Generic 'current' or 'recent' does NOT count.\n"
        "- annexure_va_form_attached / annexure_vb_form_attached: TRUE only if "
        "  the doc actually includes the FORM templates (heading 'FORM OF "
        "  SOLVENCY CERTIFICATE...' visible in candidate text). Mere references "
        "  ('as per Annexure V(b)') without the form text itself do NOT count.\n"
        "\n"
        "Choose ONE evidence quote that best demonstrates the strongest signal "
        "found across candidates (prefer the multi-option clause that covers "
        "Tahsildar + Bank + 1-year-validity if it exists; else the strongest "
        "available signal).\n"
        "\n"
        "- Evidence MUST be an exact substring of the chosen candidate's text.\n"
        "- If no candidate states a solvency-certificate framework, set "
        "  chosen_index=null, all booleans=false, found=false.\n"
        "\n"
        "QUOTE FORMAT — STRICT (per L35):\n"
        "- Return a SINGLE CONTIGUOUS quote from ONE sentence or ONE clause only.\n"
        "- Do NOT stitch multiple paragraphs into one quote.\n"
        "- Do NOT add ellipsis ('...') between lines.\n"
        "- Do NOT paraphrase, summarise, or condense — quote EXACTLY as it appears in the supplied text.\n"
        "- Do NOT introduce or remove markdown formatting (asterisks, underscores, italics, backslash-escapes) — preserve the source formatting verbatim.\n"
        "- Pick the SHORTEST contiguous span that proves the framework signals; one sentence or one row is usually enough."
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


def select_solvency_rule(tender_facts: dict) -> dict | None:
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

def _delete_prior_tier1_solvency(doc_id: str) -> tuple[int, int]:
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
    print(f"  Tier-1 Solvency-Stale (BGE-M3 + LLM, NO regex)")
    print(f"  doc_id : {DOC_ID}")
    print(f"  model  : {LLM_MODEL}")
    print("=" * 76)

    n_f, n_e = _delete_prior_tier1_solvency(DOC_ID)
    if n_f or n_e:
        print(f"  cleared {n_f} prior Tier-1 Solvency finding node(s) and "
              f"{n_e} edge(s) before re-running")

    facts = fetch_tender_facts(DOC_ID)
    rule  = select_solvency_rule(facts)
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

    # Per-section-type quota retrieval per L49 + grep-seeded
    # supplements per L50.
    #
    # Solvency content is sparse (one PQ row + one Forms proforma per
    # doc) but lives inside long sections with misleading headings:
    # JA's solvency PQ row at L678 sits in ITB L618-737 "SETTLEMENT OF
    # CLAIMS (part 1)" — rank 7 by BGE-M3 cosine. K_VAL=3 misses it.
    # Bumping K_VAL to surface rank-7 bloats the prompt; instead we
    # grep for the literal "solvency" keyword (extremely specific —
    # near-zero false positives unlike "scheduled bank") and add any
    # matching sections directly to the candidate pool. The LLM then
    # sees both the BGE-M3 cosine top-K and the explicit solvency-
    # bearing sections.
    K_FW     = 4
    K_VAL    = 3
    K_MERGED = 14   # +2 slots for grep-seeded sections
    t0 = time.perf_counter()

    # Framework lens: PQ-table-style sections (NIT / Evaluation when available).
    fw_filter = [t for t in section_types if t in ("NIT", "Evaluation")]
    if not fw_filter:
        fw_filter = section_types[:1]   # safety fallback
    points_fw: list[dict] = []
    try:
        points_fw = qdrant_topk(qvec_fw, DOC_ID, k=K_FW, section_types=fw_filter)
    except RuntimeError:
        points_fw = []

    # Value lens: per-section-type top-K_VAL across the remaining types
    # (Forms in particular — Annexure V proformas).
    points_val: list[dict] = []
    val_breakdown: list[tuple[str, int]] = []
    for st in section_types:
        try:
            pts = qdrant_topk(qvec_val, DOC_ID, k=K_VAL, section_types=[st])
            points_val.extend(pts)
            val_breakdown.append((st, len(pts)))
        except RuntimeError:
            val_breakdown.append((st, 0))

    # Merge cosine candidates, dedupe, max-cosine wins.
    by_id: dict = {}
    for p in points_fw + points_val:
        pid = p["id"]
        if pid not in by_id or p["score"] > by_id[pid]["score"]:
            by_id[pid] = p

    # L50: grep-seeded supplement. Run a tight grep for the literal
    # "solvency" keyword (extremely specific) and find any sections
    # carrying it that aren't already in the cosine-merged pool.
    # Preserve their cosines (reuse from BGE-M3 if available, else
    # mark as 0.0 grep-seeded).
    SEED_KEYWORDS = ["solvency"]
    _, seed_hits = grep_source_for_keywords(
        DOC_ID, section_types, SEED_KEYWORDS,
    )
    seeded_section_ids = {h["section_node_id"] for h in seed_hits}
    n_seeded_added = 0
    if seeded_section_ids:
        # Look up Qdrant points for these section_node_ids by scanning
        # all section_types' top-50 so we have payload + cosine. Faster:
        # query Qdrant with a "must" filter on section_id.
        for sid in seeded_section_ids:
            # Skip if already in candidate pool by Qdrant point id.
            already_in = any(
                (p["payload"].get("section_id") == sid) for p in by_id.values()
            )
            if already_in:
                continue
            # Fetch payload from kg_nodes to construct a candidate dict
            # mirroring the Qdrant point shape.
            sec_rows = rest_get("kg_nodes", {
                "select":  "node_id,properties",
                "node_id": f"eq.{sid}",
            })
            if not sec_rows:
                continue
            mp = sec_rows[0].get("properties") or {}
            # Use a small synthetic point id (string keyed by node_id)
            # and zero cosine so the candidate ranks at the bottom
            # but is included.
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
    print(f"  L50 grep-seeded ['solvency'] → "
          f"{len(seeded_section_ids)} matching section(s), "
          f"{n_seeded_added} new (deduped from cosine pool)")
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
    print(f"\n── Step 3: LLM rerank + Solvency multi-field extraction ──")
    user_prompt = build_solvency_rerank_prompt(candidates)
    print(f"  prompt size: {len(user_prompt)} chars (~{len(user_prompt)//4} tokens)")
    raw_content = call_llm(LLM_SYSTEM, user_prompt, max_tokens=900)
    timings["llm"] = time.perf_counter() - t0
    print(f"  wall: {timings['llm']:.2f}s")
    print(f"\n── Raw LLM JSON ──")
    print(raw_content)

    parsed = parse_llm_response(raw_content)
    chosen          = parsed.get("chosen_index")
    tahsildar_opt   = bool(parsed.get("tahsildar_option_present"))
    bank_opt        = bool(parsed.get("bank_option_present"))
    validity_1yr    = bool(parsed.get("validity_one_year_stated"))
    annexure_va     = bool(parsed.get("annexure_va_form_attached"))
    annexure_vb     = bool(parsed.get("annexure_vb_form_attached"))
    threshold_amt   = parsed.get("solvency_threshold_amount")
    reg_citation    = parsed.get("regulatory_citation")
    evidence        = (parsed.get("evidence") or "").strip()
    found           = bool(parsed.get("found"))
    reason          = (parsed.get("reasoning") or "").strip()

    print(f"\n── Parsed ──")
    print(f"  chosen_index                 : {chosen}")
    print(f"  found                        : {found}")
    print(f"  tahsildar_option_present     : {tahsildar_opt}")
    print(f"  bank_option_present          : {bank_opt}")
    print(f"  validity_one_year_stated     : {validity_1yr}")
    print(f"  annexure_va_form_attached    : {annexure_va}")
    print(f"  annexure_vb_form_attached    : {annexure_vb}")
    print(f"  solvency_threshold_amount    : {threshold_amt!r}")
    print(f"  regulatory_citation          : {reg_citation!r}")
    print(f"  reasoning                    : {reason[:200]}")
    print(f"  evidence                     : {evidence[:300]!r}")

    section = None
    similarity = None
    ev_passed = False
    ev_score = 0
    ev_method = "skipped"

    # Treat as "LLM found framework signal" if any of the booleans
    # is true OR a chosen_index is given. The boolean booleans drive
    # the COMPLIANT/GAP_VIOLATION decision; the chosen_index drives
    # the L24 evidence quote check.
    llm_found_signal = (chosen is not None) and (
        tahsildar_opt or bank_opt or validity_1yr or annexure_va or annexure_vb
    )
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

    # Decision tree per the user-confirmed contract:
    #   COMPLIANT  if (bank OR tahsildar) AND validity_one_year_stated
    #   GAP_VIOLATION if neither option present OR validity rule absent
    has_issuer_option   = tahsildar_opt or bank_opt
    has_validity_rule   = validity_1yr
    framework_compliant = has_issuer_option and has_validity_rule

    is_compliant_l24  = framework_compliant and llm_chose_candidate and ev_passed
    is_unverified_l24 = llm_chose_candidate and (not ev_passed) and llm_found_signal
    raw_is_absence    = (not llm_found_signal)
    is_gap_violation_pre_grep = (
        llm_chose_candidate and ev_passed and (not framework_compliant)
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
    is_gap_violation = is_gap_violation_pre_grep or is_absence

    if is_compliant_l24:
        reason_label = (
            f"compliant_solvency_framework_present_"
            f"tahsildar={tahsildar_opt}_bank={bank_opt}_validity_1yr={validity_1yr}"
        )
    elif is_gap_violation_pre_grep:
        # Framework signal found but at least one required element missing.
        missing = []
        if not has_issuer_option: missing.append("no_issuer_option")
        if not has_validity_rule: missing.append("no_validity_rule_stated")
        reason_label = "solvency_framework_incomplete_" + "_".join(missing)
    elif is_absence:
        reason_label = "solvency_framework_absent"
    elif grep_promoted_to_unverified:
        reason_label = "solvency_unverified_grep_fallback_retrieval_gap"
    elif full_grep_promoted:
        reason_label = ("solvency_unverified_kg_coverage_gap"
                        if kg_coverage_gap
                        else "solvency_unverified_whole_file_grep_only")
    elif is_unverified_l24:
        reason_label = "solvency_unverified_llm_quote_failed_l24"
    else:
        reason_label = "solvency_indeterminate"

    print(f"\n── Decision ──")
    print(f"  rule              : {rule['rule_id']} ({rule['severity']}, shape={rule['shape']})")
    print(f"  llm_found_signal  : {llm_found_signal}")
    print(f"  ev_passed         : {ev_passed}  (score={ev_score}, method={ev_method})")
    print(f"  has_issuer_option : {has_issuer_option}  (tahsildar={tahsildar_opt}, bank={bank_opt})")
    print(f"  has_validity_rule : {has_validity_rule}")
    print(f"  framework_compliant: {framework_compliant}")
    print(f"  is_compliant_l24  : {is_compliant_l24}")
    print(f"  is_gap_violation  : {is_gap_violation}")
    print(f"  is_unverified     : {is_unverified}")
    print(f"  is_absence        : {is_absence}")
    print(f"  reason_label      : {reason_label}")

    # COMPLIANT (silent)
    if is_compliant_l24:
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

    if is_absence:
        ev_passed_out = None
        ev_score_out  = None
        ev_method_out = "absence_finding_no_evidence"
        evidence_out  = (f"Solvency Certificate framework not found in document "
                         f"after BGE-M3 retrieval, L36 Section-bounded grep, and "
                         f"L40 whole-file grep across {', '.join(section_types)}. "
                         f"Per AP-GO-089 (HARD_BLOCK), AP Works/EPC contracts must "
                         f"prescribe the solvency-certificate framework: 10% of "
                         f"class minimum, issued by Tahsildar OR Scheduled Bank, "
                         f"valid 1 year from date of issue.")
        print(f"  → GAP_VIOLATION finding — LLM rerank empty AND grep fallbacks "
              f"empty; framework genuinely absent")
    elif is_gap_violation_pre_grep:
        ev_passed_out = ev_passed
        ev_score_out  = ev_score
        ev_method_out = ev_method
        evidence_out  = evidence
        missing_summary = []
        if not has_issuer_option:
            missing_summary.append("no Tahsildar/Bank issuer option specified")
        if not has_validity_rule:
            missing_summary.append("no 1-year-validity rule stated")
        evidence_out = (f"{evidence}  [Framework gap: "
                        f"{'; '.join(missing_summary)}]")
        print(f"  → GAP_VIOLATION finding — framework signal found but "
              f"incomplete: {', '.join(missing_summary)}")
    elif grep_promoted_to_unverified:
        ev_passed_out = None
        ev_score_out  = None
        ev_method_out = "grep_fallback_retrieval_gap"
        evidence_out  = (f"LLM rerank top-{K} returned no Solvency framework "
                         f"signal, but exhaustive grep across "
                         f"{', '.join(section_types)} found keyword hits in "
                         f"{len(grep_hits)} section(s). First match: "
                         f"{grep_hits[0]['snippet'][:160] if grep_hits else 'n/a'}")
        print(f"  → UNVERIFIED finding (L36 grep fallback) — retrieval missed "
              f"{len(grep_hits)} section(s) with Solvency keywords")
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
        print(f"  → UNVERIFIED finding — LLM identified Solvency signal but "
              f"quote failed L24 (score={ev_score}, method={ev_method})")
    else:
        ev_passed_out = ev_passed
        ev_score_out  = ev_score
        ev_method_out = ev_method
        evidence_out  = evidence

    rule_node_id = get_or_create_rule_node(DOC_ID, rule["rule_id"])

    if is_gap_violation_pre_grep:
        missing_summary = []
        if not has_issuer_option:
            missing_summary.append("no Tahsildar/Bank issuer option")
        if not has_validity_rule:
            missing_summary.append("no 1-year-validity rule")
        label = (
            f"{TYPOLOGY}: Solvency framework incomplete — "
            f"{', '.join(missing_summary)}; {rule['rule_id']} "
            f"({rule['severity']}) requires Tahsildar OR Bank issuer + "
            f"1-year validity rule"
        )
    elif is_absence:
        label = (
            f"{TYPOLOGY}: Solvency framework absent — {rule['rule_id']} "
            f"({rule['severity']}) requires AP Works/EPC contracts to "
            f"prescribe the regulated solvency-certificate framework"
        )
    elif grep_promoted_to_unverified:
        label = (
            f"{TYPOLOGY}: UNVERIFIED (L36 grep-fallback) — LLM rerank "
            f"missed the Solvency framework; exhaustive grep found "
            f"{len(grep_hits)} section(s) with Solvency keyword hits; "
            f"requires human review"
        )
    elif full_grep_promoted:
        label = (
            f"{TYPOLOGY}: UNVERIFIED (L40 whole-file fallback) — "
            f"{'KG-coverage gap' if kg_coverage_gap else 'whole-file only'}; "
            f"{len(full_grep_hits)} match line(s)"
        )
    else:
        label = (
            f"{TYPOLOGY}: UNVERIFIED — LLM found Solvency signal but quote "
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
        "extraction_path":       "presence_multi_field",
        "llm_found_signal":      llm_found_signal,
        # Solvency framework extraction snapshot
        "tahsildar_option_present":     tahsildar_opt,
        "bank_option_present":          bank_opt,
        "validity_one_year_stated":     validity_1yr,
        "annexure_va_form_attached":    annexure_va,
        "annexure_vb_form_attached":    annexure_vb,
        "solvency_threshold_amount":    threshold_amt,
        "regulatory_citation":          reg_citation,
        "framework_compliant":          framework_compliant,
        "rule_shape":            rule["shape"],
        "violation_reason":      reason_label,
        "tier":                  1,
        "extracted_by":          "bge-m3+llm-rerank:qwen-2.5-72b@openrouter",
        "retrieval_strategy": (
            f"qdrant_top{K}_router_{family}_section_filter_"
            f"{'-'.join(section_types)}_per_type_quota+grep_fallback"
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
            f"L36 grep fallback fired: LLM rerank top-{K} did not surface the "
            f"Solvency framework, but exhaustive grep across {section_types} "
            f"found keyword hits in {len(grep_hits)} section(s). Reviewer "
            f"should open the listed sections in grep_audit.hits and confirm "
            f"the framework signals."
            if grep_promoted_to_unverified else
            f"L40 whole-file fallback: LLM rerank AND Section-bounded grep both "
            f"empty, but whole-file grep found {len(full_grep_hits)} match "
            f"line(s). "
            f"{'kg_coverage_gap=TRUE — reviewer should re-run kg_builder.' if kg_coverage_gap else 'Whole-file only hits — reviewer should verify retrieval coverage.'}"
            if full_grep_promoted else
            f"LLM identified Solvency signal but quote failed L24 (score={ev_score}, "
            f"method={ev_method}). Reviewer should open the section above and confirm."
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
        "source_ref": f"tier1:solvency_check:{rule['rule_id']}",
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
                "extraction_path":      "presence_multi_field",
                "tahsildar_option_present": tahsildar_opt,
                "bank_option_present":      bank_opt,
                "validity_one_year_stated": validity_1yr,
                "framework_compliant":      framework_compliant,
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
