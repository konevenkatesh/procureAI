"""
scripts/tier1_force_majeure_check.py

Tier-1 Missing-Force-Majeure check, BGE-M3 + LLM, NO regex.

PRESENCE shape — fourth presence-shape typology after PVC / IP / LD.
Every Works/Services/Goods contract is required to contain a Force
Majeure clause defining extraordinary events beyond reasonable control
(act of God, war, strike, riot, epidemic, etc.) and the parties'
obligations during such an event. Per the read-first scan:

    MPG-174  TenderType=ANY                      HARD_BLOCK  (universal)
    MPS-100  TenderType=Services                 HARD_BLOCK
    MPW-122  TenderType=Works AND
             FMEventInvoked=true                 HARD_BLOCK  (execution-stage)

defeats=[] across all three rules — knowledge-layer gap, no
defeasibility wired. AP corpus has zero FM rules — Central baseline only.

For pre-RFP / document-presence Tier-1 validation MPW-122 SKIPs
(FMEventInvoked is execution-stage, false at pre-RFP) and MPS-100
SKIPs on Works/PPP corpus. **MPG-174 is the universal firing rule.**

Pipeline mirrors LD/PVC/IP:
  1. Pick rule via condition_evaluator (MPG-174 fires on all 6 docs).
  2. Section filter via FORCE_MAJEURE_SECTION_ROUTER —
        APCRDA_Works → [GCC, SCC]
        SBD_Format   → [GCC, SCC, Evaluation]
        NREDCAP_PPP  → [GCC, SCC]
        default      → [GCC, SCC, Specifications]
  3. BGE-M3 embed an answer-shaped query.
  4. Qdrant top-K=10 within the filter.
  5. LLM rerank with FM-specific ignore rules (Insurance / Indemnity /
     Change-in-Law / Suspension-for-non-FM / Frustration of contract)
     and structured FM extraction (presence + event taxonomy + notice
     obligation + termination window + exclusions).
  6. L24 evidence-guard hallucination check.
  7. L36 Section-bounded grep + L40 whole-file grep (kg_coverage_gap
     detection) on raw absence path.
  8. Apply rule check — three-state contract per L35:
        COMPLIANT (LLM found + L24 verified) → silent, NO row emitted.
        UNVERIFIED (LLM found but L24 failed, OR raw absence
                    promoted by grep fallback) → row, NO edge.
        GAP_VIOLATION (raw absence + grep fallback also empty) →
                    row + VIOLATES_RULE edge.

The COMPLIANT-silent decision is intentional (per typology-18 build
spec): emitting COMPLIANT rows would (a) inflate kg_nodes with audit
noise, (b) be preserved across rebuilds by L32 snapshot/restore, and
(c) make the VIOLATES_RULE edge count misleading. The portal derives
"no violations found" from the absence of a row for this typology —
that IS the positive signal, no row required.

Tested on judicial_academy_exp_001 first (expected: COMPLIANT,
silent, no row).
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

TYPOLOGY = "Missing-Force-Majeure"

QDRANT_URL  = os.environ.get("QDRANT_URL", "http://localhost:6333")
COLLECTION  = "tender_sections"

LLM_MODEL = os.environ.get("LLM_MODEL", "qwen/qwen-2.5-72b-instruct")


# L36/L40 source-grep fallback vocabulary. FM-specific phrases only —
# do NOT include broad "control" or "delay" tokens; the read-first
# scan showed those false-positive on Engineer's-instruction extension
# and EOT clauses (Kakinada L2131 "beyond the control of the contractor"
# is a generic delay clause, NOT a Force Majeure clause; the LLM is
# the authority on whether such language counts as FM, the grep just
# has to surface it).
GREP_FALLBACK_KEYWORDS = [
    "Force Majeure",
    "Force Majeure Event",
    "act of God",
    "acts of God",
    "natural disaster",
    "civil commotion",
    "act of public enemy",
    "epidemic",
    "pandemic",
    "Non-Political Event",
    "Indirect Political Event",
    "Political Event",
    "beyond the reasonable control",
    "beyond reasonable control",
]


# Answer-shaped query — mirrors the literal wording of FM clause
# templates (CLAUSE-FORCE-MAJEURE-001, CLAUSE-WORKS-FORCE-MAJEURE-001,
# CLAUSE-FM-3-TIER-COST-001, CLAUSE-FORCE-MAJEURE-SERVICES-001).
QUERY_TEXT = (
    "Force Majeure Force Majeure Event extraordinary events beyond reasonable control "
    "act of God war strike riot epidemic pandemic civil commotion lockout sabotage "
    "Non-Political Indirect Political Political Event notice termination "
    "GCC SCC condonable delay 30 day 90 day 120 day"
)


# Rule candidates evaluated via condition_evaluator. Priority order:
# most specific first. MPS-100 (Services) and MPW-122 (Works execution-
# stage) precede the catch-all MPG-174.
RULE_CANDIDATES = [
    {
        "rule_id":         "MPS-100",
        "natural_language": "Services / consultancy contracts MUST include a Force Majeure clause defining extraordinary events beyond control",
        "severity":        "HARD_BLOCK",
        "layer":           "Central",
        "shape":           "presence",
    },
    {
        "rule_id":         "MPW-122",
        "natural_language": "Works contracts MUST treat Force Majeure events (war, hostility, sabotage, fire, explosion, epidemics, strikes, lockouts, acts of God) as condonable delays without right to terminate",
        "severity":        "HARD_BLOCK",
        "layer":           "Central",
        "shape":           "presence",
    },
    {
        "rule_id":         "MPG-174",
        "natural_language": "Every contract MUST include a Force Majeure clause — extraordinary events beyond human control (act of God, war, strike, riot, crime; explicitly excluding negligence and predictable seasonal events)",
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


# ── LLM rerank prompt for FM ─────────────────────────────────────────

LLM_SYSTEM = (
    "You are a precise procurement-document fact extractor. "
    "Return JSON only. Quote evidence verbatim from the supplied text. "
    "Never fabricate."
)


# Keyword vocabulary for smart_truncate windowing (FM-specific). FM
# clause headings are short and the cap-defining phrases sit in the
# first paragraph — centring the window on the literal phrases prevents
# elision (L26).
FM_TRUNCATE_KEYWORDS = [
    r"force\s+majeure",
    r"\bact\s+of\s+god\b",
    r"acts\s+of\s+god",
    r"natural\s+disaster",
    r"civil\s+commotion",
    r"act\s+of\s+public\s+enemy",
    r"epidemic",
    r"pandemic",
    r"non-political\s+event",
    r"indirect\s+political\s+event",
    r"political\s+event",
    r"beyond.{0,20}reasonable\s+control",
    r"beyond.{0,20}control\s+of\s+(?:either\s+)?part",
    r"war",
    r"strike",
    r"riot",
    r"sabotage",
    r"lockout",
    r"\b30\s*\(?thirty\)?\s*days?\b",
    r"\b90\s*\(?ninety\)?\s*days?\b",
    r"\b120\s*\(?one\s*hundred\s*(?:and\s*)?twenty\)?\s*days?\b",
]


def build_fm_rerank_prompt(candidates: list[dict]) -> str:
    blocks = []
    for i, c in enumerate(candidates):
        body = smart_truncate(c["full_text"], window=3000,
                               keywords=FM_TRUNCATE_KEYWORDS)
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
        "Exactly ONE of them (or none) is the actual FORCE MAJEURE clause — the "
        "contract provision that defines extraordinary events beyond the parties' "
        "reasonable control (acts of God, war, hostility, civil commotion, sabotage, "
        "fire, explosion, epidemic, strike, lockout, riot, etc.) and treats delays "
        "or non-performance attributable to such events as CONDONABLE — without "
        "right to terminate, without liability for damages, and (typically) with "
        "an obligation to give written notice within a stated window.\n\n"
        f"{candidates_block}\n\n"
        "Question: Which candidate states the Force Majeure clause? "
        "Extract its presence and structure (event taxonomy, notice obligation, "
        "termination window, exclusions).\n\n"
        "Return JSON only:\n"
        "{\n"
        "  \"chosen_index\":            integer 0..N-1 of the FM candidate, OR null if no candidate is an FM clause,\n"
        "  \"fm_clause_present\":       bool,\n"
        "  \"fm_event_definition_present\": bool   (does the clause enumerate the event taxonomy — acts of God, war, strike, etc.),\n"
        "  \"notice_obligation_present\":   bool   (does the affected party have to give written notice within a stated window),\n"
        "  \"termination_window_days\":     integer OR null  (e.g. 90 / 120 — number of days of continued FM after which a party may terminate),\n"
        "  \"excludes_negligence\":         bool OR null  (true if the clause explicitly EXCLUDES wrong-doing / negligence from FM),\n"
        "  \"excludes_predictable_rain\":   bool OR null  (true if the clause explicitly EXCLUDES predictable / seasonal rain),\n"
        "  \"three_tier_cost_allocation_present\": bool OR null  (true if Non-Political / Indirect Political / Political event taxonomy with separate cost rules),\n"
        "  \"go_reference\":             string OR null  (e.g. 'MPW 2022 §6.6.4', 'MPG 2022 §10.6', or contract-clause cite),\n"
        "  \"evidence\":                 \"verbatim quote from the chosen candidate's text identifying the FM clause\",\n"
        "  \"found\":                    bool,\n"
        "  \"reasoning\":                \"one short sentence explaining the choice\"\n"
        "}\n\n"
        "Selection rules — IGNORE the following content (NOT a Force Majeure clause):\n"
        "- INSURANCE / INDEMNITY clauses (allocate risk via insurance proceeds; "
        "  separate from FM event-taxonomy + condonable-delay structure).\n"
        "- CHANGE IN LAW / change-of-tax / regulatory-change clauses (a separate "
        "  clause family with its own pricing mechanism).\n"
        "- EXTENSION OF TIME / EOT clauses that grant more time for ANY delay "
        "  cause, including engineer's instructions, payment disputes, drawing "
        "  delays — these are NOT Force Majeure unless they explicitly invoke "
        "  the FM event taxonomy (acts of God, war, strike, etc.) and treat "
        "  the delay as condonable.\n"
        "- SUSPENSION-OF-WORK clauses for engineer's instruction or payment "
        "  default (separate remedy from FM).\n"
        "- FRUSTRATION-OF-CONTRACT references at common-law-doctrine level "
        "  (cite-only, not the contract's own FM clause).\n"
        "- Generic 'beyond the control of the contractor' lines that grant a "
        "  reasonable extension WITHOUT enumerating the FM event taxonomy AND "
        "  WITHOUT the condonable-delay / no-liability / notice-window "
        "  structure — those are extension-of-time clauses, not FM clauses.\n"
        "- TERMINATION FOR DEFAULT / TERMINATION FOR CONVENIENCE clauses (FM "
        "  termination is a separate sub-clause WITHIN the FM clause; if the "
        "  candidate is purely default/convenience, skip).\n"
        "- DISPUTE RESOLUTION / ARBITRATION clauses.\n"
        "- LIQUIDATED DAMAGES clauses (LD recovers for non-FM delay).\n"
        "\n"
        "Selection rules — DO pick if the candidate states:\n"
        "- A clause titled 'Force Majeure' / 'Force Majeure Event' / 'Force "
        "  Majeure and Termination'.\n"
        "- An event taxonomy enumerating extraordinary events beyond the "
        "  parties' reasonable control (acts of God, war, hostility, civil "
        "  commotion, sabotage, fire, explosion, epidemic, strike, lockout, "
        "  riot — typically several of these together).\n"
        "- The 3-tier event taxonomy (Non-Political / Indirect Political / "
        "  Political Event) used in PPP DCAs and APCRDA Works Concession-shape "
        "  GCC §62 / §26.\n"
        "- A condonable-delay / no-liability framework treating performance "
        "  during such events as excused.\n"
        "- A written-notice obligation within a stated window (e.g. 'notice "
        "  forthwith', 'within 7 days', 'within 30 days').\n"
        "- A termination clause triggered after continued FM beyond a stated "
        "  window (e.g. 90 / 120 / 180 days).\n"
        "- Reference to MPW 2022 §6.6.4, MPG 2022 §10.6, or contract-clause "
        "  cite of the FM clause.\n"
        "- Even if the day-counts (notice window, termination window) are "
        "  stamped as PCC/SCC placeholders ('as stated in PCC'), if the section "
        "  EXPLICITLY invokes the FM event taxonomy and condonable-delay "
        "  framework, treat as fm_clause_present=true and capture the framework "
        "  reference (termination_window_days may be null if it's by-reference).\n"
        "\n"
        "- If the candidate has only generic extension-of-time / engineer's "
        "  instruction / payment-default suspension language (no FM event "
        "  taxonomy + no condonable-delay framework), set "
        "  fm_clause_present=false and chosen_index=null.\n"
        "- Evidence MUST be an exact substring of the chosen candidate's text.\n"
        "- If no candidate is an FM clause, set chosen_index=null, "
        "  fm_clause_present=false, found=false.\n"
        "\n"
        "QUOTE FORMAT — STRICT (per L35):\n"
        "- Return a SINGLE CONTIGUOUS quote from ONE sentence or ONE clause only.\n"
        "- Do NOT stitch multiple paragraphs into one quote.\n"
        "- Do NOT add ellipsis ('...') between lines.\n"
        "- Do NOT paraphrase, summarise, or condense — quote EXACTLY as it appears in the supplied text.\n"
        "- Do NOT introduce or remove markdown formatting (asterisks, underscores, italics) — preserve the source formatting verbatim.\n"
        "- Pick the SHORTEST contiguous span that proves the clause is present; one sentence (the FM-event-definition opener) is usually enough."
    )


def parse_llm_response(raw: str) -> dict:
    return parse_llm_json(raw)


# ── Rule selection via condition_evaluator ────────────────────────────

def fetch_tender_facts(doc_id: str) -> dict:
    """Build the facts dict that condition_evaluator reads.

    Strict LLM-only sources (per L28). FMEventInvoked is execution-stage
    (false at pre-RFP) — set explicitly so MPW-122's UNKNOWN-on-missing-
    fact doesn't fire-as-ADVISORY on the universal rule MPG-174's
    coattails.
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
        # Pre-RFP / document-presence layer: no FM event has been invoked.
        # MPW-122 ("FMEventInvoked=true") therefore evaluates SKIP, not
        # UNKNOWN — that rule fires only at execution stage.
        "FMEventInvoked":  False,
        # MPS-100 references ServiceCategory implicitly via TenderType=
        # Services. The corpus is Works/PPP only — MPS-100 SKIPs.
    }

    ev_cr = p.get("estimated_value_cr")
    if ev_cr is not None:
        try:
            facts["EstimatedValue"] = float(ev_cr) * 1e7   # crores → rupees
            facts["_estimated_value_cr"] = float(ev_cr)
        except (TypeError, ValueError):
            pass

    return facts


