"""
scripts/tier1_spec_tailoring_check.py

Tier-1 Spec-Tailoring check, BGE-M3 + LLM, NO regex.

PRESENCE shape with anti-pattern detection. Per the read-first scan,
14 TYPE_1_ACTIONABLE rules in the Spec-Tailoring typology — most are
Goods-only or evaluation-method-specific. The Tier-1-fittable subset
on AP Works/PPP corpus collapses to:

    GFR-G-030  TenderType=ANY              HARD_BLOCK
               "Description of subject matter shall NOT indicate a
               requirement for a particular trade mark, trade name
               or brand."

Excluded from RULE_CANDIDATES:
    GFR-G-031  WARNING                     BIS-based specs (compliance
               check, not anti-pattern; corpus already uses BIS).
    MPG-027/028, CVC-019                   Goods-only (SKIPs).
    CVC-039/072/107                        Goods sample / evaluation
               method (SKIPs or different scope).
    GFR-G-029                              Vague meta-quality.

Anchors (clause templates):
    CLAUSE-PAC-001                         Proprietary Article
                                           Certificate (Forms) — the
                                           compliance escape valve when
                                           a brand IS named with
                                           recorded justification.
    CLAUSE-MAKE-IN-INDIA-SPEC-001          Specs calibration for local
                                           suppliers.

Corpus pattern (read-first):
    All 6 docs use the standard Indian Works convention — "approved
    make and quality" with engineer pre-approval, BIS / IS standard
    references, functional/performance specs. No specific trade
    marks or brand names called out.
    Predicted: 6/6 silent COMPLIANT.

Pipeline:
  1. Pick rule (GFR-G-030 fires on all 6 — TenderType=ANY).
  2. Section filter via SPEC_TAILORING_SECTION_ROUTER.
  3. BGE-M3 dual queries (framework + value).
  4. Per-section-type quota retrieval (L49) + grep-seeded supplement
     for "approved make" / "or equivalent" / "BIS" / "IS \\d+" (L50).
  5. LLM rerank with Spec-Tailoring-specific ignore rules (non-spec
     "or equivalent" matches in nationality / engineer-rank declarations;
     scope of work boilerplate; payment terms) and 5-field structured
     extraction.
  6. L24 evidence-guard hallucination check.
  7. L36/L40 grep fallback for absence path.
  8. Decision tree (silent-on-COMPLIANT per L48):
        COMPLIANT silent if:
          - no specific brand name, OR
          - brand name + "or equivalent", OR
          - brand name + PAC justification, OR
          - generic "approved make" pattern, OR
          - BIS / IS standard reference.
        GAP_VIOLATION if:
          - specific brand named WITHOUT "or equivalent" AND
            WITHOUT PAC justification.
        UNVERIFIED if L24 fails.

Tested on judicial_academy_exp_001 first (expected: COMPLIANT silent —
JA uses BIS standards + "approved make" generic pattern, no brand
names).
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

TYPOLOGY = "Spec-Tailoring"

QDRANT_URL  = os.environ.get("QDRANT_URL", "http://localhost:6333")
COLLECTION  = "tender_sections"

LLM_MODEL = os.environ.get("LLM_MODEL", "qwen/qwen-2.5-72b-instruct")


# For anti-pattern typologies, the grep fallback only fires on
# "absence + signal-bearing keywords found anywhere". The keywords
# below are ANTI-PATTERN signals only — generic compliance markers
# like "BIS" / "IS " / "or equivalent" are excluded because their
# presence is COMPLIANT (not anti-pattern). False-positive substring
# matches like "IS " in "this is" / "exists" are eliminated by
# keeping the vocabulary tight.
GREP_FALLBACK_KEYWORDS = [
    "approved make",
    "approved brand",
    "Proprietary Article",
    "trade mark",
    "trademark",
    "brand name",
    "manufactured by",
    "approved manufacturer",
]


QUERY_FRAMEWORK = (
    "Technical specifications brand make manufacturer trade mark "
    "approved make BIS standard IS Code generic functional performance "
    "specifications GFR Rule 173 Proprietary Article Certificate"
)
QUERY_VALUE = (
    "approved make and quality of approved manufacturer make brand "
    "or equivalent BIS standard IS Code 456 IS 800 Bureau of Indian "
    "Standards specifications materials workmanship"
)
QUERY_TEXT = QUERY_VALUE


RULE_CANDIDATES = [
    {
        "rule_id":          "GFR-G-030",
        "natural_language": "Description of subject matter of procurement shall be objective, functional, generic, measurable; shall NOT indicate a requirement for a particular trade mark, trade name or brand",
        "severity":         "HARD_BLOCK",
        "layer":            "Central",
        "shape":            "presence_anti_pattern",
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


LLM_SYSTEM = (
    "You are a precise procurement-document fact extractor. "
    "Return JSON only. Quote evidence verbatim from the supplied text. "
    "Never fabricate."
)


SPEC_TRUNCATE_KEYWORDS = [
    r"approved\s+make",
    r"approved\s+brand",
    r"or\s+equivalent",
    r"or\s+equal",
    r"BIS",
    r"\bIS\s*[:\s]\s*\d+",
    r"\bIS\s+\d+",
    r"trade\s*mark",
    r"trade\s*name",
    r"brand\s*name",
    r"manufactured\s+by",
    r"approved\s+manufacturer",
    r"proprietary\s+article",
    r"\bPAC\b",
]


def build_spec_rerank_prompt(candidates: list[dict]) -> str:
    blocks = []
    for i, c in enumerate(candidates):
        body = smart_truncate(c["full_text"], window=3000,
                               keywords=SPEC_TRUNCATE_KEYWORDS)
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
        "Extract whether the document's TECHNICAL SPECIFICATIONS engage in BRAND-"
        "TAILORING — i.e. naming a specific trade mark, trade name, brand, or "
        "manufacturer in a way that restricts competition, in violation of "
        "GFR-G-030 ('Description of subject matter shall NOT indicate a "
        "requirement for a particular trade mark, trade name or brand').\n\n"
        f"{candidates_block}\n\n"
        "Question: Across ALL candidates, do the technical specifications name "
        "specific brands/manufacturers? If yes, is the naming qualified by 'or "
        "equivalent' OR a Proprietary Article Certificate (PAC) justification? "
        "Pick the SINGLE BEST candidate (or null) for the verbatim evidence "
        "quote, but evaluate the boolean extractions against the FULL evidence "
        "visible in any candidate.\n\n"
        "Return JSON only:\n"
        "{\n"
        "  \"chosen_index\":                              integer 0..N-1, OR null if no candidate states relevant spec content,\n"
        "  \"spec_names_specific_brand_or_manufacturer\": bool   (TRUE only if a SPECIFIC company / trade name is named — e.g. 'Polycab', 'Havells', 'Tata', 'Schneider', 'Siemens', 'Crompton', 'L&T', 'Jindal', 'Kalpataru'. Generic 'approved make' / 'approved manufacturer' WITHOUT naming a specific company is FALSE.),\n"
        "  \"spec_has_or_equivalent_qualifier\":          bool   (TRUE if a brand-name spec is followed by 'or equivalent' / 'or equal' / equivalent qualifier within the SAME spec line. Note: 'or equivalent' in non-spec contexts — nationality declarations, engineer-rank equivalence, articles of incorporation — does NOT count.),\n"
        "  \"spec_uses_generic_approved_make\":           bool   (TRUE if doc uses 'approved make and quality' / 'approved manufacturer' / 'as approved by the Engineer' WITHOUT naming specific brands — this is the standard Indian Works convention; COMPLIANT.),\n"
        "  \"spec_uses_bis_or_iso_standard\":             bool   (TRUE if doc references BIS / IS:[number] / Indian Standard / ISO standard / IEC standard for the spec — these are objective standards-based specs; COMPLIANT.),\n"
        "  \"spec_has_pac_justification\":                bool   (TRUE if doc includes a Proprietary Article Certificate / PAC clause justifying a specific brand with recorded reasoning. Generic 'PAC' substring matches in unrelated words — PACKAGE, IMPACT, CAPACITY — do NOT count.),\n"
        "  \"evidence\":                                  \"verbatim quote from the chosen candidate's text identifying the strongest spec signal\",\n"
        "  \"found\":                                     bool,\n"
        "  \"reasoning\":                                 \"one short sentence explaining the choice\"\n"
        "}\n\n"
        "Selection rules — IGNORE the following content (NOT spec-tailoring):\n"
        "- 'or equivalent' in NATIONALITY / Engineer-rank / Articles-of-Incorporation contexts. These are standing language patterns, not spec qualifiers.\n"
        "- BIDDER ELIGIBILITY rules / class restrictions (covered by Criteria-Restriction-Narrow).\n"
        "- BANK / EMD / PBG instruments (Scheduled Bank etc.).\n"
        "- INSURANCE clauses naming insurers.\n"
        "- FOREIGN-BIDDER bans (covered by Geographic-Restriction).\n"
        "- POST-AWARD operations & maintenance clauses.\n"
        "- BOQ line-item descriptions that are FUNCTIONAL ('150 mm x 150 mm x 6 mm thick glazed tiles') without brand names.\n"
        "- Mechanical / civil-engineering work descriptions citing material properties (cement mortar 1:4) without brand names.\n"
        "\n"
        "Selection rules — DO pick if the candidate states:\n"
        "- A specification line naming a SPECIFIC company / trade mark / brand (e.g. 'Cement: ACC' / 'Wires: Polycab' / 'Switchgear: Schneider'). This is the violation pattern.\n"
        "- A generic spec line 'all materials shall be of approved make and quality' / 'approved by the Engineer' — this is COMPLIANT (no brand named).\n"
        "- A BIS / IS standard reference 'as per IS:456' / 'conforming to BIS' — COMPLIANT.\n"
        "- A Proprietary Article Certificate clause — JUSTIFIED brand naming.\n"
        "\n"
        "- Evidence MUST be an exact substring of the chosen candidate's text.\n"
        "- If no candidate has spec content (e.g. all candidates are non-spec sections), set chosen_index=null, all booleans=false, found=false.\n"
        "\n"
        "QUOTE FORMAT — STRICT (per L35):\n"
        "- Return a SINGLE CONTIGUOUS quote from ONE sentence or ONE clause only.\n"
        "- Do NOT stitch multiple paragraphs into one quote.\n"
        "- Do NOT add ellipsis ('...') between lines.\n"
        "- Do NOT paraphrase, summarise, or condense — quote EXACTLY as it appears in the supplied text.\n"
        "- Do NOT introduce or remove markdown formatting (asterisks, underscores, italics, backslash-escapes) — preserve the source formatting verbatim.\n"
        "- Pick the SHORTEST contiguous span that proves the spec signal."
    )


def parse_llm_response(raw: str) -> dict:
    return parse_llm_json(raw)


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
        "TechnicalSpecificationsPresent": True,
    }

    ev_cr = p.get("estimated_value_cr")
    if ev_cr is not None:
        try:
            facts["EstimatedValue"] = float(ev_cr) * 1e7
            facts["_estimated_value_cr"] = float(ev_cr)
        except (TypeError, ValueError):
            pass

    return facts


def select_spec_rule(tender_facts: dict) -> dict | None:
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


def _delete_prior_tier1_spec(doc_id: str) -> tuple[int, int]:
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


def main() -> int:
    timings: dict[str, float] = {}
    t_start = time.perf_counter()

    print("=" * 76)
    print(f"  Tier-1 Spec-Tailoring (BGE-M3 + LLM, NO regex)")
    print(f"  doc_id : {DOC_ID}")
    print(f"  model  : {LLM_MODEL}")
    print("=" * 76)

    n_f, n_e = _delete_prior_tier1_spec(DOC_ID)
    if n_f or n_e:
        print(f"  cleared {n_f} prior Tier-1 Spec-Tailoring finding node(s) and "
              f"{n_e} edge(s) before re-running")

    facts = fetch_tender_facts(DOC_ID)
    rule  = select_spec_rule(facts)
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

    K_FW     = 4
    K_VAL    = 3
    K_MERGED = 14
    t0 = time.perf_counter()

    fw_filter = [t for t in section_types if t in ("Specifications", "Scope", "BOQ")]
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

    SEED_KEYWORDS = ["approved make", "approved brand", "Proprietary Article"]
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
    print(f"\n── Step 3: LLM rerank + Spec-Tailoring extraction ──")
    user_prompt = build_spec_rerank_prompt(candidates)
    print(f"  prompt size: {len(user_prompt)} chars (~{len(user_prompt)//4} tokens)")
    raw_content = call_llm(LLM_SYSTEM, user_prompt, max_tokens=900)
    timings["llm"] = time.perf_counter() - t0
    print(f"  wall: {timings['llm']:.2f}s")
    print(f"\n── Raw LLM JSON ──")
    print(raw_content)

    parsed = parse_llm_response(raw_content)
    chosen          = parsed.get("chosen_index")
    names_brand     = bool(parsed.get("spec_names_specific_brand_or_manufacturer"))
    has_or_equiv    = bool(parsed.get("spec_has_or_equivalent_qualifier"))
    generic_approved = bool(parsed.get("spec_uses_generic_approved_make"))
    bis_iso         = bool(parsed.get("spec_uses_bis_or_iso_standard"))
    has_pac         = bool(parsed.get("spec_has_pac_justification"))
    evidence        = (parsed.get("evidence") or "").strip()
    found           = bool(parsed.get("found"))
    reason          = (parsed.get("reasoning") or "").strip()

    print(f"\n── Parsed ──")
    print(f"  chosen_index                                : {chosen}")
    print(f"  found                                       : {found}")
    print(f"  spec_names_specific_brand_or_manufacturer   : {names_brand}")
    print(f"  spec_has_or_equivalent_qualifier            : {has_or_equiv}")
    print(f"  spec_uses_generic_approved_make             : {generic_approved}")
    print(f"  spec_uses_bis_or_iso_standard               : {bis_iso}")
    print(f"  spec_has_pac_justification                  : {has_pac}")
    print(f"  reasoning                                   : {reason[:200]}")
    print(f"  evidence                                    : {evidence[:300]!r}")

    section = None
    similarity = None
    ev_passed = False
    ev_score = 0
    ev_method = "skipped"

    llm_chose_candidate = chosen is not None and isinstance(chosen, int) and 0 <= chosen < len(candidates)
    llm_found_signal = llm_chose_candidate and (
        names_brand or has_or_equiv or generic_approved or bis_iso or has_pac
    )

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
                print(f"  L24_FAILED — quote unverifiable.")
        else:
            print(f"  ⚠ no evidence quote provided — treating as L24-failed (empty)")
            ev_passed = False; ev_score = 0; ev_method = "empty"
    else:
        print(f"  → no candidate chosen by LLM (no spec signal)")

    # Decision: GAP_VIOLATION iff brand named AND no compliance escape valve.
    # Compliance escape valves (any one is sufficient):
    #   - "or equivalent" / "or equal" qualifier
    #   - PAC justification
    #   - generic "approved make" pattern (LLM may co-flag with names_brand
    #     when the doc uses both — compliance overrides)
    #   - BIS / ISO standard reference (objective standards-based spec)
    is_arbitrary_brand = (
        names_brand
        and not has_or_equiv
        and not has_pac
        and not generic_approved   # compliance override (L54)
        and not bis_iso            # compliance override (L54)
    )

    is_compliant_l24  = llm_chose_candidate and ev_passed and not is_arbitrary_brand
    is_unverified_l24 = llm_chose_candidate and (not ev_passed) and llm_found_signal
    raw_is_absence    = (not llm_found_signal)
    is_gap_violation_pre_grep = (
        llm_chose_candidate and ev_passed and is_arbitrary_brand
    )

    # L36/L40 grep fallback — DISABLED for this anti-pattern typology.
    # Spec-Tailoring is detection-by-presence: LLM saying "no brand-
    # tailoring found" IS the definitive COMPLIANT outcome. Grep
    # keywords like "manufactured by" / "trademark" are too noisy in
    # non-spec contexts (e.g. "ready-mix concrete manufactured by
    # outside agencies shall not be allowed" is an anti-bidder-supplied
    # clause, not brand-tailoring). The LLM's full-context judgment
    # over the rerank candidates is the authoritative signal.
    #
    # Keywords are kept in GREP_FALLBACK_KEYWORDS for the audit-trail
    # `grep_fallback_audit` field (so a reviewer can see the keyword
    # vocabulary considered) but no automatic promotion happens.
    grep_hits: list[dict] = []
    full_grep_hits: list[dict] = []
    grep_promoted_to_unverified = False
    full_grep_promoted = False
    kg_coverage_gap = False
    if raw_is_absence:
        print(f"\n── L36 grep fallback DISABLED for anti-pattern typology ──")
        print(f"  (LLM's 'no signal' over rerank candidates is definitive)")

    # Note: for Spec-Tailoring, "absence" of a brand-tailoring SIGNAL
    # is COMPLIANT (no brand named = no violation). Treat raw_is_absence
    # AND grep-empty as silent-compliant (corpus uses functional/BIS
    # specs throughout — there's nothing to flag).
    is_absence       = raw_is_absence and not grep_promoted_to_unverified and not full_grep_promoted
    is_unverified    = is_unverified_l24 or grep_promoted_to_unverified or full_grep_promoted
    is_gap_violation = is_gap_violation_pre_grep

    if is_compliant_l24:
        if generic_approved:
            reason_label = "compliant_generic_approved_make_pattern"
        elif bis_iso:
            reason_label = "compliant_bis_iso_standard_reference"
        elif names_brand and (has_or_equiv or has_pac):
            reason_label = ("compliant_brand_named_with_or_equivalent"
                            if has_or_equiv else
                            "compliant_brand_named_with_pac_justification")
        else:
            reason_label = "compliant_no_brand_tailoring_detected"
    elif is_gap_violation_pre_grep:
        reason_label = "spec_brand_named_without_or_equivalent_or_pac"
    elif is_absence:
        # No brand-tailoring signal anywhere — COMPLIANT silent.
        reason_label = "compliant_no_brand_tailoring_signal"
    elif grep_promoted_to_unverified:
        reason_label = "spec_unverified_grep_fallback_retrieval_gap"
    elif full_grep_promoted:
        reason_label = ("spec_unverified_kg_coverage_gap"
                        if kg_coverage_gap
                        else "spec_unverified_whole_file_grep_only")
    elif is_unverified_l24:
        reason_label = "spec_unverified_llm_quote_failed_l24"
    else:
        reason_label = "spec_indeterminate"

    print(f"\n── Decision ──")
    print(f"  rule              : {rule['rule_id']} ({rule['severity']})")
    print(f"  llm_found_signal  : {llm_found_signal}")
    print(f"  ev_passed         : {ev_passed}  (score={ev_score}, method={ev_method})")
    print(f"  names_brand       : {names_brand}")
    print(f"  has_or_equiv      : {has_or_equiv}")
    print(f"  has_pac           : {has_pac}")
    print(f"  is_arbitrary_brand: {is_arbitrary_brand}")
    print(f"  is_compliant_l24  : {is_compliant_l24}")
    print(f"  is_gap_violation  : {is_gap_violation}")
    print(f"  is_unverified     : {is_unverified}")
    print(f"  is_absence        : {is_absence}")
    print(f"  reason_label      : {reason_label}")

    # COMPLIANT or absence-treated-as-compliant → silent
    if (is_compliant_l24 or is_absence) and not is_gap_violation_pre_grep:
        print(f"\n  → COMPLIANT — no row, no edge emitted")
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

    if is_gap_violation_pre_grep:
        evidence_out  = (f"{evidence}  [Brand-tailoring detected without "
                         f"'or equivalent' qualifier and without Proprietary "
                         f"Article Certificate justification — violates "
                         f"GFR-G-030]")
        ev_passed_out = ev_passed; ev_score_out = ev_score; ev_method_out = ev_method
    elif grep_promoted_to_unverified:
        ev_passed_out = None; ev_score_out = None; ev_method_out = "grep_fallback_retrieval_gap"
        evidence_out  = (f"LLM rerank top-{K} returned no spec signal; grep "
                         f"found keyword hits in {len(grep_hits)} section(s).")
    elif full_grep_promoted:
        ev_passed_out = None; ev_score_out = None
        ev_method_out = ("whole_file_grep_kg_coverage_gap"
                         if kg_coverage_gap else "whole_file_grep_match")
        evidence_out  = f"L40 whole-file grep — {len(full_grep_hits)} match line(s)"
    else:
        ev_passed_out = ev_passed; ev_score_out = ev_score; ev_method_out = ev_method
        evidence_out  = evidence

    rule_node_id = get_or_create_rule_node(DOC_ID, rule["rule_id"])

    if is_gap_violation_pre_grep:
        label = (
            f"{TYPOLOGY}: Specific brand/manufacturer named in spec without "
            f"'or equivalent' qualifier and without PAC justification — "
            f"{rule['rule_id']} ({rule['severity']}) prohibits brand-tailoring"
        )
    elif grep_promoted_to_unverified:
        label = (
            f"{TYPOLOGY}: UNVERIFIED (L36 grep-fallback) — retrieval missed "
            f"{len(grep_hits)} section(s) with brand keyword hits"
        )
    elif full_grep_promoted:
        label = (
            f"{TYPOLOGY}: UNVERIFIED (L40 whole-file fallback) — "
            f"{'KG-coverage gap' if kg_coverage_gap else 'whole-file only'}"
        )
    else:
        label = (
            f"{TYPOLOGY}: UNVERIFIED — LLM found brand signal but quote failed L24"
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
        }

    finding_props = {
        "rule_id":               rule["rule_id"],
        "typology_code":         TYPOLOGY,
        "severity":              rule["severity"],
        "evidence":              evidence_out,
        "extraction_path":       "presence_anti_pattern",
        "llm_found_signal":      llm_found_signal,
        "spec_names_specific_brand_or_manufacturer": names_brand,
        "spec_has_or_equivalent_qualifier":          has_or_equiv,
        "spec_uses_generic_approved_make":           generic_approved,
        "spec_uses_bis_or_iso_standard":             bis_iso,
        "spec_has_pac_justification":                has_pac,
        "is_arbitrary_brand":    is_arbitrary_brand,
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
        "grep_fallback_audit":         grep_audit,
        "defeated":            False,
    }

    finding = rest_post("kg_nodes", [{
        "doc_id":    DOC_ID,
        "node_type": "ValidationFinding",
        "label":     label,
        "properties": finding_props,
        "source_ref": f"tier1:spec_tailoring_check:{rule['rule_id']}",
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
                "extraction_path":      "presence_anti_pattern",
                "spec_names_specific_brand_or_manufacturer": names_brand,
                "spec_has_or_equivalent_qualifier":          has_or_equiv,
                "spec_has_pac_justification":                has_pac,
                "is_arbitrary_brand":   is_arbitrary_brand,
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
        print(f"  → VIOLATES_RULE     {edge['edge_id']}")

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
