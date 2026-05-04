"""
scripts/tier1_integrity_pact_check.py

Tier-1 Missing-Integrity-Pact check, BGE-M3 + LLM, NO regex.

PRESENCE shape — same lineage as Missing-PVC-Clause:
  - "Does the document contain a Pre-bid Integrity Pact applicability
     statement (or an IP proforma in Forms)?" rather than a numeric
     threshold.
  - LLM extracts integrity_pact_present (bool) + go_reference for the
     audit trail.
  - L27 contract: when condition_when can't be fully evaluated (the
     IP_Threshold gate is organisation-defined per CVC-116; no AP-GO
     rule sets a numeric value), the rule selector downgrades severity
     HARD_BLOCK → ADVISORY rather than going silent.

Pipeline (same shape as PVC):
  1. Pick rule via condition_evaluator on:
        CVC-086 (CVC, HARD_BLOCK,
                 condition: TenderType=ANY AND
                            EstimatedValue>=IP_Threshold (organisation-defined))
        MPS-022 (Central, HARD_BLOCK,
                 condition: TenderType=ANY AND EstimatedValue>=IPThreshold)
     IP_Threshold is org-defined and not present in tender_facts ─
     condition_evaluator returns UNKNOWN ⇒ rule fires as ADVISORY.
     PPP/DBFOT does NOT have an IP carve-out in the current rule set
     (none of the 38 Missing-Integrity-Pact rules SKIP on TenderType=PPP).
     We let the rule fire on PPP too; if it turns out IP doesn't apply
     to NREDCAP DBFOT, the AP-State knowledge layer needs a SKIP rule.
  2. Section filter via IP_SECTION_ROUTER — [NIT, Forms] for every
     family (per the user's read-first decision; IP anchors are stable
     across document templates).
  3. BGE-M3 embed an answer-shaped query (see QUERY_TEXT below).
  4. Qdrant top-K=10 candidates within the filter.
  5. LLM rerank with IP-specific ignore rules (general ethics
     declarations that aren't an Integrity Pact, debarment notices,
     standalone CIPP declarations) and structured IP extraction
     (presence + go_reference).
  6. Hallucination guard (L24): verify evidence is in the chosen
     section's full_text. Discard on score < 85.
  7. Apply rule check:
        integrity_pact_present=True  → compliant (presence shape)
        integrity_pact_present=False → ADVISORY violation (UNKNOWN→
                                       ADVISORY downgrade per L27)
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
from modules.validation.llm_client       import call_llm


# ── Constants ─────────────────────────────────────────────────────────

DOC_ID = sys.argv[1] if len(sys.argv) > 1 else "judicial_academy_exp_001"

TYPOLOGY = "Missing-Integrity-Pact"

QDRANT_URL  = os.environ.get("QDRANT_URL", "http://localhost:6333")
COLLECTION  = "tender_sections"

LLM_MODEL = os.environ.get("LLM_MODEL", "qwen/qwen-2.5-72b-instruct")


# Answer-shaped query — mirrors the literal wording of CVC IP / CIPP
# clauses. We DO blend in CIPP vocabulary because docs frequently
# bundle the IP applicability statement next to the CIPP declaration
# (both live in NIT/Forms); the LLM rerank disambiguates downstream.
QUERY_TEXT = (
    "Integrity Pact pre-bid ethical conduct "
    "transparency binding buyer seller CIPP "
    "code of integrity procurement"
)


# Rule candidates evaluated via condition_evaluator. Both rules gate on
# `EstimatedValue >= IP_Threshold` where IP_Threshold is organisation-
# defined (per CVC-116) — there's no numeric value in the rules table
# and no AP-GO sets one for AP State. The condition_evaluator will
# therefore return UNKNOWN for the threshold-comparison subterm even
# when EstimatedValue is known; the rule still fires as ADVISORY
# under the L27 UNKNOWN→ADVISORY downgrade.
#
# CVC-086 ranks first because it's the headline major-procurement rule
# referenced in every Central tender pack; MPS-022 backs it up via the
# DoE/MoF mandate. defeats=[] across the entire typology — no
# defeasibility wired (knowledge-layer gap, flagged in the read-first
# round).
RULE_CANDIDATES = [
    {
        "rule_id":         "CVC-086",
        "natural_language": "Major procurements (≥ org-defined IP threshold) MUST adopt the Pre-bid Integrity Pact",
        "severity":        "HARD_BLOCK",
        "layer":           "CVC",
        "shape":           "presence",
    },
    {
        "rule_id":         "MPS-022",
        "natural_language": "DoE/MoF mandate — IP must be included in tenders above prescribed threshold",
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


# ── LLM rerank prompt for Integrity Pact ─────────────────────────────

LLM_SYSTEM = (
    "You are a precise procurement-document fact extractor. "
    "Return JSON only. Quote evidence verbatim from the supplied text. "
    "Never fabricate."
)


# Keyword vocabulary for smart_truncate windowing (IP-specific).
# IP applicability statements often sit in long NIT preambles or
# in long Forms blocks that bundle several declarations together;
# centring the window on the literal phrase prevents elision.
IP_TRUNCATE_KEYWORDS = [
    r"integrity pact",
    r"\bIP\b",
    r"pre-bid integrity",
    r"independent external monitor",
    r"\bIEM\b",
    r"code of integrity",
    r"\bCIPP\b",
    r"ethical conduct",
    r"binding buyer",
    r"binding both",
    r"prohibited practices",
]


def build_ip_rerank_prompt(candidates: list[dict]) -> str:
    blocks = []
    for i, c in enumerate(candidates):
        body = smart_truncate(c["full_text"], window=3000,
                               keywords=IP_TRUNCATE_KEYWORDS)
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
        "Multilateral-funded Indian tenders (e.g. ADB-funded or World Bank-funded "
        "Amaravati capital city works) often carry TWO different anticorruption "
        "instruments that you MUST keep separate:\n"
        "\n"
        "  (1) The CVC INTEGRITY PACT (the regulated Indian instrument) — a "
        "      bilateral PRE-BID PACT signed BETWEEN buyer (procuring entity) AND "
        "      bidder/seller, monitored by INDEPENDENT EXTERNAL MONITORS (IEMs) "
        "      approved by the CENTRAL VIGILANCE COMMISSION (CVC). Mandated by "
        "      CVC Office Orders, MoF/DoE circulars, and GFR.\n"
        "\n"
        "  (2) The MULTILATERAL LENDER'S ANTICORRUPTION FRAMEWORK (ADB / World "
        "      Bank / JICA) — distinct documents like ADB's Anticorruption Policy "
        "      (1998), Integrity Principles and Guidelines (2015), Office of "
        "      Anticorruption and Integrity (OAI) sanctions list, IEF "
        "      Investigation and Enforcement Framework, World Bank Sanctions "
        "      Procedures, etc. These are LENDER-IMPOSED conditions, NOT the "
        "      CVC Integrity Pact. They do NOT substitute for the CVC IP.\n"
        "\n"
        "Indian procurement law requires the CVC IP regardless of the funding "
        "source — multilateral funding does NOT waive the CVC IP requirement.\n"
        "\n"
        f"{candidates_block}\n\n"
        "Detect both instruments INDEPENDENTLY and report their presence "
        "separately.\n\n"
        "Return JSON only:\n"
        "{\n"
        "  \"chosen_index\":           integer 0..N-1 of the BEST candidate to attribute (prefer CVC IP if found, else the ADB/WB framework, else null),\n"
        "  \"adb_framework_detected\": bool   (ADB / World Bank / JICA anticorruption framework, OAI sanctions, IEF, Anticorruption Policy, etc.),\n"
        "  \"cvc_ip_detected\":        bool   (the regulated CVC Pre-bid Integrity Pact specifically — IEMs, CVC Office Order, signed pact between Principal and Bidder),\n"
        "  \"integrity_pact_present\": bool   (TRUE only if cvc_ip_detected is TRUE — the CVC IP is the only thing that satisfies Indian law),\n"
        "  \"pact_type\":              one of 'CVC_IP' | 'ADB_framework_only' | 'WB_framework_only' | 'multilateral_framework_only' | 'none',\n"
        "  \"go_reference\":           string OR null  (any GO/circular/CVC reference for the CVC IP),\n"
        "  \"evidence\":               \"verbatim quote from the chosen candidate's text identifying whichever instrument was detected\",\n"
        "  \"found\":                  bool   (TRUE if any of CVC IP or multilateral framework was detected; FALSE only if NEITHER was found),\n"
        "  \"reasoning\":              \"one short sentence explaining what you found and what you did NOT find\"\n"
        "}\n\n"
        "Detection rules — what counts as CVC IP (cvc_ip_detected=true):\n"
        "- 'Pre-bid Integrity Pact' / 'Integrity Pact (IP)' as a binding pact "
        "  between BUYER (Principal) and BIDDER.\n"
        "- Bidders MUST sign and submit the IP proforma along with the bid.\n"
        "- The IP is MONITORED by INDEPENDENT EXTERNAL MONITOR(s) (IEMs) approved "
        "  by the CENTRAL VIGILANCE COMMISSION.\n"
        "- A 'CVC Office Order' on Integrity Pact, or any DoE/MoF IP mandate.\n"
        "- An IP applicability threshold statement ('IP applicable above Rs X cr') "
        "  — counts even if the proforma itself is in a separate Schedule.\n"
        "\n"
        "Detection rules — what counts as ADB / World Bank / multilateral framework "
        "(adb_framework_detected=true) but is NOT a CVC IP:\n"
        "- ADB Anticorruption Policy (1998), Integrity Principles and Guidelines, "
        "  Office of Anticorruption and Integrity (OAI), OAI sanctions list.\n"
        "- ADB's Investigation and Enforcement Framework (IEF), 'integrity violations' "
        "  terminology, ADB published sanctions URL (adb.org/who-we-are/integrity).\n"
        "- World Bank Sanctions Procedures, WB Listing of Ineligible Firms, WB "
        "  Anticorruption Guidelines.\n"
        "- 'Fraud and Corruption' boilerplate enumerating Corrupt / Fraudulent / "
        "  Coercive / Collusive / Obstructive practices via the multilateral "
        "  lender's definitions (these ARE the lender framework, not CVC IP).\n"
        "- Eligibility/sanctions cross-checks against multilateral lender lists.\n"
        "\n"
        "Detection rules — IGNORE / NEITHER:\n"
        "- Standalone CIPP / Code of Integrity DECLARATIONS by the bidder alone "
        "  (a one-sided ethics declaration mandated by GFR Rule 175 — neither "
        "  the bilateral CVC IP nor the multilateral framework).\n"
        "- DEBARMENT/blacklisting notices, conflict-of-interest declarations, "
        "  prior-litigation disclosures (bidder-side declarations).\n"
        "- POWER OF ATTORNEY, JOINT VENTURE AGREEMENT, MoU clauses.\n"
        "- ANTI-COLLUSION / NO-CARTEL declarations alone (CIPP sub-clauses).\n"
        "- Material/work 'integrity' (engineering quality language, not ethics).\n"
        "\n"
        "Output rules:\n"
        "- integrity_pact_present is TRUE if AND ONLY IF cvc_ip_detected is TRUE. "
        "  An ADB or World Bank framework alone does NOT make integrity_pact_present "
        "  TRUE — Indian law requires the CVC IP separately.\n"
        "- pact_type:\n"
        "    'CVC_IP'                       if cvc_ip_detected (regardless of multilateral)\n"
        "    'ADB_framework_only'           if adb_framework_detected, NOT cvc_ip_detected, ADB-specific text\n"
        "    'WB_framework_only'            if adb_framework_detected, NOT cvc_ip_detected, World-Bank-specific text\n"
        "    'multilateral_framework_only'  if adb_framework_detected, NOT cvc_ip_detected, generic multilateral language\n"
        "    'none'                         if neither detected\n"
        "- chosen_index: prefer the CVC IP candidate if cvc_ip_detected; "
        "  otherwise the multilateral-framework candidate (so the audit trail "
        "  captures what WAS found); null only if NEITHER instrument is in any candidate.\n"
        "- Evidence MUST be an exact substring of the chosen candidate's text.\n"
        "- If neither instrument is in any candidate, set chosen_index=null, "
        "  integrity_pact_present=false, adb_framework_detected=false, "
        "  cvc_ip_detected=false, pact_type='none', found=false."
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

    Strict LLM-only sources. The legacy regex-classifier fields have
    been removed from the schema. If `estimated_value_cr` is null,
    the EstimatedValue key is omitted from facts and condition_evaluator
    returns UNKNOWN for any condition that references it.

    Note: even when EstimatedValue is known, the IP_Threshold subterm
    is org-defined and not in facts — so the condition still returns
    UNKNOWN. Rule selector downgrades to ADVISORY per L27.
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
        "tender_type":  p.get("tender_type"),
        "is_ap_tender": bool(p.get("is_ap_tender")),
        "TenderType":   p.get("tender_type"),
        "TenderState":  "AndhraPradesh" if p.get("is_ap_tender") else "Other",
    }

    # EstimatedValue (rupees) — LLM-extracted only
    ev_cr = p.get("estimated_value_cr")
    if ev_cr is not None:
        try:
            facts["EstimatedValue"] = float(ev_cr) * 1e7   # crores → rupees
            facts["_estimated_value_cr"] = float(ev_cr)
        except (TypeError, ValueError):
            pass

    return facts


def select_ip_rule(tender_facts: dict) -> dict | None:
    """Pick the highest-priority rule whose condition_when is satisfied
    or unknown (UNKNOWN→ADVISORY per L27). Returns None only when
    every rule's condition is definitively SKIP — which won't happen
    for IP today because the threshold subterm is always UNKNOWN."""
    fired: list[dict] = []
    ev_rs = tender_facts.get("EstimatedValue")
    ev_str = (f"{ev_rs:.0f} rs ({tender_facts.get('_estimated_value_cr')} cr)"
              if ev_rs is not None else "UNKNOWN (no LLM extract)")
    print(f"\n  Rule selection — facts: tender_type={tender_facts.get('tender_type')!r}, "
          f"is_ap_tender={tender_facts.get('is_ap_tender')}, "
          f"EstimatedValue={ev_str}, IP_Threshold=UNKNOWN (org-defined per CVC-116)")
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

    # Defeasibility filter (no rule defeats anything in this typology
    # today, but kept for symmetry with PVC).
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
                f"ADVISORY because IP_Threshold is org-defined and not in facts]")
    print(f"  → selected {chosen['rule_id']} (severity={chosen['severity']}, "
          f"shape={chosen['shape']}){note}")
    return chosen


# ── Idempotent re-run cleanup ─────────────────────────────────────────

def _delete_prior_tier1_ip(doc_id: str) -> tuple[int, int]:
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
    print(f"  Tier-1 Missing-Integrity-Pact (BGE-M3 + LLM, NO regex)")
    print(f"  doc_id : {DOC_ID}")
    print(f"  model  : {LLM_MODEL}")
    print("=" * 76)

    n_f, n_e = _delete_prior_tier1_ip(DOC_ID)
    if n_f or n_e:
        print(f"  cleared {n_f} prior Tier-1 IP finding node(s) and "
              f"{n_e} edge(s) before re-running")

    # 1. Pick rule via condition_evaluator
    facts = fetch_tender_facts(DOC_ID)
    rule  = select_ip_rule(facts)
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
    print(f"\n── Step 3: LLM rerank + IP extraction ──")
    user_prompt = build_ip_rerank_prompt(candidates)
    print(f"  prompt size: {len(user_prompt)} chars (~{len(user_prompt)//4} tokens)")
    raw_content = call_llm(LLM_SYSTEM, user_prompt, max_tokens=900)
    timings["llm"] = time.perf_counter() - t0
    print(f"  wall: {timings['llm']:.2f}s")
    print(f"\n── Raw LLM JSON ──")
    print(raw_content)

    # 7. Parse + select chosen candidate
    parsed = parse_llm_response(raw_content)
    chosen        = parsed.get("chosen_index")
    ip_present    = bool(parsed.get("integrity_pact_present"))
    adb_detected  = bool(parsed.get("adb_framework_detected"))
    cvc_detected  = bool(parsed.get("cvc_ip_detected"))
    pact_type     = (parsed.get("pact_type") or "").strip() or None
    go_reference  = parsed.get("go_reference")
    evidence      = (parsed.get("evidence") or "").strip()
    found         = bool(parsed.get("found"))
    reason        = (parsed.get("reasoning") or "").strip()

    # Cross-check: integrity_pact_present is TRUE iff cvc_ip_detected
    # is TRUE. The prompt says so explicitly; defend against an LLM
    # that gets the boolean wrong (per L24 honesty principle, never
    # trust the LLM's invariant — re-derive locally).
    if ip_present != cvc_detected:
        print(f"  ⚠ LLM returned ip_present={ip_present} but cvc_ip_detected="
              f"{cvc_detected}; re-deriving: ip_present := cvc_ip_detected")
        ip_present = cvc_detected

    print(f"\n── Parsed ──")
    print(f"  chosen_index            : {chosen}")
    print(f"  found                   : {found}")
    print(f"  cvc_ip_detected         : {cvc_detected}")
    print(f"  adb_framework_detected  : {adb_detected}")
    print(f"  integrity_pact_present  : {ip_present}  (= cvc_ip_detected)")
    print(f"  pact_type               : {pact_type!r}")
    print(f"  go_reference            : {go_reference!r}")
    print(f"  reasoning               : {reason[:200]}")
    print(f"  evidence                : {evidence[:300]!r}")

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
                # Hallucinated quote — cannot trust either detection bool.
                # Discard: treat as absence, drop both flags.
                section = None
                ip_present = False
                cvc_detected = False
                adb_detected = False
                pact_type = "none"
        else:
            print(f"  ⚠ no evidence quote provided — treating as not-verified")
            ev_passed = False; ev_score = 0; ev_method = "empty"
            section = None
            # Empty quote is the same failure mode as hallucination —
            # we can't audit what we can't see. Drop both detection
            # bools so the finding doesn't claim ADB-detected on
            # unverifiable text.
            ip_present = False
            cvc_detected = False
            adb_detected = False
            pact_type = "none"
    else:
        print(f"  → no candidate chosen by LLM")
        if ip_present:
            print(f"  ⚠ ip_present=True but chosen_index=null — treating as False")
            ip_present = False
            cvc_detected = False

    # 8. Apply rule check (presence shape).
    # CVC IP is the only thing that satisfies Indian law. ADB / WB
    # multilateral frameworks DO NOT substitute. ip_present is already
    # locked to cvc_ip_detected above.
    is_violation = (rule["shape"] == "presence" and not ip_present)
    if ip_present:
        reason_label = "compliant_integrity_pact_present"
        finding_note = None
    elif adb_detected:
        # Multilateral framework was found but CVC IP was not. The
        # CVC-IP-missing violation still stands; the audit note
        # captures what WAS found so the reviewer doesn't waste time
        # re-searching for the lender framework.
        reason_label = "integrity_pact_absent_violation_multilateral_only"
        finding_note = (
            "Multilateral lender anticorruption framework detected "
            f"(pact_type={pact_type!r}), but does not substitute for "
            "the CVC Pre-bid Integrity Pact requirement under Indian "
            "procurement law (CVC-086, MPS-022). CVC IP still missing."
        )
    else:
        reason_label = "integrity_pact_absent_violation"
        finding_note = None

    print(f"\n── Decision ──")
    print(f"  rule           : {rule['rule_id']} ({rule['severity']}, shape={rule['shape']})")
    print(f"  ip_present     : {ip_present}")
    print(f"  pact_type      : {pact_type!r}")
    print(f"  reason_label   : {reason_label}")
    print(f"  is_violation   : {is_violation}")
    if finding_note:
        print(f"  note           : {finding_note}")

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

    # L29: ABSENCE findings skip evidence_guard semantics.
    # Two distinct absence shapes here:
    #   (a) Pure absence — neither CVC IP nor multilateral framework
    #       detected. section is None. Evidence is the search-trace
    #       description; ev_* are nulled.
    #   (b) Multilateral-only — ADB/WB framework detected (so we have
    #       a verified quote and a non-null section) but CVC IP is
    #       missing. NOT an absence finding for L29 purposes — the
    #       quote IS in the document. The L24 evidence_guard already
    #       ran and verified the multilateral-framework quote; we
    #       keep those audit fields. The CVC-IP-missing violation
    #       carries the verified ADB quote and the explanatory note.
    is_absence_finding = (not ip_present and section is None)
    if is_absence_finding:
        ev_passed = None
        ev_score  = None
        ev_method = "absence_finding_no_evidence"
        evidence  = ("Integrity Pact not found in document "
                     "after searching NIT, Forms section types")
        print(f"  → ABSENCE finding — skipping evidence_guard "
              f"(no quote to verify)")

    rule_node_id = get_or_create_rule_node(DOC_ID, rule["rule_id"])

    label_suffix = (
        " (multilateral framework detected, CVC IP missing)"
        if adb_detected and not ip_present else ""
    )
    label = (
        f"{TYPOLOGY}: Integrity Pact absent — {rule['rule_id']} "
        f"({rule['severity']}) requires Pre-bid Integrity Pact for this "
        f"tender{label_suffix}"
    )

    finding_props = {
        "rule_id":            rule["rule_id"],
        "typology_code":      TYPOLOGY,
        "severity":           rule["severity"],
        "evidence":           evidence,
        "extraction_path":    "presence",
        "integrity_pact_present": ip_present,
        "cvc_ip_detected":        cvc_detected,
        "adb_framework_detected": adb_detected,
        "pact_type":              pact_type,
        "note":                   finding_note,
        "go_reference":       go_reference,
        "rule_shape":         rule["shape"],
        "violation_reason":   reason_label,
        "tier":               1,
        "extracted_by":       "bge-m3+llm-rerank:qwen-2.5-72b@openrouter",
        "retrieval_strategy": (
            f"qdrant_top{K}_router_{family}_section_filter_"
            f"{'-'.join(section_types)}_llm_rerank"
        ),
        "doc_family":          family,
        "section_filter":      section_types,
        "rerank_chosen_index": chosen,
        "rerank_reasoning":    reason,
        "section_node_id":     section_node_id,
        "section_heading":     section_heading,
        "source_file":         source_file,
        "line_start_local":    line_start_local,
        "line_end_local":      line_end_local,
        "qdrant_similarity":   qdrant_similarity,
        # L24 audit fields
        "evidence_in_source":    ev_passed,
        "evidence_verified":     ev_passed,
        "evidence_match_score":  ev_score,
        "evidence_match_method": ev_method,
        # Rule-evaluator inputs
        "estimated_value_cr":          facts.get("_estimated_value_cr"),
        # L27 audit — record the UNKNOWN→ADVISORY downgrade origin
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
        "source_ref": f"tier1:integrity_pact_check:{rule['rule_id']}",
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
            "extraction_path":      "presence",
            "integrity_pact_present": ip_present,
            "cvc_ip_detected":        cvc_detected,
            "adb_framework_detected": adb_detected,
            "pact_type":              pact_type,
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
