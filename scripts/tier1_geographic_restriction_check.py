"""
scripts/tier1_geographic_restriction_check.py

Tier-1 Geographic-Restriction check, BGE-M3 + LLM, NO regex.

MULTI-RULE typology with FOUR sub-checks evaluated from a single
LLM extraction pass:

  Sub-check 1 — FOREIGN-CONTRACTOR BAN ANTI-PATTERN (MPS-184,
    HARD_BLOCK).
    Does the doc explicitly bar foreign bidders ("any contractor
    from abroad not be permitted" / "Indian nationality only")
    WITHOUT the Annexure-2F framework that conditions eligibility
    on DPIIT registration? Per MPS-184, "tender eligibility
    conditions must NOT result in unreasonable exclusion of
    bidders by country" beyond what the PP-MII Order / DoE OM
    23-Jul-2020 authorises.

  Sub-check 2 — LAND-BORDER ANNEXURE-2F PRESENCE (MPG-243 /
    MPS-213 / MPW-180, HARD_BLOCK).
    Per Public Procurement Order No.1 (DoE OM F.No.6/18/2019-PPD
    23-Jul-2020), every tender must include the Annexure-2F model
    clauses + a bidder compliance certificate. ABSENCE = violation
    UNLESS the doc invokes MDB/BFA funding exemption.

  Sub-check 3 — AP-STATE REGISTRATION REQUIREMENT (AP-GO-091,
    ADVISORY informational).
    Per AP-GO-091, contractors of OTHER STATES must register in
    AP BEFORE offering tender for AP works. AP-acceptable per
    GO Ms No 94/2003. Emitted as standalone informational marker
    when AP-State registration is required.

  Sub-check 4 — MDB / BFA FUNDED PROJECT EXEMPTION (informational
    marker).
    Per MPS 2022 §5.1.6(f) (DoE OM 29-Oct-2021), MDB/BFA-funded
    projects may invoke conditions negotiated with the MDB rather
    than the GTE / Annexure-2F default. Emitted as standalone
    informational marker ONLY when the doc explicitly invokes
    the exemption (per user spec).

CRITICAL distinction from the L43 Arbitration-AP-defeats-Central
pattern: AP-GO-091 does NOT defeat MPS-184 / MPG-243. AP-GO-091
authorizes "other-state contractors must register in AP first"
ONLY — it does NOT authorize a foreign-bidder ban or absent
Annexure-2F framework. The AP marker is INDEPENDENT of the
primary outcome here, not a defeats relation.

A doc may emit 0/1/2/3 findings:
  • 0: COMPLIANT (Annexure-2F + bidder cert + no foreign ban)
  • 1 violation OR 1 informational marker
  • 1 primary violation + 1 informational marker
  • 1 primary + 2 informational markers (AP-State Works that
    invokes MDB exemption AND requires AP registration)

Pipeline (post-L43 multi-rule + four-state contract):
  1. Pick rules via condition_evaluator (collect all firing).
  2. Section filter via GEOGRAPHIC_SECTION_ROUTER.
  3. BGE-M3 embed + Qdrant top-K.
  4. LLM rerank with 11-field extraction.
  5. L24 evidence guard.
  6. L36 → L40 grep fallback chain on absence path.
  7. Apply decision tree.
  8. Emit primary + (optionally) informational markers.

Tested on judicial_academy_exp_001 first.
JA L878: "Participation by JV/Consortium/SPV not allowed. Any
contractor from abroad not be permitted." — MPS-184 candidate
violation.
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

TYPOLOGY = "Geographic-Restriction"

QDRANT_URL  = os.environ.get("QDRANT_URL", "http://localhost:6333")
COLLECTION  = "tender_sections"

LLM_MODEL = os.environ.get("LLM_MODEL", "qwen/qwen-2.5-72b-instruct")


# L36 source-grep fallback vocabulary. Phrase-precise enough to flag
# geographic-restriction content without matching every "country" /
# "register" reference in the doc.
GREP_FALLBACK_KEYWORDS = [
    "land border country",
    "land-border country",
    "sharing a land border",
    "shares a land border",
    "Annexure 2F",
    "Annexure-2F",
    "DPIIT",
    "F.No.6/18/2019",
    "F.No.6/18/2019-PPD",
    "DoE OM",
    "23 July 2020",
    "Public Procurement (No.1)",
    "PP No.1",
    "Competent Authority",
    "any contractor from abroad",
    "contractor from abroad not be permitted",
    "Indian nationality",
    "foreign contractor",
    "foreign bidder",
    "foreign supplier",
    "GO.MS. No.94",
    "GO Ms No 94",
    "registered with Government of Andhra Pradesh",
    "apeprocurement.gov.in",
    "Global Tender Enquiry",
    "\\bGTE\\b",
    "MDB",
    "multilateral development bank",
]


# Answer-shaped query — mirrors the literal wording of CLAUSE-AP-LAND-
# BORDER-COUNTRY-RESTRICTION-001 / CLAUSE-AP-NO-JV-CONSORTIUM-001 /
# CLAUSE-LAND-BORDER-WORKS-CERT-001 / CLAUSE-MDB-FUNDED-EXEMPTION-001
# plus the rule-text vocabulary for the four sub-checks.
QUERY_TEXT = (
    "Land-border country bidder restriction Public Procurement "
    "Order No 1 DoE OM F.No.6/18/2019-PPD 23 July 2020 DPIIT "
    "Competent Authority registration Annexure 2F bidder "
    "compliance certificate sub-contracting prohibition foreign "
    "contractor abroad not permitted Indian nationality eligibility "
    "AP Special Class Civil registration Government of Andhra "
    "Pradesh GO Ms No 94 other states e-procurement marketplace "
    "Global Tender Enquiry GTE Multilateral Development Bank MDB "
    "ADB World Bank funded project exemption"
)


# Rule candidates per user-confirmed priority:
#   1. MPS-184    — foreign-ban anti-pattern (HARD_BLOCK Central)
#   2. MPG-243    — Annexure-2F PRESENCE (HARD_BLOCK Central)
#   3. AP-GO-091  — AP-State registration informational (ADVISORY-MARKER)
#   4. MDB-EXEMPTION-MARKER — informational only when explicitly cited
#      (no rule_id; pure marker shape — doesn't cite a rule).
#
# MPS-184's condition_when (TenderType=ANY AND BidderClassification=Local)
# uses BidderClassification which we don't extract → UNKNOWN → L27
# downgrade to ADVISORY. Same for MPG-243 (TenderType=ANY, FIRES).
# The decision tree below applies the actual sub-check semantics from
# the LLM extraction; condition_when is just the rule-applicability
# gate.
RULE_CANDIDATES = [
    {
        "rule_id":          "MPS-184",
        "natural_language": "Tender eligibility conditions on previous experience must NOT require proof of supply in OTHER COUNTRIES or proof of EXPORTS; must NOT result in unreasonable exclusion of Class-I/Class-II local suppliers beyond what is essential for ensuring quality or creditworthiness",
        "severity":         "HARD_BLOCK",
        "layer":             "Central",
        "shape":              "anti_pattern",
        "sub_check":          "foreign_contractor_ban",
    },
    {
        "rule_id":          "MPG-243",
        "natural_language": "Tenders subject to PP No.1 must include Annexure-2F model clauses on land-border-country eligibility, plus the bidder compliance certificate per Section 5",
        "severity":         "HARD_BLOCK",
        "layer":             "Central",
        "shape":              "presence",
        "sub_check":          "annexure_2f_presence",
    },
    {
        "rule_id":          "AP-GO-091",
        "natural_language": "Contractors of OTHER STATES must register in AP BEFORE offering tender for any AP works (per GO Ms No 94/2003). AP-acceptable departure — emitted as informational marker, NOT a violation.",
        "severity":         "ADVISORY",
        "layer":             "AP-State",
        "shape":              "informational",
        "sub_check":          "ap_state_registration",
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


# ── LLM rerank prompt for multi-field geographic extraction ──────────

LLM_SYSTEM = (
    "You are a precise procurement-document fact extractor. "
    "Return JSON only. Quote evidence verbatim from the supplied text. "
    "Never fabricate."
)


# Anchor-keyword discipline (per L39): only patterns that uniquely
# identify geographic-restriction content. Avoid bare "country" /
# "registration" / "nationality" without context-disambiguating
# tokens.
GEOGRAPHIC_TRUNCATE_KEYWORDS = [
    r"land[\s\-]?border",
    r"sharing\s+a?\s*land\s+border",
    r"shares\s+a\s+land\s+border",
    r"\bAnnexure[\s\-]?2F\b",
    r"\bDPIIT\b",
    r"F\.No\.6/18/2019",
    r"F\.No\.\s*6/18/2019",
    r"DoE\s+OM",
    r"23\s+July\s+2020",
    r"PP\s+No\.\s*1",
    r"Public\s+Procurement\s+\(?No\.\s*1\)?",
    r"Competent\s+Authority",
    r"any\s+contractor\s+from\s+abroad",
    r"contractor\s+from\s+abroad\s+not",
    r"Indian\s+nationality",
    r"foreign\s+contractor",
    r"foreign\s+bidder",
    r"GO\s*Ms\s*No\.?\s*94",
    r"G\.O\.MS\.\s*No\.\s*94",
    r"registered\s+with\s+Government\s+of\s+Andhra\s+Pradesh",
    r"apeprocurement\.gov\.in",
    r"Global\s+Tender\s+Enquiry",
    r"\bGTE\b",
    r"\bMDB\b",
    r"multilateral\s+development\s+bank",
    r"\bADB\b",
    r"\bWorld\s+Bank\b",
]


def build_geographic_rerank_prompt(candidates: list[dict]) -> str:
    blocks = []
    for i, c in enumerate(candidates):
        body = smart_truncate(c["full_text"], window=6000,
                              keywords=GEOGRAPHIC_TRUNCATE_KEYWORDS)
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
        "a multi-field summary so four geographic-restriction sub-"
        "checks can be evaluated from a single read.\n"
        "\n"
        "Sub-checks the caller will run from your output:\n"
        "  1. FOREIGN-CONTRACTOR BAN (anti-pattern) — does the doc "
        "explicitly bar foreign bidders ('any contractor from abroad "
        "not be permitted', 'Indian nationality only', 'no JV / "
        "Consortium / SPV / Foreign') WITHOUT the Annexure-2F "
        "framework that conditions eligibility on DPIIT registration?\n"
        "  2. ANNEXURE-2F PRESENCE — does the doc include the DoE OM "
        "F.No.6/18/2019-PPD 23-Jul-2020 land-border-country eligibility "
        "clause AND a bidder compliance certificate?\n"
        "  3. AP-STATE REGISTRATION REQUIREMENT — does the doc require "
        "AP-State contractor registration (Special Class Civil per "
        "GO Ms No 94/2003 OR AP e-procurement marketplace registration)?\n"
        "  4. MDB / BFA FUNDING EXEMPTION — does the doc EXPLICITLY "
        "INVOKE multilateral development bank / bilateral funding "
        "agency funding as a basis for departing from the GTE / "
        "Annexure-2F default? (Just being ADB/WB-funded is NOT enough; "
        "the doc must literally cite the exemption.)\n"
        "\n"
        f"{candidates_block}\n\n"
        "Return JSON only:\n"
        "{\n"
        "  \"chosen_index\":                            integer 0..N-1 of the most relevant geographic-restriction candidate, OR null if no candidate carries any of the sub-checks,\n"
        "  \"foreign_contractor_ban_present\":          bool   (TRUE if doc explicitly bars foreign bidders),\n"
        "  \"jv_consortium_spv_ban_present\":           bool   (TRUE if doc explicitly bars JV/Consortium/SPV — usually clusters with foreign-bidder ban),\n"
        "  \"indian_nationality_only_required\":        bool   (TRUE if doc requires bidder to have Indian nationality with no equivalent DPIIT-registration carve-out),\n"
        "  \"annexure_2f_present\":                     bool   (TRUE if doc carries the DoE OM 23-Jul-2020 land-border-country clause body),\n"
        "  \"land_border_bidder_certificate_present\":  bool   (TRUE if doc requires a bidder compliance certificate per MPS-213 / MPW-180 — usually in Forms section),\n"
        "  \"subcontracting_landborder_prohibition_present\": bool   (TRUE if Works docs prohibit sub-contracting to land-border-country contractors),\n"
        "  \"doe_om_july_2020_referenced\":             bool   (TRUE if the F.No.6/18/2019-PPD 23-Jul-2020 OM is literally cited),\n"
        "  \"competent_authority_named\":               bool   (TRUE if DPIIT Competent Authority is named),\n"
        "  \"ap_state_registration_required\":          bool   (TRUE if doc requires Andhra Pradesh State registration / Special Class Civil per GO Ms No 94),\n"
        "  \"go_ms_no_94_referenced\":                  bool   (TRUE if GO Ms No 94/2003 is cited as the AP-State registration anchor),\n"
        "  \"mdb_bfa_funding_exemption_invoked\":       bool   (TRUE ONLY if doc literally invokes MDB/BFA funding as a GTE/Annexure-2F exemption basis — NOT just because the project is ADB/WB funded),\n"
        "  \"evidence\":                                \"verbatim quote (single contiguous span) — the line(s) most relevant to the strongest sub-check signal you found\",\n"
        "  \"found\":                                   bool,\n"
        "  \"reasoning\":                               \"one or two short sentences explaining what shape the doc takes\"\n"
        "}\n\n"
        "Selection rules — IGNORE the following content (NOT geographic-restriction):\n"
        "- Eligibility class / past-experience requirements (those are PQ / Class-Mismatch typology territory).\n"
        "- COI clauses about consortium members or affiliates.\n"
        "- Generic 'jurisdiction of <city> courts' clauses (those are arbitration-venue territory).\n"
        "- ITB 4.4 SBD-template nationality clauses by themselves — they are descriptive of WB/ADB SBD nationality logic; only count them when the doc OVERRIDES with an explicit Indian-nationality-only or foreign-bidder ban.\n"
        "- Inter-state migrant workmen Acts (those are labor-law clauses).\n"
        "\n"
        "Selection rules — DO pick if the candidate states:\n"
        "- 'Any contractor from abroad not be permitted' / 'Foreign contractors not allowed' / 'Indian nationality required' / 'no JV / Consortium / SPV' (FOREIGN-BAN anti-pattern).\n"
        "- 'Any bidder from a country which shares a land border with India will be eligible to bid only if registered with the Competent Authority' (ANNEXURE-2F).\n"
        "- 'I have read the clause regarding restrictions on procurement from a Bidder of a country which shares a land border... certify...' (BIDDER CERTIFICATE).\n"
        "- 'Special Class Civil registration with Government of Andhra Pradesh per GO Ms No 94' / 'tenders invited from Contractors registered with Government of Andhra Pradesh' (AP-STATE REGISTRATION).\n"
        "- 'This procurement is funded by the World Bank / ADB / multilateral development bank and the conditions negotiated with the lender shall apply' (MDB EXEMPTION).\n"
        "\n"
        "Sub-check 1 disambiguation (ANTI-PATTERN vs SBD-template):\n"
        "- TRUE only if doc has an EXPLICIT, OPERATIVE foreign-bidder ban. The standard SBD ITB 4.4 ('Bidder may have the nationality of any country, subject to ITB 4.8') is NOT itself a ban — it's the SBD nationality clause body. Only count as ban when the doc LITERALLY says 'foreign contractors not permitted' or 'Indian nationality only required'.\n"
        "- Look for sentences combining (foreign / abroad / nationality) + (not allowed / not permitted / shall have / required / barred).\n"
        "\n"
        "Sub-check 4 disambiguation (MDB exemption invoked vs project funded by MDB):\n"
        "- TRUE only if doc explicitly invokes the EXEMPTION (e.g., 'per MPS 2022 §5.1.6(f), this MDB-funded project is governed by World Bank Procurement Regulations and is exempt from the GTE / land-border default').\n"
        "- FALSE if the doc only mentions ADB/WB funding without invoking the exemption clause. The Judicial Academy and High Court docs are ADB/WB-funded but do NOT generally cite the MPS 2022 §5.1.6(f) exemption.\n"
        "\n"
        "QUOTE FORMAT — STRICT (per L35):\n"
        "- Return a SINGLE CONTIGUOUS quote from ONE sentence, ONE clause, or ONE table row. Do NOT stitch multiple paragraphs.\n"
        "- Do NOT add ellipsis ('...') between lines.\n"
        "- Do NOT paraphrase, summarise, or condense — quote EXACTLY as it appears in the supplied text.\n"
        "- Do NOT introduce or remove markdown formatting.\n"
        "- Pick the SHORTEST contiguous span that proves the STRONGEST single sub-check signal you found.\n"
        "\n"
        "MULTI-SUB-CHECK QUOTE DISCIPLINE (CRITICAL):\n"
        "- This typology has 4 sub-checks, but you must STILL return only ONE evidence quote.\n"
        "- DO NOT concatenate text from multiple sub-checks into one quote.\n"
        "- DO NOT include text from multiple non-adjacent paragraphs.\n"
        "- DO NOT paste together (foreign-ban sentence) + (nationality sentence) + (registration sentence) into one block — that is stitching, even if the source paragraphs sit close together.\n"
        "- Priority for which sub-check to quote: (1) foreign_contractor_ban if present → quote that one sentence. (2) annexure_2f_present=true → quote the DoE OM sentence. (3) ap_state_registration_required → quote that one sentence. Pick the FIRST applicable; ignore the rest for the evidence field.\n"
        "- Maximum 2 consecutive sentences. Best is ONE sentence. The other sub-check booleans speak for the other signals — the evidence quote only needs to ground ONE.\n"
        "\n"
        "- If no candidate carries any geographic-restriction signal, set chosen_index=null, found=false, all booleans=false. The L36 grep fallback will then take over.\n"
        "- Evidence MUST be an exact substring of the chosen candidate's text — verifiable byte-for-byte against the source markdown."
    )


def parse_llm_response(raw: str) -> dict:
    """Thin wrapper; actual parsing logic in modules.validation.llm_client.parse_llm_json."""
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
            facts["EstimatedValue"] = float(ev_cr) * 1e7
            facts["_estimated_value_cr"] = float(ev_cr)
        except (TypeError, ValueError):
            pass

    return facts


def select_geographic_rules(tender_facts: dict) -> list[dict]:
    """Pick ALL rules that fire (or fire-as-UNKNOWN per L27 downgrade).
    Like Arbitration (L43), this returns the FULL list — the decision
    tree below evaluates each sub-check against the same LLM extraction."""
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

def _delete_prior_tier1_geographic(doc_id: str) -> tuple[int, int]:
    """Multi-finding-aware cleanup (per L43): a prior run may have
    emitted a primary violation AND informational markers. All are
    wiped here."""
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
    """Insert ValidationFinding + (optionally) VIOLATES_RULE edge.
    Returns (finding_node_id, edge_id_or_none)."""
    finding = rest_post("kg_nodes", [{
        "doc_id":     doc_id,
        "node_type":  "ValidationFinding",
        "label":      label,
        "properties": props,
        "source_ref": f"tier1:geographic_restriction_check:{props.get('rule_id') or 'mdb_marker'}",
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
    print(f"  Tier-1 Geographic-Restriction (BGE-M3 + LLM, NO regex)")
    print(f"  doc_id : {DOC_ID}")
    print(f"  model  : {LLM_MODEL}")
    print(f"  shape  : multi-rule (foreign-ban / Annexure-2F / AP-marker / MDB-exemption)")
    print("=" * 76)

    n_f, n_e = _delete_prior_tier1_geographic(DOC_ID)
    if n_f or n_e:
        print(f"  cleared {n_f} prior Tier-1 Geographic finding node(s) "
              f"and {n_e} edge(s) before re-running")

    facts = fetch_tender_facts(DOC_ID)
    fired_rules = select_geographic_rules(facts)
    if not fired_rules:
        return 0

    rule_foreign_ban  = get_rule_by_sub_check(fired_rules, "foreign_contractor_ban")
    rule_annexure_2f  = get_rule_by_sub_check(fired_rules, "annexure_2f_presence")
    rule_ap_state     = get_rule_by_sub_check(fired_rules, "ap_state_registration")

    print(f"\n  Sub-check rule mapping:")
    print(f"    foreign-ban (MPS-184)    : {rule_foreign_ban['rule_id'] if rule_foreign_ban else 'n/a'}")
    print(f"    Annexure-2F (MPG-243)    : {rule_annexure_2f['rule_id'] if rule_annexure_2f else 'n/a'}")
    print(f"    AP-State (AP-GO-091)     : {rule_ap_state['rule_id'] if rule_ap_state else 'n/a (non-AP)'}")
    print(f"    MDB-exemption marker     : (no rule_id; pure marker)")

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
    print(f"\n── Step 3: LLM rerank + multi-field geographic extraction ──")
    user_prompt = build_geographic_rerank_prompt(candidates)
    print(f"  prompt size: {len(user_prompt)} chars (~{len(user_prompt)//4} tokens)")
    raw_content = call_llm(LLM_SYSTEM, user_prompt, max_tokens=1100)
    timings["llm"] = time.perf_counter() - t0
    print(f"  wall: {timings['llm']:.2f}s")
    print(f"\n── Raw LLM JSON ──")
    print(raw_content)

    parsed = parse_llm_response(raw_content)
    chosen           = parsed.get("chosen_index")
    foreign_ban      = bool(parsed.get("foreign_contractor_ban_present"))
    jv_ban           = bool(parsed.get("jv_consortium_spv_ban_present"))
    indian_nat_only  = bool(parsed.get("indian_nationality_only_required"))
    annex_2f         = bool(parsed.get("annexure_2f_present"))
    bidder_cert      = bool(parsed.get("land_border_bidder_certificate_present"))
    sub_landborder   = bool(parsed.get("subcontracting_landborder_prohibition_present"))
    doe_om_ref       = bool(parsed.get("doe_om_july_2020_referenced"))
    competent_auth   = bool(parsed.get("competent_authority_named"))
    ap_reg_required  = bool(parsed.get("ap_state_registration_required"))
    go_ms_94         = bool(parsed.get("go_ms_no_94_referenced"))
    mdb_exemption    = bool(parsed.get("mdb_bfa_funding_exemption_invoked"))
    evidence         = (parsed.get("evidence") or "").strip()
    found            = bool(parsed.get("found"))
    reason           = (parsed.get("reasoning") or "").strip()

    print(f"\n── Parsed (multi-field) ──")
    print(f"  chosen_index                                : {chosen}")
    print(f"  found                                       : {found}")
    print(f"  foreign_contractor_ban_present              : {foreign_ban}   ← MPS-184 anti-pattern")
    print(f"  jv_consortium_spv_ban_present               : {jv_ban}")
    print(f"  indian_nationality_only_required            : {indian_nat_only}")
    print(f"  annexure_2f_present                         : {annex_2f}     ← MPG-243 PRESENCE")
    print(f"  land_border_bidder_certificate_present      : {bidder_cert}  ← MPS-213/MPW-180 PRESENCE")
    print(f"  subcontracting_landborder_prohibition_present : {sub_landborder}")
    print(f"  doe_om_july_2020_referenced                 : {doe_om_ref}")
    print(f"  competent_authority_named                   : {competent_auth}")
    print(f"  ap_state_registration_required              : {ap_reg_required}  ← AP-GO-091 marker")
    print(f"  go_ms_no_94_referenced                      : {go_ms_94}")
    print(f"  mdb_bfa_funding_exemption_invoked           : {mdb_exemption}    ← MDB-exemption marker")
    print(f"  reasoning                                   : {reason[:200]}")
    print(f"  evidence                                    : {evidence[:300]!r}")

    section = None
    similarity = None
    ev_passed = False
    ev_score = 0
    ev_method = "skipped"

    llm_chose_candidate = (chosen is not None and isinstance(chosen, int)
                           and 0 <= chosen < len(candidates))
    any_signal = (foreign_ban or annex_2f or ap_reg_required or mdb_exemption
                  or jv_ban or indian_nat_only)
    llm_found_signal = found and any_signal and llm_chose_candidate

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
            print(f"  ⚠ no evidence quote provided — treating as L24-failed (empty)")
            ev_passed = False; ev_score = 0; ev_method = "empty"
    else:
        print(f"  → no candidate chosen by LLM")

    # L36 → L40 fallback chain on absence / L24-fail
    raw_is_absence = (not llm_chose_candidate) or (not any_signal)
    is_unverified_l24_fail = (llm_chose_candidate and llm_found_signal and not ev_passed)

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

    # Decision tree (multi-finding) ──────────────────────────────────
    # Priority for PRIMARY finding:
    #   1. foreign_contractor_ban_present (or indian_nationality_only)
    #      → MPS-184 HARD_BLOCK
    #   2. annexure_2f_present=False AND not MDB-exempted
    #      → MPG-243 HARD_BLOCK
    #   3. UNVERIFIED chain → MPG-243 UNVERIFIED
    #   4. true ABSENCE → MPG-243 ABSENCE
    #   5. COMPLIANT → no primary finding
    #
    # SEPARATE informational markers (independent of primary):
    #   • AP-GO-091 marker: emitted if ap_state_registration_required
    #     AND AP-State context AND ev_passed
    #   • MDB-exemption marker: emitted if mdb_bfa_funding_exemption_invoked
    #     AND ev_passed (only when literally cited per user spec)

    is_works = (facts.get("tender_type") in ("Works", "EPC"))
    is_ap_works = bool(facts.get("is_ap_tender") and is_works)

    primary_rule: dict | None = None
    primary_severity: str | None = None
    primary_status: str = "OPEN"
    primary_label: str = ""
    primary_reason_label: str = ""
    primary_evidence: str = evidence
    primary_attach_section: bool = section is not None

    # Branch (1) — foreign-bidder-ban anti-pattern (MPS-184)
    if (foreign_ban or indian_nat_only) and rule_foreign_ban and ev_passed:
        primary_rule = rule_foreign_ban
        primary_severity = primary_rule["severity"]
        primary_status = "OPEN"
        ban_kinds = []
        if foreign_ban:        ban_kinds.append("foreign_contractor_ban")
        if jv_ban:             ban_kinds.append("jv_consortium_spv_ban")
        if indian_nat_only:    ban_kinds.append("indian_nationality_only")
        primary_reason_label = f"foreign_ban_anti_pattern_{'_'.join(ban_kinds)}"
        primary_label = (
            f"{TYPOLOGY}: {primary_rule['rule_id']} ({primary_severity}) — "
            f"foreign-bidder ban detected ({' AND '.join(ban_kinds)}) without "
            f"the Annexure-2F framework that conditions eligibility on DPIIT "
            f"registration; tender eligibility must NOT result in unreasonable "
            f"exclusion of bidders by country (per MPS-184 / DoE OM "
            f"23-Jul-2020)"
        )
    # Branch (2) — UNVERIFIED chain
    elif is_unverified_primary:
        primary_rule = rule_annexure_2f or rule_foreign_ban
        primary_severity = primary_rule["severity"]
        primary_status = "UNVERIFIED"
        if grep_promoted_to_unverified:
            primary_reason_label = "geographic_unverified_grep_fallback_retrieval_gap"
        elif full_grep_promoted:
            primary_reason_label = ("geographic_unverified_kg_coverage_gap"
                                    if kg_coverage_gap
                                    else "geographic_unverified_whole_file_grep_only")
        else:
            primary_reason_label = "geographic_unverified_llm_quote_failed_l24"
        primary_label = (
            f"{TYPOLOGY}: UNVERIFIED — {primary_reason_label}; requires human review"
        )
        primary_attach_section = (section is not None
                                  and not grep_promoted_to_unverified
                                  and not full_grep_promoted)
        primary_evidence = (
            f"L36 fallback found {len(grep_hits)} section(s) with geographic-restriction keyword hits"
            if grep_promoted_to_unverified else
            f"L40 whole-file fallback found {len(full_grep_hits)} match line(s)"
            f"{' (KG-coverage GAP)' if kg_coverage_gap else ''}"
            if full_grep_promoted else
            f"LLM identified geographic-restriction signal but quote failed L24 "
            f"(score={ev_score}, method={ev_method})"
        )
    # Branch (3) — Annexure-2F absent, no MDB exemption
    elif (rule_annexure_2f and not annex_2f and not mdb_exemption
          and (is_absence or not annex_2f)):
        primary_rule = rule_annexure_2f
        primary_severity = primary_rule["severity"]
        primary_status = "OPEN"
        primary_reason_label = "annexure_2f_absent"
        primary_label = (
            f"{TYPOLOGY}: Annexure-2F land-border-country eligibility clause "
            f"absent — {primary_rule['rule_id']} ({primary_severity}) "
            f"requires every tender to include the DoE OM 23-Jul-2020 PP-No.1 "
            f"land-border-country eligibility framework + bidder compliance "
            f"certificate per MPS-213/MPW-180"
        )
        primary_attach_section = section is not None
        primary_evidence = (
            f"Annexure-2F land-border-country eligibility clause not detected "
            f"in document after BGE-M3 retrieval, L36 Section-bounded grep, "
            f"and L40 whole-file grep on {', '.join(section_types)} section "
            f"types. No MDB/BFA funding exemption invoked. Per MPG-243, every "
            f"tender subject to PP-No.1 must include this framework."
        )
    # Branch (4) — COMPLIANT (clause present + ev_passed + no anti-pattern)
    else:
        primary_rule = None
        if annex_2f and bidder_cert:
            print(f"\n  → COMPLIANT — Annexure-2F present, bidder certificate present, no foreign-bidder ban.")
        elif mdb_exemption:
            print(f"\n  → COMPLIANT — MDB/BFA funding exemption invoked; Annexure-2F default does not apply.")
        else:
            print(f"\n  → no primary violation — partial signal but anti-pattern not detected, Annexure-2F partial or out of scope.")

    print(f"\n── Decision (primary) ──")
    print(f"  rule              : {primary_rule['rule_id'] if primary_rule else '(none — COMPLIANT)'}")
    print(f"  severity          : {primary_severity}")
    print(f"  status            : {primary_status}")
    print(f"  reason_label      : {primary_reason_label}")
    print(f"  is_compliant      : {primary_rule is None}")

    findings_emitted: list[str] = []
    edges_emitted:    list[str] = []

    # 10. Materialise primary finding (if any)
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

        # Coerce evidence_match_method appropriately
        ev_in_source = ev_passed
        ev_score_out = ev_score
        ev_method_out = ev_method
        if is_absence:
            ev_in_source = None; ev_score_out = None; ev_method_out = "absence_finding_no_evidence"
        elif grep_promoted_to_unverified:
            ev_in_source = None; ev_score_out = None; ev_method_out = "grep_fallback_retrieval_gap"
        elif full_grep_promoted:
            ev_in_source = None; ev_score_out = None
            ev_method_out = "whole_file_grep_kg_coverage_gap" if kg_coverage_gap else "whole_file_grep_match"

        primary_props = {
            "rule_id":                          primary_rule["rule_id"],
            "typology_code":                    TYPOLOGY,
            "severity":                         primary_severity,
            "evidence":                         primary_evidence,
            "extraction_path":                  primary_rule["shape"],
            "llm_found_signal":                 llm_found_signal,
            # Multi-field LLM extraction snapshot
            "foreign_contractor_ban_present":              foreign_ban,
            "jv_consortium_spv_ban_present":               jv_ban,
            "indian_nationality_only_required":            indian_nat_only,
            "annexure_2f_present":                         annex_2f,
            "land_border_bidder_certificate_present":      bidder_cert,
            "subcontracting_landborder_prohibition_present": sub_landborder,
            "doe_om_july_2020_referenced":                 doe_om_ref,
            "competent_authority_named":                   competent_auth,
            "ap_state_registration_required":              ap_reg_required,
            "go_ms_no_94_referenced":                      go_ms_94,
            "mdb_bfa_funding_exemption_invoked":           mdb_exemption,
            "rule_shape":                       primary_rule["shape"],
            "violation_reason":                 primary_reason_label,
            "tier":                             1,
            "extracted_by":                     "bge-m3+llm-rerank:qwen-2.5-72b@openrouter",
            "retrieval_strategy": (
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
            "evidence_in_source":               ev_in_source,
            "evidence_verified":                ev_in_source,
            "evidence_match_score":             ev_score_out,
            "evidence_match_method":            ev_method_out,
            "estimated_value_cr":               facts.get("_estimated_value_cr"),
            "verdict_origin":                   primary_rule.get("verdict_origin"),
            "severity_origin":                  primary_rule.get("severity_origin"),
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

        edge_props = ({
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
            "evidence_match_score":  ev_score_out,
            "evidence_match_method": ev_method_out,
        } if primary_status == "OPEN" else None)

        finding_id, edge_id = _materialise_finding(
            DOC_ID, primary_props, primary_label,
            rule_node_id, section_node_id,
            emit_edge=(primary_status == "OPEN"),
            edge_props=edge_props,
        )
        findings_emitted.append(finding_id)
        if edge_id: edges_emitted.append(edge_id)
        timings["materialise_primary"] = time.perf_counter() - t0
        print(f"\n  → primary ValidationFinding {finding_id}  "
              f"(status={primary_status}, severity={primary_severity})")
        if edge_id:
            print(f"  → VIOLATES_RULE             {edge_id}")
        else:
            print(f"  → no VIOLATES_RULE edge (UNVERIFIED — awaiting human review)")

    # 11. AP-GO-091 informational marker (independent of primary)
    if (rule_ap_state is not None and ap_reg_required and ev_passed
            and facts.get("is_ap_tender") and is_works):
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
        marker_rule_node_id = get_or_create_rule_node(DOC_ID, rule_ap_state["rule_id"])

        marker_label = (
            f"{TYPOLOGY}: AP-STATE-REGISTRATION-RECOGNISED — "
            f"{rule_ap_state['rule_id']} (ADVISORY informational) — doc requires "
            f"AP-State contractor registration ({'GO Ms No 94 cited' if go_ms_94 else 'AP registration cited'}) "
            f"per AP-GO-091; AP-acceptable departure from open-bidding default, "
            f"NOT a violation"
        )
        marker_props = {
            "rule_id":                          rule_ap_state["rule_id"],
            "typology_code":                    TYPOLOGY,
            "severity":                         "ADVISORY",
            "evidence":                         evidence,
            "extraction_path":                  "informational",
            "llm_found_signal":                 llm_found_signal,
            "ap_state_registration_required":   True,
            "go_ms_no_94_referenced":           go_ms_94,
            "rule_shape":                       "informational",
            "violation_reason":                 "ap_state_registration_recognised_acceptable_departure",
            "tier":                             1,
            "marker_kind":                      "informational",
            "extracted_by":                     "bge-m3+llm-rerank:qwen-2.5-72b@openrouter",
            "doc_family":                       family,
            "section_filter":                   section_types,
            "rerank_chosen_index":              chosen,
            "section_node_id":                  marker_section_node_id,
            "section_heading":                  section["heading"]            if section else None,
            "source_file":                      section["source_file"]        if section else None,
            "line_start_local":                 section["line_start_local"]   if section else None,
            "line_end_local":                   section["line_end_local"]     if section else None,
            "qdrant_similarity":                round(similarity, 4) if similarity is not None else None,
            "evidence_in_source":               ev_passed,
            "evidence_verified":                ev_passed,
            "evidence_match_score":             ev_score,
            "evidence_match_method":            ev_method,
            "estimated_value_cr":               facts.get("_estimated_value_cr"),
            "status":                           "OPEN",
            "requires_human_review":            False,
            "defeated":                         False,
        }
        marker_edge_props = {
            "rule_id":               rule_ap_state["rule_id"],
            "typology":              TYPOLOGY,
            "severity":              "ADVISORY",
            "defeated":              False,
            "tier":                  1,
            "extraction_path":       "informational",
            "violation_reason":      "ap_state_registration_recognised_acceptable_departure",
            "marker_kind":           "informational",
            "doc_family":            family,
            "qdrant_similarity":     round(similarity, 4) if similarity is not None else None,
            "evidence":              evidence,
            "evidence_match_score":  ev_score,
            "evidence_match_method": ev_method,
        }
        marker_id, marker_edge_id = _materialise_finding(
            DOC_ID, marker_props, marker_label,
            marker_rule_node_id, marker_section_node_id,
            emit_edge=True, edge_props=marker_edge_props,
        )
        findings_emitted.append(marker_id)
        if marker_edge_id: edges_emitted.append(marker_edge_id)
        timings["materialise_ap_marker"] = time.perf_counter() - t0
        print(f"\n  → AP-GO-091 informational ValidationFinding {marker_id}  "
              f"(status=OPEN, severity=ADVISORY, marker_kind=informational)")
        print(f"  → VIOLATES_RULE              {marker_edge_id}")

    # 12. MDB-exemption informational marker (only when explicitly cited)
    if mdb_exemption and ev_passed and llm_chose_candidate:
        t0 = time.perf_counter()
        marker_section_node_id = section["section_node_id"]
        # No rule_id for MDB marker — it's a pure marker shape. The
        # finding still attaches to a Section (no edge to a RuleNode).
        marker_label = (
            f"{TYPOLOGY}: MDB-FUNDING-EXEMPTION-RECOGNISED (ADVISORY "
            f"informational) — doc explicitly invokes MDB/BFA funding "
            f"as an exemption from the GTE / Annexure-2F default per "
            f"MPS 2022 §5.1.6(f); the lender's procurement regulations "
            f"govern eligibility instead"
        )
        marker_props = {
            "rule_id":                          None,
            "typology_code":                    TYPOLOGY,
            "severity":                         "ADVISORY",
            "evidence":                         evidence,
            "extraction_path":                  "informational",
            "llm_found_signal":                 llm_found_signal,
            "mdb_bfa_funding_exemption_invoked": True,
            "rule_shape":                       "informational",
            "violation_reason":                 "mdb_funding_exemption_recognised",
            "tier":                             1,
            "marker_kind":                      "informational",
            "extracted_by":                     "bge-m3+llm-rerank:qwen-2.5-72b@openrouter",
            "doc_family":                       family,
            "section_filter":                   section_types,
            "rerank_chosen_index":              chosen,
            "section_node_id":                  marker_section_node_id,
            "section_heading":                  section["heading"],
            "source_file":                      section["source_file"],
            "line_start_local":                 section["line_start_local"],
            "line_end_local":                   section["line_end_local"],
            "qdrant_similarity":                round(similarity, 4) if similarity is not None else None,
            "evidence_in_source":               ev_passed,
            "evidence_verified":                ev_passed,
            "evidence_match_score":             ev_score,
            "evidence_match_method":            ev_method,
            "estimated_value_cr":               facts.get("_estimated_value_cr"),
            "status":                           "OPEN",
            "requires_human_review":            False,
            "defeated":                         False,
        }
        # No VIOLATES_RULE edge (no rule_id; pure marker)
        marker_id, _ = _materialise_finding(
            DOC_ID, marker_props, marker_label,
            None, marker_section_node_id,
            emit_edge=False, edge_props=None,
        )
        findings_emitted.append(marker_id)
        timings["materialise_mdb_marker"] = time.perf_counter() - t0
        print(f"\n  → MDB-exemption informational ValidationFinding {marker_id}  "
              f"(status=OPEN, severity=ADVISORY, marker_kind=informational, no edge — pure marker)")

    # Summary
    timings["total_wall"] = time.perf_counter() - t_start
    print()
    print("=" * 76)
    print(f"  SUMMARY — {len(findings_emitted)} finding(s), {len(edges_emitted)} edge(s)")
    print("=" * 76)
    for k, v in timings.items():
        unit = "s" if v >= 1 else "ms"
        val  = v if v >= 1 else v * 1000
        print(f"    {k:24s} {val:8.2f} {unit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
