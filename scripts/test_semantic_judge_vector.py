"""
scripts/test_semantic_judge_vector.py

Final integration test — SemanticJudge with vector-based section retrieval.

Compares two retrieval modes on the same first-10 P4 rules against the
Judicial Academy bid document:

    KEYWORD MODE  — _find_relevant_section() (heading-keyword overlap)
    VECTOR MODE   — _find_relevant_section_via_vector() (BGE-M3 + Qdrant)

Both modes use the same Claude-Code-in-conversation judgement_fn, so any
difference in findings is purely a function of WHICH section the LLM saw.

The known violations in this document are:
  1. CVC-030  Criteria-Restriction-Narrow  — "Special Class Civil registration with Govt of AP"
  2. CVC-032  Geographic-Restriction       — "Indian nationality" / "no foreign bidder" / "AP-only GST"
  3. (Same evidence stems support both above — these are the doc's two clear P4 issues)

Pass A is supposed to surface CVC-030 and CVC-032 if the section
retrieval works.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from builder.config import settings
from engines.classifier import TenderClassifier
from engines.parameter_cascade import ParameterCascadeEngine, TenderInputs
from modules.validator.semantic_judge import (
    SemanticJudge, SYSTEM_PROMPT, build_user_prompt,
    _find_relevant_section,
)
from modules.validator.vector_checker import VectorChecker


REPO = Path(__file__).resolve().parent.parent
DOC  = REPO / "source_documents" / "e_procurement" / "processed_md" / "Bid Document of Judicial Academy.md"


# ───────────────────────────────────────────────────────────────────────
# Claude-Code-in-conversation judgement_fn
#
# The fn looks at the (rule_id, relevant_section) pair given in the
# user prompt and returns a verdict. It DOES NOT have privileged access
# to violations elsewhere in the doc — it can only judge from what's in
# the user prompt's "Document excerpt" block. This is the same contract
# the real Anthropic API would have.
#
# We make verdicts data-driven: a small set of "violation indicator"
# regexes for each P4 typology. If the indicator appears in the section
# the LLM was shown, we FAIL with HIGH confidence; otherwise PASS.
# ───────────────────────────────────────────────────────────────────────

import re

# Per-typology: (regex → (verdict_if_match, evidence_extractor))
# Multiple indicators per typology — each tested independently.
TYPOLOGY_INDICATORS: dict[str, list[tuple[re.Pattern, str]]] = {
    "Criteria-Restriction-Narrow": [
        (re.compile(r"Special\s+Class\s+Civil\s+registration[^.]{0,160}", re.I),
         "AP-State Special Class Civil registration is mandated — narrows eligibility to one state's contractor pool."),
        (re.compile(r"Joint\s+Venture[^.]{0,40}not\s+allowed|JV[^.]{0,20}not\s+allowed", re.I),
         "JV/consortium/SPV explicitly prohibited — narrows eligibility to single firms only."),
        (re.compile(r"Participation[^.]{0,60}Joint\s+Venture[^.]{0,60}not\s+allowed[^.]{0,160}", re.I),
         "JV/consortium/SPV explicitly prohibited."),
    ],
    "Geographic-Restriction": [
        (re.compile(r"Indian\s+nationality[^.]{0,200}", re.I),
         "Mandatory Indian nationality requirement — denies prequalification on grounds unrelated to capability."),
        (re.compile(r"contractor\s+from\s+abroad[^.]{0,80}not\s+be\s+permitted", re.I),
         "Foreign contractors not permitted — geographic exclusion unrelated to capability."),
        (re.compile(r"GST\s+registration\s+within\s+Andhra\s+Pradesh", re.I),
         "AP-only GST registration required — excludes capable bidders registered elsewhere in India."),
    ],
    "Bid-Splitting-Pattern": [
        (re.compile(r"split[^.]{0,80}smaller\s+values", re.I),
         "Order splitting language present."),
    ],
    "COI-PMC-Works": [
        # The doc's ITB 4.2 ADDRESSES this rule (PASS-supporting), so no FAIL
        # indicators here. Default → PASS.
    ],
    "Available-Bid-Capacity-Error": [
        # Rule scope is procurement provisioning — N/A for a Works tender.
    ],
    "Missing-Mandatory-Field": [
        # The first three CVC-005/006/007 rules are about e-tendering platforms
        # (security, encryption, RBAC) — not applicable to a construction tender.
    ],
    "Cover-Bidding-Signal": [],
    "Spec-Tailoring": [],
    "Criteria-Restriction-Loose": [],
}


def _approx_input_tokens(system: str, user: str) -> int:
    return (len(system) + len(user)) // 4


def make_judgement_fn(typology_indicators: dict):
    """A judgement_fn that simulates Claude reading a (rule, section)
    pair and looking for typology-specific violation evidence."""

    def fn(system: str, user: str) -> dict:
        # Parse the rule's Typology line out of the prompt
        m = re.search(r"^Typology:\s*(.+?)\s*$", user, re.M)
        typology = m.group(1).strip() if m else ""

        # Extract the document excerpt block
        m = re.search(r'Document excerpt[^"]*?"""\s*\n(.*?)\n"""', user, re.S)
        excerpt = m.group(1) if m else user

        in_tok  = _approx_input_tokens(system, user)
        out_tok = 95

        # Test indicators for this typology
        for pat, reasoning in typology_indicators.get(typology, []):
            mm = pat.search(excerpt)
            if mm:
                ev = mm.group(0).strip()
                # Trim verbose evidence to <50 words per spec
                ev_words = ev.split()
                if len(ev_words) > 45:
                    ev = " ".join(ev_words[:45])
                return {
                    "verdict":       "FAIL",
                    "evidence":      ev,
                    "confidence":    "HIGH",
                    "reasoning":     reasoning,
                    "input_tokens":  in_tok,
                    "output_tokens": out_tok,
                }

        # No indicator matched in the section the LLM was shown → PASS
        return {
            "verdict":       "PASS",
            "evidence":      "",
            "confidence":    "MEDIUM",
            "reasoning":     "No evidence of this violation in the section provided.",
            "input_tokens":  in_tok,
            "output_tokens": 60,
        }

    return fn


# ───────────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────────

def fetch_p4_rules() -> list[dict]:
    url = f"{settings.supabase_rest_url}/rest/v1/rules"
    params = {
        "select": "rule_id,natural_language,verification_method,typology_code,"
                  "severity,layer,source_clause,pattern_type,rule_type",
        "pattern_type": "eq.P4",
        "rule_type":    "eq.TYPE_1_ACTIONABLE",
        "order":        "rule_id.asc",
    }
    headers = {
        "apikey":        settings.supabase_anon_key,
        "Authorization": f"Bearer {settings.supabase_anon_key}",
        "Range-Unit":    "items",
        "Range":         "0-999",
    }
    r = requests.get(url, params=params, headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()


def setup_context():
    text = DOC.read_text(encoding="utf-8")
    cls  = TenderClassifier().classify(text)
    cascade = ParameterCascadeEngine()
    params = cascade.compute(TenderInputs(
        department="AGICL",
        tender_type=cls.primary_type,
        estimated_value=cls.estimated_value or 1_25_50_00_000,
        duration_months=cls.duration_months or 18,
        procurement_method="Open",
        is_ap_tender=cls.is_ap_tender,
        funding_source=cls.funding_source,
    ))
    return text, cls, params


# ───────────────────────────────────────────────────────────────────────
# Pass A — keyword vs vector
# ───────────────────────────────────────────────────────────────────────

def run_keyword_pass(rules: list[dict], text: str, cls, params, judge_fn):
    judge = SemanticJudge(judgement_fn=judge_fn, max_rules=10)
    t0 = time.perf_counter()
    findings = judge.judge_document(text, cls, params)
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    return judge, findings, elapsed_ms


def run_vector_pass(rules: list[dict], text: str, cls, params, judge_fn,
                    vec: VectorChecker, doc_id: str):
    judge = SemanticJudge(judgement_fn=judge_fn, max_rules=10, vector_checker=vec)
    t0 = time.perf_counter()
    findings = judge.judge_document(text, cls, params, pre_indexed_doc_id=doc_id)
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    return judge, findings, elapsed_ms


# ───────────────────────────────────────────────────────────────────────
# Diagnostics — show which sections each retrieval mode picks
# ───────────────────────────────────────────────────────────────────────

def diagnose_retrieval(rules_first10: list[dict], text: str, vec: VectorChecker, doc_id: str):
    """For each of the 10 rules, print the heading the keyword locator
    picked and the headings the vector locator picked. Helps explain WHY
    findings differ between modes."""
    judge = SemanticJudge(judgement_fn=lambda s, u: {
        "verdict": "PASS", "evidence": "", "confidence": "LOW",
        "reasoning": "diag", "input_tokens": 0, "output_tokens": 0,
    }, vector_checker=vec)

    rows = []
    for r in rules_first10:
        # Keyword
        kw_section = _find_relevant_section(text, r["natural_language"])
        kw_heading = kw_section.split("\n", 1)[0][:60]
        # Vector top-3
        vec_section = judge._find_relevant_section_via_vector(
            r, doc_id, document_text=text, top_k=3,
        )
        vec_headings = []
        for chunk in vec_section.split("\n\n---\n\n"):
            head = chunk.split("\n", 1)[0]
            vec_headings.append(head[:55])
        rows.append((r["rule_id"], r["typology_code"], kw_heading, vec_headings))
    return rows


# ───────────────────────────────────────────────────────────────────────
# Main
# ───────────────────────────────────────────────────────────────────────

def main() -> int:
    print("=" * 78)
    print("SemanticJudge — keyword vs vector section retrieval")
    print("Document: Bid Document of Judicial Academy.md")
    print("=" * 78)
    print()

    text, cls, params = setup_context()
    print(f"Classification:  {cls.primary_type} | AP={cls.is_ap_tender} | "
          f"Value=Rs.{(cls.estimated_value or 0)/1_00_00_000:.2f} Cr")
    print(f"Cascade params:  EMD={params.emd_percentage}% | "
          f"PBG={params.pbg_percentage}% | bid-validity={params.bid_validity_days}d")
    print()

    rules_all = fetch_p4_rules()
    rules_first10 = rules_all[:10]
    print(f"Total P4 TYPE_1_ACTIONABLE rules in Supabase: {len(rules_all)}")
    print(f"Pass A scope (first 10 alphabetically):")
    for r in rules_first10:
        print(f"  - {r['rule_id']:10s} {r['typology_code']:32s} {r['severity']}")
    print()

    # ── Step 1: index document via VectorChecker (cache hit if rerun)
    print("Step 1: VectorChecker.check_document() — index doc into Qdrant")
    print("-" * 78)
    t0 = time.perf_counter()
    vec = VectorChecker()
    init_ms = int((time.perf_counter() - t0) * 1000)
    t0 = time.perf_counter()
    vec_out = vec.check_document(
        document_text=text,
        source_file=DOC.name,
        is_ap_tender=cls.is_ap_tender,
        estimated_value=cls.estimated_value or 1_25_50_00_000,
        duration_months=cls.duration_months or 18,
    )
    index_ms = int((time.perf_counter() - t0) * 1000)
    doc_id = vec_out["doc_id"]
    cache_hit = vec_out["timing_ms"].get("cache_hit", False)
    print(f"VectorChecker init:    {init_ms} ms  (BGE-M3 load)")
    print(f"check_document():      {index_ms} ms  (cache_hit={cache_hit})")
    print(f"doc_id:                {doc_id}")
    print(f"Sections indexed:      {len(vec_out['sections'])}")
    print()

    # ── Step 2: diagnostic — show what each retrieval mode picks
    print("Step 2: Section-retrieval diagnosis (which heading does each mode pick?)")
    print("-" * 78)
    diag = diagnose_retrieval(rules_first10, text, vec, doc_id)
    for rid, typ, kw_head, vec_heads in diag:
        print(f"\n  [{rid}] {typ}")
        print(f"    keyword    → {kw_head}")
        for i, vh in enumerate(vec_heads, 1):
            print(f"    vector#{i}   → {vh}")
    print()

    # ── Step 3: judgement_fn run — keyword mode
    print("Step 3: KEYWORD-mode Pass A (10 rules)")
    print("-" * 78)
    judge_fn = make_judgement_fn(TYPOLOGY_INDICATORS)
    j_kw, fdg_kw, ms_kw = run_keyword_pass(rules_first10, text, cls, params, judge_fn)
    print(f"Wall time:       {ms_kw} ms")
    print(f"Rules judged:    {j_kw.token_usage.rules_judged}")
    print(f"Input tokens:    ~{j_kw.token_usage.input_tokens:,}")
    print(f"Output tokens:   ~{j_kw.token_usage.output_tokens:,}")
    print(f"Findings (FAIL): {len(fdg_kw)}")
    for i, f in enumerate(fdg_kw, 1):
        print(f"  [{i}] {f.rule_id} ({f.typology_code}, {f.severity}, {f.confidence})")
        print(f"      Evidence: {f.evidence[:160]}")
    print()

    # ── Step 4: judgement_fn run — vector mode
    print("Step 4: VECTOR-mode Pass A (10 rules) — same prompts, better sections")
    print("-" * 78)
    j_vec, fdg_vec, ms_vec = run_vector_pass(rules_first10, text, cls, params,
                                              judge_fn, vec, doc_id)
    print(f"Wall time:       {ms_vec} ms")
    print(f"Rules judged:    {j_vec.token_usage.rules_judged}")
    print(f"Input tokens:    ~{j_vec.token_usage.input_tokens:,}")
    print(f"Output tokens:   ~{j_vec.token_usage.output_tokens:,}")
    print(f"Findings (FAIL): {len(fdg_vec)}")
    for i, f in enumerate(fdg_vec, 1):
        print(f"  [{i}] {f.rule_id} ({f.typology_code}, {f.severity}, {f.confidence})")
        print(f"      Evidence: {f.evidence[:200]}")
    print()

    # ── Step 5: comparison ────────────────────────────────────────────
    print("=" * 78)
    print("COMPARISON")
    print("=" * 78)
    kw_rules  = {f.rule_id for f in fdg_kw}
    vec_rules = {f.rule_id for f in fdg_vec}
    print(f"  keyword findings:  {sorted(kw_rules) or '∅'}")
    print(f"  vector findings:   {sorted(vec_rules) or '∅'}")
    print(f"  unique to vector:  {sorted(vec_rules - kw_rules) or '∅'}")
    print(f"  unique to keyword: {sorted(kw_rules - vec_rules) or '∅'}")
    print()
    print(f"  keyword tokens (in/out): ~{j_kw.token_usage.input_tokens:,} / "
          f"~{j_kw.token_usage.output_tokens:,}")
    print(f"  vector tokens  (in/out): ~{j_vec.token_usage.input_tokens:,} / "
          f"~{j_vec.token_usage.output_tokens:,}")
    delta_in = j_vec.token_usage.input_tokens - j_kw.token_usage.input_tokens
    pct = (100.0 * delta_in / j_kw.token_usage.input_tokens) if j_kw.token_usage.input_tokens else 0
    print(f"  Δ input tokens:          {delta_in:+,}  ({pct:+.1f}%)")
    print()

    # ── Step 6: assertion against known violations ────────────────────
    expected_rules = {
        "CVC-030",   # Criteria-Restriction-Narrow → AP-only registration / no JV
        "CVC-032",   # Geographic-Restriction → Indian nationality / foreign ban / AP GST
    }
    found_expected_kw  = expected_rules & kw_rules
    found_expected_vec = expected_rules & vec_rules
    print(f"  Known violations expected in Pass A: {sorted(expected_rules)}")
    print(f"    keyword caught: {sorted(found_expected_kw) or '∅'}  "
          f"({len(found_expected_kw)}/{len(expected_rules)})")
    print(f"    vector caught:  {sorted(found_expected_vec) or '∅'}  "
          f"({len(found_expected_vec)}/{len(expected_rules)})")
    print()

    # ── Save artefact for downstream review
    out_dir = REPO / "data" / "validation_tests" / "semantic_judge"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "judicial_academy_vector_vs_keyword.json"
    out_path.write_text(json.dumps({
        "document":   DOC.name,
        "doc_id":     doc_id,
        "rules_pass_a": [r["rule_id"] for r in rules_first10],
        "keyword_findings": [f.model_dump() for f in fdg_kw],
        "vector_findings":  [f.model_dump() for f in fdg_vec],
        "keyword_tokens":   {"in": j_kw.token_usage.input_tokens,
                              "out": j_kw.token_usage.output_tokens,
                              "calls": j_kw.token_usage.calls},
        "vector_tokens":    {"in": j_vec.token_usage.input_tokens,
                              "out": j_vec.token_usage.output_tokens,
                              "calls": j_vec.token_usage.calls},
        "expected_rules":          sorted(expected_rules),
        "expected_caught_keyword": sorted(found_expected_kw),
        "expected_caught_vector":  sorted(found_expected_vec),
        "diagnostics": [
            {"rule_id": rid, "typology": typ,
             "keyword_heading": kw, "vector_top3_headings": vh}
            for rid, typ, kw, vh in diag
        ],
    }, indent=2, default=str))
    print(f"  Artefact written: {out_path.relative_to(REPO)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
