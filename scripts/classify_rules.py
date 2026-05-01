"""
Classify all rules in Supabase as TYPE_1_ACTIONABLE / TYPE_2_INSTRUCTIONAL /
TYPE_3_CONTEXTUAL based on the rule text + verification method + pattern type.

Few-shot examples (encoded as classification signals):

TYPE_1_ACTIONABLE — checkable on a tender document
  - "EMD must be 2% of estimated cost" (numeric formula)
  - "Integrity Pact mandatory above Rs.5 crore" (presence check)
  - "Reverse tendering mandatory above Rs.1 crore" (process visible in tender)

TYPE_2_INSTRUCTIONAL — process guidance for officers (no tender artefact)
  - "Officers should maintain contract register"
  - "EOT must be dealt with promptly"
  - "Tender committee should meet within 7 days"

TYPE_3_CONTEXTUAL — definitions or scope statements
  - "These rules apply to all departments"
  - "Works means construction, repair or maintenance"

The classifier reads each rule's natural_language + verification_method +
pattern_type + severity, applies the signals from the examples, and PATCHes
the rule_type column in Supabase.

Usage:
    python scripts/classify_rules.py             # classify + patch all rules
    python scripts/classify_rules.py --dry-run   # classify but don't patch
    python scripts/classify_rules.py --batch 20  # custom batch size
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from typing import Iterable

import requests
import typer
from loguru import logger

from builder.config import settings


REST = settings.supabase_rest_url
HEADERS = {
    "apikey": settings.supabase_anon_key,
    "Authorization": f"Bearer {settings.supabase_anon_key}",
    "Content-Type": "application/json",
}


# ───────────────────────────────────────────────────────────────────────────
# Classification — encodes the few-shot signals as deterministic rules over
# (rule_text, verification_method, pattern_type, severity, typology_code)
# ───────────────────────────────────────────────────────────────────────────

# TYPE_3 signals — definitional / scope / foundational
DEFN_VERIF_PATTERNS = (
    "foundational definition",
    "treat all listed",
    "treat all listed activities",
    "not tender-verifiable",
    "not directly tender-verifiable",
    "audit-level",
)
DEFN_TEXT_PATTERNS = (
    re.compile(r"\bdefines?\b.*\bto include\b", re.IGNORECASE),
    re.compile(r"\bdefines?\b.*\bbroadly\b", re.IGNORECASE),
    re.compile(r"\bmeans\b\s+(?:any|all|the)", re.IGNORECASE),
    re.compile(r"\bapplies\s+to\s+all\b", re.IGNORECASE),
    re.compile(r"\bshall\s+be\s+deemed\b", re.IGNORECASE),
    re.compile(r"\bClassification of\b", re.IGNORECASE),
)

# TYPE_2 signals — internal process / management / officer-instruction
PROC_VERIF_PATTERNS = (
    "audit-level",
    "audit trail",
    "not directly tender-verifiable",
    "audit-callable",
    "audit-callable as personal liability",
    "treat ap financial code",
    "internal procedure",
    "audit-level check",
    "audit-level review",
)
PROC_TEXT_PATTERNS = (
    re.compile(r"\bGovernment servant\s+(should|shall)\s+(maintain|conduct|review|inspect|examine|consolidate|investigate)\b", re.IGNORECASE),
    re.compile(r"\b(register|inventory|stock account)\b.*\b(shall|should)\s+be\s+maintained\b", re.IGNORECASE),
    re.compile(r"\bdealt with (promptly|severely|carefully)\b", re.IGNORECASE),
    re.compile(r"\bcommittee\s+should\s+meet\b", re.IGNORECASE),
    re.compile(r"\bTreat\s+(?:GO|AP|the)\b.*\bauthority\b", re.IGNORECASE),
    re.compile(r"\bthe entire (judicial\s+)?preview exercise\b", re.IGNORECASE),
)


# TYPE_1 signals — tender-document-checkable
ACT_VERIF_PATTERNS = (
    "check tender",
    "check ndt",
    "check the tender",
    "for tender",
    "verify tender",
    "verify ndt",
    "verify nit",
    "check nit",
    "check bds",
    "check scc",
    "check gcc",
    "check boq",
    "check the bdr",
    "for goods tenders",
    "for ecv",
    "for emergency purchases",
    "if tender method waived",
    "if l1 bypassed",
    "check tender bds",
    "check tender file",
    "check tender ITB",
    "tender file must contain",
    "if l1 discount",
    "check evaluation",
    "check supply",
    "if mobilization advance",
    "for ap tenders",
    "for tenders post",
    "for pre-30",
    "for post-",
    "for tenders dated",
    "for tender_date",
    "for ecv >",
    "for pol pa",
    "for di pipe",
    "for ms pipe",
    "for non-epc",
    "audit trail: drawing",
    "for ap goods",
    "for ap public",
    "tender ndt:",
    "check rate-contract",
    "check supply agreement",
    "check pa bill",
    "check labour",
    "check repeat order",
    "check extension",
    "for pol",
    "the bidder",
    "check working",
    "epc tender",
    "epc mobilization",
    "epc additional-items",
    "for emergency work-starts",
    "for tenders >= rs",
    "for goods >",
    "for direct departmental",
    "khadi tenders",
    "office-furniture tenders",
    "transport contractor",
    "structural-steel contractor",
    "out-of-state-bidder",
    "for class-i",
    "scheduled-area contractor",
    "auction-sale records",
    "check survey-tender",
    "check pa formula",
    "check pa computation",
    "check di pipe pa",
    "check ms pipe pa",
    "check pipe pa",
    "check labour pa",
    "check excess-rate",
    "check sub-contract",
    "check existence",
    "check non-ap bidder",
    "check bidder past-experience",
    "verify pf=15",
    "check fo",
    "check tender requires",
    "reject ad-hoc",
    "check tender cl",
    "for tenders for",
    "check public-domain",
    "check preview-completion",
    "for ap tenders",
    "for emd >",
    "for performance security",
    "performance security",
    "check supply order",
    "check pa bills",
    "check pa is in",
    "supply order",
    "for ap-state",
    "check cot",
    "check mobilization",
    "check tax-clearance",
    "for ap construction",
    "tender supply",
    "tender bids",
    "tender shall",
    "verify",
    "reject",
    "the ce/enc",
    "check tender percentage",
    "for ap procurement",
    "if l1 quoted",
    "for advance against",
    "for jail works",
    "for sd",
    "for 'Limited",
    "for limited tender",
    "for single tender",
    "for tender-document",
    "if duplicate",
    "check duplicate",
    "if non-ap",
    "if other-state",
    "non-ap bidder",
    "subsequent confirmation",
    "for repeat orders",
    "tender is",
    "if award method",
    "for nomination basis",
    "if subcontract",
    "check tender uses form 9",
    "check po-issuing",
    "check stores receipts",
    "check layered-inspection",
    "audit-level check on stock",
    "audit-level check on stores",
    "for excessive deviations",
    "if validity",
    "tender bds",
    "scc",
    "check tender supply",
    "verify",
    "check the contractor",
)

# Quick high-signal keywords inside rule_text indicating tender artefact check
TENDER_ARTEFACT_KEYWORDS = (
    "estimated_value",
    "% of bid",
    "% of contract",
    "rs.",
    "% of estimat",
    "must be",
    "shall be",
    "mandatory",
    "must contain",
    "must show",
    "must be present",
)


def _classify_one(rule: dict) -> str:
    """Apply the few-shot signals to a single rule. Returns one of:
    TYPE_1_ACTIONABLE, TYPE_2_INSTRUCTIONAL, TYPE_3_CONTEXTUAL."""
    text = (rule.get("natural_language") or "").strip()
    verif = (rule.get("verification_method") or "").strip().lower()
    pattern = (rule.get("pattern_type") or "").upper()
    severity = (rule.get("severity") or "").upper()
    text_l = text.lower()

    # ── TYPE_3: definitions / scope (highest priority)
    for pat in DEFN_TEXT_PATTERNS:
        if pat.search(text):
            # but if it's actually a verifiable rule with a tender-check method, override later
            if "check tender" not in verif and "for tender" not in verif:
                return "TYPE_3_CONTEXTUAL"

    if "foundational" in verif:
        return "TYPE_3_CONTEXTUAL"

    # ── TYPE_2: internal process / officer guidance
    if any(p in verif for p in ("audit-level", "audit-trail", "not tender-verifiable",
                                  "not directly tender-verifiable", "audit-callable",
                                  "internal procedure")):
        return "TYPE_2_INSTRUCTIONAL"

    if pattern == "P4" and severity == "ADVISORY":
        return "TYPE_2_INSTRUCTIONAL"

    for pat in PROC_TEXT_PATTERNS:
        if pat.search(text):
            # if the same rule has a "Check tender..." verifier, it's TYPE_1
            if not (verif.startswith("check tender") or verif.startswith("for tender")
                    or verif.startswith("verify tender")):
                return "TYPE_2_INSTRUCTIONAL"

    # ── TYPE_1: tender-checkable
    if pattern in ("P1", "P2"):
        return "TYPE_1_ACTIONABLE"

    if any(p in verif for p in ("check tender", "for tender", "verify tender",
                                  "check ndt", "check nit", "check bds", "check scc",
                                  "check gcc", "check boq", "for goods tenders",
                                  "for ap tenders", "tender file must contain",
                                  "for ecv", "for tender_date", "for ap goods",
                                  "for emd", "for pa", "check pa", "for sd",
                                  "for performance security", "check supply",
                                  "if l1", "check evaluation", "epc tender",
                                  "for emergency work")):
        return "TYPE_1_ACTIONABLE"

    if pattern == "P3":
        # P3 = override/conditional. If verifiable, TYPE_1; else TYPE_2.
        if "check" in verif or "verify" in verif or "reject" in verif or "for " in verif:
            return "TYPE_1_ACTIONABLE"
        return "TYPE_2_INSTRUCTIONAL"

    # Default for genuinely ambiguous: lean on severity
    if severity in ("HARD_BLOCK", "WARNING"):
        return "TYPE_1_ACTIONABLE"
    return "TYPE_2_INSTRUCTIONAL"


# ───────────────────────────────────────────────────────────────────────────
# Supabase fetch / patch
# ───────────────────────────────────────────────────────────────────────────

def _fetch_all_rules() -> list[dict]:
    out: list[dict] = []
    page = 1000
    fr = 0
    select = "rule_id,natural_language,verification_method,pattern_type,severity,typology_code,layer"
    while True:
        h = {**HEADERS, "Range-Unit": "items", "Range": f"{fr}-{fr + page - 1}"}
        url = f"{REST}/rest/v1/rules?select={select}"
        res = requests.get(url, headers=h, timeout=60)
        if res.status_code not in (200, 206):
            logger.error(f"Fetch failed HTTP {res.status_code}: {res.text[:300]}")
            break
        chunk = res.json()
        out.extend(chunk)
        if len(chunk) < page:
            break
        fr += page
    return out


def _patch_rule_type(rule_id: str, rule_type: str, dry: bool) -> bool:
    if dry:
        return True
    url = f"{REST}/rest/v1/rules?rule_id=eq.{requests.utils.quote(rule_id, safe='')}"
    h = {**HEADERS, "Prefer": "return=minimal"}
    payload = json.dumps({"rule_type": rule_type})
    res = requests.patch(url, headers=h, data=payload, timeout=30)
    if 200 <= res.status_code < 300:
        return True
    logger.error(f"PATCH {rule_id} → HTTP {res.status_code}: {res.text[:200]}")
    return False


def _chunks(lst: list, n: int) -> Iterable[list]:
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


# ───────────────────────────────────────────────────────────────────────────
# Main
# ───────────────────────────────────────────────────────────────────────────

app = typer.Typer(add_completion=False)


@app.command()
def main(
    dry_run: bool = typer.Option(False, "--dry-run", help="Classify but don't PATCH"),
    batch_size: int = typer.Option(20, "--batch", help="Batch size for PATCH (informational)"),
):
    rules = _fetch_all_rules()
    logger.info(f"Fetched {len(rules)} rules from Supabase")
    if not rules:
        return 1

    # Classify
    classifications: dict[str, str] = {}
    for r in rules:
        classifications[r["rule_id"]] = _classify_one(r)

    counts = Counter(classifications.values())
    logger.info("=== Classification (pre-patch) ===")
    for k in ("TYPE_1_ACTIONABLE", "TYPE_2_INSTRUCTIONAL", "TYPE_3_CONTEXTUAL"):
        logger.info(f"  {k}: {counts.get(k, 0)}")
    logger.info(f"  Total: {sum(counts.values())}")

    if dry_run:
        logger.info("\nDry run — skipping PATCHes.")
        return 0

    # PATCH in batches
    items = list(classifications.items())
    ok = 0
    fail = 0
    batches = list(_chunks(items, batch_size))
    logger.info(f"\nPATCHing {len(items)} rules in {len(batches)} batches of {batch_size}...")
    for i, batch in enumerate(batches, 1):
        for rid, rtype in batch:
            if _patch_rule_type(rid, rtype, dry=False):
                ok += 1
            else:
                fail += 1
        if i % 10 == 0 or i == len(batches):
            logger.info(f"  Batch {i}/{len(batches)}: cumulative {ok} OK / {fail} FAIL")

    logger.info(f"\n=== Final ===")
    logger.info(f"  Rules updated: {ok} OK / {fail} FAIL")
    logger.info(f"  TYPE_1_ACTIONABLE:    {counts.get('TYPE_1_ACTIONABLE', 0)}")
    logger.info(f"  TYPE_2_INSTRUCTIONAL: {counts.get('TYPE_2_INSTRUCTIONAL', 0)}")
    logger.info(f"  TYPE_3_CONTEXTUAL:    {counts.get('TYPE_3_CONTEXTUAL', 0)}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(app() or 0)
