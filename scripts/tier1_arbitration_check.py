"""
scripts/tier1_arbitration_check.py

Tier-1 Arbitration-Clause-Violation check, BGE-M3 + LLM, NO regex.

MULTI-RULE typology with FOUR sub-checks evaluated from a single
LLM extraction pass:

  Sub-check 1 — PRESENCE (MPG-304 / MPW-139 baseline, HARD_BLOCK).
    Does the doc carry a dispute-resolution clause naming arbitration?

  Sub-check 2 — UNILATERAL-APPOINTMENT ANTI-PATTERN (MPW25-104,
    HARD_BLOCK; Works only).
    Does the doc retain unilateral-appointment language by Govt/PSU
    or mandate selection from a Govt-curated panel? Per Supreme Court
    ruling 08-Nov-2024 (CORE v ECL-SPIC-SMO-MCML, 2024 INSC 857)
    such clauses are INVALIDATED. Anti-pattern presence = violation.

  Sub-check 3 — VENUE / SBD-STANDARD COMPLIANCE (MPG-186 / MPW-141,
    advisory in this typology — downgraded below the absence/anti-
    pattern severities because seat specification frequently lives in
    SCC/PCC and a no-find here is often SBD-template-default-compliant).

  Sub-check 4 — AP CIVIL-COURT LADDER MARKER (AP-GO-229, ADVISORY-
    INFORMATIONAL; AP Works/EPC only).
    APSS Clause 61 + GO Ms No 94/2003 §14 routes claims > Rs.50,000
    to civil court. AP-acceptable departure from Central arbitration
    default — `do NOT block tender publication`. Emitted as a
    standalone OPEN ADVISORY informational finding when applicable.

A doc may emit 0, 1, or 2 findings: a primary finding (compliant /
violation / UNVERIFIED / absence) AND an AP-GO-229 informational
finding when AP Works AND ladder visible.

Pipeline (post-L41 four-state contract + L40 whole-file fallback):
  1. Pick rule via condition_evaluator (priority order: MPG-304 →
     MPW-139 → MPW25-104 → AP-GO-229).
  2. Section filter via ARBITRATION_SECTION_ROUTER.
  3. BGE-M3 embed + Qdrant top-K.
  4. LLM rerank with multi-field extraction.
  5. L24 evidence guard.
  6. L36 → L40 grep fallback chain on absence path.
  7. Apply decision tree.
  8. Emit primary finding + (optionally) AP-GO-229 informational.

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

TYPOLOGY = "Arbitration-Clause-Violation"

QDRANT_URL  = os.environ.get("QDRANT_URL", "http://localhost:6333")
COLLECTION  = "tender_sections"

LLM_MODEL = os.environ.get("LLM_MODEL", "qwen/qwen-2.5-72b-instruct")


# L36 source-grep fallback vocabulary. Phrase-precise enough that a
# random "arbitration" mention in dispute-resolution-adjacent prose
# triggers the fallback while typology-irrelevant content doesn't.
GREP_FALLBACK_KEYWORDS = [
    "arbitration",
    "dispute resolution",
    "adjudication",
    "arbitrator",
    "civil court",
    "APSS Clause 61",
    "APSS clause 61",
    "Dispute Board",
    "amicable",
    "ICA",
    "UNCITRAL",
    "Arbitration Act 1996",
    "Arbitration and Conciliation Act",
    "GO Ms No. 94",
    "GO Ms No 94",
]


# Answer-shaped query — mirrors the wording of the canonical clause
# templates (CLAUSE-DISPUTE-RESOLUTION-001, CLAUSE-ARBITRATION-001,
# CLAUSE-ARBITRATION-AGREEMENT-001, CLAUSE-AP-CIVIL-COURT-50K-001,
# CLAUSE-AP-3-ARBITRATOR-ICA-001) plus the rule-text vocabulary.
QUERY_TEXT = (
    "Arbitration clause dispute resolution Indian Arbitration "
    "and Conciliation Act 1996 sole arbitrator three arbitrator "
    "tribunal seat venue civil court APSS clause 61 escalation "
    "ladder Superintending Engineer Chief Engineer adjudicator "
    "amicable consultation conciliation Dispute Board UNCITRAL "
    "ICA Indian Council of Arbitration"
)


# Rule candidates evaluated via condition_evaluator. Priority order
# per user spec:
#   1. MPG-304   — TenderType=ANY, HARD_BLOCK absence baseline
#   2. MPW-139   — Works specialisation, HARD_BLOCK absence
#   3. MPW25-104 — Works anti-pattern, HARD_BLOCK unilateral-appointment
#   4. AP-GO-229 — AP Works/EPC, ADVISORY informational ladder marker
#
# Rule selection collects ALL fired rules; the typology may evaluate
# multiple sub-checks against the same LLM extraction.
RULE_CANDIDATES = [
    {
        "rule_id":          "MPG-304",
        "natural_language": "Every contract MUST carry an arbitration clause/agreement; absence makes arbitration unavailable and forces costly civil litigation",
        "severity":         "HARD_BLOCK",
        "layer":            "Central",
        "shape":            "presence",
        "sub_check":        "presence",
    },
    {
        "rule_id":          "MPW-139",
        "natural_language": "Works contracts MUST contain a dispute-resolution clause specifying the dispute method, with amicable consultation as the pre-formal step",
        "severity":         "HARD_BLOCK",
        "layer":            "Central",
        "shape":            "presence",
        "sub_check":        "presence",
    },
    {
        "rule_id":          "MPW25-104",
        "natural_language": "Per Supreme Court ruling 08-Nov-2024 (CORE v ECL-SPIC) clauses allowing Govt/PSU unilateral appointment of sole arbitrator OR mandating selection from a Govt-curated panel are INVALIDATED in Works contracts",
        "severity":         "HARD_BLOCK",
        "layer":            "Central",
        "shape":            "anti_pattern",
        "sub_check":        "unilateral_appointment",
    },
    {
        "rule_id":          "AP-GO-229",
        "natural_language": "AP Works/EPC tenders > Rs.50,000 routed to civil court per APSS Clause 61 + GO Ms No 94/2003 §14 — AP-acceptable departure from Central arbitration default; do NOT block tender publication",
        "severity":         "ADVISORY",
        "layer":            "AP-State",
        "shape":            "informational",
        "sub_check":        "ap_ladder",
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


# ── LLM rerank prompt for multi-field arbitration extraction ──────────

LLM_SYSTEM = (
    "You are a precise procurement-document fact extractor. "
    "Return JSON only. Quote evidence verbatim from the supplied text. "
    "Never fabricate."
)


# Anchor-keyword discipline (per L39): only patterns that uniquely
# identify arbitration / dispute-resolution content. Avoid broad
# keywords that match dispute-adjacent prose elsewhere (e.g. bare
# "court", "settlement"). The formula-style and Act-reference patterns
# anchor the smart_truncate window on the right line ranges.
ARBITRATION_TRUNCATE_KEYWORDS = [
    r"\bArbitration\b",
    r"Arbitration\s+Agreement",
    r"Arbitration\s+and\s+Conciliation\s+Act",
    r"Arbitration\s+Act\s+1996",
    r"\bDispute\s+Resolution\b",
    r"Disputes?\s+Resolution\s+Board",
    r"\bAdjudicat(?:or|ion)\b",
    r"\bArbitrators?\b",
    r"\bUNCITRAL\b",
    r"\bICA\b",
    r"Indian\s+Council\s+of\s+Arbitration",
    r"sole\s+arbitrator",
    r"three\s+arbitrators?",
    r"unilateral(?:ly)?\s+appoint",
    r"appoint(?:ed|ment)\s+by\s+(?:the\s+)?Authority",
    r"empanel(?:led|ment)",
    r"civil\s+court",
    r"APSS\s+Clause",
    r"GO\s*Ms\.?\s*No\.?\s*94",
    r"Rs\.?\s*50,?000",
    r"50,?000",
    r"venue\s+of\s+arbitration",
    r"seat\s+of\s+arbitration",
]


def build_arbitration_rerank_prompt(candidates: list[dict]) -> str:
    blocks = []
    for i, c in enumerate(candidates):
        body = smart_truncate(c["full_text"], window=6000,
                              keywords=ARBITRATION_TRUNCATE_KEYWORDS)
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
        "Indian / Andhra Pradesh procurement tender document. Exactly "
        "ONE of them (or none) carries the dispute-resolution / "
        "arbitration clause. Extract a multi-field summary so multiple "
        "sub-checks can be evaluated from a single read.\n"
        "\n"
        "Sub-checks the caller will run from your output:\n"
        "  1. PRESENCE       — does the doc state arbitration as the "
        "dispute-resolution mode (Indian Arbitration & Conciliation "
        "Act 1996 named OR equivalent reference)?\n"
        "  2. UNILATERAL     — does the doc retain prohibited "
        "language allowing the Authority/Govt/PSU to unilaterally "
        "appoint a sole arbitrator OR mandating bidder selection from "
        "a Govt-curated panel? (Supreme Court ruling 08-Nov-2024 in "
        "CORE v ECL-SPIC-SMO-MCML, 2024 INSC 857 INVALIDATES this.)\n"
        "  3. VENUE/SEAT     — is the seat or venue of arbitration "
        "specified (and at the contract-issuing place, if visible)?\n"
        "  4. AP CIVIL-COURT LADDER — does the doc carry the AP-State "
        "value-based escalation ladder (e.g. claims up to Rs.10,000 → "
        "Superintending Engineer; up to Rs.50,000 → Chief Engineer; "
        "above Rs.50,000 → CIVIL COURT) per APSS Clause 61 / GO Ms "
        "No 94/2003? This is an AP-acceptable departure from the "
        "Central arbitration default — informational, not a violation.\n"
        "\n"
        f"{candidates_block}\n\n"
        "Return JSON only:\n"
        "{\n"
        "  \"chosen_index\":                       integer 0..N-1 of the dispute-resolution / arbitration candidate, OR null if no candidate carries it,\n"
        "  \"arbitration_clause_present\":         bool   (TRUE if doc names arbitration as the dispute-resolution mode — under Indian Act 1996 / UNCITRAL / equivalent),\n"
        "  \"dispute_resolution_clause_present\":  bool   (TRUE if doc has any dispute-resolution clause — broader than just arbitration),\n"
        "  \"arbitration_act_referenced\":         \"Indian_1996\" | \"UNCITRAL\" | \"both\" | \"unspecified\" | null,\n"
        "  \"seat_or_venue_specified\":            bool,\n"
        "  \"seat_or_venue_text\":                 string OR null  (e.g. 'Vijayawada' or 'place of contract issue'),\n"
        "  \"unilateral_appointment_present\":     bool   (TRUE if Authority/Govt/PSU may unilaterally appoint sole arbitrator),\n"
        "  \"appointment_by_curated_panel\":       bool   (TRUE if doc mandates bidder choice from a Govt-curated panel),\n"
        "  \"ap_civil_court_ladder_present\":      bool   (TRUE if doc carries AP-State value-tier escalation ladder routing > Rs.50,000 to civil court),\n"
        "  \"escalation_tiers_visible\":           bool   (TRUE if doc lists specific value tiers and adjudicator roles e.g. SE/CE/civil court),\n"
        "  \"three_arbitrator_panel\":             bool   (TRUE if doc specifies 3-arbitrator tribunal — typically each party 1 + presiding by ICA fallback),\n"
        "  \"foreign_arbitration_option\":         bool   (TRUE if foreign supplier given Indian Act 1996 OR UNCITRAL choice),\n"
        "  \"evidence\":                           \"verbatim quote (single contiguous span) — the line(s) that establish the arbitration / dispute-resolution clause\",\n"
        "  \"found\":                              bool,\n"
        "  \"reasoning\":                          \"one short sentence explaining the choice and what shape the clause takes\"\n"
        "}\n\n"
        "Selection rules — IGNORE the following content (NOT a dispute-resolution clause):\n"
        "- Litigation / 'court' references in disqualification / past-litigation history clauses (those are eligibility bars).\n"
        "- 'Settlement of accounts' / 'reconciliation' references in payment / contract-closure clauses.\n"
        "- 'Dispute resolution' references in scope-of-work definitions where they describe what the contractor's deliverable resolves.\n"
        "- Force majeure / suspension clauses unless they explicitly route disputes to arbitration.\n"
        "- Generic 'differences shall be resolved by mutual discussion' WITHOUT naming arbitration as the formal mode.\n"
        "\n"
        "Selection rules — DO pick if the candidate states:\n"
        "- 'All disputes shall be referred to arbitration under the Arbitration and Conciliation Act 1996'.\n"
        "- 'The arbitration shall be conducted by a sole arbitrator/three arbitrators appointed as per…'.\n"
        "- 'Each party shall appoint one arbitrator and the two so appointed shall appoint the third (presiding) arbitrator…'.\n"
        "- AP value-based ladder: 'Claims up to Rs X to Superintending Engineer; claims above Rs Y to civil court'.\n"
        "- 'Seat / venue of arbitration shall be at <place>'.\n"
        "- A Disputes Resolution Board / DRB clause for Works (per MPW 2022 §6.8).\n"
        "\n"
        "Sub-check 2 — UNILATERAL APPOINTMENT detection:\n"
        "- TRUE only if the clause explicitly says the Authority / Government / PSU 'shall appoint the sole arbitrator' OR 'shall constitute the tribunal' OR 'the contractor shall choose from a panel curated by the Authority'.\n"
        "- FALSE for symmetric / multi-party / ICA-fallback shapes where each party appoints one arbitrator and the two appointed arbitrators agree on the third (or the appointment is by a neutral institution like ICA).\n"
        "\n"
        "Sub-check 4 — AP CIVIL-COURT LADDER detection:\n"
        "- TRUE only if the clause has VALUE TIERS with civil-court routing for the highest tier (typically claims above Rs.50,000).\n"
        "- FALSE for generic 'jurisdiction of <city> courts' clauses without value tiers.\n"
        "\n"
        "QUOTE FORMAT — STRICT (per L35):\n"
        "- Return a SINGLE CONTIGUOUS quote from ONE sentence, ONE clause, or ONE table row. Do NOT stitch multiple paragraphs.\n"
        "- Do NOT add ellipsis ('...') between lines.\n"
        "- Do NOT paraphrase, summarise, or condense — quote EXACTLY as it appears in the supplied text.\n"
        "- Do NOT introduce or remove markdown formatting (asterisks, underscores, italics, table-cell pipes) — preserve the source formatting verbatim.\n"
        "- Pick the SHORTEST contiguous span that proves the clause; one sentence or one short paragraph is usually enough.\n"
        "\n"
        "- If no candidate carries an arbitration / dispute-resolution clause, set chosen_index=null, arbitration_clause_present=false, found=false. The L36 grep fallback will then take over.\n"
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

    # Both sub-checks — MPW25-104 and AP-GO-229 — gate on an extracted
    # fact (ArbitratorAppointmentClauseExists / ClaimValue) that we
    # don't have at the rule-selection stage. The condition_evaluator
    # will return UNKNOWN and the L27 downgrade applies. That's
    # acceptable — the LLM output is what actually drives the
    # decision tree below.
    return facts


def select_arbitration_rules(tender_facts: dict) -> list[dict]:
    """Pick ALL rules that fire (or fire-as-UNKNOWN per L27 downgrade).
    Unlike most prior typologies, this returns the FULL list of fired
    rules — the decision tree below evaluates each sub-check against
    the same LLM extraction."""
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
    if not fired:
        print(f"  → no rule fires for these facts (correct silence — typology N/A on this doc)")
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
    """Pick the highest-priority fired rule for a given sub-check."""
    for cand in RULE_CANDIDATES:
        if cand["sub_check"] != sub_check:
            continue
        for r in fired_rules:
            if r["rule_id"] == cand["rule_id"]:
                return r
    return None


# ── Idempotent re-run cleanup ─────────────────────────────────────────

def _delete_prior_tier1_arbitration(doc_id: str) -> tuple[int, int]:
    """Cleanup is multi-finding-aware: a prior run may have emitted
    both a primary finding AND an AP-GO-229 informational finding.
    Both sets are wiped here."""
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
    print(f"  Tier-1 Arbitration-Clause-Violation (BGE-M3 + LLM, NO regex)")
    print(f"  doc_id : {DOC_ID}")
    print(f"  model  : {LLM_MODEL}")
    print(f"  shape  : multi-rule (presence + unilateral-anti-pattern + AP-ladder)")
    print("=" * 76)

    n_f, n_e = _delete_prior_tier1_arbitration(DOC_ID)
    if n_f or n_e:
        print(f"  cleared {n_f} prior Tier-1 Arbitration finding node(s) "
              f"and {n_e} edge(s) before re-running")

    # 1. Pick rules via condition_evaluator (collect ALL fired rules).
    facts = fetch_tender_facts(DOC_ID)
    fired_rules = select_arbitration_rules(facts)
    if not fired_rules:
        return 0

    # Identify which sub-checks have a fired rule available.
    rule_presence    = (get_rule_by_sub_check(fired_rules, "presence")
                        or get_rule_by_sub_check([], "presence"))
    rule_unilateral  = get_rule_by_sub_check(fired_rules, "unilateral_appointment")
    rule_ap_ladder   = get_rule_by_sub_check(fired_rules, "ap_ladder")

    # The presence-baseline rule is always one of MPG-304 (any tender)
    # or MPW-139 (Works specialisation). We pick whichever fired —
    # MPW-139 takes precedence on Works because its condition resolves
    # FIRE; MPG-304 fires on every tender as well, so on Works both
    # fire and we prefer MPW-139 (more specific).
    if rule_presence is None:
        # Should not happen (MPG-304 has TenderType=ANY which always
        # fires) but defend against missing rule rows.
        print(f"  ⚠ no presence-baseline rule fired; using MPG-304 placeholder")
        rule_presence = next((r for r in RULE_CANDIDATES
                              if r["rule_id"] == "MPG-304"), None)

    print(f"\n  Sub-check rule mapping:")
    print(f"    presence baseline  : {rule_presence['rule_id'] if rule_presence else 'n/a'}")
    print(f"    unilateral         : {rule_unilateral['rule_id'] if rule_unilateral else 'n/a (Works only)'}")
    print(f"    AP ladder          : {rule_ap_ladder['rule_id'] if rule_ap_ladder else 'n/a (AP Works only)'}")

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

    # 6. LLM rerank + multi-field extraction
    t0 = time.perf_counter()
    print(f"\n── Step 3: LLM rerank + arbitration multi-field extraction ──")
    user_prompt = build_arbitration_rerank_prompt(candidates)
    print(f"  prompt size: {len(user_prompt)} chars (~{len(user_prompt)//4} tokens)")
    raw_content = call_llm(LLM_SYSTEM, user_prompt, max_tokens=1100)
    timings["llm"] = time.perf_counter() - t0
    print(f"  wall: {timings['llm']:.2f}s")
    print(f"\n── Raw LLM JSON ──")
    print(raw_content)

    # 7. Parse + select chosen candidate
    parsed = parse_llm_response(raw_content)
    chosen          = parsed.get("chosen_index")
    arb_present     = bool(parsed.get("arbitration_clause_present"))
    drc_present     = bool(parsed.get("dispute_resolution_clause_present"))
    act_referenced  = parsed.get("arbitration_act_referenced")
    seat_specified  = bool(parsed.get("seat_or_venue_specified"))
    seat_text       = parsed.get("seat_or_venue_text")
    unilateral      = bool(parsed.get("unilateral_appointment_present"))
    panel_curated   = bool(parsed.get("appointment_by_curated_panel"))
    ap_ladder       = bool(parsed.get("ap_civil_court_ladder_present"))
    tiers_visible   = bool(parsed.get("escalation_tiers_visible"))
    three_arb       = bool(parsed.get("three_arbitrator_panel"))
    foreign_opt     = bool(parsed.get("foreign_arbitration_option"))
    evidence        = (parsed.get("evidence") or "").strip()
    found           = bool(parsed.get("found"))
    reason          = (parsed.get("reasoning") or "").strip()

    print(f"\n── Parsed (multi-field) ──")
    print(f"  chosen_index                       : {chosen}")
    print(f"  found                              : {found}")
    print(f"  arbitration_clause_present         : {arb_present}")
    print(f"  dispute_resolution_clause_present  : {drc_present}")
    print(f"  arbitration_act_referenced         : {act_referenced!r}")
    print(f"  seat_or_venue_specified            : {seat_specified}  ({seat_text!r})")
    print(f"  unilateral_appointment_present     : {unilateral}   ← anti-pattern (MPW25-104)")
    print(f"  appointment_by_curated_panel       : {panel_curated} ← anti-pattern (MPW25-104)")
    print(f"  ap_civil_court_ladder_present      : {ap_ladder}    ← AP-State marker (AP-GO-229)")
    print(f"  escalation_tiers_visible           : {tiers_visible}")
    print(f"  three_arbitrator_panel             : {three_arb}")
    print(f"  foreign_arbitration_option         : {foreign_opt}")
    print(f"  reasoning                          : {reason[:200]}")
    print(f"  evidence                           : {evidence[:300]!r}")

    section = None
    similarity = None
    ev_passed = False
    ev_score = 0
    ev_method = "skipped"

    llm_chose_candidate = (chosen is not None and isinstance(chosen, int)
                           and 0 <= chosen < len(candidates))
    llm_found_clause   = found and (arb_present or drc_present) and llm_chose_candidate

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
                      f"Routing to UNVERIFIED finding.")
                print(f"    LLM evidence  : {evidence[:200]!r}")
        else:
            print(f"  ⚠ no evidence quote provided — treating as L24-failed (empty)")
            ev_passed = False; ev_score = 0; ev_method = "empty"
    else:
        print(f"  → no candidate chosen by LLM (arb_present={arb_present})")

    # 8. L36 → L40 fallback chain on absence / L24-fail (per L41 lesson)
    raw_is_absence = (not llm_chose_candidate) or (not arb_present and not drc_present)
    is_unverified_l24_fail = (llm_chose_candidate and llm_found_clause and not ev_passed)

    grep_hits: list[dict] = []
    full_grep_hits: list[dict] = []
    grep_promoted_to_unverified = False
    full_grep_promoted = False
    kg_coverage_gap = False
    run_fallback_chain = raw_is_absence or is_unverified_l24_fail
    if run_fallback_chain:
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
                print(f"  → L24-fail path: keeping is_unverified_l24_fail label "
                      f"(grep hits noted in audit but not promoting)")
        else:
            # L40 — Tier-2 whole-file fallback for kg_coverage_gap detection.
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
                if raw_is_absence or kg_coverage_gap:
                    full_grep_promoted = True
                    if is_unverified_l24_fail and kg_coverage_gap:
                        is_unverified_l24_fail = False
                    print(f"  → "
                          f"{'absence' if raw_is_absence else 'L24-fail'} "
                          f"downgraded to UNVERIFIED — "
                          f"{'kg_coverage_gap' if kg_coverage_gap else 'whole-file-only'} hit")

    is_absence    = raw_is_absence and not grep_promoted_to_unverified and not full_grep_promoted
    is_unverified_primary = (is_unverified_l24_fail or grep_promoted_to_unverified
                             or full_grep_promoted)

    # 9. Decision tree — primary finding outcome ─────────────────────
    # Priority order (per user spec):
    #   1. unilateral_appointment_present + Works → MPW25-104 HARD_BLOCK
    #   2. arbitration_clause_present=False + L24/grep paths exhausted → MPG-304 / MPW-139 HARD_BLOCK ABSENCE
    #   3. UNVERIFIED branches → primary UNVERIFIED finding
    #   4. arbitration_clause_present=True + ev_passed → COMPLIANT (no primary finding)
    #
    # Sub-check 4 (AP-GO-229 informational) is a SEPARATE finding
    # emitted independently of the primary outcome — see emission
    # block at the end of main().
    is_works = (facts.get("tender_type") == "Works")

    primary_rule: dict | None = None
    primary_severity: str | None = None
    primary_status: str = "OPEN"
    primary_label: str = ""
    primary_reason_label: str = ""
    primary_evidence: str = evidence
    primary_attach_section = section is not None

    # Branch (0) — AP-State value-tier escalation ladder DEFEATS the
    # Central arbitration-presence requirement. AP-GO-229's `defeats`
    # list explicitly includes MPG-304, MPW-139, MPG-186 etc., so on
    # AP Works/EPC docs that carry the ladder we DON'T treat
    # arbitration-absence as a violation. The marker fires below.
    is_ap_works = bool(facts.get("is_ap_tender") and facts.get("tender_type") in ("Works", "EPC"))
    ap_ladder_accepted = (ap_ladder and llm_chose_candidate and ev_passed
                          and is_ap_works and rule_ap_ladder is not None)

    # Branch (1) — unilateral-appointment anti-pattern (Works only, requires LLM-found clause)
    if (unilateral or panel_curated) and is_works and rule_unilateral and ev_passed:
        primary_rule = rule_unilateral
        primary_severity = primary_rule["severity"]
        primary_status = "OPEN"
        anti_kind = []
        if unilateral:    anti_kind.append("Govt/PSU unilateral appointment")
        if panel_curated: anti_kind.append("Govt-curated arbitrator panel")
        primary_reason_label = (
            f"unilateral_appointment_anti_pattern_{'+'.join(anti_kind).replace(' ', '_').lower()}"
        )
        primary_label = (
            f"{TYPOLOGY}: {primary_rule['rule_id']} ({primary_severity}) — "
            f"{' AND '.join(anti_kind)} retained in arbitration clause; "
            f"INVALIDATED per Supreme Court ruling 08-Nov-2024 "
            f"(CORE v ECL-SPIC-SMO-MCML, 2024 INSC 857)"
        )
    # Branch (2) — UNVERIFIED chain
    elif is_unverified_primary:
        # Use whichever presence rule fired (Works → MPW-139, else MPG-304)
        primary_rule = rule_presence
        primary_severity = primary_rule["severity"]
        primary_status = "UNVERIFIED"
        if grep_promoted_to_unverified:
            primary_reason_label = "arbitration_unverified_grep_fallback_retrieval_gap"
        elif full_grep_promoted:
            primary_reason_label = ("arbitration_unverified_kg_coverage_gap"
                                    if kg_coverage_gap
                                    else "arbitration_unverified_whole_file_grep_only")
        else:
            primary_reason_label = "arbitration_unverified_llm_quote_failed_l24"
        primary_label = (
            f"{TYPOLOGY}: UNVERIFIED — {primary_reason_label}; requires human review"
        )
        primary_attach_section = (section is not None
                                  and not grep_promoted_to_unverified
                                  and not full_grep_promoted)
        primary_evidence = (
            f"LLM rerank top-{K} returned no arbitration candidate, but "
            f"exhaustive grep across {section_types} found keyword hits "
            f"in {len(grep_hits)} section(s)"
            if grep_promoted_to_unverified else
            f"LLM rerank, Section-bounded grep BOTH empty but whole-file "
            f"grep found {len(full_grep_hits)} match line(s)"
            f"{' (KG-coverage GAP)' if kg_coverage_gap else ''}"
            if full_grep_promoted else
            f"LLM found arbitration clause but quote failed L24 "
            f"verification (score={ev_score}, method={ev_method})"
        )
    # Branch (3) — true ABSENCE (no LLM, no L36 hit, no L40 hit).
    # On AP Works/EPC, the AP ladder defeats the absence violation —
    # see Branch (0). This branch only fires for non-AP or non-Works
    # docs where genuine arbitration-clause absence IS a violation.
    elif is_absence and not ap_ladder_accepted:
        primary_rule = rule_presence
        primary_severity = primary_rule["severity"]
        primary_status = "OPEN"
        primary_reason_label = "arbitration_clause_absent"
        primary_label = (
            f"{TYPOLOGY}: arbitration / dispute-resolution clause absent — "
            f"{primary_rule['rule_id']} ({primary_severity}) requires this "
            f"{facts.get('tender_type') or 'tender'} to carry an arbitration "
            f"clause/agreement"
        )
        primary_attach_section = False
        primary_evidence = (
            f"Arbitration / dispute-resolution clause not found in document "
            f"after searching {', '.join(section_types)} section types "
            f"(also exhaustive grep across all matching sections — no "
            f"keyword hits)"
        )
    # Branch (4) — AP ladder accepted (no arbitration but ladder present).
    # Per AP-GO-229's defeats list, this is an AP-acceptable departure
    # from the Central arbitration default — NOT a violation. The
    # AP-GO-229 informational marker fires below.
    elif ap_ladder_accepted and not arb_present:
        primary_rule = None
        print(f"\n  → AP-State value-tier escalation ladder present and "
              f"recognised (AP-GO-229 acceptable departure; defeats MPG-304 / "
              f"MPW-139). No primary violation. Informational marker emitted "
              f"below.")
    # Branch (5) — COMPLIANT (clause present + ev_passed + no anti-pattern)
    else:
        # No primary finding emitted on COMPLIANT path.
        primary_rule = None
        print(f"\n  → no primary violation: arbitration clause present, "
              f"L24-verified, no anti-pattern. COMPLIANT.")

    print(f"\n── Decision (primary) ──")
    print(f"  rule              : {primary_rule['rule_id'] if primary_rule else '(none — COMPLIANT)'}")
    print(f"  severity          : {primary_severity}")
    print(f"  status            : {primary_status}")
    print(f"  reason_label      : {primary_reason_label}")
    print(f"  is_compliant      : {primary_rule is None}")

    # 10. Materialise primary finding (if any) ────────────────────────
    findings_emitted: list[str] = []
    edges_emitted:    list[str] = []

    if primary_rule is not None:
        t0 = time.perf_counter()
        if primary_attach_section and section is not None:
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

        rule_node_id = get_or_create_rule_node(DOC_ID, primary_rule["rule_id"])

        primary_props = {
            "rule_id":                          primary_rule["rule_id"],
            "typology_code":                    TYPOLOGY,
            "severity":                         primary_severity,
            "evidence":                         primary_evidence,
            "extraction_path":                  primary_rule["shape"],
            "llm_found_clause":                 llm_found_clause,
            # Multi-field LLM extraction snapshot
            "arbitration_clause_present":         arb_present,
            "dispute_resolution_clause_present":  drc_present,
            "arbitration_act_referenced":         act_referenced,
            "seat_or_venue_specified":            seat_specified,
            "seat_or_venue_text":                 seat_text,
            "unilateral_appointment_present":     unilateral,
            "appointment_by_curated_panel":       panel_curated,
            "ap_civil_court_ladder_present":      ap_ladder,
            "escalation_tiers_visible":           tiers_visible,
            "three_arbitrator_panel":             three_arb,
            "foreign_arbitration_option":         foreign_opt,
            "rule_shape":                       primary_rule["shape"],
            "violation_reason":                 primary_reason_label,
            "tier":                             1,
            "extracted_by":                     "bge-m3+llm-rerank:qwen-2.5-72b@openrouter",
            "retrieval_strategy":               (
                f"qdrant_top{K}_router_{family}_section_filter_"
                f"{'-'.join(section_types)}_llm_rerank+grep_fallback"
            ),
            "doc_family":                       family,
            "section_filter":                   section_types,
            "rerank_chosen_index":              chosen,
            "rerank_reasoning":                 reason,
            "section_node_id":                  section_node_id,
            "section_heading":                  section_heading,
            "source_file":                      source_file,
            "line_start_local":                 line_start_local,
            "line_end_local":                   line_end_local,
            "qdrant_similarity":                qdrant_similarity,
            # L24 audit fields
            "evidence_in_source":               (None if (is_absence or grep_promoted_to_unverified or full_grep_promoted)
                                                 else ev_passed),
            "evidence_verified":                (None if (is_absence or grep_promoted_to_unverified or full_grep_promoted)
                                                 else ev_passed),
            "evidence_match_score":             (None if (is_absence or grep_promoted_to_unverified or full_grep_promoted)
                                                 else ev_score),
            "evidence_match_method":            ("absence_finding_no_evidence"  if is_absence else
                                                 "grep_fallback_retrieval_gap"  if grep_promoted_to_unverified else
                                                 "whole_file_grep_kg_coverage_gap" if (full_grep_promoted and kg_coverage_gap) else
                                                 "whole_file_grep_match"        if full_grep_promoted else
                                                 ev_method),
            # Rule-evaluator inputs
            "estimated_value_cr":               facts.get("_estimated_value_cr"),
            # L27 audit
            "verdict_origin":                   primary_rule.get("verdict_origin"),
            "severity_origin":                  primary_rule.get("severity_origin"),
            # Status / human-review markers
            "status":                           primary_status,
            "requires_human_review":            primary_status == "UNVERIFIED",
            "defeated":                         False,
        }

        if grep_promoted_to_unverified or full_grep_promoted:
            primary_props["grep_fallback_audit"] = {
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

        primary_finding = rest_post("kg_nodes", [{
            "doc_id":    DOC_ID,
            "node_type": "ValidationFinding",
            "label":     primary_label,
            "properties": primary_props,
            "source_ref": f"tier1:arbitration_check:{primary_rule['rule_id']}",
        }])[0]
        findings_emitted.append(primary_finding["node_id"])

        # Edge emitted only on OPEN findings (per L37 four-state).
        if primary_status == "OPEN":
            primary_edge = rest_post("kg_edges", [{
                "doc_id":       DOC_ID,
                "from_node_id": section_node_id,
                "to_node_id":   rule_node_id,
                "edge_type":    "VIOLATES_RULE",
                "weight":       1.0,
                "properties": {
                    "rule_id":               primary_rule["rule_id"],
                    "typology":              TYPOLOGY,
                    "severity":              primary_severity,
                    "defeated":              False,
                    "tier":                  1,
                    "extraction_path":       primary_rule["shape"],
                    "violation_reason":      primary_reason_label,
                    "doc_family":            family,
                    "qdrant_similarity":     qdrant_similarity,
                    "evidence":              primary_evidence,
                    "evidence_match_score":  primary_props["evidence_match_score"],
                    "evidence_match_method": primary_props["evidence_match_method"],
                    "finding_node_id":       primary_finding["node_id"],
                },
            }])[0]
            edges_emitted.append(primary_edge["edge_id"])

        timings["materialise_primary"] = time.perf_counter() - t0
        print(f"\n  → primary ValidationFinding {primary_finding['node_id']}  "
              f"(status={primary_status}, severity={primary_severity})")
        if primary_status == "OPEN":
            print(f"  → VIOLATES_RULE             {primary_edge['edge_id']}")
        else:
            print(f"  → no VIOLATES_RULE edge (UNVERIFIED — awaiting human review)")

    # 11. AP-GO-229 informational marker — emitted independently of
    # the primary finding, when AP Works/EPC AND the LLM detected the
    # ladder. Severity is ADVISORY by rule definition; the rule
    # explicitly says `do NOT block tender publication`. NOTE: we do
    # NOT gate on `arb_present` because the AP ladder is most
    # interesting precisely when it REPLACES arbitration (e.g. JA
    # explicitly says "...by way of Civil suit and not by
    # arbitration"). The marker requires `ap_ladder=True`,
    # `ev_passed=True`, AP Works/EPC tender, and an LLM-chosen
    # candidate — that's enough to ground the audit record.
    if (rule_ap_ladder is not None and ap_ladder
            and ev_passed and llm_chose_candidate
            and is_ap_works):
        t0 = time.perf_counter()
        marker_section_node_id = (section["section_node_id"] if section is not None
                                  else None)
        if marker_section_node_id is None:
            td_rows = rest_get("kg_nodes", {
                "select":    "node_id",
                "doc_id":    f"eq.{DOC_ID}",
                "node_type": "eq.TenderDocument",
            })
            marker_section_node_id = td_rows[0]["node_id"] if td_rows else None
        marker_rule_node_id = get_or_create_rule_node(DOC_ID, rule_ap_ladder["rule_id"])

        marker_label = (
            f"{TYPOLOGY}: AP-LADDER-RECOGNISED — {rule_ap_ladder['rule_id']} "
            f"(ADVISORY informational) — doc carries AP-State value-tier "
            f"escalation ladder routing claims > Rs.50,000 to civil court "
            f"per APSS Clause 61 + GO Ms No 94/2003 §14; this is an "
            f"AP-acceptable departure from the Central arbitration default, "
            f"NOT a violation"
        )
        marker_props = {
            "rule_id":                       rule_ap_ladder["rule_id"],
            "typology_code":                 TYPOLOGY,
            "severity":                      "ADVISORY",
            "evidence":                      evidence,
            "extraction_path":               "informational",
            "llm_found_clause":              llm_found_clause,
            "arbitration_clause_present":    arb_present,
            "ap_civil_court_ladder_present": True,
            "escalation_tiers_visible":      tiers_visible,
            "rule_shape":                    "informational",
            "violation_reason":              "ap_ladder_recognised_acceptable_departure",
            "tier":                          1,
            "marker_kind":                   "informational",
            "extracted_by":                  "bge-m3+llm-rerank:qwen-2.5-72b@openrouter",
            "doc_family":                    family,
            "section_filter":                section_types,
            "rerank_chosen_index":           chosen,
            "section_node_id":               marker_section_node_id,
            "section_heading":               section["heading"]            if section else None,
            "source_file":                   section["source_file"]        if section else None,
            "line_start_local":              section["line_start_local"]   if section else None,
            "line_end_local":                section["line_end_local"]     if section else None,
            "qdrant_similarity":             round(similarity, 4) if similarity is not None else None,
            "evidence_in_source":            ev_passed,
            "evidence_verified":             ev_passed,
            "evidence_match_score":          ev_score,
            "evidence_match_method":         ev_method,
            "estimated_value_cr":            facts.get("_estimated_value_cr"),
            "status":                        "OPEN",
            "requires_human_review":         False,
            "defeated":                      False,
        }
        marker_finding = rest_post("kg_nodes", [{
            "doc_id":    DOC_ID,
            "node_type": "ValidationFinding",
            "label":     marker_label,
            "properties": marker_props,
            "source_ref": f"tier1:arbitration_check:{rule_ap_ladder['rule_id']}",
        }])[0]
        findings_emitted.append(marker_finding["node_id"])

        # Edge — informational marker still gets a VIOLATES_RULE edge
        # because status=OPEN. The severity is ADVISORY so downstream
        # gates can filter out informationals from BLOCK aggregations.
        marker_edge = rest_post("kg_edges", [{
            "doc_id":       DOC_ID,
            "from_node_id": marker_section_node_id,
            "to_node_id":   marker_rule_node_id,
            "edge_type":    "VIOLATES_RULE",
            "weight":       1.0,
            "properties": {
                "rule_id":               rule_ap_ladder["rule_id"],
                "typology":              TYPOLOGY,
                "severity":              "ADVISORY",
                "defeated":              False,
                "tier":                  1,
                "extraction_path":       "informational",
                "violation_reason":      "ap_ladder_recognised_acceptable_departure",
                "marker_kind":           "informational",
                "doc_family":            family,
                "qdrant_similarity":     round(similarity, 4) if similarity is not None else None,
                "evidence":              evidence,
                "evidence_match_score":  ev_score,
                "evidence_match_method": ev_method,
                "finding_node_id":       marker_finding["node_id"],
            },
        }])[0]
        edges_emitted.append(marker_edge["edge_id"])
        timings["materialise_marker"] = time.perf_counter() - t0
        print(f"\n  → AP-GO-229 informational ValidationFinding {marker_finding['node_id']}  "
              f"(status=OPEN, severity=ADVISORY, marker_kind=informational)")
        print(f"  → VIOLATES_RULE              {marker_edge['edge_id']}")

    # Summary
    timings["total_wall"] = time.perf_counter() - t_start
    print()
    print("=" * 76)
    print(f"  SUMMARY — {len(findings_emitted)} finding(s), {len(edges_emitted)} edge(s)")
    print("=" * 76)
    for k, v in timings.items():
        unit = "s" if v >= 1 else "ms"
        val  = v if v >= 1 else v * 1000
        print(f"    {k:22s} {val:8.2f} {unit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
