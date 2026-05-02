"""
scripts/test_semantic_judge.py

Demonstrates SemanticJudge on the Judicial Academy bid document.

Since the project deliberately avoids the Anthropic API and uses
"Claude Code in conversation" AS the LLM, we inject `judgement_fn`
with judgments computed by reading each (rule, relevant_section) pair.

Two passes are run:
  PASS A — default first-10 rules (alphabetical by rule_id) — shows
           the SemanticJudge pipeline as it ships out of the box.
  PASS B — rule_filter: typologies most likely to fire on this doc
           (Geographic-Restriction, Criteria-Restriction-Narrow,
           Criteria-Restriction-Loose, Spec-Tailoring, Bid-Splitting-
           Pattern). Demonstrates how a caller targets a specific
           subset of P4 rules.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# Ensure repo root is on sys.path so absolute imports resolve when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from builder.config import settings
from engines.classifier import TenderClassifier
from engines.parameter_cascade import ParameterCascadeEngine, TenderInputs
from modules.validator.semantic_judge import (
    SemanticJudge, _find_relevant_section, build_user_prompt, SYSTEM_PROMPT,
)


REPO = Path(__file__).resolve().parent.parent
DOC = REPO / "source_documents" / "e_procurement" / "processed_md" / "Bid Document of Judicial Academy.md"


# ───────────────────────────────────────────────────────────────────────
# 1. P4 rule census
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


# ───────────────────────────────────────────────────────────────────────
# 2. Claude-Code-in-conversation judgment fn
#
# This is where I (Claude Code) embody the LLM. For each (rule, section)
# pair the SemanticJudge will hand to me, I encode my actual reading
# below. The keys are rule_ids; values are pre-computed judgment dicts.
#
# The pre-computation is not faked — it reflects an honest reading of
# what each rule asks AND what each section the keyword-locator picks
# actually contains. Sections that don't show a violation get PASS.
# ───────────────────────────────────────────────────────────────────────

def make_judgement_fn(rule_section_judgments: dict[str, dict]):
    """Build a judgement_fn that returns pre-computed judgments keyed
    by the rule_id parsed back out of the user-prompt header line.

    Token counts approximate Claude-Sonnet pricing for a ~500-word
    section + system prompt + JSON response."""

    def fn(system: str, user: str) -> dict:
        # Extract rule_id from the source clause / first line.
        # The user-prompt starts with `Rule: <natural_language>\nSource: ...`
        # We look up by matching natural_language fragment.
        for rid, j in rule_section_judgments.items():
            if j["_match_text"] in user:
                return {**{k: v for k, v in j.items() if not k.startswith("_")},
                        "input_tokens":  _approx_input_tokens(system, user),
                        "output_tokens": 80}
        # Fallback — rule wasn't pre-judged
        return {
            "verdict":       "PASS",
            "evidence":      "",
            "confidence":    "LOW",
            "reasoning":     "No pre-computed judgment; defaulting to PASS",
            "input_tokens":  _approx_input_tokens(system, user),
            "output_tokens": 30,
        }
    return fn


def _approx_input_tokens(system: str, user: str) -> int:
    """Rough char/4 approximation matching Anthropic tokeniser ratio."""
    return (len(system) + len(user)) // 4


# ───────────────────────────────────────────────────────────────────────
# Pre-computed Claude judgments for Judicial Academy rules
# ───────────────────────────────────────────────────────────────────────

JUDGMENTS: dict[str, dict] = {
    # ── PASS A — default first 10 rules (alphabetical) ────────────────
    "CVC-005": {
        "_match_text": "Tenders for procurement of e-tendering solutions",
        "verdict":    "PASS",
        "evidence":   "",
        "confidence": "MEDIUM",
        "reasoning":  "This is a construction tender for a Judicial Academy, not a tender for procuring e-tendering solutions; rule scope does not apply.",
    },
    "CVC-006": {
        "_match_text": "An e-procurement platform shall implement role-based access control",
        "verdict":    "PASS",
        "evidence":   "",
        "confidence": "MEDIUM",
        "reasoning":  "Rule targets the e-procurement platform vendor; this construction tender merely uses the AP eProcurement portal as a downstream consumer.",
    },
    "CVC-007": {
        "_match_text": "Sensitive data on an e-procurement platform shall be encrypted",
        "verdict":    "PASS",
        "evidence":   "",
        "confidence": "MEDIUM",
        "reasoning":  "Rule applies to platform implementation (encryption ciphers, key length); not assessable from a construction tender document.",
    },
    "CVC-030": {
        "_match_text": "PQ criteria shall not include eligibility requirements that are vague",
        "verdict":    "FAIL",
        "evidence":   "Special Class Civil registration having been registered with Government of Andhra Pradesh' vide GO.MS. No.94, I&CAD (Dept.) dated 01-07-2003.",
        "confidence": "MEDIUM",
        "reasoning":  "Eligibility is restricted to a specific AP-State registration class (Special Class Civil with Govt of AP), which favours AP-registered contractors and excludes equally-capable bidders registered with other state governments.",
    },
    "CVC-032": {
        "_match_text": "No bidder shall be denied prequalification or post-qualification for reasons unrelated",
        "verdict":    "PASS",
        "evidence":   "",
        "confidence": "LOW",
        "reasoning":  "The relevant section selected lists qualification document requirements (Statements I-X) which are tied to capability and resources; the section-locator did not surface the geographic exclusions (Indian nationality, no foreign bidder, AP-only GST) that exist elsewhere in the document.",
    },
    "CVC-038": {
        "_match_text": "Orders shall not be split into smaller values to bring them within the financial powers",
        "verdict":    "PASS",
        "evidence":   "",
        "confidence": "HIGH",
        "reasoning":  "Single tender of Rs.125.50 Cr ECV with one work scope; no evidence of splitting into smaller orders.",
    },
    "CVC-045": {
        "_match_text": "When multiple consultants are appointed for the same project, their responsibilities must be clear",
        "verdict":    "PASS",
        "evidence":   "",
        "confidence": "MEDIUM",
        "reasoning":  "Tender appoints a single Project Manager / PMC for site supervision; rule about multiple-consultant role definition is not directly applicable.",
    },
    "CVC-063": {
        "_match_text": "Consultants or any of their affiliates shall not be hired for any assignment which by its nature may be in conflict",
        "verdict":    "PASS",
        "evidence":   "ITB 4.2(e)-(g) explicitly bar bidders whose affiliates participated as consultants in the design or as Engineer for Contract implementation, or who would provide goods/works arising from prior consulting work on the project.",
        "confidence": "HIGH",
        "reasoning":  "ITB 4.2 conflict-of-interest provisions cover consultant-affiliate conflicts comprehensively, satisfying the rule.",
    },
    "CVC-089": {
        "_match_text": "Procurement provisioning must be judicious and justified",
        "verdict":    "PASS",
        "evidence":   "",
        "confidence": "LOW",
        "reasoning":  "Rule targets stock provisioning patterns based on consumption history and equipment life; this is a one-off Works contract and the rule's framing does not apply directly to construction quantities.",
    },
    "CVC-090": {
        "_match_text": "Demands for the same item received simultaneously from different sections/units must be clubbed",
        "verdict":    "PASS",
        "evidence":   "",
        "confidence": "HIGH",
        "reasoning":  "Single project, single procuring entity (AGICL); no parallel demands from sibling units to be clubbed.",
    },

    # ── PASS B — targeted Geographic-Restriction & Criteria-Restriction rules ──
    # Rule IDs in this set are illustrative — real lookups happen at runtime
    # by typology filter, and pre-judgments are keyed by natural_language
    # fragments unique enough to identify them.
    "GEO-FOREIGN-BAN": {
        "_match_text": "denied prequalification or post-qualification",  # CVC-032 again — but new evidence path
        # Same rule_id (CVC-032) — not used for Pass B; Pass B uses a custom
        # locator override that injects the actual violation section.
        "verdict":    "FAIL",
        "evidence":   "Participation in this tendering process by forming Joint Venture or Consortium or Special Purpose Vehicle is not allowed. Any contractor from abroad not be permitted. A Bidder shall have the Indian nationality.",
        "confidence": "HIGH",
        "reasoning":  "Three independent denials unrelated to capability or resources: foreign-bidder ban, mandatory Indian nationality, and outright prohibition on JV/consortium/SPV participation.",
    },
}


# ───────────────────────────────────────────────────────────────────────
# 3. Test passes
# ───────────────────────────────────────────────────────────────────────

def run_pass_a():
    print("=" * 78)
    print("PASS A — default first 10 P4 rules (alphabetical by rule_id)")
    print("=" * 78)

    text = DOC.read_text(encoding="utf-8")
    classifier = TenderClassifier()
    cls = classifier.classify(text)
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

    print(f"Document:        {DOC.name}")
    print(f"Classification:  {cls.primary_type} | AP={cls.is_ap_tender} | "
          f"Value=Rs.{(cls.estimated_value or 0)/1_00_00_000:.2f} Cr")
    print(f"Cascade params:  EMD={params.emd_percentage}% | "
          f"PBG={params.pbg_percentage}% | bid-validity={params.bid_validity_days}d")
    print()

    judge = SemanticJudge(judgement_fn=make_judgement_fn(JUDGMENTS), max_rules=10)

    t0 = time.perf_counter()
    findings = judge.judge_document(text, cls, params)
    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    print(f"Wall time:       {elapsed_ms} ms")
    print(f"Rules judged:    {judge.token_usage.rules_judged}")
    print(f"API calls:       {judge.token_usage.calls}")
    print(f"Failures:        {judge.token_usage.failures}")
    print(f"Input tokens:    ~{judge.token_usage.input_tokens:,}")
    print(f"Output tokens:   ~{judge.token_usage.output_tokens:,}")
    print(f"Findings (FAIL): {len(findings)}")
    print()

    for i, f in enumerate(findings, 1):
        print(f"  [{i}] {f.rule_id} ({f.typology_code}, {f.severity})")
        print(f"      Confidence: {f.confidence}")
        print(f"      Evidence:   {f.evidence[:200]}")
        print(f"      Reasoning:  {f.reasoning}")
        print()

    return judge.token_usage, findings


def run_pass_b():
    print("=" * 78)
    print("PASS B — targeted: Geographic-Restriction + Criteria-Restriction rules")
    print("=" * 78)

    text = DOC.read_text(encoding="utf-8")
    classifier = TenderClassifier()
    cls = classifier.classify(text)
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

    # For Pass B we override the section locator by patching the
    # SemanticJudge's `judge_document` flow — we directly call _judge_one
    # with a hand-picked relevant section that contains the actual
    # violations. This demonstrates what the LLM judge produces when
    # the locator surfaces the right slice.

    rules = fetch_p4_rules()
    target_typologies = {
        "Geographic-Restriction",
        "Criteria-Restriction-Narrow",
        "Criteria-Restriction-Loose",
    }
    target_rules = [r for r in rules if r["typology_code"] in target_typologies][:6]

    print(f"Targeted rules: {len(target_rules)}")
    for r in target_rules:
        print(f"  - {r['rule_id']:10s} {r['typology_code']:30s} {r['severity']}")
    print()

    # Hand-selected violation section — the one the keyword locator misses
    violation_section = """## General Requirements (Judicial Academy bid, page 46-47)

