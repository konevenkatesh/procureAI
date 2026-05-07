"""
scripts/draft_tender.py

procureAI Drafter (Module 3) — composes a draft tender document from
the knowledge layer, gated by the same condition_when machinery the
24 Tier-1 validators use.

Pipeline:
    STEP 1  build tender_facts from CLI inputs
    STEP 2  pull all 499 DRAFTING_CLAUSE templates from knowledge layer
            for each clause:
              - filter by applicable_tender_types
              - for each linked rule_id, run condition_evaluator
              - status: MANDATORY (any rule FIRES)
                       ADVISORY  (only UNKNOWN verdicts)
                       OPTIONAL  (no rules; mandatory=False; emit by default)
                       MANDATORY-DEFAULT (no rules; mandatory=True)
                       EXCLUDED  (all rules SKIP)
    STEP 3  substitute {{placeholder}} tokens in each clause's text_english
            from a derived parameter map (CLI inputs + AP regulatory
            defaults + clause-supplied examples for unknown values)
    STEP 4  sort by position_section + position_order, emit structured
            markdown grouped by Volume/Section/Type
    STEP 5  (optional --validate) re-run a subset of tier1_*_check.py
            scripts on the draft to confirm no HARD_BLOCK violations

The drafter is rule-driven, not an LLM generator: every clause comes
verbatim from a human-verified DRAFTING_CLAUSE template in the
knowledge layer, so the draft inherits the audit trail of the
clause+rule store (700 clause_templates, 1,223 rules, 200+ SHACL
shapes — see README "Knowledge Layer" section).

Test:
    python3 scripts/draft_tender.py \\
        --project-name "Construction of Judicial Academy" \\
        --tender-type Works \\
        --is-ap-tender true \\
        --ecv-cr 125.5 \\
        --duration-months 24 \\
        --department APCRDA \\
        --output draft_judicial_academy.md
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import requests

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from builder.config import settings
from modules.validator.condition_evaluator import (
    evaluate as evaluate_when, Verdict,
)


REST = settings.supabase_rest_url
H = {"apikey":        settings.supabase_anon_key,
     "Authorization": f"Bearer {settings.supabase_anon_key}"}


# ── REST helpers ──────────────────────────────────────────────────────

def rest_get(path: str, params: dict | None = None) -> list[dict]:
    """REST GET with paginated fallback for large result sets."""
    p = dict(params or {})
    p.setdefault("limit", 5000)
    r = requests.get(f"{REST}/rest/v1/{path}", params=p, headers=H, timeout=60)
    r.raise_for_status()
    out = r.json()
    return out if isinstance(out, list) else []


# ── STEP 1: Build tender_facts from CLI inputs ────────────────────────

def build_tender_facts(args: argparse.Namespace) -> dict:
    """Construct the facts dict that condition_evaluator reads.

    Mirrors the structure used by every tier1_*_check.py:
        TenderType         e.g. 'Works' / 'EPC' / 'PPP' / 'Goods' / 'Services'
        TenderState        'AndhraPradesh' if is_ap_tender else 'Other'
        EstimatedValue     in rupees (ecv_cr * 1e7)
        OriginalContractPeriodMonths
        is_ap_tender       bool
        ContractType       'EPC' if tender_type=='EPC' else None  (best-effort)
        ProcurementMethod  'OpenTender' (default; CLI override TBD)
        plus several pre-RFP defaults set to False so execution-stage rules SKIP
    """
    is_ap = bool(args.is_ap_tender)
    facts: dict[str, Any] = {
        # Document-level
        "tender_type":   args.tender_type,
        "TenderType":    args.tender_type,
        "is_ap_tender":  is_ap,
        "TenderState":   "AndhraPradesh" if is_ap else "Other",
        # Numeric
        "EstimatedValue":              float(args.ecv_cr) * 1e7,   # rupees
        "OriginalContractPeriodMonths": int(args.duration_months),
        "_estimated_value_cr":         float(args.ecv_cr),
        # Procurement method default
        "ProcurementMethod":           "OpenTender",
        "ProcurementMode":             "OpenTender",
        # Best-effort ContractType derivation
        "ContractType":                "EPC" if args.tender_type == "EPC" else None,
        # Pre-RFP / execution-stage signals default to False so rules
        # gated on these (FMEventInvoked, BidAmbiguityDetected, etc.) SKIP.
        "FMEventInvoked":              False,
        "BidAmbiguityDetected":        False,
        "PQRequired":                  False,
        "PQB":                         False,
        "PrequalificationApplied":     False,
        "PrequalificationOrPostQualificationApplied": False,
        "TechnicalSpecificationsPresent":  True,
        "TechnicalSpecsPresent":           True,
        "DetailedSpecificationsPresent":   True,
        "MobilizationAdvanceProvided":     False,
    }
    # Class-of-Bidders by ECV (per AP-GO-094)
    if is_ap and args.tender_type in ("Works", "EPC"):
        facts["BidderClassRequired"] = _ap_class_for_ecv(args.ecv_cr)
    return facts


def _ap_class_for_ecv(ecv_cr: float) -> str:
    """AP class-of-bidders mapping per AP-GO-094 (used in L43 Eligibility-Class)."""
    ecv_lakh = ecv_cr * 100   # crore → lakh
    if ecv_cr > 10:           return "Special"
    if ecv_cr >= 2:           return "Class-I"
    if ecv_cr >= 1:           return "Class-II"
    if ecv_lakh >= 50:        return "Class-III"
    if ecv_lakh >= 10:        return "Class-IV"
    return "Class-V"


# ── STEP 2: Clause selection via condition_evaluator ─────────────────

def fetch_drafting_clauses() -> list[dict]:
    """Pull all DRAFTING_CLAUSE templates."""
    rows = rest_get("clause_templates", {
        "clause_type": "eq.DRAFTING_CLAUSE",
        "select":      ("clause_id,title,text_english,parameters,"
                        "applicable_tender_types,mandatory,position_section,"
                        "position_order,rule_ids,cross_references"),
    })
    return rows


def fetch_rules_by_id(rule_ids: list[str]) -> dict[str, dict]:
    """Batch-fetch rules and index by rule_id."""
    if not rule_ids:
        return {}
    # PostgREST in() filter
    in_clause = "(" + ",".join(rule_ids) + ")"
    rows = rest_get("rules", {
        "rule_id": f"in.{in_clause}",
        "select":  "rule_id,natural_language,layer,severity,condition_when,defeats",
    })
    return {r["rule_id"]: r for r in rows}


def select_clauses(
    clauses: list[dict],
    facts: dict,
) -> list[dict]:
    """Apply the rule-driven clause filter described in the docstring.

    Returns each clause enriched with:
        status   : MANDATORY / ADVISORY / OPTIONAL / MANDATORY-DEFAULT / EXCLUDED
        rule_verdicts : {rule_id: Verdict.value}
        firing_rules  : list of FIRE rule_ids
    """
    # Pre-fetch all referenced rules in one shot
    all_rule_ids: set[str] = set()
    for c in clauses:
        for rid in (c.get("rule_ids") or []):
            all_rule_ids.add(rid)
    rules_by_id = fetch_rules_by_id(sorted(all_rule_ids))

    tt = facts.get("TenderType")
    out: list[dict] = []

    for c in clauses:
        # Filter by applicable_tender_types — both strict-match and the
        # 'ANY' / empty-list catch-all, since some clauses have no list.
        att = c.get("applicable_tender_types") or []
        att_match = (not att) or (tt in att) or ("ANY" in att)
        if not att_match:
            c2 = dict(c, status="EXCLUDED",
                      _exclusion_reason=f"tender_type={tt!r} not in {att}",
                      rule_verdicts={}, firing_rules=[])
            out.append(c2)
            continue

        # Evaluate every linked rule
        rule_verdicts: dict[str, str] = {}
        firing_rules: list[str] = []
        unknown_rules: list[str] = []
        skip_rules: list[str] = []
        for rid in (c.get("rule_ids") or []):
            r = rules_by_id.get(rid)
            if not r:
                rule_verdicts[rid] = "MISSING"
                continue
            cw = r.get("condition_when") or ""
            verdict = evaluate_when(cw, facts).verdict
            rule_verdicts[rid] = verdict.value
            if verdict == Verdict.FIRE:
                firing_rules.append(rid)
            elif verdict == Verdict.UNKNOWN:
                unknown_rules.append(rid)
            elif verdict == Verdict.SKIP:
                skip_rules.append(rid)

        # Determine status
        if firing_rules:
            status = "MANDATORY"
            reason = f"{len(firing_rules)} rule(s) FIRE: {firing_rules[:3]}"
        elif unknown_rules and not skip_rules:
            status = "ADVISORY"
            reason = f"all rules UNKNOWN ({len(unknown_rules)}): {unknown_rules[:3]}"
        elif (c.get("rule_ids") or []) and not (firing_rules or unknown_rules):
            # All linked rules SKIP — clause not applicable
            status = "EXCLUDED"
            reason = f"all {len(skip_rules)} rule(s) SKIP for facts"
        elif c.get("mandatory"):
            status = "MANDATORY-DEFAULT"
            reason = "no linked rules; mandatory=True (template default)"
        else:
            status = "OPTIONAL"
            reason = "no linked rules; mandatory=False"

        c2 = dict(c, status=status, _selection_reason=reason,
                  rule_verdicts=rule_verdicts, firing_rules=firing_rules)
        out.append(c2)

    return out


# ── STEP 3: Parameter substitution ────────────────────────────────────

# Mustache-style placeholder regex: {{name}} or {{ name }}
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def build_parameter_map(args: argparse.Namespace, facts: dict) -> dict[str, Any]:
    """Derive a {{placeholder_name}} → value map from CLI inputs +
    AP regulatory defaults. The drafter substitutes these into each
    clause's text_english. Per-clause `parameters[].example` values
    are used as fallback for any placeholder name not in this map.

    Defaults are anchored on the validators we already wrote:
        AP-GO-050 (EMD %)    → 1%
        AP-GO-175 (PBG %)    → 5% (post-Mar-2024 reduced)
        AP-GO-052 (BidVal)   → 90 days
        AP-GO-084 (DLP)      → 24 months
        AP-GO-062 (ABC M)    → 2 (AP Works)
        MPW 2022 §6.5.3 (MA) → 10%
    """
    ecv_cr   = float(args.ecv_cr)
    ecv_inr  = ecv_cr * 1e7
    is_ap    = bool(args.is_ap_tender)
    today    = date.today()
    bid_open = today + timedelta(days=14)   # AP-GO-057 minimum 14-day window

    # AP-Works pre-bid meeting commonly held ~7 days before bid submission
    pre_bid  = bid_open - timedelta(days=7)

    pmap: dict[str, Any] = {
        # Direct CLI passthroughs
        "project_name":         args.project_name,
        "tender_type":          args.tender_type,
        "department":           args.department or ("APCRDA" if is_ap else "Department"),
        "employer_name":        args.department or ("APCRDA" if is_ap else "Department"),
        "estimated_value_cr":   f"{ecv_cr:.2f}",
        "estimated_value":      f"Rs. {ecv_cr:.2f} Crore (Rs. {ecv_inr:,.0f})",
        "estimated_contract_value":  f"Rs. {ecv_cr:.2f} Crore",
        "ecv":                  f"Rs. {ecv_cr:.2f} Crore",
        "contract_value":       f"Rs. {ecv_cr:.2f} Crore",
        "contract_duration":    f"{args.duration_months} months",
        "duration_months":      str(args.duration_months),
        "completion_period":    f"{args.duration_months} months",
        "n_years":              str(max(1, round(args.duration_months / 12))),
        # State + class
        "tender_state":         "Andhra Pradesh" if is_ap else "Other",
        "state":                "Andhra Pradesh" if is_ap else "Other",
        "bidder_class":         _ap_class_for_ecv(ecv_cr) if is_ap else "ANY",
        "contractor_class":     _ap_class_for_ecv(ecv_cr) if is_ap else "ANY",
        # AP regulatory defaults (validated by the 24 Tier-1 typologies)
        "emd_percentage":       "1%",
        "emd_pct":              "1",
        "emd_amount":           f"Rs. {ecv_cr * 1e7 * 0.01:,.0f} ({ecv_cr*0.01:.4f} Crore)",
        "pbg_percentage":       "5%",
        "pbg_pct":              "5",
        "pbg_amount":           f"Rs. {ecv_cr * 1e7 * 0.05:,.0f} ({ecv_cr*0.05:.4f} Crore)",
        "bid_validity_days":    "90",
        "bid_validity":         "90 days",
        "dlp_months":           "24",
        "dlp":                  "24 months (2 years)",
        "defect_liability_period": "24 months from the date of completion of the work",
        "abc_multiplier":       "2",
        "ma_percentage":        "10%",
        "mobilisation_advance": "10% of contract value",
        # Solvency framework (per AP-GO-089)
        "solvency_threshold":   f"Rs. {ecv_cr * 0.10:.4f} Crore",   # 10% of contract value
        "solvency_validity":    "1 year from date of issue",
        # Force Majeure (per L48)
        "fm_notice_days":       "30",
        "fm_termination_window_days": "120",
        # Liquidated Damages (per L25 LD typology)
        "ld_rate_per_week":     "0.5%",
        "ld_cap_pct":           "10%",
        # Pre-bid + dates
        "pre_bid_meeting_date": pre_bid.strftime("%d-%m-%Y"),
        "bid_submission_deadline": bid_open.strftime("%d-%m-%Y"),
        "tender_publication_date": today.strftime("%d-%m-%Y"),
        # AP regulatory anchors (citations, when a clause asks for one)
        "ap_go_emd":            "GO Ms No 50 dt 12-04-2024",
        "ap_go_pbg":            "GO Ms No 175 dt 25-03-2024",
        "ap_go_dlp":            "GO Ms No 84 (AP Works DLP — 2 years)",
        "ap_go_abc":            "GO Ms No 62 (AP Works ABC formula M=2)",
        "ap_go_solvency":       "GO MS No 129 dt 05-10-2015",
        "ap_go_class":          "GO Ms No 94 dt 01-07-2003",
        # Fallbacks for common parameters
        "currency":             "INR",
    }
    return pmap


def substitute_placeholders(
    text: str,
    pmap: dict[str, Any],
    clause_params: list[dict],
) -> tuple[str, list[str], list[str]]:
    """Substitute {{name}} tokens in text. Returns (new_text, substituted, unresolved).

    Resolution order per placeholder:
      1. pmap[name]   — global parameter map (CLI + AP defaults)
      2. clause-supplied parameters[].example by name match
      3. leave as `[[FILL: name]]` placeholder for the procurement officer
    """
    by_name = {p["name"]: p for p in (clause_params or []) if isinstance(p, dict) and p.get("name")}
    substituted: list[str] = []
    unresolved: list[str] = []

    def _replace(m: re.Match) -> str:
        nm = m.group(1)
        if nm in pmap:
            substituted.append(nm)
            return str(pmap[nm])
        if nm in by_name and by_name[nm].get("example"):
            substituted.append(f"{nm}(example)")
            return str(by_name[nm]["example"])
        unresolved.append(nm)
        return f"[[FILL: {nm}]]"

    return _PLACEHOLDER_RE.sub(_replace, text), substituted, unresolved


# ── STEP 4: Assembly in position_section order ───────────────────────

# Volume → Section ordering used to sort the markdown output.
# Lifted from clause_templates.position_section distribution:
#   Volume-I/Section-1/NIT, Volume-I/Section-2/ITB,
#   Volume-I/Section-3/Datasheet, Volume-I/Section-4/Evaluation,
#   Volume-I/Section-5/Forms, Volume-II/Section-1/GCC,
#   Volume-II/Section-2/SCC, Volume-II/Section-3/Scope,
#   Volume-II/Section-4/Specifications, Volume-II/Section-5/BOQ.
SECTION_ORDER = [
    "Volume-I/Section-1/NIT",
    "Volume-I/Section-2/ITB",
    "Volume-I/Section-3/Datasheet",
    "Volume-I/Section-4/Evaluation",
    "Volume-I/Section-5/Forms",
    "Volume-II/Section-1/GCC",
    "Volume-II/Section-2/SCC",
    "Volume-II/Section-3/Scope",
    "Volume-II/Section-4/Specifications",
    "Volume-II/Section-5/BOQ",
]


def _section_sort_key(c: dict) -> tuple[int, int, str]:
    pos = c.get("position_section") or "ZZZ"
    try:
        sec_idx = SECTION_ORDER.index(pos)
    except ValueError:
        sec_idx = len(SECTION_ORDER)
    order = c.get("position_order") or 9999
    return (sec_idx, int(order), c.get("clause_id") or "")


# ── STEP 5: Markdown emission ────────────────────────────────────────

def render_markdown(
    args: argparse.Namespace,
    facts: dict,
    selected: list[dict],
    pmap: dict,
    timing: dict,
) -> str:
    """Render the draft tender as structured markdown."""
    by_section: dict[str, list[dict]] = defaultdict(list)
    excluded: list[dict] = []
    for c in selected:
        if c["status"] == "EXCLUDED":
            excluded.append(c); continue
        by_section[c.get("position_section") or "(uncategorised)"].append(c)

    # Order sections per SECTION_ORDER
    out: list[str] = []
    out.append(f"# Draft Tender Document")
    out.append("")
    out.append(f"**Project Name:** {args.project_name}")
    out.append(f"**Tender Type:** {args.tender_type}  ")
    out.append(f"**Department:** {args.department or '(unspecified)'}  ")
    out.append(f"**Estimated Contract Value:** Rs. {args.ecv_cr:.2f} Crore  ")
    out.append(f"**Contract Duration:** {args.duration_months} months  ")
    out.append(f"**State Jurisdiction:** {'Andhra Pradesh' if args.is_ap_tender else 'Other'}  ")
    out.append(f"**Drafted On:** {date.today().isoformat()}")
    out.append("")
    out.append(f"_Generated by procureAI Drafter from {len(selected)} candidate "
               f"clauses ({sum(1 for c in selected if c['status']=='MANDATORY')} mandatory, "
               f"{sum(1 for c in selected if c['status']=='ADVISORY')} advisory, "
               f"{sum(1 for c in selected if c['status']=='MANDATORY-DEFAULT')} default-mandatory, "
               f"{sum(1 for c in selected if c['status']=='OPTIONAL')} optional, "
               f"{len(excluded)} excluded). Knowledge layer: 700 clause_templates_, "
               f"1,223 rules. Validators: 24 Tier-1 typology checks._")
    out.append("")
    out.append("---")
    out.append("")

    sections_in_order = sorted(by_section.keys(), key=lambda s: (
        SECTION_ORDER.index(s) if s in SECTION_ORDER else len(SECTION_ORDER),
        s,
    ))

    for sec in sections_in_order:
        sec_clauses = sorted(by_section[sec], key=_section_sort_key)
        out.append(f"## {sec}")
        out.append("")
        for c in sec_clauses:
            text, _, unresolved = substitute_placeholders(
                c.get("text_english") or "",
                pmap,
                c.get("parameters") or [],
            )
            out.append(f"### {c['title']}")
            out.append("")
            out.append(f"`{c['clause_id']}` · status: **{c['status']}**"
                       + (f" · firing rules: {', '.join(c['firing_rules'])}"
                          if c['firing_rules'] else "")
                       + (f" · unresolved placeholders: {sorted(set(unresolved))}"
                          if unresolved else ""))
            out.append("")
            out.append(text)
            out.append("")
        out.append("")

    # Coverage footer
    out.append("---")
    out.append("")
    out.append("## Coverage report")
    out.append("")
    # `selected` already contains EXCLUDED rows; don't double-count them.
    out.append(f"- Total DRAFTING_CLAUSE templates considered: {len(selected)}")
    by_status = defaultdict(int)
    for c in selected:
        by_status[c["status"]] += 1
    for status, n in sorted(by_status.items(), key=lambda kv: -kv[1]):
        out.append(f"- {status}: {n}")
    out.append("")
    out.append(f"- Sections populated: {len(by_section)}")
    out.append(f"- Generation wall-time: {timing.get('total_wall', 0):.2f}s")
    out.append("")

    return "\n".join(out)


# ── CLI ───────────────────────────────────────────────────────────────

def _bool(v: str) -> bool:
    return str(v).lower() in {"1", "true", "yes", "y", "t"}


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="procureAI Drafter — compose a tender doc from the knowledge layer",
    )
    ap.add_argument("--project-name", required=True)
    ap.add_argument("--tender-type",  required=True,
                    choices=["Works", "EPC", "PPP", "Goods", "Services", "Consultancy"])
    ap.add_argument("--is-ap-tender", required=True, type=_bool)
    ap.add_argument("--ecv-cr",       required=True, type=float,
                    help="Estimated Contract Value in crores")
    ap.add_argument("--duration-months", required=True, type=int)
    ap.add_argument("--department",   default=None)
    ap.add_argument("--output",       default="draft_output.md",
                    help="Output markdown path")
    ap.add_argument("--validate",     action="store_true",
                    help="Run a subset of tier1_*_check.py against the draft (TBD)")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    timings: dict[str, float] = {}
    t0 = time.perf_counter()

    print("=" * 76)
    print("  procureAI Drafter")
    print("=" * 76)
    print(f"  project_name      : {args.project_name}")
    print(f"  tender_type       : {args.tender_type}")
    print(f"  is_ap_tender      : {args.is_ap_tender}")
    print(f"  estimated_value   : Rs. {args.ecv_cr:.2f} Crore")
    print(f"  duration_months   : {args.duration_months}")
    print(f"  department        : {args.department}")
    print(f"  output            : {args.output}")
    print()

    # STEP 1
    facts = build_tender_facts(args)
    print(f"── STEP 1: tender_facts ──")
    print(f"  TenderType={facts['TenderType']!r}, TenderState={facts['TenderState']!r}, "
          f"EstimatedValue=Rs.{facts['_estimated_value_cr']:.2f}Cr, "
          f"OriginalContractPeriodMonths={facts['OriginalContractPeriodMonths']}")
    if facts.get("BidderClassRequired"):
        print(f"  derived BidderClassRequired={facts['BidderClassRequired']} "
              f"(per AP-GO-094)")
    print()

    # STEP 2
    print(f"── STEP 2: clause selection ──")
    t = time.perf_counter()
    clauses = fetch_drafting_clauses()
    timings["fetch_clauses"] = time.perf_counter() - t
    print(f"  fetched {len(clauses)} DRAFTING_CLAUSE templates in {timings['fetch_clauses']*1000:.0f}ms")
    t = time.perf_counter()
    selected = select_clauses(clauses, facts)
    timings["select_clauses"] = time.perf_counter() - t
    by_status: dict[str, int] = defaultdict(int)
    for c in selected:
        by_status[c["status"]] += 1
    print(f"  classified in {timings['select_clauses']*1000:.0f}ms:")
    for status in ["MANDATORY", "ADVISORY", "MANDATORY-DEFAULT", "OPTIONAL", "EXCLUDED"]:
        print(f"    {status:18s} {by_status.get(status, 0):4d}")
    print()

    # STEP 3 + 4 + 5
    print(f"── STEP 3+4: parameter substitution + markdown assembly ──")
    pmap = build_parameter_map(args, facts)
    print(f"  parameter map: {len(pmap)} keys")
    t = time.perf_counter()
    md = render_markdown(args, facts, selected, pmap,
                         {**timings,
                          "total_wall": time.perf_counter() - t0})
    timings["render"] = time.perf_counter() - t
    timings["total_wall"] = time.perf_counter() - t0
    md = md.replace("0.00s", f"{timings['total_wall']:.2f}s")  # late-bind wall-time
    print(f"  rendered {len(md):,} chars / {md.count(chr(10))+1:,} lines in {timings['render']*1000:.0f}ms")
    print()

    # Write output
    out_path = Path(args.output).resolve()
    out_path.write_text(md, encoding="utf-8")
    print(f"── Output written ──")
    print(f"  {out_path}")
    print(f"  size: {out_path.stat().st_size:,} bytes")
    print()

    # Coverage summary
    excl_keep = [c for c in selected if c["status"] != "EXCLUDED"]
    placeholder_total = 0
    placeholder_filled_pmap = 0
    placeholder_filled_example = 0
    placeholder_unresolved: set[str] = set()
    for c in excl_keep:
        text = c.get("text_english") or ""
        ms = _PLACEHOLDER_RE.findall(text)
        placeholder_total += len(ms)
        by_name = {p["name"]: p for p in (c.get("parameters") or [])
                   if isinstance(p, dict) and p.get("name")}
        for nm in ms:
            if nm in pmap:
                placeholder_filled_pmap += 1
            elif nm in by_name and by_name[nm].get("example"):
                placeholder_filled_example += 1
            else:
                placeholder_unresolved.add(nm)

    print(f"── Coverage summary ──")
    print(f"  clauses included    : {len(excl_keep)}")
    print(f"  sections populated  : {len({c.get('position_section') for c in excl_keep})}")
    print(f"  placeholders total  : {placeholder_total}")
    print(f"    filled by pmap    : {placeholder_filled_pmap}")
    print(f"    filled by example : {placeholder_filled_example}")
    print(f"    unresolved        : {placeholder_total - placeholder_filled_pmap - placeholder_filled_example}"
          f" ({len(placeholder_unresolved)} unique)")
    if placeholder_unresolved:
        print(f"  unresolved sample   : {sorted(placeholder_unresolved)[:10]}")
    print(f"  wall                : {timings['total_wall']:.2f}s")
    print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
