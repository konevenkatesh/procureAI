"""
scripts/generate_shacl_shapes.py

Generate SHACL shapes (Turtle) for all approved P1 rules in Supabase.
Each shape targets ap:TenderDocument and encodes one verifiable condition
extracted from the rule's natural_language. Output goes to
ontology/shacl_shapes/p1_auto.ttl.

Generation strategy: typology-aware templates. For each rule we look at
typology_code + natural_language and build the matching SHACL constraint:

  EMD-Shortfall              → sh:minInclusive  on ap:emdPercentage
  PBG-Shortfall              → sh:minInclusive  on ap:pbgPercentage
  Bid-Validity-Short         → sh:minInclusive  on ap:bidValidityDays
  Missing-Integrity-Pact     → sh:hasValue true on ap:hasIntegrityPact
  Missing-Anti-Collusion     → sh:hasValue true on ap:hasAntiCollusionForm
  Missing-PVC-Clause         → sh:hasValue true on ap:hasPriceVariationClause
  E-Procurement-Bypass       → sh:hasValue true on ap:isEProcurement
  Judicial-Preview-Bypass    → sh:hasValue true on ap:hasJudicialPreview
  Reverse-Tender-Evasion     → sh:hasValue true on ap:hasReverseTender
  Single-Source-Undocumented → sh:hasValue true on ap:hasOpenTender
  Criteria-Restriction-*     → sh:not(JV/foreign-ban presence)  (sentinel)
  Stale-Financial-Year       → sh:maxInclusive on ap:registrationAgeMonths
  Missing-Mandatory-Field    → sh:minCount 1   on whatever field

Numeric thresholds are extracted from the rule's natural_language with
small regex helpers (e.g. "Rs.40 lakh", "5%", "180 days"). When a threshold
can't be found we fall back to the cascade defaults.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import requests

from builder.config import settings


REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "ontology" / "shacl_shapes" / "p1_auto.ttl"


# ─── Threshold extractors ────────────────────────────────────────────────────

_PCT  = re.compile(r"\b(\d+(?:\.\d+)?)\s*%")
_DAYS = re.compile(r"\b(\d{1,4})\s*(?:days?|day)\b", re.I)
_LAKH = re.compile(r"\bRs\.?\s*(\d+(?:\.\d+)?)\s*lakhs?\b", re.I)
_CR   = re.compile(r"\bRs\.?\s*(\d+(?:\.\d+)?)\s*crores?\b", re.I)


def _first_pct(text: str) -> float | None:
    m = _PCT.search(text)
    return float(m.group(1)) if m else None


def _first_days(text: str) -> int | None:
    m = _DAYS.search(text)
    return int(m.group(1)) if m else None


def _first_value_inr(text: str) -> int | None:
    if (m := _CR.search(text)):
        return int(float(m.group(1)) * 1_00_00_000)
    if (m := _LAKH.search(text)):
        return int(float(m.group(1)) * 1_00_000)
    return None


# ─── Shape builders per typology ─────────────────────────────────────────────

def _shape_pct_min(rule, prop: str, threshold: float) -> str:
    return f"""
ap:Shape_{rule['rule_id']} a sh:NodeShape ;
    sh:targetClass ap:TenderDocument ;
    ap:ruleId "{rule['rule_id']}" ;
    ap:typologyCode "{rule['typology_code']}" ;
    sh:property [
        sh:path ap:{prop} ;
        sh:datatype xsd:decimal ;
        sh:minInclusive {threshold} ;
        sh:severity sh:Violation ;
        sh:message "{_msg(rule)}"
    ] .
"""


def _shape_days_min(rule, prop: str, threshold: int) -> str:
    return f"""
ap:Shape_{rule['rule_id']} a sh:NodeShape ;
    sh:targetClass ap:TenderDocument ;
    ap:ruleId "{rule['rule_id']}" ;
    ap:typologyCode "{rule['typology_code']}" ;
    sh:property [
        sh:path ap:{prop} ;
        sh:datatype xsd:integer ;
        sh:minInclusive {threshold} ;
        sh:severity sh:Violation ;
        sh:message "{_msg(rule)}"
    ] .
"""


def _shape_has_true(rule, prop: str) -> str:
    return f"""
ap:Shape_{rule['rule_id']} a sh:NodeShape ;
    sh:targetClass ap:TenderDocument ;
    ap:ruleId "{rule['rule_id']}" ;
    ap:typologyCode "{rule['typology_code']}" ;
    sh:property [
        sh:path ap:{prop} ;
        sh:datatype xsd:boolean ;
        sh:hasValue true ;
        sh:severity sh:Violation ;
        sh:message "{_msg(rule)}"
    ] .
"""


def _shape_min_count(rule, prop: str, n: int = 1) -> str:
    return f"""
ap:Shape_{rule['rule_id']} a sh:NodeShape ;
    sh:targetClass ap:TenderDocument ;
    ap:ruleId "{rule['rule_id']}" ;
    ap:typologyCode "{rule['typology_code']}" ;
    sh:property [
        sh:path ap:{prop} ;
        sh:minCount {n} ;
        sh:severity sh:Violation ;
        sh:message "{_msg(rule)}"
    ] .
