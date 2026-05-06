"""
scripts/tier1_mandatory_fields_check.py

Tier-1 Works-Universal-Mandatory-Fields check (sub-typology of
Missing-Mandatory-Field), BGE-M3 + LLM, NO regex.

MULTI-RULE typology with FOUR atomic mandatory-field sub-checks,
all evaluated from a single LLM extraction pass per the L43
pattern. Doc may emit 0–4 findings independently per sub-check
(unlike Arbitration L43 / Geographic L44 which had primary+marker
shape; here all 4 are equal-rank sub-checks).

Sub-checks:

  1. MPG-148 (HARD_BLOCK) — Representation officer + contact + window
     The bidding document must explicitly mention name, designation,
     and contact details of the officer(s) nominated to receive
     bidder representations against rejection. 10-day bidder window;
     15-day decision window.

  2. MPG-293 (HARD_BLOCK, L27→ADVISORY) — Contract Effective Date
     (CED) — must be invariably indicated in each contract, set as
     a date AFTER signing/PB-furnished/BG-advance/export-licence.
     PPP-DCA "Appointed Date" is a near-equivalent shape — emitted
     as ADVISORY-INFORMATIONAL marker rather than HARD_BLOCK.

  3. MPG-150 (HARD_BLOCK) — Post-LoA acknowledgement window
     Successful bidder must acknowledge + sign + return the contract
     agreement within 14 days (OTE) / 28 days (GTE). The bidding
     doc must state this window.

  4. MPG-124 (HARD_BLOCK, L27→ADVISORY) — Figures-vs-words
     discrepancy resolution rule. Words prevail over figures; unit
     price prevails over total. SKIPs cleanly on PPP-DCAs (no BoQ
     unit prices by design).

This is the 17th Tier-1 typology. First sub-typology of
Missing-Mandatory-Field (596 rules, 454 HB) — the user-confirmed
focused subset (MPG-136 + MPG-237 dropped per typology-16 read-first).

Pipeline (post-L44 multi-rule + Method 3 evidence guard):
  1. Pick rules via condition_evaluator. MPG-148 + MPG-150 fire
     clean HARD_BLOCK. MPG-293 + MPG-124 have execution-stage
     subterms (ContractAwarded / PriceDiscrepancyDetected /
     BidSubmissionMode) → UNKNOWN → L27 ADVISORY downgrade.
  2. Section filter via WORKS_MANDATORY_SECTION_ROUTER.
  3. BGE-M3 embed + Qdrant top-K.
  4. LLM rerank with 8-field extraction (one quote, multiple
     sub-check booleans).
  5. L24 evidence guard (Method 3 longest-sentence per L44).
  6. L36 → L40 grep fallback chain on failure paths.
  7. Apply per-sub-check decision tree → emit independent findings.

Tested on judicial_academy_exp_001 first.
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

TYPOLOGY = "Works-Universal-Mandatory-Fields"

QDRANT_URL  = os.environ.get("QDRANT_URL", "http://localhost:6333")
COLLECTION  = "tender_sections"

LLM_MODEL = os.environ.get("LLM_MODEL", "qwen/qwen-2.5-72b-instruct")


# L46 — Per-sub-check grep keywords. Multi-sub-check typologies need
# per-sub-check fallback verification, not just the global all-failed
# absence path. Initial JA test exposed false-positive ABSENCE on
# MPG-148 + MPG-124 because the LLM's top-10 saw only the section
# carrying MPG-150's signal and missed the sections with the others.
# Without per-sub-check grep, the script falsely emitted ABSENCE for
# sub-checks the doc actually satisfies but in different sections.
# Each sub-check now has its own keyword vocabulary; the decision
# tree calls `_grep_for_sub_check` before emitting ABSENCE to verify
# the signal genuinely doesn't exist anywhere in the section_filter
# coverage. If grep finds the signal, downgrade ABSENCE → UNVERIFIED.
SUB_CHECK_GREP_KEYWORDS: dict[str, list[str]] = {
    "representation_officer": [
        "Contact Person",
        "Point of Contact",
        "POC for procurement",
        "procurement related grievances",
        "procurement-related grievances",
        "representation officer",
        "representations against rejection",
        "nodal officer",
        "grievance redressal",
    ],
    "post_loa_window": [
        "14 (fourteen) days",
        "Fourteen (14) days",
        "fourteen days of receipt of LoA",
        "fourteen (14) days",
        "28 (twenty-eight) days",
        "twenty-eight (28) days",
        "Twenty-Eight (28) days",
        "Letter of Acceptance",
        "acknowledge and unconditionally accept",
        "sign, date and return",
        "sign and return",
    ],
    "contract_effective_date": [
        "Contract Effective Date",
        "Effective Date of the Contract",
        "Effective Date of the Agreement",
        "Appointed Date",
    ],
    "figures_vs_words": [
        "figures and words",
        "words shall prevail",
        "words only shall prevail",
        "Correction of Arithmetical Errors",
        "unit price shall prevail",
        "amount in words",
        "amount in figures",
        "discrepancy between figures and words",
        "words and figures",
    ],
}

# Aggregate of all per-sub-check keywords for the global L36/L40
# fallback (used when LLM came back fully empty across all sub-checks).
GREP_FALLBACK_KEYWORDS = [
    kw for kws in SUB_CHECK_GREP_KEYWORDS.values() for kw in kws
]


# Answer-shaped query — mirrors the literal wording of the four
# rule texts plus the AP-CRDA / NREDCAP / SBD clause patterns.
QUERY_TEXT = (
    "Contact Person Point of Contact representation officer "
    "procurement grievances clarification email phone bidder "
    "representation against rejection 10 days 15 days "
    "Contract Effective Date Appointed Date effective from the date "
    "Letter of Acceptance acknowledgement 14 days 28 days "
    "fourteen twenty-eight days post-LoA contract sign return "
    "figures and words discrepancy unit price prevails words prevail "
    "Correction of Arithmetical Errors amount in words"
)


# Rule candidates. Per user spec, MPG-136 + MPG-237 dropped.
# Priority order:
#   1. MPG-148 — TenderType=ANY HARD_BLOCK (representation officer)
#   2. MPG-150 — TenderType=ANY AND ContractValue>=250000 HARD_BLOCK
#   3. MPG-293 — TenderType=ANY AND ContractAwarded=true (UNKNOWN→ADV)
#   4. MPG-124 — TenderType=ANY AND BidSubmissionMode=Physical AND
#                PriceDiscrepancyDetected=true (UNKNOWN→ADV)
RULE_CANDIDATES = [
    {
        "rule_id":          "MPG-148",
        "natural_language": "The bidding document must explicitly mention the name, designation, and contact details of the officer(s) nominated to receive bidder representations against rejection. A representation against post-LoA rejection must be sent within 10 days; the procuring entity must take a decision within 15 days.",
        "severity":         "HARD_BLOCK",
        "layer":            "Central",
        "shape":            "presence",
        "sub_check":        "representation_officer",
    },
    {
        "rule_id":          "MPG-150",
        "natural_language": "After LoA, the successful bidder must acknowledge and unconditionally accept, sign, date and return the contract agreement within 14 days (OTE) / 28 days (GTE). The bidding document must state this window.",
        "severity":         "HARD_BLOCK",
        "layer":            "Central",
        "shape":            "presence",
        "sub_check":        "post_loa_window",
    },
    {
        "rule_id":          "MPG-293",
        "natural_language": "The Contract Effective Date — the date on which contractual obligations commence — must be invariably indicated in each contract, set as a date AFTER signing of contract / PB / BG advance / export licence (where applicable).",
        "severity":         "HARD_BLOCK",
        "layer":            "Central",
        "shape":            "presence",
        "sub_check":        "contract_effective_date",
    },
    {
        "rule_id":          "MPG-124",
        "natural_language": "When discrepancies arise between figures and words: (i) unit price prevails over total price (corrected); (ii) sum-total errors are corrected; (iii) figures-vs-words: WORDS prevail. The bidding document must state these rules.",
        "severity":         "HARD_BLOCK",
        "layer":            "Central",
        "shape":            "presence",
        "sub_check":        "figures_vs_words",
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
            f"Empty section_types for {doc_id}; rule selector should have exited earlier"
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


# ── LLM rerank prompt for 4-sub-check mandatory-field detection ──────

LLM_SYSTEM = (
    "You are a precise procurement-document fact extractor. "
    "Return JSON only. Quote evidence verbatim from the supplied text. "
    "Never fabricate."
)


# Anchor-keyword discipline (per L39 + L45) — only patterns that
# uniquely identify the four sub-check signals. Avoid bare "officer"
# or "date" which match too broadly across procurement docs.
MANDATORY_FIELDS_TRUNCATE_KEYWORDS = [
    # MPG-148
    r"Contact\s+Person",
    r"Point\s+of\s+Contact",
    r"POC\s+for\s+procurement",
    r"procurement[- ]related\s+grievances",
    r"representation\s+(?:officer|to)",
    r"grievance",
    r"nodal\s+officer",
    r"Chief\s+Engineer.*\([H&B]",
    # MPG-293 / Appointed Date
    r"Contract\s+Effective\s+Date",
    r"Effective\s+Date\s+of\s+(?:the\s+)?(?:Contract|Agreement)",
    r"Appointed\s+Date",
    # MPG-150
    r"14\s*\(?\s*fourteen\s*\)?\s+days",
    r"28\s*\(?\s*twenty[- ]eight\s*\)?\s+days",
    r"fourteen\s+days\s+(?:of|from)",
    r"Letter\s+of\s+Acceptance",
    r"acknowledge\s+and\s+(?:unconditionally\s+)?accept",
    # MPG-124
    r"figures\s+and\s+words",
    r"words\s+(?:only\s+)?(?:shall|will)\s+prevail",
    r"unit\s+price\s+(?:shall|will)\s+prevail",
    r"Correction\s+of\s+Arithmetical\s+Errors",
    r"amount\s+in\s+words",
]


def build_mandatory_fields_rerank_prompt(candidates: list[dict]) -> str:
    blocks = []
    for i, c in enumerate(candidates):
        body = smart_truncate(c["full_text"], window=6000,
                              keywords=MANDATORY_FIELDS_TRUNCATE_KEYWORDS)
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
        "Indian / Andhra Pradesh procurement tender document. Extract "
        "a multi-field summary so four atomic mandatory-field sub-"
        "checks can be evaluated from a single read.\n"
        "\n"
        "Sub-checks the caller will run from your output:\n"
        "  1. MPG-148 — REPRESENTATION OFFICER + CONTACT + WINDOW. "
        "Does the doc explicitly name an officer (with designation + "
        "email or phone) for receiving bidder representations against "
        "rejection / procurement grievances? Does it state a window "
        "(typically 10 days for bidders + 15 days for PE decision)?\n"
        "  2. MPG-293 — CONTRACT EFFECTIVE DATE. Does the doc "
        "explicitly indicate the Contract Effective Date — the date "
        "on which contractual obligations commence — set AFTER "
        "signing/PB/BG-advance/export-licence preconditions? PPP-DCA "
        "'Appointed Date' (with conditions-precedent satisfaction) is "
        "a near-equivalent shape — capture separately.\n"
        "  3. MPG-150 — POST-LoA ACKNOWLEDGEMENT WINDOW. After LoA, "
        "does the doc require the bidder to acknowledge + sign + "
        "return the contract agreement within 14 days (OTE) / 28 days "
        "(GTE)?\n"
        "  4. MPG-124 — FIGURES-vs-WORDS DISCREPANCY RULE. Does the "
        "doc state how price discrepancies between figures and words "
        "are resolved — words prevail over figures; unit price "
        "prevails over total?\n"
        "\n"
        f"{candidates_block}\n\n"
        "Return JSON only:\n"
        "{\n"
        "  \"chosen_index\":                              integer 0..N-1 of the most relevant candidate, OR null if no candidate carries any of the four sub-check signals,\n"
        "  \"representation_officer_named\":              bool   (TRUE if specific officer with name AND designation is stated),\n"
        "  \"representation_officer_contact_present\":    bool   (TRUE if email OR phone is given),\n"
        "  \"representation_window_specified\":           bool   (TRUE if 10-day bidder window OR 15-day decision window is stated),\n"
        "  \"contract_effective_date_present\":           bool   (TRUE only for explicit 'Contract Effective Date' / 'Effective Date of the Contract' terminology with conditions),\n"
        "  \"appointed_date_equivalent_present\":         bool   (TRUE for PPP-DCA 'Appointed Date' near-equivalent — date after conditions-precedent satisfied; common in Concession Agreements),\n"
        "  \"post_loa_acknowledgement_window_present\":   bool   (TRUE if doc requires bidder to acknowledge/sign/return contract within 14 days OTE / 28 days GTE post-LoA),\n"
        "  \"figures_vs_words_rule_present\":             bool   (TRUE if doc states words-prevail / unit-price-prevails / Correction of Arithmetical Errors rule),\n"
        "  \"evidence\":                                  \"verbatim quote (single contiguous span) — pick the strongest sub-check signal you found and quote ONLY that one's evidence\",\n"
        "  \"found\":                                     bool,\n"
        "  \"reasoning\":                                 \"one short sentence noting which sub-checks you found\"\n"
        "}\n\n"
        "Selection rules — IGNORE the following content (NOT mandatory-field signals):\n"
        "- 'authorised representatives' references that describe BIDDER's representatives / power-of-attorney holders (those are bidder-side signatory clauses, not procuring-entity representation officers).\n"
        "- 'witnesses' or 'observers' for tender opening — those are procedural, not the bidder-representation-against-rejection officer.\n"
        "- Generic '14-day' or '28-day' references unrelated to post-LoA contract acknowledgement (e.g., adjudicator appointment windows, monthly-statement-checking windows, claim-statement-windows). Only count when explicitly tied to LoA / contract sign-and-return.\n"
        "- 'Appointed Date' references that are about a Project Manager appointment or an adjudicator appointment — only count Appointed Date when it's the contract-commencement-date concept (typically PPP DCAs).\n"
        "- 'Force Majeure costs after Appointed Date' references — that's downstream, count Appointed Date only when DEFINED in the doc.\n"
        "\n"
        "Selection rules — DO pick if the candidate states:\n"
        "- A 'Contact Person' / 'Point of Contact (POC) for procurement related grievances' table or header (typically NIT) with name + email + phone (MPG-148 COMPLIANT).\n"
        "- 'Contract Effective Date shall be...' / 'Appointed Date means the date on which all conditions precedent...' (MPG-293 / Appointed Date).\n"
        "- 'Within 14 (fourteen) days of receipt of LoA, the successful bidder shall sign and return...' (MPG-150).\n"
        "- 'In case of discrepancy between figures and words, the words shall prevail' / 'Unit price shall prevail and total price corrected' (MPG-124).\n"
        "\n"
        "QUOTE FORMAT — STRICT (per L35 + L44):\n"
        "- Return a SINGLE CONTIGUOUS quote from ONE sentence, ONE clause, or ONE table row. Do NOT stitch multiple paragraphs.\n"
        "- Maximum 2 consecutive sentences. Best is ONE sentence.\n"
        "- Do NOT add ellipsis ('...') between lines.\n"
        "- Do NOT paraphrase, summarise, or condense — quote EXACTLY as it appears in the supplied text.\n"
        "- Do NOT introduce or remove markdown formatting.\n"
        "- Pick the SHORTEST contiguous span that proves the STRONGEST single sub-check signal. The other 7 booleans speak for the other signals — the evidence quote only needs to ground ONE.\n"
        "\n"
        "MULTI-SUB-CHECK QUOTE DISCIPLINE (per L44):\n"
        "- This typology has 4 sub-checks. You must STILL return only ONE evidence quote.\n"
        "- DO NOT concatenate text from multiple sub-checks (e.g. (officer line) + (figures-vs-words line)).\n"
        "- Priority for which sub-check to quote: (1) MPG-148 representation officer if present → quote that line. (2) MPG-293/Appointed Date → quote that line. (3) MPG-150 → quote that line. (4) MPG-124 → quote that line. Pick the FIRST applicable; ignore the rest for the evidence field.\n"
        "\n"
        "- If no candidate carries ANY of the four signals, set chosen_index=null, found=false, all 7 booleans=false. The L36 grep fallback will then take over.\n"
        "- Evidence MUST be an exact substring of the chosen candidate's text — verifiable byte-for-byte against the source markdown."
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
            ev_rs = float(ev_cr) * 1e7   # crores → rupees
            facts["EstimatedValue"]      = ev_rs
            facts["ContractValue"]       = ev_rs   # same as EV at bid time; MPG-150 uses ContractValue
            facts["_estimated_value_cr"] = float(ev_cr)
        except (TypeError, ValueError):
            pass

    return facts


def select_mandatory_field_rules(tender_facts: dict) -> list[dict]:
    """Pick ALL rules that fire (or fire-as-UNKNOWN per L27 downgrade).
    Multi-rule shape per L43: returns FULL list — the per-sub-check
    decision tree below evaluates each from the same LLM extraction."""
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
        print(f"    [{rid}] condition={cw!r}  verdict={verdict.value}")
        if verdict == Verdict.FIRE:
            fired.append(dict(cand, defeats=defeats, verdict_origin="FIRE"))
        elif verdict == Verdict.UNKNOWN:
            downgraded = dict(cand, defeats=defeats,
                              severity="ADVISORY",
                              severity_origin=cand["severity"],
                              verdict_origin="UNKNOWN")
            fired.append(downgraded)
    if not fired:
        print(f"  → no rule fires for these facts (correct silence)")
    else:
        print(f"  → {len(fired)} rule(s) fire(s):")
        for r in fired:
            note = ""
            if r.get("verdict_origin") == "UNKNOWN":
                note = (f" [downgraded {r.get('severity_origin')} → "
                        f"ADVISORY because subterm UNKNOWN]")
            print(f"      • {r['rule_id']} ({r['severity']}, sub_check={r['sub_check']}){note}")
    return fired


def get_rule_by_sub_check(fired_rules: list[dict], sub_check: str) -> dict | None:
    """Pick the fired rule for a given sub-check."""
    for r in fired_rules:
        if r["sub_check"] == sub_check:
            return r
    return None


# ── Idempotent re-run cleanup ─────────────────────────────────────────

def _delete_prior_tier1_mandatory(doc_id: str) -> tuple[int, int]:
    """Multi-finding-aware cleanup (per L43): a prior run may have
    emitted up to 4 findings. All are wiped here."""
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


def _materialise_finding(doc_id: str, props: dict, label: str,
                         rule_node_id: str | None,
                         section_node_id: str | None,
                         emit_edge: bool, edge_props: dict | None) -> tuple[str, str | None]:
    """Insert ValidationFinding + (optionally) VIOLATES_RULE edge."""
    finding = rest_post("kg_nodes", [{
        "doc_id":     doc_id,
        "node_type":  "ValidationFinding",
        "label":      label,
        "properties": props,
        "source_ref": f"tier1:mandatory_fields_check:{props.get('rule_id') or 'marker'}",
    }])[0]
    edge_id = None
    if emit_edge and section_node_id and rule_node_id:
        edge = rest_post("kg_edges", [{
            "doc_id":       doc_id,
            "from_node_id": section_node_id,
            "to_node_id":   rule_node_id,
            "edge_type":    "VIOLATES_RULE",
            "weight":       1.0,
            "properties":   {**edge_props, "finding_node_id": finding["node_id"]},
        }])[0]
        edge_id = edge["edge_id"]
    return finding["node_id"], edge_id


# ── Main ──────────────────────────────────────────────────────────────

def main() -> int:
    timings: dict[str, float] = {}
    t_start = time.perf_counter()

    print("=" * 76)
    print(f"  Tier-1 {TYPOLOGY} (BGE-M3 + LLM, NO regex)")
    print(f"  doc_id : {DOC_ID}")
    print(f"  model  : {LLM_MODEL}")
    print(f"  shape  : multi-rule (4 sub-checks: MPG-148/-150/-293/-124)")
    print("=" * 76)

    n_f, n_e = _delete_prior_tier1_mandatory(DOC_ID)
    if n_f or n_e:
        print(f"  cleared {n_f} prior Tier-1 Mandatory-Fields finding node(s) "
              f"and {n_e} edge(s) before re-running")

    facts = fetch_tender_facts(DOC_ID)
    fired_rules = select_mandatory_field_rules(facts)
    if not fired_rules:
        return 0

    rule_148 = get_rule_by_sub_check(fired_rules, "representation_officer")
    rule_150 = get_rule_by_sub_check(fired_rules, "post_loa_window")
    rule_293 = get_rule_by_sub_check(fired_rules, "contract_effective_date")
    rule_124 = get_rule_by_sub_check(fired_rules, "figures_vs_words")
    print(f"\n  Sub-check rule mapping:")
    print(f"    MPG-148 (rep officer)        : {rule_148['rule_id'] if rule_148 else 'n/a'} ({rule_148['severity'] if rule_148 else 'n/a'})")
    print(f"    MPG-150 (post-LoA window)    : {rule_150['rule_id'] if rule_150 else 'n/a'} ({rule_150['severity'] if rule_150 else 'n/a'})")
    print(f"    MPG-293 (contract eff. date) : {rule_293['rule_id'] if rule_293 else 'n/a'} ({rule_293['severity'] if rule_293 else 'n/a'})")
    print(f"    MPG-124 (figures vs words)   : {rule_124['rule_id'] if rule_124 else 'n/a'} ({rule_124['severity'] if rule_124 else 'n/a'})")

    family, section_types = family_for_doc_with_filter(DOC_ID, TYPOLOGY)
    print(f"\n── Document family / retrieval filter ──")
    print(f"  family         : {family}")
    print(f"  section_types  : {section_types}")

    print(f"\n── Query (answer-shaped) ──")
    print(f"  ({len(QUERY_TEXT)} chars)")
    print(f"  {QUERY_TEXT[:300]}...")
    t0 = time.perf_counter()
    qvec = embed_query(QUERY_TEXT)
    timings["embed"] = time.perf_counter() - t0
    print(f"\n── Step 1: BGE-M3 embed ── vec_dim={len(qvec)}  wall={timings['embed']:.2f}s")

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

    t0 = time.perf_counter()
    candidates = []
    for p in points:
        sec = resolve_section(DOC_ID, p["payload"])
        sec["similarity"] = p["score"]
        candidates.append(sec)
    timings["fetch_section"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    print(f"\n── Step 3: LLM rerank + 4-sub-check extraction ──")
    user_prompt = build_mandatory_fields_rerank_prompt(candidates)
    print(f"  prompt size: {len(user_prompt)} chars (~{len(user_prompt)//4} tokens)")
    raw_content = call_llm(LLM_SYSTEM, user_prompt, max_tokens=1100)
    timings["llm"] = time.perf_counter() - t0
    print(f"  wall: {timings['llm']:.2f}s")
    print(f"\n── Raw LLM JSON ──")
    print(raw_content)

    parsed = parse_llm_response(raw_content)
    chosen           = parsed.get("chosen_index")
    rep_officer_named = bool(parsed.get("representation_officer_named"))
    rep_contact       = bool(parsed.get("representation_officer_contact_present"))
    rep_window        = bool(parsed.get("representation_window_specified"))
    ced_present       = bool(parsed.get("contract_effective_date_present"))
    appointed_date    = bool(parsed.get("appointed_date_equivalent_present"))
    post_loa_window   = bool(parsed.get("post_loa_acknowledgement_window_present"))
    figs_vs_words     = bool(parsed.get("figures_vs_words_rule_present"))
    evidence          = (parsed.get("evidence") or "").strip()
    found             = bool(parsed.get("found"))
    reason            = (parsed.get("reasoning") or "").strip()

    print(f"\n── Parsed (multi-sub-check) ──")
    print(f"  chosen_index                                : {chosen}")
    print(f"  found                                       : {found}")
    print(f"  representation_officer_named                : {rep_officer_named}")
    print(f"  representation_officer_contact_present      : {rep_contact}")
    print(f"  representation_window_specified             : {rep_window}")
    print(f"  contract_effective_date_present             : {ced_present}")
    print(f"  appointed_date_equivalent_present           : {appointed_date}")
    print(f"  post_loa_acknowledgement_window_present     : {post_loa_window}")
    print(f"  figures_vs_words_rule_present               : {figs_vs_words}")
    print(f"  reasoning                                   : {reason[:200]}")
    print(f"  evidence                                    : {evidence[:300]!r}")

    section = None
    similarity = None
    ev_passed = False
    ev_score = 0
    ev_method = "skipped"

    llm_chose_candidate = (chosen is not None and isinstance(chosen, int)
                           and 0 <= chosen < len(candidates))
    any_signal = (rep_officer_named or rep_contact or ced_present or appointed_date
                  or post_loa_window or figs_vs_words)

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
        else:
            ev_passed = False; ev_score = 0; ev_method = "empty"
            print(f"  ⚠ no evidence quote provided — treating as L24-failed (empty)")
    else:
        print(f"  → no candidate chosen by LLM")

    raw_is_absence = (not llm_chose_candidate) or (not any_signal)
    is_unverified_l24_fail = (llm_chose_candidate and any_signal and not ev_passed)

    grep_hits: list[dict] = []
    full_grep_hits: list[dict] = []
    grep_promoted_to_unverified = False
    full_grep_promoted = False
    kg_coverage_gap = False
    if raw_is_absence or is_unverified_l24_fail:
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
                print(f"  → absence downgraded to UNVERIFIED — retrieval-coverage gap")
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
            if any_full:
                kg_coverage_gap = any(h["kg_coverage_gap"] for h in full_grep_hits)
                if raw_is_absence or kg_coverage_gap:
                    full_grep_promoted = True
                    if is_unverified_l24_fail and kg_coverage_gap:
                        is_unverified_l24_fail = False
                    print(f"  → "
                          f"{'absence' if raw_is_absence else 'L24-fail'} "
                          f"downgraded to UNVERIFIED — "
                          f"{'kg_coverage_gap' if kg_coverage_gap else 'whole-file-only'} hit")

    is_absence    = raw_is_absence and not grep_promoted_to_unverified and not full_grep_promoted
    is_unverified_global = (is_unverified_l24_fail or grep_promoted_to_unverified
                            or full_grep_promoted)

    # ── Decision tree per sub-check ──────────────────────────────────
    # If global UNVERIFIED, emit ONE primary UNVERIFIED finding with all
    # sub-check booleans captured (auditor can review). If absence,
    # emit per-sub-check ABSENCE findings. If L24 passes, evaluate
    # each sub-check independently.
    findings_emitted: list[str] = []
    edges_emitted:    list[str] = []
    is_works = (facts.get("tender_type") in ("Works", "EPC"))
    is_ppp   = (facts.get("tender_type") == "PPP")

    print(f"\n── Decision (4 sub-checks) ──")

    if is_unverified_global:
        # Single UNVERIFIED finding citing the highest-priority rule
        # (MPG-148). All sub-check booleans captured for review.
        primary_rule = rule_148 or rule_150 or rule_293 or rule_124
        print(f"  → UNVERIFIED chain triggered — single audit-only finding emitted")
        if primary_rule is None:
            return 0

        td_rows = rest_get("kg_nodes", {
            "select":    "node_id",
            "doc_id":    f"eq.{DOC_ID}",
            "node_type": "eq.TenderDocument",
        })
        td_node_id = td_rows[0]["node_id"] if td_rows else None
        rn_id = get_or_create_rule_node(DOC_ID, primary_rule["rule_id"])

        ev_method_out = ("grep_fallback_retrieval_gap"
                         if grep_promoted_to_unverified
                         else "whole_file_grep_kg_coverage_gap" if (full_grep_promoted and kg_coverage_gap)
                         else "whole_file_grep_match" if full_grep_promoted
                         else ev_method)
        evidence_out = (
            f"L36 fallback found {len(grep_hits)} section(s) with mandatory-field "
            f"keyword hits across {section_types}"
            if grep_promoted_to_unverified else
            f"L40 whole-file fallback found {len(full_grep_hits)} match line(s)"
            f"{' (KG-coverage GAP)' if kg_coverage_gap else ''}"
            if full_grep_promoted else
            f"LLM identified mandatory-field signal but quote failed L24 "
            f"(score={ev_score}, method={ev_method})"
        )

        unv_props = {
            "rule_id":                   primary_rule["rule_id"],
            "typology_code":             TYPOLOGY,
            "severity":                  primary_rule["severity"],
            "evidence":                  evidence_out,
            "extraction_path":           "presence",
            "rule_shape":                "presence",
            "violation_reason":          ("mandatory_fields_unverified_grep_fallback_retrieval_gap"
                                          if grep_promoted_to_unverified else
                                          "mandatory_fields_unverified_kg_coverage_gap"
                                          if (full_grep_promoted and kg_coverage_gap) else
                                          "mandatory_fields_unverified_whole_file_grep_only"
                                          if full_grep_promoted else
                                          "mandatory_fields_unverified_llm_quote_failed_l24"),
            # All sub-check booleans captured
            "representation_officer_named":              rep_officer_named,
            "representation_officer_contact_present":    rep_contact,
            "representation_window_specified":           rep_window,
            "contract_effective_date_present":           ced_present,
            "appointed_date_equivalent_present":         appointed_date,
            "post_loa_acknowledgement_window_present":   post_loa_window,
            "figures_vs_words_rule_present":             figs_vs_words,
            "tier":                      1,
            "extracted_by":              "bge-m3+llm-rerank:qwen-2.5-72b@openrouter",
            "doc_family":                family,
            "section_filter":            section_types,
            "rerank_chosen_index":       chosen,
            "rerank_reasoning":          reason,
            "section_node_id":           td_node_id,
            "evidence_in_source":        None,
            "evidence_verified":         None,
            "evidence_match_score":      None,
            "evidence_match_method":     ev_method_out,
            "estimated_value_cr":        facts.get("_estimated_value_cr"),
            "verdict_origin":            primary_rule.get("verdict_origin"),
            "severity_origin":           primary_rule.get("severity_origin"),
            "status":                    "UNVERIFIED",
            "requires_human_review":     True,
            "defeated":                  False,
        }
        if grep_promoted_to_unverified or full_grep_promoted:
            unv_props["grep_fallback_audit"] = {
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
        unv_label = (f"{TYPOLOGY}: UNVERIFIED — multi-sub-check evidence "
                     f"failed L24 / grep-fallback-only signal; requires human review")
        fid, _ = _materialise_finding(DOC_ID, unv_props, unv_label,
                                      None, td_node_id,
                                      emit_edge=False, edge_props=None)
        findings_emitted.append(fid)
        print(f"  → UNVERIFIED ValidationFinding {fid}")
    else:
        # Per-sub-check decision
        td_rows = rest_get("kg_nodes", {
            "select":    "node_id",
            "doc_id":    f"eq.{DOC_ID}",
            "node_type": "eq.TenderDocument",
        })
        td_node_id = td_rows[0]["node_id"] if td_rows else None

        # Section attachment for COMPLIANT/PARTIAL signals (LLM evidence
        # quote points there). Absences attach to TenderDocument.
        if section is not None:
            section_node_id_for_compliant = section["section_node_id"]
            sec_heading_for_compliant     = section["heading"]
            src_file_for_compliant        = section["source_file"]
            ls_for_compliant              = section["line_start_local"]
            le_for_compliant              = section["line_end_local"]
            qsim_for_compliant            = round(similarity, 4) if similarity is not None else None
        else:
            section_node_id_for_compliant = td_node_id
            sec_heading_for_compliant     = None
            src_file_for_compliant        = None
            ls_for_compliant              = None
            le_for_compliant              = None
            qsim_for_compliant            = None

        # Helper: per-sub-check finding emission
        def _emit_sub_check_finding(rule, sub_check_kind, severity, status, reason_label,
                                     label, evidence_str, attach_to_section, ev_method_local):
            attach_node_id = (section_node_id_for_compliant if attach_to_section
                              else td_node_id)
            sec_heading = sec_heading_for_compliant if attach_to_section else None
            src_file    = src_file_for_compliant    if attach_to_section else None
            ls          = ls_for_compliant          if attach_to_section else None
            le          = le_for_compliant          if attach_to_section else None
            qsim        = qsim_for_compliant        if attach_to_section else None
            ev_in_src   = (None if status != "OPEN" or not attach_to_section else ev_passed)
            ev_score_l  = (None if status != "OPEN" or not attach_to_section else ev_score)
            rn_id_local = get_or_create_rule_node(DOC_ID, rule["rule_id"])
            props = {
                "rule_id":               rule["rule_id"],
                "typology_code":         TYPOLOGY,
                "severity":              severity,
                "evidence":              evidence_str,
                "extraction_path":       "presence",
                "rule_shape":            rule["shape"],
                "sub_check_kind":        sub_check_kind,
                "violation_reason":      reason_label,
                # Multi-field LLM extraction snapshot (audit context for all sub-checks)
                "representation_officer_named":              rep_officer_named,
                "representation_officer_contact_present":    rep_contact,
                "representation_window_specified":           rep_window,
                "contract_effective_date_present":           ced_present,
                "appointed_date_equivalent_present":         appointed_date,
                "post_loa_acknowledgement_window_present":   post_loa_window,
                "figures_vs_words_rule_present":             figs_vs_words,
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
                "section_node_id":       attach_node_id,
                "section_heading":       sec_heading,
                "source_file":           src_file,
                "line_start_local":      ls,
                "line_end_local":        le,
                "qdrant_similarity":     qsim,
                "evidence_in_source":    ev_in_src,
                "evidence_verified":     ev_in_src,
                "evidence_match_score":  ev_score_l,
                "evidence_match_method": ev_method_local,
                "estimated_value_cr":    facts.get("_estimated_value_cr"),
                "verdict_origin":        rule.get("verdict_origin"),
                "severity_origin":       rule.get("severity_origin"),
                "status":                status,
                "requires_human_review": False,
                "defeated":              False,
            }
            edge_props = ({
                "rule_id":               rule["rule_id"],
                "typology":              TYPOLOGY,
                "severity":              severity,
                "defeated":              False,
                "tier":                  1,
                "extraction_path":       "presence",
                "violation_reason":      reason_label,
                "sub_check_kind":        sub_check_kind,
                "doc_family":            family,
                "qdrant_similarity":     qsim,
                "evidence":              evidence_str,
                "evidence_match_score":  ev_score_l,
                "evidence_match_method": ev_method_local,
            } if status == "OPEN" else None)
            fid, eid = _materialise_finding(
                DOC_ID, props, label, rn_id_local, attach_node_id,
                emit_edge=(status == "OPEN"),
                edge_props=edge_props,
            )
            findings_emitted.append(fid)
            if eid: edges_emitted.append(eid)

        # L46 — Per-sub-check grep verification before emitting ABSENCE.
        # When the LLM says a sub-check signal is False, we don't trust
        # that as ABSENCE without checking the rest of the section_filter
        # coverage with a sub-check-specific grep. If grep finds the
        # signal in any section, downgrade ABSENCE → UNVERIFIED with
        # grep_audit. If grep is empty, ABSENCE stands.
        def _verify_sub_check_absence(sub_check_kind: str) -> tuple[bool, list[dict], list[dict], bool]:
            """Returns (any_section_hit, section_hits, full_file_hits, kg_gap)."""
            kws = SUB_CHECK_GREP_KEYWORDS.get(sub_check_kind, [])
            any_hit, hits = grep_source_for_keywords(DOC_ID, section_types, kws)
            if any_hit:
                return True, hits, [], False
            # Tier-2 whole-file fallback for kg_coverage_gap detection
            any_full, full_hits = grep_full_source_for_keywords(DOC_ID, kws)
            kg_gap = any(h["kg_coverage_gap"] for h in full_hits) if any_full else False
            return any_full, [], full_hits, kg_gap

        # ── Sub-check 1: MPG-148 (representation officer + contact)
        if rule_148 is not None:
            print(f"\n  Sub-check 1 (MPG-148 representation officer):")
            if rep_officer_named and rep_contact:
                if rep_window:
                    print(f"    → COMPLIANT — officer named + contact + window all present")
                else:
                    print(f"    → ADVISORY-PARTIAL — officer + contact present but window not specified")
                    _emit_sub_check_finding(
                        rule_148, "advisory_partial", "ADVISORY", "OPEN",
                        "representation_officer_present_window_missing",
                        f"{TYPOLOGY}: {rule_148['rule_id']} (ADVISORY-PARTIAL) — representation officer + contact present but 10-day bidder / 15-day decision window not specified",
                        evidence, attach_to_section=True, ev_method_local=ev_method,
                    )
            else:
                # LLM says officer/contact absent. L46 verification.
                any_h, sec_hits, full_hits, kg_gap = _verify_sub_check_absence("representation_officer")
                if any_h:
                    via = "L36" if sec_hits else "L40"
                    print(f"    → UNVERIFIED ({via} grep — LLM missed signal but doc has keywords)")
                    _emit_sub_check_finding(
                        rule_148, "unverified_grep_promoted", rule_148["severity"], "UNVERIFIED",
                        f"representation_officer_unverified_{via.lower()}_grep_promoted",
                        f"{TYPOLOGY}: {rule_148['rule_id']} UNVERIFIED — LLM did not surface representation officer in top-10, but {via} grep found signal in {len(sec_hits) or len(full_hits)} location(s); requires human review",
                        f"L46 per-sub-check {via}-grep found {len(sec_hits) or len(full_hits)} hit(s) for representation_officer keywords (LLM missed)",
                        attach_to_section=False,
                        ev_method_local=f"l46_per_subcheck_{via.lower()}_grep_promoted",
                    )
                else:
                    print(f"    → HARD_BLOCK ABSENCE — no representation officer signal anywhere in section_filter")
                    _emit_sub_check_finding(
                        rule_148, "absence", rule_148["severity"], "OPEN",
                        "representation_officer_absent",
                        f"{TYPOLOGY}: {rule_148['rule_id']} ({rule_148['severity']}) — no specific representation officer named with name + designation + contact details for receiving bidder representations against rejection (verified absent via LLM rerank + L46 per-sub-check grep + L40 whole-file grep)",
                        f"Representation officer not found after LLM retrieval, L46 Section-bounded grep, and L40 whole-file grep on {', '.join(section_types)}",
                        attach_to_section=False,
                        ev_method_local="absence_finding_no_evidence",
                    )

        # ── Sub-check 2: MPG-150 (post-LoA window)
        if rule_150 is not None:
            print(f"\n  Sub-check 2 (MPG-150 post-LoA window):")
            if post_loa_window:
                print(f"    → COMPLIANT — 14d/28d post-LoA acknowledgement window stated")
            else:
                any_h, sec_hits, full_hits, kg_gap = _verify_sub_check_absence("post_loa_window")
                if any_h:
                    via = "L36" if sec_hits else "L40"
                    print(f"    → UNVERIFIED ({via} grep — LLM missed signal but doc has keywords)")
                    _emit_sub_check_finding(
                        rule_150, "unverified_grep_promoted", rule_150["severity"], "UNVERIFIED",
                        f"post_loa_window_unverified_{via.lower()}_grep_promoted",
                        f"{TYPOLOGY}: {rule_150['rule_id']} UNVERIFIED — LLM did not surface post-LoA window in top-10, but {via} grep found signal; requires human review",
                        f"L46 per-sub-check {via}-grep found hit(s) for post_loa_window keywords",
                        attach_to_section=False,
                        ev_method_local=f"l46_per_subcheck_{via.lower()}_grep_promoted",
                    )
                else:
                    print(f"    → {rule_150['severity']} ABSENCE — post-LoA 14d/28d window not stated anywhere")
                    _emit_sub_check_finding(
                        rule_150, "absence", rule_150["severity"], "OPEN",
                        "post_loa_acknowledgement_window_absent",
                        f"{TYPOLOGY}: {rule_150['rule_id']} ({rule_150['severity']}) — post-LoA acknowledgement window (14 days OTE / 28 days GTE for bidder to sign and return contract) not stated (verified absent via LLM + L46 + L40)",
                        f"Post-LoA acknowledgement window not found after LLM, L46 grep, L40 whole-file grep on {', '.join(section_types)}",
                        attach_to_section=False,
                        ev_method_local="absence_finding_no_evidence",
                    )

        # ── Sub-check 3: MPG-293 (Contract Effective Date / Appointed Date)
        if rule_293 is not None:
            print(f"\n  Sub-check 3 (MPG-293 contract effective date):")
            if ced_present:
                print(f"    → COMPLIANT — explicit Contract Effective Date terminology present")
            elif appointed_date and is_ppp:
                print(f"    → ADVISORY-INFORMATIONAL — PPP-DCA Appointed Date near-equivalent recognised")
                _emit_sub_check_finding(
                    rule_293, "appointed_date_marker", "ADVISORY", "OPEN",
                    "contract_effective_date_appointed_date_equivalent_recognised",
                    f"{TYPOLOGY}: {rule_293['rule_id']} (ADVISORY-INFO) — PPP-DCA 'Appointed Date' near-equivalent to Contract Effective Date present (date of conditions-precedent satisfaction)",
                    evidence, attach_to_section=True, ev_method_local=ev_method,
                )
            elif appointed_date and not is_ppp:
                print(f"    → ADVISORY-PARTIAL — Appointed Date used but not the explicit Contract Effective Date framework")
                _emit_sub_check_finding(
                    rule_293, "advisory_partial", "ADVISORY", "OPEN",
                    "contract_effective_date_partial_appointed_date_only",
                    f"{TYPOLOGY}: {rule_293['rule_id']} (ADVISORY-PARTIAL) — Appointed Date concept used but explicit Contract Effective Date with all 4 preconditions (signing/PB/BG-advance/export-licence) not stated",
                    evidence, attach_to_section=True, ev_method_local=ev_method,
                )
            else:
                any_h, sec_hits, full_hits, kg_gap = _verify_sub_check_absence("contract_effective_date")
                if any_h:
                    via = "L36" if sec_hits else "L40"
                    print(f"    → UNVERIFIED ({via} grep — LLM missed signal but doc has keywords)")
                    _emit_sub_check_finding(
                        rule_293, "unverified_grep_promoted", rule_293["severity"], "UNVERIFIED",
                        f"contract_effective_date_unverified_{via.lower()}_grep_promoted",
                        f"{TYPOLOGY}: {rule_293['rule_id']} UNVERIFIED — LLM did not surface Contract Effective Date / Appointed Date, but {via} grep found signal; requires human review",
                        f"L46 per-sub-check {via}-grep found hit(s) for contract_effective_date keywords",
                        attach_to_section=False,
                        ev_method_local=f"l46_per_subcheck_{via.lower()}_grep_promoted",
                    )
                else:
                    print(f"    → {rule_293['severity']} ABSENCE — neither Contract Effective Date nor Appointed Date anywhere")
                    _emit_sub_check_finding(
                        rule_293, "absence", rule_293["severity"], "OPEN",
                        "contract_effective_date_absent",
                        f"{TYPOLOGY}: {rule_293['rule_id']} ({rule_293['severity']}) — Contract Effective Date not specified (and no PPP Appointed Date equivalent present); contractual-obligations commencement is undefined (verified absent via LLM + L46 + L40)",
                        f"Contract Effective Date / Appointed Date not found after LLM, L46 grep, L40 whole-file grep on {', '.join(section_types)}",
                        attach_to_section=False,
                        ev_method_local="absence_finding_no_evidence",
                    )

        # ── Sub-check 4: MPG-124 (figures-vs-words rule)
        if rule_124 is not None:
            print(f"\n  Sub-check 4 (MPG-124 figures vs words):")
            if figs_vs_words:
                print(f"    → COMPLIANT — figures-vs-words / unit-price-prevails rule stated")
            elif is_ppp:
                # PPP-DCAs don't have BoQ unit-price discrepancies; absence by design
                print(f"    → SKIP — PPP-DCA has no BoQ unit prices; figures-vs-words rule absent by design")
            else:
                any_h, sec_hits, full_hits, kg_gap = _verify_sub_check_absence("figures_vs_words")
                if any_h:
                    via = "L36" if sec_hits else "L40"
                    print(f"    → UNVERIFIED ({via} grep — LLM missed signal but doc has keywords)")
                    _emit_sub_check_finding(
                        rule_124, "unverified_grep_promoted", rule_124["severity"], "UNVERIFIED",
                        f"figures_vs_words_unverified_{via.lower()}_grep_promoted",
                        f"{TYPOLOGY}: {rule_124['rule_id']} UNVERIFIED — LLM did not surface figures-vs-words rule, but {via} grep found signal; requires human review",
                        f"L46 per-sub-check {via}-grep found hit(s) for figures_vs_words keywords",
                        attach_to_section=False,
                        ev_method_local=f"l46_per_subcheck_{via.lower()}_grep_promoted",
                    )
                else:
                    print(f"    → {rule_124['severity']} ABSENCE — figures-vs-words rule not stated anywhere")
                    _emit_sub_check_finding(
                        rule_124, "absence", rule_124["severity"], "OPEN",
                        "figures_vs_words_rule_absent",
                        f"{TYPOLOGY}: {rule_124['rule_id']} ({rule_124['severity']}) — figures-vs-words discrepancy resolution rule (words prevail / unit-price prevails) not stated in bidding document (verified absent via LLM + L46 + L40)",
                        f"Figures-vs-words rule not found after LLM, L46 grep, L40 whole-file grep on {', '.join(section_types)}",
                        attach_to_section=False,
                        ev_method_local="absence_finding_no_evidence",
                    )

    timings["total_wall"] = time.perf_counter() - t_start
    print()
    print("=" * 76)
    print(f"  SUMMARY — {len(findings_emitted)} finding(s), {len(edges_emitted)} edge(s)")
    print("=" * 76)
    for k, v in timings.items():
        unit = "s" if v >= 1 else "ms"
        val  = v if v >= 1 else v * 1000
        print(f"    {k:18s} {val:8.2f} {unit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