def select_fm_rule(tender_facts: dict) -> dict | None:
    """Pick the highest-priority rule that fires (or fires-as-UNKNOWN
    per L27 downgrade). Returns None only when every rule's condition
    evaluates to SKIP."""
    fired: list[dict] = []
    print(f"\n  Rule selection — facts: tender_type={tender_facts.get('tender_type')!r}, "
          f"is_ap_tender={tender_facts.get('is_ap_tender')}, "
          f"FMEventInvoked={tender_facts.get('FMEventInvoked')}")
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
          f"shape={chosen['shape']}){note}")
    return chosen


# ── Idempotent re-run cleanup ─────────────────────────────────────────

def _delete_prior_tier1_force_majeure(doc_id: str) -> tuple[int, int]:
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
    print(f"  Tier-1 Missing-Force-Majeure (BGE-M3 + LLM, NO regex)")
    print(f"  doc_id : {DOC_ID}")
    print(f"  model  : {LLM_MODEL}")
    print("=" * 76)

    n_f, n_e = _delete_prior_tier1_force_majeure(DOC_ID)
    if n_f or n_e:
        print(f"  cleared {n_f} prior Tier-1 FM finding node(s) and "
              f"{n_e} edge(s) before re-running")

    # 1. Pick rule via condition_evaluator
    facts = fetch_tender_facts(DOC_ID)
    rule  = select_fm_rule(facts)
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
    print(f"\n── Step 3: LLM rerank + FM extraction ──")
    user_prompt = build_fm_rerank_prompt(candidates)
    print(f"  prompt size: {len(user_prompt)} chars (~{len(user_prompt)//4} tokens)")
    raw_content = call_llm(LLM_SYSTEM, user_prompt, max_tokens=900)
    timings["llm"] = time.perf_counter() - t0
    print(f"  wall: {timings['llm']:.2f}s")
    print(f"\n── Raw LLM JSON ──")
    print(raw_content)

    # 7. Parse + select chosen candidate
    parsed = parse_llm_response(raw_content)
    chosen          = parsed.get("chosen_index")
    fm_present      = bool(parsed.get("fm_clause_present"))
    fm_event_def    = parsed.get("fm_event_definition_present")
    notice_oblig    = parsed.get("notice_obligation_present")
    term_window_d   = parsed.get("termination_window_days")
    excl_negl       = parsed.get("excludes_negligence")
    excl_seasonal   = parsed.get("excludes_predictable_rain")
    three_tier      = parsed.get("three_tier_cost_allocation_present")
    go_reference    = parsed.get("go_reference")
    evidence        = (parsed.get("evidence") or "").strip()
    found           = bool(parsed.get("found"))
    reason          = (parsed.get("reasoning") or "").strip()

    print(f"\n── Parsed ──")
    print(f"  chosen_index                       : {chosen}")
    print(f"  found                              : {found}")
    print(f"  fm_clause_present                  : {fm_present}")
    print(f"  fm_event_definition_present        : {fm_event_def}")
    print(f"  notice_obligation_present          : {notice_oblig}")
    print(f"  termination_window_days            : {term_window_d}")
    print(f"  excludes_negligence                : {excl_negl}")
    print(f"  excludes_predictable_rain          : {excl_seasonal}")
    print(f"  three_tier_cost_allocation_present : {three_tier}")
    print(f"  go_reference                       : {go_reference!r}")
    print(f"  reasoning                          : {reason[:200]}")
    print(f"  evidence                           : {evidence[:300]!r}")

    # L35 three-state contract: COMPLIANT / UNVERIFIED / GAP_VIOLATION.
    section = None
    similarity = None
    ev_passed = False
    ev_score = 0
    ev_method = "skipped"

    llm_found_clause   = fm_present and (chosen is not None)
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
        if fm_present:
            print(f"  ⚠ fm_present=True but chosen_index=null — treating as False")
            fm_present = False
            llm_found_clause = False

    # 8. Apply rule check — three-state branch + L36/L40 grep fallback.
    is_compliant   = llm_found_clause and ev_passed
    is_unverified  = llm_found_clause and not ev_passed
    raw_is_absence = not llm_found_clause

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
            # L40 whole-file fallback (kg_coverage_gap detection)
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
    is_unverified = is_unverified or grep_promoted_to_unverified or full_grep_promoted

    if is_compliant:
        reason_label = "compliant_force_majeure_clause_present"
    elif grep_promoted_to_unverified:
        reason_label = "fm_unverified_grep_fallback_retrieval_gap"
    elif full_grep_promoted:
        reason_label = ("fm_unverified_kg_coverage_gap"
                        if kg_coverage_gap
                        else "fm_unverified_whole_file_grep_only")
    elif is_unverified:
        reason_label = "fm_unverified_llm_quote_failed_l24"
    else:
        reason_label = "force_majeure_absent_violation"

    print(f"\n── Decision ──")
    print(f"  rule              : {rule['rule_id']} ({rule['severity']}, shape={rule['shape']})")
    print(f"  llm_found_clause  : {llm_found_clause}")
    print(f"  ev_passed         : {ev_passed}  (score={ev_score}, method={ev_method})")
    print(f"  raw_is_absence    : {raw_is_absence}")
    print(f"  grep_fallback_hit : {grep_promoted_to_unverified or full_grep_promoted}")
    print(f"  is_compliant      : {is_compliant}")
    print(f"  is_unverified     : {is_unverified}")
    print(f"  is_absence        : {is_absence}")
    print(f"  reason_label      : {reason_label}")

    # COMPLIANT → no row, no edge. The portal derives "no violations
    # found" for this typology from the absence of a row. That IS the
    # positive signal.
    if is_compliant:
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

    # 9. Materialise finding (UNVERIFIED or GAP_VIOLATION).
    # VIOLATES_RULE edge is emitted ONLY for GAP_VIOLATION (is_absence) —
    # UNVERIFIED findings are NOT violations until a human reviewer
    # confirms (per L35 contract).
    t0 = time.perf_counter()
    if section is not None and is_unverified and not (grep_promoted_to_unverified or full_grep_promoted):
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
        evidence_out  = (f"Force Majeure clause not found in document after "
                         f"BGE-M3 retrieval, L36 Section-bounded grep, and L40 "
                         f"whole-file grep across {', '.join(section_types)}. "
                         f"Per MPG-174 (universal HARD_BLOCK), every contract "
                         f"must include a Force Majeure clause defining "
                         f"extraordinary events beyond human control (act of "
                         f"God, war, strike, riot, epidemic, etc.) and treating "
                         f"delays under such events as condonable.")
        print(f"  → GAP_VIOLATION finding — LLM rerank empty AND grep "
              f"fallbacks empty; genuine absence")
    elif grep_promoted_to_unverified:
        ev_passed_out = None
        ev_score_out  = None
        ev_method_out = "grep_fallback_retrieval_gap"
        evidence_out  = (f"LLM rerank top-{K} returned no FM candidate, but "
                         f"exhaustive grep across {', '.join(section_types)} "
                         f"found keyword hits in {len(grep_hits)} section(s). "
                         f"First match: "
                         f"{grep_hits[0]['snippet'][:160] if grep_hits else 'n/a'}")
        print(f"  → UNVERIFIED finding (L36 grep fallback) — retrieval missed "
              f"{len(grep_hits)} section(s) with FM keywords")
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
    elif is_unverified:
        ev_passed_out = ev_passed
        ev_score_out  = ev_score
        ev_method_out = ev_method
        evidence_out  = evidence
        print(f"  → UNVERIFIED finding — LLM identified FM signal but quote "
              f"failed L24 (score={ev_score}, method={ev_method})")
    else:
        ev_passed_out = ev_passed
        ev_score_out  = ev_score
        ev_method_out = ev_method
        evidence_out  = evidence

    rule_node_id = get_or_create_rule_node(DOC_ID, rule["rule_id"])

    if is_absence:
        label = (
            f"{TYPOLOGY}: Force Majeure clause absent — {rule['rule_id']} "
            f"({rule['severity']}) requires every contract to include an FM "
            f"clause defining extraordinary events beyond human control"
        )
    elif grep_promoted_to_unverified:
        label = (
            f"{TYPOLOGY}: UNVERIFIED (L36 grep-fallback) — LLM rerank "
            f"missed the FM clause; exhaustive grep found {len(grep_hits)} "
            f"section(s) with FM keyword hits; requires human review"
        )
    elif full_grep_promoted:
        label = (
            f"{TYPOLOGY}: UNVERIFIED (L40 whole-file fallback) — "
            f"{'KG-coverage gap' if kg_coverage_gap else 'whole-file only'}; "
            f"{len(full_grep_hits)} match line(s)"
        )
    elif is_unverified:
        label = (
            f"{TYPOLOGY}: UNVERIFIED — LLM found FM clause but quote "
            f"failed L24 (score={ev_score}, method={ev_method}); requires "
            f"human review against "
            f"{(section['heading'][:60] if section else 'TenderDocument')!r}"
        )
    else:
        label = ""

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
        "extraction_path":       "presence",
        "llm_found_clause":      llm_found_clause,
        # Multi-field FM extraction snapshot
        "fm_clause_present":               llm_found_clause,
        "fm_event_definition_present":     fm_event_def,
        "notice_obligation_present":       notice_oblig,
        "termination_window_days":         term_window_d,
        "excludes_negligence":             excl_negl,
        "excludes_predictable_rain":       excl_seasonal,
        "three_tier_cost_allocation_present": three_tier,
        "go_reference":                    go_reference,
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
            f"L36 grep fallback fired: LLM rerank top-{K} did not surface FM, "
            f"but exhaustive grep across {section_types} found keyword hits in "
            f"{len(grep_hits)} section(s). Reviewer should open the listed "
            f"sections in grep_audit.hits and confirm the FM clause."
            if grep_promoted_to_unverified else
            f"L40 whole-file fallback: LLM rerank AND Section-bounded grep both "
            f"empty, but whole-file grep found {len(full_grep_hits)} match "
            f"line(s). "
            f"{'kg_coverage_gap=TRUE — reviewer should re-run kg_builder.' if kg_coverage_gap else 'Whole-file only hits — reviewer should verify retrieval coverage.'}"
            if full_grep_promoted else
            f"LLM identified FM clause but quote failed L24 (score={ev_score}, "
            f"method={ev_method}). Reviewer should open the section above and confirm."
            if is_unverified else None
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
        "source_ref": f"tier1:force_majeure_check:{rule['rule_id']}",
    }])[0]

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
                "fm_clause_present":    False,
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