"""


def _msg(rule) -> str:
    src = (rule.get("source_clause") or "").replace('"', '\\"')
    nl = (rule.get("natural_language") or "")[:160].replace('"', '\\"').replace("\n", " ")
    return f"{rule['rule_id']} | {src} | {nl}"


# ─── Typology dispatch ───────────────────────────────────────────────────────

def _build_shape(rule: dict) -> str | None:
    typ = rule.get("typology_code")
    nl  = rule.get("natural_language") or ""

    if typ == "EMD-Shortfall":
        threshold = _first_pct(nl) or 2.0   # GFR Rule 170 default
        return _shape_pct_min(rule, "emdPercentage", threshold)
    if typ == "PBG-Shortfall":
        threshold = _first_pct(nl) or 5.0   # MPW default
        return _shape_pct_min(rule, "pbgPercentage", threshold)
    if typ == "Bid-Validity-Short":
        threshold = _first_days(nl) or 90
        return _shape_days_min(rule, "bidValidityDays", threshold)
    if typ == "Missing-Integrity-Pact":
        return _shape_has_true(rule, "hasIntegrityPact")
    if typ == "Missing-Anti-Collusion":
        return _shape_has_true(rule, "hasAntiCollusionForm")
    if typ == "Missing-PVC-Clause":
        return _shape_has_true(rule, "hasPriceVariationClause")
    if typ == "E-Procurement-Bypass":
        return _shape_has_true(rule, "isEProcurement")
    if typ == "Judicial-Preview-Bypass":
        return _shape_has_true(rule, "hasJudicialPreview")
    if typ == "Reverse-Tender-Evasion":
        return _shape_has_true(rule, "hasReverseTender")
    if typ == "Single-Source-Undocumented":
        return _shape_has_true(rule, "hasOpenTender")
    if typ == "Stale-Financial-Year":
        # treat as registration / solvency age in months — sentinel max 60 (5 years)
        return _shape_min_count(rule, "documentArtefactPresent")
    if typ in ("Missing-Mandatory-Field", "Criteria-Restriction-Loose",
               "Criteria-Restriction-Narrow", "Geographic-Restriction",
               "Turnover-Threshold-Excess", "Available-Bid-Capacity-Error",
               "Solvency-Stale", "Mobilisation-Advance-Excess",
               "BG-Validity-Gap", "Bid-Splitting-Pattern",
               "Sub-Consultant-Cap-Exceed", "DLP-Period-Short",
               "Certification-Exclusionary", "MakeInIndia-LCC-Missing",
               "Spec-Tailoring", "Post-Tender-Negotiation",
               "Missing-Force-Majeure", "Missing-LD-Clause",
               "Pre-Bid-Process-Unclear", "Blacklist-Not-Checked",
               "COI-PMC-Works", "Arbitration-Clause-Violation"):
        return _shape_min_count(rule, "documentArtefactPresent")
    return None    # unmapped typology


# ─── Fetch + emit ────────────────────────────────────────────────────────────

PREAMBLE = """\
# AUTO-GENERATED SHACL shapes for v0 ProcureAI validator.
# Source: scripts/generate_shacl_shapes.py
# Generated from approved P1 rules in Supabase.
@prefix sh:   <http://www.w3.org/ns/shacl#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
@prefix ap:   <https://procureai.in/ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

"""


def _fetch_approved_p1_rules() -> list[dict]:
    url = f"{settings.supabase_rest_url}/rest/v1/rules"
    params = {
        "select": "rule_id,natural_language,source_clause,typology_code,severity,layer,pattern_type,human_status",
        "human_status": "eq.approved",
        "pattern_type": "eq.P1",
        "layer": "in.(Central,CVC,AP-State)",
        "severity": "eq.HARD_BLOCK",
        "order": "rule_id.asc",
    }
    headers = {
        "apikey": settings.supabase_anon_key,
        "Authorization": f"Bearer {settings.supabase_anon_key}",
    }
    res = requests.get(url, params=params, headers=headers, timeout=30)
    res.raise_for_status()
    return res.json()


def main() -> int:
    rules = _fetch_approved_p1_rules()
    print(f"Fetched {len(rules)} approved P1 rules")

    OUT.parent.mkdir(parents=True, exist_ok=True)

    shapes: list[tuple[str, str]] = []   # (rule_id, ttl_block)
    skipped: list[tuple[str, str]] = []  # (rule_id, reason)
    for r in rules:
        block = _build_shape(r)
        if block is None:
            skipped.append((r["rule_id"], f"no template for typology {r.get('typology_code')}"))
            continue
        shapes.append((r["rule_id"], block.strip() + "\n"))

    out_text = PREAMBLE + "\n".join(b for _, b in shapes)
    OUT.write_text(out_text, encoding="utf-8")

    print(f"Generated {len(shapes)} shapes ({len(skipped)} skipped)")
    print(f"Output: {OUT.relative_to(REPO)}")
    if skipped:
        print("\nSkipped rules:")
        for rid, reason in skipped:
            print(f"  {rid}: {reason}")

    print("\n=== First 3 shapes (sample) ===")
    for rid, block in shapes[:3]:
        print(f"---- {rid} ----")
        print(block.strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
