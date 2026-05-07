"""
scripts/tier1_prebid_check.py

Tier-1 Pre-Bid-Process-Unclear check, BGE-M3 + LLM, NO regex.

PRESENCE shape with multi-field protocol extraction. Per the read-
first scan, 5 rules fall under this typology code but only one fits
the Tier-1 doc-content / clarification-protocol-presence framing:

    MPW-061    TenderType=Works   HARD_BLOCK
               Bid Documents for Works must be SELF-CONTAINED and
               COMPREHENSIVE without ambiguity. Operationalised as:
               doc must contain a clarification protocol so bidders
               can resolve ambiguities, plus reasonable pre-bid
               meeting / site visit provisions.

Excluded from RULE_CANDIDATES (with reasoning):
    AP-GO-057  WARNING — TIMELINE shape (14-day publication-to-
               submission window). Different question; will be a
               separate Tier-1 typology when corpus has the data.
    AP-GO-211  WARNING — ADVERTISEMENT shape (Gazette + newspapers +
               1-month for large contracts). Different question.
    AP-GO-156  Goods-only — SKIPs corpus-wide.
    MPG-283    HARD_BLOCK — `BidAmbiguityDetected=true`. Execution-
               stage signal; at pre-RFP `false` by default (same
               L48 FM precedent for FMEventInvoked) → SKIPs.

Anchors (clause templates):
    CLAUSE-CLARIFICATION-001 (Volume-I/Section-2/ITB) — clarification
        of tender documents (bidder-side query mechanism)
    CLAUSE-CLARIFICATION-AMENDMENT-001 (Volume-I/Section-2/ITB) —
        symmetric-information amendment via clarification

Pipeline:
  1. Pick rule via condition_evaluator (MPW-061 fires on 4 AP Works;
     SKIPs on 2 PPP DCAs).
  2. Section filter via PREBID_SECTION_ROUTER.
  3. BGE-M3 dual queries (framework + value).
  4. Per-section-type quota retrieval (L49) + grep-seeded supplement
     for the literal "pre-bid" / "clarification" keywords (L50).
  5. LLM rerank with Pre-Bid-specific ignore rules (Bid clarification
     during EVALUATION stage = different clause; Bid Validity period;
     EMD bank-guarantee validity; etc.) and 5-field structured
     extraction.
  6. L24 evidence-guard hallucination check.
  7. L36/L40 grep fallback for absence path.
  8. Decision tree (silent-on-COMPLIANT per L48):
        COMPLIANT  if (clarification_request_protocol_present AND
                       clarification_response_protocol_present)
                   → silent (no row).
        GAP_VIOLATION if neither protocol present
                   → row + VIOLATES_RULE edge.
        UNVERIFIED if L24 fails OR grep-promoted absence.

Tested on judicial_academy_exp_001 first (expected: COMPLIANT silent
— ITB §7 "Clarification of Bidding Document, Site Visit, Pre-Bid
Meeting" L193-199 + BDS L501/L509 with full protocol).
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

TYPOLOGY = "Pre-Bid-Process-Unclear"

QDRANT_URL  = os.environ.get("QDRANT_URL", "http://localhost:6333")
COLLECTION  = "tender_sections"

LLM_MODEL = os.environ.get("LLM_MODEL", "qwen/qwen-2.5-72b-instruct")


GREP_FALLBACK_KEYWORDS = [
    "pre-bid",
    "pre bid",
    "prebid",
    "Pre-Bid Meeting",
    "Clarification of Bidding Document",
    "Clarification of Tender",
    "clarification request",
    "clarification of bidding",
    "site visit",
    "Site Visit",
    "bidder query",
    "bidder shall contact",
    "pre-Bid meeting",
]


# Dual-query retrieval per L49.
QUERY_FRAMEWORK = (
    "Clarification of Bidding Document Site Visit Pre-Bid Meeting "
    "ITB Section 7 bidder requiring clarification shall contact "
    "Employer in writing respond in writing pre-Bid meeting"
)
QUERY_VALUE = (
    "Pre-Bid Meeting Date time address venue queries cutoff submitted "
    "in writing before pre-bid meeting date BDS Bid Data Sheet site visit "
    "geo-tagging photographs e-procurement portal"
)
QUERY_TEXT = QUERY_VALUE   # banner-only alias


# Single-rule candidate list. Other rules in this typology are excluded
# per the header docstring (different shape / execution-stage / Goods-
# only / vague meta-quality).
RULE_CANDIDATES = [
    {
        "rule_id":          "MPW-061",
        "natural_language": "Works bid documents must be self-contained and comprehensive without ambiguity, with a clarification protocol so bidders can resolve any ambiguity",
        "severity":         "HARD_BLOCK",
        "layer":            "Central",
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


# ── LLM rerank prompt for Pre-Bid framework ──────────────────────────

LLM_SYSTEM = (
    "You are a precise procurement-document fact extractor. "
    "Return JSON only. Quote evidence verbatim from the supplied text. "
    "Never fabricate."
)


PREBID_TRUNCATE_KEYWORDS = [
    r"clarification\s+of\s+bidding\s+document",
    r"clarification\s+of\s+tender\s+document",
    r"pre[-\s]?bid\s+meeting",
    r"pre[-\s]?bid\s+conference",
    r"site\s+visit",
    r"bidder\s+requiring\s+any\s+clarification",
    r"shall\s+contact\s+the\s+(employer|authority)",
    r"respond\s+in\s+writing",
    r"BDS\s+ITB\s+7",
    r"queries.{0,30}before.{0,20}pre[-\s]?bid",
    r"e[-\s]?procurement\s+portal",
    r"geo[-\s]?tagging",
]


def build_prebid_rerank_prompt(candidates: list[dict]) -> str:
    blocks = []
    for i, c in enumerate(candidates):
        body = smart_truncate(c["full_text"], window=3000,
                               keywords=PREBID_TRUNCATE_KEYWORDS)
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
        "Extract the bidding document's PRE-BID CLARIFICATION PROTOCOL — the regulated "
        "mechanism that allows bidders to resolve ambiguities BEFORE bid submission, "
        "typically structured as: (a) a written-query path (bidder contacts employer), "
        "(b) a written-response commitment (employer responds, often publishes on "
        "e-portal symmetrically), (c) optionally a pre-bid meeting / conference (date/"
        "time/place stated), (d) optionally a site visit provision, (e) often a query "
        "deadline.\n\n"
        f"{candidates_block}\n\n"
        "Question: Across ALL candidates above, what does the bidding doc say about "
        "the pre-bid clarification protocol? Pick the SINGLE BEST candidate (or null) "
        "for the verbatim evidence quote, but evaluate the boolean extractions against "
        "the FULL evidence visible in any candidate.\n\n"
        "Return JSON only:\n"
        "{\n"
        "  \"chosen_index\":                              integer 0..N-1, OR null if no candidate states the pre-bid protocol,\n"
        "  \"pre_bid_meeting_specified\":                 bool   (TRUE if doc states pre-bid meeting date/time/address OR a 'meeting if specified in BDS' provision),\n"
        "  \"clarification_request_protocol_present\":    bool   (TRUE if doc explicitly states bidders may submit written queries to the employer/authority for clarification),\n"
        "  \"clarification_response_protocol_present\":   bool   (TRUE if doc explicitly states the employer/authority will respond in writing — typically also via e-procurement portal),\n"
        "  \"clarification_deadline_stated\":             bool   (TRUE if doc states a query cut-off — e.g. 'before 5PM of date of pre-bid meeting' / 'within period specified in BDS' / 'X days prior to bid deadline'),\n"
        "  \"site_visit_provision_present\":              bool   (TRUE if doc states bidders may/shall visit the site of works — including geo-tagging / photo evidence variants),\n"
        "  \"evidence\":                                  \"verbatim quote from the chosen candidate's text identifying the strongest pre-bid protocol signal\",\n"
        "  \"found\":                                     bool,\n"
        "  \"reasoning\":                                 \"one short sentence explaining the choice\"\n"
        "}\n\n"
        "Selection rules — IGNORE the following content (NOT the pre-bid clarification protocol):\n"
        "- CLARIFICATION OF BIDS DURING EVALUATION (Section IV / ITB 27 — different "
        "  clause; this is post-submission clarification, not pre-bid).\n"
        "- BID VALIDITY periods, EMD VALIDITY periods (different threshold typologies).\n"
        "- BID OPENING procedures (post-deadline, different stage).\n"
        "- AMENDMENT OF BIDDING DOCUMENT clauses standalone (these are addendum-issuance "
        "  procedures; PICK them ONLY if integrated with the clarification protocol "
        "  i.e. 'should the clarification result in changes... the Employer shall amend').\n"
        "- WITHDRAWAL OF BIDS, MODIFICATION OF BIDS clauses.\n"
        "- BIDDER ELIGIBILITY / FRAUD AND CORRUPTION clauses.\n"
        "- POST-AWARD clarification clauses (LOA / contract execution).\n"
        "\n"
        "Selection rules — DO pick if the candidate states:\n"
        "- An ITB §7 / §8 'Clarification of Bidding Document, Site Visit, Pre-Bid "
        "  Meeting' clause (the canonical anchor).\n"
        "- A BDS rewrite specifying pre-bid meeting date/time/address (e.g. 'A Pre-Bid "
        "  meeting shall take place at the following date, time and place: Date: ...').\n"
        "- A pre-bid meeting / pre-Bid conference paragraph in PPP DCA/RFP §8.2 or "
        "  similar.\n"
        "- An e-procurement portal / email query-submission mechanism with a stated "
        "  cut-off ('queries before 5PM of date of pre-bid meeting').\n"
        "- A site-visit provision including geo-tagging / photographic evidence "
        "  requirements.\n"
        "\n"
        "Boolean-extraction rules:\n"
        "- pre_bid_meeting_specified is TRUE if EITHER a specific date/time/address is "
        "  given OR the doc explicitly references 'pre-Bid meeting if so specified in "
        "  BDS' / equivalent (the BDS-by-reference path counts).\n"
        "- clarification_request_protocol_present requires explicit bidder-side "
        "  query path ('a Bidder requiring any clarification of the Bidding document "
        "  shall contact the Employer in writing'). Generic 'queries' references "
        "  without the structured path do NOT count.\n"
        "- clarification_response_protocol_present requires explicit employer/"
        "  authority response commitment ('The Employer will respond in writing' / "
        "  'shall promptly publish its response on the e-procurement portal').\n"
        "- clarification_deadline_stated requires an EXPLICIT cut-off statement. "
        "  Generic 'reasonable time' does NOT count.\n"
        "- site_visit_provision_present is TRUE if the doc explicitly invites/requires "
        "  bidders to visit the site of works. Generic 'familiarisation with site "
        "  conditions' WITHOUT a visit-procedure does NOT count.\n"
        "\n"
        "Choose ONE evidence quote that best demonstrates the strongest signal found "
        "across candidates (prefer the canonical ITB §7 clause if available; else "
        "the strongest available signal).\n"
        "\n"
        "- Evidence MUST be an exact substring of the chosen candidate's text.\n"
        "- If no candidate states a pre-bid protocol, set chosen_index=null, all "
        "  booleans=false, found=false.\n"
        "\n"
        "QUOTE FORMAT — STRICT (per L35):\n"
        "- Return a SINGLE CONTIGUOUS quote from ONE sentence or ONE clause only.\n"
        "- Do NOT stitch multiple paragraphs into one quote.\n"
        "- Do NOT add ellipsis ('...') between lines.\n"
        "- Do NOT paraphrase, summarise, or condense — quote EXACTLY as it appears in the supplied text.\n"
        "- Do NOT introduce or remove markdown formatting (asterisks, underscores, italics, backslash-escapes) — preserve the source formatting verbatim.\n"
        "- Pick the SHORTEST contiguous span that proves the protocol signals; one sentence or one clause is usually enough."
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
        "tender_type":           p.get("tender_type"),
        "is_ap_tender":          bool(p.get("is_ap_tender")),
        "TenderType":            p.get("tender_type"),
        "TenderState":           "AndhraPradesh" if p.get("is_ap_tender") else "Other",
        # Pre-RFP / document-presence layer: no bid ambiguity has been
        # raised yet (no bidder has signalled an ambiguity claim).
        # MPG-283 ("BidAmbiguityDetected=true") therefore SKIPs at
        # this layer — same precedent as L48 FM (FMEventInvoked=False
        # for MPW-122). MPG-283 is excluded from RULE_CANDIDATES so
        # this fact isn't strictly required for the active rule, but
        # set explicitly for any future re-introduction.
        "BidAmbiguityDetected":  False,
    }

    ev_cr = p.get("estimated_value_cr")
    if ev_cr is not None:
        try:
            facts["EstimatedValue"] = float(ev_cr) * 1e7
            facts["_estimated_value_cr"] = float(ev_cr)
        except (TypeError, ValueError):
            pass

    return facts


def select_prebid_rule(tender_facts: dict) -> dict | None:
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

def _delete_prior_tier1_prebid(doc_id: str) -> tuple[int, int]:
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
    print(f"  Tier-1 Pre-Bid-Process-Unclear (BGE-M3 + LLM, NO regex)")
    print(f"  doc_id : {DOC_ID}")
    print(f"  model  : {LLM_MODEL}")
    print("=" * 76)

    n_f, n_e = _delete_prior_tier1_prebid(DOC_ID)
    if n_f or n_e:
        print(f"  cleared {n_f} prior Tier-1 PreBid finding node(s) and "
              f"{n_e} edge(s) before re-running")

    facts = fetch_tender_facts(DOC_ID)
    rule  = select_prebid_rule(facts)
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

    # L49 quotas + L50 grep-seeded supplement
    K_FW     = 4
    K_VAL    = 3
    K_MERGED = 14
    t0 = time.perf_counter()

    fw_filter = [t for t in section_types if t in ("ITB", "NIT")]
    if not fw_filter:
        fw_filter = section_types[:1]
    points_fw: list[dict] = []
    try:
        points_fw = qdrant_topk(qvec_fw, DOC_ID, k=K_FW, section_types=fw_filter)
    except RuntimeError:
        points_fw = []

    points_val: list[dict] = []
    val_breakdown: list[tuple[str, int]] = []
    for st in section_types:
        try:
            pts = qdrant_topk(qvec_val, DOC_ID, k=K_VAL, section_types=[st])
            points_val.extend(pts)
            val_breakdown.append((st, len(pts)))
        except RuntimeError:
            val_breakdown.append((st, 0))

    by_id: dict = {}
    for p in points_fw + points_val:
        pid = p["id"]
        if pid not in by_id or p["score"] > by_id[pid]["score"]:
            by_id[pid] = p

    # L50 grep-seeded supplement: tight literal grep for "pre-bid"
    # variants. Pre-bid sections sometimes get diluted in long ITB
    # blocks (HC L153 spans an entire 7-clause section); seeded
    # supplement guarantees the canonical pre-bid section is visible
    # to the LLM regardless of cosine rank.
    SEED_KEYWORDS = ["pre-bid", "Pre-Bid Meeting"]
    _, seed_hits = grep_source_for_keywords(
        DOC_ID, section_types, SEED_KEYWORDS,
    )
    seeded_section_ids = {h["section_node_id"] for h in seed_hits}
    n_seeded_added = 0
    if seeded_section_ids:
        for sid in seeded_section_ids:
            already_in = any(
                (p["payload"].get("section_id") == sid) for p in by_id.values()
            )
            if already_in:
                continue
            sec_rows = rest_get("kg_nodes", {
                "select":  "node_id,properties",
                "node_id": f"eq.{sid}",
            })
            if not sec_rows:
                continue
            mp = sec_rows[0].get("properties") or {}
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
    print(f"  L50 grep-seeded {SEED_KEYWORDS} → "
          f"{len(seeded_section_ids)} matching section(s), "
          f"{n_seeded_added} new (deduped)")
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
    print(f"\n── Step 3: LLM rerank + Pre-Bid multi-field extraction ──")
    user_prompt = build_prebid_rerank_prompt(candidates)
    print(f"  prompt size: {len(user_prompt)} chars (~{len(user_prompt)//4} tokens)")
    raw_content = call_llm(LLM_SYSTEM, user_prompt, max_tokens=900)
    timings["llm"] = time.perf_counter() - t0
    print(f"  wall: {timings['llm']:.2f}s")
    print(f"\n── Raw LLM JSON ──")
    print(raw_content)

    parsed = parse_llm_response(raw_content)
    chosen        = parsed.get("chosen_index")
    pre_bid_mtg   = bool(parsed.get("pre_bid_meeting_specified"))
    clar_request  = bool(parsed.get("clarification_request_protocol_present"))
    clar_response = bool(parsed.get("clarification_response_protocol_present"))
    clar_deadline = bool(parsed.get("clarification_deadline_stated"))
    site_visit    = bool(parsed.get("site_visit_provision_present"))
    evidence      = (parsed.get("evidence") or "").strip()
    found         = bool(parsed.get("found"))
    reason        = (parsed.get("reasoning") or "").strip()

    print(f"\n── Parsed ──")
    print(f"  chosen_index                              : {chosen}")
    print(f"  found                                     : {found}")
    print(f"  pre_bid_meeting_specified                 : {pre_bid_mtg}")
    print(f"  clarification_request_protocol_present    : {clar_request}")
    print(f"  clarification_response_protocol_present   : {clar_response}")
    print(f"  clarification_deadline_stated             : {clar_deadline}")
    print(f"  site_visit_provision_present              : {site_visit}")
    print(f"  reasoning                                 : {reason[:200]}")
    print(f"  evidence                                  : {evidence[:300]!r}")

    section = None
    similarity = None
    ev_passed = False
    ev_score = 0
    ev_method = "skipped"

    llm_found_signal = (chosen is not None) and (
        pre_bid_mtg or clar_request or clar_response or clar_deadline or site_visit
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

    # Decision: COMPLIANT silent if BOTH the request AND response
    # protocols are present. Pre-bid-meeting / site-visit / deadline
    # are nice-to-have audit fields but not required for COMPLIANT —
    # MPW-061's "self-contained" is satisfied if bidders have a path
    # to ask and the employer is committed to answer.
    has_minimum_protocol = clar_request and clar_response
    framework_compliant  = has_minimum_protocol

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
            f"compliant_pre_bid_protocol_present_request={clar_request}_"
            f"response={clar_response}_meeting={pre_bid_mtg}"
        )
    elif is_gap_violation_pre_grep:
        missing = []
        if not clar_request:  missing.append("no_request_protocol")
        if not clar_response: missing.append("no_response_protocol")
        reason_label = "pre_bid_protocol_incomplete_" + "_".join(missing)
    elif is_absence:
        reason_label = "pre_bid_protocol_absent"
    elif grep_promoted_to_unverified:
        reason_label = "pre_bid_unverified_grep_fallback_retrieval_gap"
    elif full_grep_promoted:
        reason_label = ("pre_bid_unverified_kg_coverage_gap"
                        if kg_coverage_gap
                        else "pre_bid_unverified_whole_file_grep_only")
    elif is_unverified_l24:
        reason_label = "pre_bid_unverified_llm_quote_failed_l24"
    else:
        reason_label = "pre_bid_indeterminate"

    print(f"\n── Decision ──")
    print(f"  rule              : {rule['rule_id']} ({rule['severity']}, shape={rule['shape']})")
    print(f"  llm_found_signal  : {llm_found_signal}")
    print(f"  ev_passed         : {ev_passed}  (score={ev_score}, method={ev_method})")
    print(f"  has_minimum_protocol: {has_minimum_protocol}  (request={clar_request}, response={clar_response})")
    print(f"  framework_compliant: {framework_compliant}")
    print(f"  is_compliant_l24  : {is_compliant_l24}")
    print(f"  is_gap_violation  : {is_gap_violation}")
    print(f"  is_unverified     : {is_unverified}")
    print(f"  is_absence        : {is_absence}")
    print(f"  reason_label      : {reason_label}")

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
        evidence_out  = (f"Pre-Bid clarification protocol not found in document "
                         f"after BGE-M3 retrieval, L36 Section-bounded grep, and "
                         f"L40 whole-file grep across {', '.join(section_types)}. "
                         f"Per MPW-061 (HARD_BLOCK Works), the bidding doc must "
                         f"be self-contained and include a clarification mechanism "
                         f"so bidders can resolve any ambiguity before submission.")
        print(f"  → GAP_VIOLATION finding — LLM rerank empty AND grep fallbacks "
              f"empty; pre-bid protocol genuinely absent")
    elif is_gap_violation_pre_grep:
        ev_passed_out = ev_passed
        ev_score_out  = ev_score
        ev_method_out = ev_method
        missing_summary = []
        if not clar_request:  missing_summary.append("no bidder query path")
        if not clar_response: missing_summary.append("no employer response commitment")
        evidence_out  = (f"{evidence}  [Protocol gap: "
                         f"{'; '.join(missing_summary)}]")
        print(f"  → GAP_VIOLATION finding — pre-bid protocol incomplete: "
              f"{', '.join(missing_summary)}")
    elif grep_promoted_to_unverified:
        ev_passed_out = None
        ev_score_out  = None
        ev_method_out = "grep_fallback_retrieval_gap"
        evidence_out  = (f"LLM rerank top-{K} returned no Pre-Bid protocol "
                         f"signal, but exhaustive grep across "
                         f"{', '.join(section_types)} found keyword hits in "
                         f"{len(grep_hits)} section(s). First match: "
                         f"{grep_hits[0]['snippet'][:160] if grep_hits else 'n/a'}")
        print(f"  → UNVERIFIED finding (L36 grep fallback) — retrieval missed "
              f"{len(grep_hits)} section(s) with Pre-Bid keywords")
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
        print(f"  → UNVERIFIED finding — LLM identified Pre-Bid signal but "
              f"quote failed L24 (score={ev_score}, method={ev_method})")
    else:
        ev_passed_out = ev_passed
        ev_score_out  = ev_score
        ev_method_out = ev_method
        evidence_out  = evidence

    rule_node_id = get_or_create_rule_node(DOC_ID, rule["rule_id"])

    if is_gap_violation_pre_grep:
        missing_summary = []
        if not clar_request:  missing_summary.append("no bidder query path")
        if not clar_response: missing_summary.append("no employer response commitment")
        label = (
            f"{TYPOLOGY}: Pre-bid clarification protocol incomplete — "
            f"{', '.join(missing_summary)}; {rule['rule_id']} "
            f"({rule['severity']}) requires self-contained bid documents "
            f"with a clarification mechanism"
        )
    elif is_absence:
        label = (
            f"{TYPOLOGY}: Pre-bid clarification protocol absent — "
            f"{rule['rule_id']} ({rule['severity']}) requires Works "
            f"bidding documents to be self-contained and comprehensive "
            f"with a clarification mechanism"
        )
    elif grep_promoted_to_unverified:
        label = (
            f"{TYPOLOGY}: UNVERIFIED (L36 grep-fallback) — LLM rerank "
            f"missed the Pre-Bid protocol; exhaustive grep found "
            f"{len(grep_hits)} section(s) with Pre-Bid keyword hits; "
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
            f"{TYPOLOGY}: UNVERIFIED — LLM found Pre-Bid signal but quote "
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
        # Pre-Bid framework extraction snapshot
        "pre_bid_meeting_specified":               pre_bid_mtg,
        "clarification_request_protocol_present":  clar_request,
        "clarification_response_protocol_present": clar_response,
        "clarification_deadline_stated":           clar_deadline,
        "site_visit_provision_present":            site_visit,
        "framework_compliant":                     framework_compliant,
        "rule_shape":            rule["shape"],
        "violation_reason":      reason_label,
        "tier":                  1,
        "extracted_by":          "bge-m3+llm-rerank:qwen-2.5-72b@openrouter",
        "retrieval_strategy": (
            f"qdrant_top{K}_router_{family}_section_filter_"
            f"{'-'.join(section_types)}_per_type_quota+grep_seeded+grep_fallback"
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
        "evidence_in_source":    ev_passed_out,
        "evidence_verified":     ev_passed_out,
        "evidence_match_score":  ev_score_out,
        "evidence_match_method": ev_method_out,
        "estimated_value_cr":          facts.get("_estimated_value_cr"),
        "verdict_origin":              rule.get("verdict_origin"),
        "severity_origin":             rule.get("severity_origin"),
        "status":                     "UNVERIFIED" if is_unverified else "OPEN",
        "requires_human_review":      bool(is_unverified),
        "human_review_reason": (
            f"L36 grep fallback fired: LLM rerank top-{K} did not surface the "
            f"Pre-Bid protocol, but exhaustive grep across {section_types} "
            f"found keyword hits in {len(grep_hits)} section(s)."
            if grep_promoted_to_unverified else
            f"L40 whole-file fallback: LLM rerank AND Section-bounded grep both "
            f"empty, but whole-file grep found {len(full_grep_hits)} match line(s). "
            f"{'kg_coverage_gap=TRUE — reviewer should re-run kg_builder.' if kg_coverage_gap else 'Whole-file only hits — verify retrieval coverage.'}"
            if full_grep_promoted else
            f"LLM identified Pre-Bid signal but quote failed L24 (score={ev_score}, "
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
        "source_ref": f"tier1:prebid_check:{rule['rule_id']}",
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
                "clarification_request_protocol_present":  clar_request,
                "clarification_response_protocol_present": clar_response,
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