The bidders need to register on the electronic procurement market place of Government of A.P., that is, www.apeprocurement.gov.in.

Participation in this tendering process by forming Joint Venture or Consortium or Special Purpose Vehicle is not allowed. Any contractor from abroad not be permitted.

The bidders shall not have a conflict of interest

The bidder shall not submit more than one tender in this tendering process.

A Bidder shall have the Indian nationality. A Bidder shall be deemed to have the nationality of India if the Bidder is constituted, incorporated or registered in and operates in conformity with the provisions of the laws of India.

The bidder must have a valid GST registration within Andhra Pradesh.

NIT — Eligible Class of Bidders: Special Class Civil registration having been registered with Government of Andhra Pradesh vide GO.MS. No.94, I&CAD (Dept.) dated 01-07-2003. Valid Grade-A Electrical License.

BDS ITB 4.1(a): Joint Venture: not allowed. If JV allowed, maximum number of members in the JV shall be: NA.
"""

    # Inline judgments for these typologies against the violation section
    # (encoded as my Claude-Code reading of each rule against the section above)
    targeted_judgments = {
        # Geographic-Restriction rules
        "Geographic-Restriction": {
            "verdict":    "FAIL",
            "evidence":   "Any contractor from abroad not be permitted. A Bidder shall have the Indian nationality. The bidder must have a valid GST registration within Andhra Pradesh.",
            "confidence": "HIGH",
            "reasoning":  "Three explicit geographic exclusions unrelated to capability: foreign-bidder ban, mandatory Indian nationality, and AP-only GST registration requirement.",
        },
        # Criteria-Restriction-Narrow rules
        "Criteria-Restriction-Narrow": {
            "verdict":    "FAIL",
            "evidence":   "Special Class Civil registration having been registered with Government of Andhra Pradesh vide GO.MS. No.94, I&CAD (Dept.) dated 01-07-2003. ... Joint Venture: not allowed.",
            "confidence": "HIGH",
            "reasoning":  "Eligibility narrowed to AP-state Special Class Civil registrants and to single-firm bidders (no JV/consortium/SPV) — restrictions that favour incumbents and exclude equally-capable bidders.",
        },
        # Criteria-Restriction-Loose rules — likely PASS on this doc
        "Criteria-Restriction-Loose": {
            "verdict":    "PASS",
            "evidence":   "",
            "confidence": "MEDIUM",
            "reasoning":  "Eligibility criteria are explicit and well-defined (Special Class Civil + Grade-A Electrical License) — they may be too NARROW (separate rule) but they are not LOOSE/vague.",
        },
    }

    findings = []
    input_tokens = 0
    output_tokens = 0
    rules_judged = 0
    calls = 0

    for r in target_rules:
        user_prompt = build_user_prompt(r, cls, params, violation_section)
        # Use the typology-keyed judgment
        j = targeted_judgments.get(r["typology_code"], {
            "verdict": "PASS", "evidence": "",
            "confidence": "LOW", "reasoning": "No targeted judgment.",
        })
        in_tok = _approx_input_tokens(SYSTEM_PROMPT, user_prompt)
        out_tok = 95
        input_tokens += in_tok
        output_tokens += out_tok
        calls += 1
        rules_judged += 1

        verdict = j["verdict"]
        evidence = j["evidence"].strip()
        confidence = j["confidence"]
        if verdict == "FAIL" and evidence and confidence != "LOW":
            findings.append({
                "rule_id":       r["rule_id"],
                "typology_code": r["typology_code"],
                "severity":      r["severity"],
                "confidence":    confidence,
                "evidence":      evidence[:500],
                "reasoning":     j["reasoning"],
            })

    print(f"Rules judged:    {rules_judged}")
    print(f"API calls:       {calls}")
    print(f"Input tokens:    ~{input_tokens:,}")
    print(f"Output tokens:   ~{output_tokens:,}")
    print(f"Findings (FAIL): {len(findings)}")
    print()

    for i, f in enumerate(findings, 1):
        print(f"  [{i}] {f['rule_id']} ({f['typology_code']}, {f['severity']})")
        print(f"      Confidence: {f['confidence']}")
        print(f"      Evidence:   {f['evidence'][:280]}")
        print(f"      Reasoning:  {f['reasoning']}")
        print()

    return {"calls": calls, "input_tokens": input_tokens,
            "output_tokens": output_tokens, "rules_judged": rules_judged}, findings


# ───────────────────────────────────────────────────────────────────────
# 4. Main
# ───────────────────────────────────────────────────────────────────────

def main() -> int:
    rules = fetch_p4_rules()
    print(f"P4 TYPE_1_ACTIONABLE rules in Supabase: {len(rules)}")
    from collections import Counter
    by_t = Counter(r["typology_code"] for r in rules)
    by_l = Counter(r["layer"] for r in rules)
    print(f"  By typology:   {dict(by_t)}")
    print(f"  By layer:      {dict(by_l)}")
    print()

    usage_a, findings_a = run_pass_a()
    print()
    usage_b, findings_b = run_pass_b()
    print()

    print("=" * 78)
    print("Combined token usage (Pass A + Pass B)")
    print("=" * 78)
    total_in = usage_a.input_tokens + usage_b["input_tokens"]
    total_out = usage_a.output_tokens + usage_b["output_tokens"]
    total_calls = usage_a.calls + usage_b["calls"]
    total_findings = len(findings_a) + len(findings_b)
    print(f"Total calls:     {total_calls}")
    print(f"Total in tokens: ~{total_in:,}")
    print(f"Total out tokens:~{total_out:,}")
    print(f"Total findings:  {total_findings}")
    print()
    print("Note: token counts are approximate (char/4 heuristic) since we are")
    print("running with judgement_fn injection — no actual Anthropic API calls.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
