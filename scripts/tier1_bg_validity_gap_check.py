"""
scripts/tier1_bg_validity_gap_check.py

Tier-1 BG-Validity-Gap check, BGE-M3 + LLM, NO regex.

FOUR-STATE shape (extends the L35 three-state contract with a
GAP_VIOLATION outcome). The doc must specify a Bank Guarantee /
Performance Security validity period that extends through DLP /
warranty period + buffer (typically 60 days beyond per MPG-097 and
CLAUSE-WBG-001). A BG that expires at contract completion (no
buffer) creates a "gap" where claims discovered during DLP cannot
be satisfied — that's the violation this typology catches.

Outcomes:
  COMPLIANT       — LLM found BG validity clause + L24 verified +
                    extends through DLP/warranty + buffer
  GAP_VIOLATION   — LLM found BG validity clause + L24 verified +
                    does NOT extend correctly (real gap, edge
                    emitted with verified inadequate quote)
  UNVERIFIED      — LLM found clause + L24 failed (per L35)
  ABSENCE         — LLM didn't find any BG validity clause →
                    L36 grep fallback → either ABSENCE (no edge
                    after grep also empty) or UNVERIFIED-via-grep

Pipeline (same shape as Blacklist post-L36):
  1. Pick rule via condition_evaluator. MPW-082 (Works AND
     BGSubmitted=true) is the canonical primary — fires
     UNKNOWN→ADVISORY on Works docs (BGSubmitted not extracted).
     PPP docs SKIP at the rule layer (no rule fires on
     TenderType=PPP — knowledge-layer gap, flagged in L37).
  2. Section filter via BG_VALIDITY_SECTION_ROUTER —
        APCRDA_Works → [GCC, Forms]
        SBD_Format   → [GCC, Forms, Evaluation]   (Kakinada n_gcc=0)
        NREDCAP_PPP  → [GCC, Forms]
        default      → [GCC, Forms, ITB]
  3. BGE-M3 embed an answer-shaped query.
  4. Qdrant top-K=10 candidates within the filter.
  5. LLM rerank with BG-validity-specific extraction:
        bg_validity_specified, bg_type, validity_period_description,
        extends_through_dlp_or_warranty (the threshold bool),
        has_buffer_beyond_dlp, buffer_days, go_reference, evidence.
  6. Hallucination guard (L24).
  7. Apply 4-state decision.
  8. L36 grep fallback on the ABSENCE branch only.
  9. Materialise — gap_violation gets edge with verified quote;
     absence gets edge with L29 marker; unverified no edge.

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
from modules.validation.grep_fallback    import grep_source_for_keywords


# ── Constants ─────────────────────────────────────────────────────────

DOC_ID = sys.argv[1] if len(sys.argv) > 1 else "judicial_academy_exp_001"

TYPOLOGY = "BG-Validity-Gap"

QDRANT_URL  = os.environ.get("QDRANT_URL", "http://localhost:6333")
COLLECTION  = "tender_sections"

LLM_MODEL = os.environ.get("LLM_MODEL", "qwen/qwen-2.5-72b-instruct")


# L36 source-grep fallback vocabulary for BG-Validity-Gap. When the
# LLM rerank returns chosen_index=null (absence), cross-check the
# full section_filter coverage for these keywords. If any match,
# downgrade ABSENCE to UNVERIFIED — likely a retrieval-coverage gap
# (the section that holds the BG validity clause didn't rank in
# top-K), not a real bypass.
GREP_FALLBACK_KEYWORDS = [
    "Performance Security",
    "Performance Bank Guarantee",
    "performance guarantee",
    "Bank Guarantee",
    "BG validity",
    "validity of the BG",
    "valid up to",
    "shall remain valid",
    "Defect Liability",
    "DLP",
    "warranty period",
    "60 days beyond",
    "until completion of",
    "irrevocable",
    "Bid Security",
    "Earnest Money",
]


# Answer-shaped query — mirrors the literal wording of CLAUSE-WBG-001
# (Warranty BG: "60 days beyond warranty period"), CLAUSE-WORKS-PBG-001
# (Performance Guarantee), and CLAUSE-AP-EMD-VALIDITY-DLP-001 (AP
# EMD validity beyond DLP).
QUERY_TEXT = (
    "Performance Bank Guarantee BG validity period valid up to "
    "warranty period Defect Liability DLP 60 days beyond completion "
    "irrevocable scheduled commercial bank Performance Security "
    "shall remain valid"
)


# Rule candidates evaluated via condition_evaluator. Priority order:
# most specific first.
#
# MPW-082 is the canonical primary for Works docs — its condition
# `TenderType=Works AND BGSubmitted=true` resolves to UNKNOWN
# (BGSubmitted not extracted) → L27 ADVISORY downgrade. Works docs
# universally require a BG so the UNKNOWN→ADVISORY behaviour is
# correct here; if the doc doesn't actually require a BG, the LLM
# rerank + grep fallback will return ABSENCE.
#
# MPG-097 covers Goods+CapEquip+Warranty (none in our corpus). The
# AP-State / CVC rules gate on subterms (MobilizationAdvanceProvided,
# IntegrityPactSigned) that don't fire cleanly on our docs.
#
# defeats=[] across the typology — knowledge-layer gap, no
# defeasibility wired (same pattern as IP/LD/MA/E-Proc/Blacklist).
RULE_CANDIDATES = [
    {
        "rule_id":         "MPW-082",
        "natural_language": "Works BGs (Bid Security/Performance Guarantee/Security Deposit) must be irrevocable + scheduled commercial bank; format per Bid Document",
        "severity":        "HARD_BLOCK",
        "layer":           "Central",
        "shape":           "presence",
    },
    {
        "rule_id":         "MPG-097",
        "natural_language": "PBG must remain valid 60 days beyond warranty period (Goods+CapEquip+Warranty)",
        "severity":        "HARD_BLOCK",
        "layer":           "Central",
        "shape":           "presence",
    },
    {
        "rule_id":         "MPW-081",
        "natural_language": "Works security-deposit/retention-money structure (5% per running bill until final acceptance)",
        "severity":        "HARD_BLOCK",
        "layer":           "Central",
        "shape":           "presence",
    },
    {
        "rule_id":         "MPW25-054",
        "natural_language": "MPW 2025 §5.1.3 — security deposit/retention-money structure for Works",
        "severity":        "HARD_BLOCK",
        "layer":           "Central",
        "shape":           "presence",
    },
    {
        "rule_id":         "AP-GO-015",
        "natural_language": "AP MA against unconditional BG valid until entire MA recovered",
        "severity":        "HARD_BLOCK",
        "layer":           "AP-State",
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


# ── LLM rerank prompt for BG-Validity detection ──────────────────────

LLM_SYSTEM = (
    "You are a precise procurement-document fact extractor. "
    "Return JSON only. Quote evidence verbatim from the supplied text. "
    "Never fabricate."
)


# Keyword vocabulary for smart_truncate windowing (BG-validity-specific).
BG_VALIDITY_TRUNCATE_KEYWORDS = [
    r"Performance Security",
    r"Performance Bank Guarantee",
    r"performance guarantee",
    r"\bPBG\b",
    r"Bank Guarantee",
    r"\bBG\b",
    r"shall remain valid",
    r"valid up to",
    r"valid until",
    r"validity of",
    r"Defect Liability",
    r"\bDLP\b",
    r"warranty period",
    r"60.*days.*beyond",
    r"until.*completion",
    r"completion.*plus",
    r"irrevocable",
    r"Bid Security",
    r"Earnest Money",
    r"\bEMD\b",
]


def build_bg_validity_rerank_prompt(candidates: list[dict]) -> str:
    blocks = []
    for i, c in enumerate(candidates):
        body = smart_truncate(c["full_text"], window=3000,
                               keywords=BG_VALIDITY_TRUNCATE_KEYWORDS)
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
        "Exactly ONE of them (or none) carries the BANK GUARANTEE VALIDITY-PERIOD "
        "specification — the contract clause that states HOW LONG the BG/PBG/Performance "
        "Security must remain valid.\n\n"
        "The regulator-required validity is:\n"
        "  - Works contracts: PBG must remain valid until completion of the Defect "
        "    Liability Period (DLP) / warranty period, typically 60 (sixty) days "
        "    BEYOND the warranty period (per MPG-097, CLAUSE-WBG-001, MPW 2022).\n"
        "  - AP Works: EMD + Additional Security shall be valid beyond DLP per "
        "    CLAUSE-AP-EMD-VALIDITY-DLP-001.\n"
        "  - Mobilisation Advance BG: must remain valid until entire MA is "
        "    recovered (AP-GO-015).\n"
        "  - General principle: a BG that expires at contract completion (with NO "
        "    buffer) creates a 'gap' where claims discovered during DLP can't be "
        "    satisfied — that's the violation.\n"
        "\n"
        f"{candidates_block}\n\n"
        "Question: Does the document specify a BG validity period? If yes, does it "
        "extend through DLP/warranty period (with appropriate buffer)?\n\n"
        "Return JSON only:\n"
        "{\n"
        "  \"chosen_index\":                    integer 0..N-1 of the BG-validity candidate, OR null if no candidate states a validity period,\n"
        "  \"bg_validity_specified\":           bool   (does the doc explicitly state BG validity?),\n"
        "  \"bg_type\":                         one of 'PBG' | 'EMD' | 'BidSecurity' | 'MobilisationAdvanceBG' | 'WarrantyBG' | 'multiple' | null,\n"
        "  \"validity_period_description\":     string OR null  (e.g. '60 days beyond warranty period', '12 months from contract date', 'until completion of DLP plus 60 days'),\n"
        "  \"extends_through_dlp_or_warranty\": bool   (TRUE if the validity explicitly extends through DLP / Defect Liability / warranty period, FALSE if it expires at or before contract completion / has no DLP buffer),\n"
        "  \"has_buffer_beyond_dlp\":           bool OR null  (TRUE if there's an explicit buffer like '60 days beyond' the DLP/warranty),\n"
        "  \"buffer_days\":                     integer OR null  (the buffer duration in days, e.g. 60),\n"
        "  \"go_reference\":                    string OR null  (e.g. 'MPW 2022 §6.4.4', 'GO Ms No XX', 'GFR Rule YY'),\n"
        "  \"evidence\":                        \"verbatim quote from the chosen candidate's text identifying the BG validity period\",\n"
        "  \"found\":                           bool,\n"
        "  \"reasoning\":                       \"one short sentence explaining the choice\"\n"
        "}\n\n"
        "Selection rules — IGNORE the following content (NOT a BG validity-period clause):\n"
        "- BG ISSUER FORMAT rules (must be Scheduled Commercial Bank, irrevocable, "
        "  per the prescribed format) — that's a separate eligibility check (MPW-082), "
        "  not a validity-DURATION spec. Pick only if the same clause ALSO states the "
        "  validity period.\n"
        "- BID-SECURITY RETURN windows ('within 30 days after award') — that's about "
        "  releasing the BG, not about how long it must remain valid.\n"
        "- BG ENCASHMENT / forfeiture clauses — operational, not validity duration.\n"
        "- PBG AMOUNT / percentage clauses (5%, 10%) — that's about the value of the "
        "  BG, not its validity duration. Pick only if the same clause ALSO states "
        "  the validity period.\n"
        "- INTEGRITY PACT validity (5 years per CVC-127/CVC-131) — different "
        "  instrument, different shape.\n"
        "- TIME EXTENSIONS to BG (CVC-049) — that's about renewing an expired BG, "
        "  not about the original validity duration.\n"
        "\n"
        "Selection rules — DO pick if the candidate states:\n"
        "- 'PBG shall remain valid until [date/event]' or 'BG validity = X months/days'\n"
        "- 'PBG valid 60 days beyond warranty period' / 'until DLP + 60 days'\n"
        "- 'shall remain valid until full conclusion of all contractual obligations'\n"
        "- 'EMD shall be valid until [date]' / 'Bid Security shall remain valid for "
        "  X days from bid due date'\n"
        "- A reference to MPG-097, MPW 2022 §X.X (BG validity), or AP-GO BG validity "
        "  orders.\n"
        "- Even when the duration is a PCC/SCC placeholder (e.g. '{{validity_days}}' "
        "  or 'as stated in PCC'), if the framework EXPLICITLY invokes a "
        "  validity-period spec, treat as bg_validity_specified=true and capture "
        "  the framework reference.\n"
        "\n"
        "QUOTE FORMAT — STRICT (per L35):\n"
        "- Return a SINGLE CONTIGUOUS quote from ONE sentence or ONE clause only.\n"
        "- Do NOT stitch multiple paragraphs into one quote.\n"
        "- Do NOT add ellipsis ('...') between lines.\n"
        "- Do NOT paraphrase, summarise, or condense — quote EXACTLY as it appears in the supplied text.\n"
        "- Do NOT introduce or remove markdown formatting (asterisks, underscores, italics) — preserve the source formatting verbatim.\n"
        "- Pick the SHORTEST contiguous span that proves the validity period; one sentence is usually enough.\n"
        "\n"
        "- If no candidate states a BG validity period, set chosen_index=null, "
        "  bg_validity_specified=false, found=false. This is the ABSENCE outcome.\n"
        "- Evidence MUST be an exact substring of the chosen candidate's text."
    )


def parse_llm_response(raw: str) -> dict:
    """Thin wrapper kept for in-script readability — actual parsing
    logic lives in modules.validation.llm_client.parse_llm_json (per L35)."""
    return parse_llm_json(raw)


# ── Rule selection via condition_evaluator ────────────────────────────

def fetch_tender_facts(doc_id: str) -> dict:
    """Build the facts dict that condition_evaluator reads.
    Strict LLM-only sources (per L28)."""
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


def select_bg_validity_rule(tender_facts: dict) -> dict | None:
    """Pick the highest-priority rule that fires (or fires-as-UNKNOWN
    per L27 downgrade). MPW-082 is the canonical primary for Works
    docs. PPP docs SKIP at the rule layer."""
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


# grep_source_for_keywords lifted to modules/validation/grep_fallback.py
# per the refactor — same semantics, single source of truth.


# ── Idempotent re-run cleanup ─────────────────────────────────────────

def _delete_prior_tier1_bg_validity(doc_id: str) -> tuple[int, int]:
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
    print(f"  Tier-1 BG-Validity-Gap (BGE-M3 + LLM, NO regex)")
    print(f"  doc_id : {DOC_ID}")
    print(f"  model  : {LLM_MODEL}")
    print("=" * 76)

    n_f, n_e = _delete_prior_tier1_bg_validity(DOC_ID)
    if n_f or n_e:
        print(f"  cleared {n_f} prior Tier-1 BG-Validity finding node(s) and "
              f"{n_e} edge(s) before re-running")

    # 1. Pick rule via condition_evaluator
    facts = fetch_tender_facts(DOC_ID)
    rule  = select_bg_validity_rule(facts)
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
    print(f"\n── Step 3: LLM rerank + BG-validity detection ──")
    user_prompt = build_bg_validity_rerank_prompt(candidates)
    print(f"  prompt size: {len(user_prompt)} chars (~{len(user_prompt)//4} tokens)")
    raw_content = call_llm(LLM_SYSTEM, user_prompt, max_tokens=900)
    timings["llm"] = time.perf_counter() - t0
    print(f"  wall: {timings['llm']:.2f}s")
    print(f"\n── Raw LLM JSON ──")
    print(raw_content)

    # 7. Parse + select chosen candidate
    parsed = parse_llm_response(raw_content)
    chosen        = parsed.get("chosen_index")
    bg_specified  = bool(parsed.get("bg_validity_specified"))
    bg_type       = parsed.get("bg_type")
    validity_desc = parsed.get("validity_period_description")
    extends_dlp   = bool(parsed.get("extends_through_dlp_or_warranty"))
    has_buffer    = parsed.get("has_buffer_beyond_dlp")
    buffer_days   = parsed.get("buffer_days")
    go_reference  = parsed.get("go_reference")
    evidence      = (parsed.get("evidence") or "").strip()
    found         = bool(parsed.get("found"))
    reason        = (parsed.get("reasoning") or "").strip()

    print(f"\n── Parsed ──")
    print(f"  chosen_index                    : {chosen}")
    print(f"  found                           : {found}")
    print(f"  bg_validity_specified           : {bg_specified}")
    print(f"  bg_type                         : {bg_type!r}")
    print(f"  validity_period_description     : {validity_desc!r}")
    print(f"  extends_through_dlp_or_warranty : {extends_dlp}")
    print(f"  has_buffer_beyond_dlp           : {has_buffer}")
    print(f"  buffer_days                     : {buffer_days}")
    print(f"  go_reference                    : {go_reference!r}")
    print(f"  reasoning                       : {reason[:200]}")
    print(f"  evidence                        : {evidence[:300]!r}")

    section = None
    similarity = None
    ev_passed = False
    ev_score = 0
    ev_method = "skipped"

    # L35 three-state contract baseline.
    llm_found_clause   = bg_specified and (chosen is not None)
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
                      f"Routing to UNVERIFIED finding (NOT absence/gap).")
                print(f"    LLM evidence  : {evidence[:200]!r}")
        else:
            print(f"  ⚠ no evidence quote provided — treating as L24-failed (empty)")
            ev_passed = False; ev_score = 0; ev_method = "empty"
    else:
        print(f"  → no candidate chosen by LLM")
        if bg_specified:
            print(f"  ⚠ bg_validity_specified=True but chosen_index=null — treating as False")
            bg_specified = False
            llm_found_clause = False

    # 8. Four-way decision (extends L35 with GAP_VIOLATION):
    #    (a) llm_found_clause AND ev_passed AND extends_dlp
    #         → COMPLIANT, no finding
    #    (b) llm_found_clause AND ev_passed AND NOT extends_dlp
    #         → GAP_VIOLATION, OPEN finding + edge with verified
    #           inadequate quote
    #    (c) llm_found_clause AND NOT ev_passed
    #         → UNVERIFIED finding (no edge)
    #    (d) NOT llm_found_clause
    #         → ABSENCE candidate; run L36 grep fallback first
    is_compliant     = llm_found_clause and ev_passed and extends_dlp
    is_gap_violation = llm_found_clause and ev_passed and not extends_dlp
    is_unverified    = llm_found_clause and not ev_passed
    raw_is_absence   = not llm_found_clause

    grep_hits: list[dict] = []
    grep_promoted_to_unverified = False
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

    is_absence    = raw_is_absence and not grep_promoted_to_unverified
    is_unverified = is_unverified or grep_promoted_to_unverified

    if is_compliant:
        reason_label = "compliant_bg_validity_extends_through_dlp"
    elif is_gap_violation:
        reason_label = "bg_validity_gap_does_not_extend_through_dlp"
    elif grep_promoted_to_unverified:
        reason_label = "bg_validity_unverified_grep_fallback_retrieval_gap"
    elif is_unverified:
        reason_label = "bg_validity_unverified_llm_found_quote_failed_l24"
    else:
        reason_label = "bg_validity_absent_violation"

    print(f"\n── Decision ──")
    print(f"  rule              : {rule['rule_id']} ({rule['severity']}, shape={rule['shape']})")
    print(f"  llm_found_clause  : {llm_found_clause}")
    print(f"  ev_passed         : {ev_passed}  (score={ev_score}, method={ev_method})")
    print(f"  extends_dlp       : {extends_dlp}")
    print(f"  raw_is_absence    : {raw_is_absence}")
    print(f"  grep_fallback_hit : {grep_promoted_to_unverified}")
    print(f"  is_compliant      : {is_compliant}")
    print(f"  is_gap_violation  : {is_gap_violation}")
    print(f"  is_unverified     : {is_unverified}")
    print(f"  is_absence        : {is_absence}")
    print(f"  reason_label      : {reason_label}")

    if is_compliant:
        return 0

    # 9. Materialise finding (GAP_VIOLATION / UNVERIFIED / ABSENCE).
    # Edge emitted for GAP_VIOLATION and ABSENCE only — UNVERIFIED
    # has no edge per L35.
    t0 = time.perf_counter()
    if section is not None and (is_gap_violation or is_unverified):
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
        evidence  = (f"BG validity-period clause not found in document "
                     f"after searching {', '.join(section_types)} section types "
                     f"(also exhaustive grep across all matching sections — no "
                     f"keyword hits)")
        print(f"  → ABSENCE finding — LLM rerank empty AND grep fallback "
              f"empty; genuine absence")
    elif grep_promoted_to_unverified:
        ev_passed = None
        ev_score  = None
        ev_method = "grep_fallback_retrieval_gap"
        evidence  = (f"LLM rerank top-{K} returned no BG-validity candidate, "
                     f"but exhaustive grep across {', '.join(section_types)} found "
                     f"keyword hits in {len(grep_hits)} section(s) — likely a "
                     f"retrieval coverage gap (relevant section(s) didn't rank in "
                     f"top-{K} by cosine similarity). First match: "
                     f"{grep_hits[0]['snippet'][:160] if grep_hits else 'n/a'}")
        print(f"  → UNVERIFIED finding (grep fallback) — retrieval missed "
              f"{len(grep_hits)} section(s) carrying BG-validity keywords")
    elif is_gap_violation:
        print(f"  → GAP_VIOLATION finding — BG validity period stated but does "
              f"NOT extend through DLP/warranty (verified evidence quote)")
    elif is_unverified:
        print(f"  → UNVERIFIED finding — LLM identified clause but quote "
              f"failed L24 verification (score={ev_score}, method={ev_method})")

    rule_node_id = get_or_create_rule_node(DOC_ID, rule["rule_id"])

    if is_gap_violation:
        label = (
            f"{TYPOLOGY}: BG validity gap — {rule['rule_id']} "
            f"({rule['severity']}) — stated validity {validity_desc!r} does NOT "
            f"extend through DLP/warranty period"
        )
    elif grep_promoted_to_unverified:
        label = (
            f"{TYPOLOGY}: UNVERIFIED (L36 grep-fallback) — LLM rerank missed "
            f"the clause; exhaustive grep found {len(grep_hits)} section(s) "
            f"with BG-validity keyword hits; requires human review"
        )
    elif is_unverified:
        label = (
            f"{TYPOLOGY}: UNVERIFIED — LLM found BG validity clause but quote "
            f"failed L24 (score={ev_score}, method={ev_method}); requires "
            f"human review against {(section['heading'][:60] if section else 'TenderDocument')!r}"
        )
    else:
        label = (
            f"{TYPOLOGY}: BG validity-period clause absent — {rule['rule_id']} "
            f"({rule['severity']}) requires the BG validity period to be "
            f"specified for this tender"
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
        "rule_id":               rule["rule_id"],
        "typology_code":         TYPOLOGY,
        "severity":              rule["severity"],
        "evidence":              evidence,
        "extraction_path":       "presence",
        "llm_found_clause":      llm_found_clause,
        "bg_validity_specified": llm_found_clause,
        "bg_type":               bg_type,
        "validity_period_description":     validity_desc,
        "extends_through_dlp_or_warranty": extends_dlp if llm_found_clause else None,
        "has_buffer_beyond_dlp":           has_buffer,
        "buffer_days":                     buffer_days,
        "go_reference":          go_reference,
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
        "status":                     "UNVERIFIED" if is_unverified else "OPEN",
        "requires_human_review":      bool(is_unverified),
        "human_review_reason": (
            f"L36 grep fallback fired: LLM rerank top-{K} did not surface a "
            f"BG-validity clause, but exhaustive grep across {section_types} "
            f"found keyword hits in {len(grep_hits)} section(s). Reviewer "
            f"should confirm whether the doc actually carries the validity "
            f"spec (open the listed sections in grep_audit.hits)."
            if grep_promoted_to_unverified else
            "LLM found BG-validity clause but evidence quote failed L24 "
            f"verification (score={ev_score}, method={ev_method}). Reviewer "
            f"should open the section above (line_start={line_start_local}, "
            f"line_end={line_end_local}) and confirm the validity period."
            if is_unverified else None
        ),
        # L36 grep-fallback audit
        "grep_fallback_audit":         grep_audit,
        "defeated":            False,
    }

    finding = rest_post("kg_nodes", [{
        "doc_id":    DOC_ID,
        "node_type": "ValidationFinding",
        "label":     label,
        "properties": finding_props,
        "source_ref": f"tier1:bg_validity_check:{rule['rule_id']}",
    }])[0]

    edge = None
    if is_absence or is_gap_violation:
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
                "bg_validity_specified": llm_found_clause,
                "extends_through_dlp_or_warranty": extends_dlp if llm_found_clause else None,
                "validity_period_description":     validity_desc,
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
