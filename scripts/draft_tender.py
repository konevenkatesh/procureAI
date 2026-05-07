"""
scripts/draft_tender.py

procureAI Drafter (Module 3) — composes a draft tender document by
filling the canonical AP Works tender SKELETON with project-specific
inputs, BDS overrides, and policy-text clause_templates from the
knowledge layer.

Architecture (v2 — skeleton-driven, replacing the v1 clause-list
concatenation):

   templates/ap_works_tender_skeleton.md.tmpl   (canonical structure)
       │   ├── 5-line GOVERNMENT/Department/NIT/Name/ISSUED-ON header
       │   ├── 9-section Table of Contents
       │   ├── NIT body table       <<SLOT:nit_body_table>>
       │   ├── Section I ITB body   <<SLOT:itb_body>>
       │   ├── Section II BDS table <<SLOT:bds_table>>     ← override surface
       │   ├── Section III Eval     <<SLOT:evaluation_criteria>>
       │   ├── Section IV Forms     <<SLOT:bidding_forms>>
       │   ├── Section V Fraud      <<SLOT:fraud_corruption>>
       │   ├── Section VI Works     <<SLOT:works_requirements>>
       │   ├── Section VII GCC      <<SLOT:gcc_body>>
       │   ├── Section VIII PCC     <<SLOT:pcc_overrides>>
       │   └── Section IX Forms     <<SLOT:contract_forms>>
       │
       └── 2-pass render:
              Pass 1: fill <<SLOT:xxx>> with rendered content
              Pass 2: substitute {{name}} placeholders globally from pmap

Two distinct render targets:
  STRUCTURED FORM rows (NIT body, BDS) — generated programmatically from
    CLI inputs + AP regulatory defaults; 100% project-specific.
  POLICY TEXT rows (ITB, GCC, Evaluation, etc.) — sourced from
    DRAFTING_CLAUSE templates filtered by position_section, condition-
    evaluator-gated, parameter-substituted.

The drafter PRODUCES COMPLIANT documents by default — BDS values are
anchored on the SAME regulatory baselines the 24 validators read:
  EMD : 1% bid stage / 1.5% additional at agreement (AP-GO-050)
  PBG : 10% of contract value (AP-GO-175)
  Bid validity : 90 days (AP-GO-067)
  DLP : 24 months (AP-GO-084)
  ABC formula : M = 2 (AP-GO-062)
  JV : Allowed up to 2 members (MPG-279 anti-arbitrary-exclusion;
       L53 found JA/HC banning JV — drafter outputs ALLOWED)
  Class of contractor : derived from ECV (AP-GO-094)

Test:
    python3 scripts/draft_tender.py \\
        --project-name "Construction of Judicial Academy" \\
        --tender-type Works --is-ap-tender true \\
        --ecv-cr 125.5 --duration-months 24 --department APCRDA
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

SKELETON_PATH = REPO / "templates" / "ap_works_tender_skeleton.md.tmpl"


# ── REST helper ───────────────────────────────────────────────────────

def rest_get(path: str, params: dict | None = None) -> list[dict]:
    p = dict(params or {})
    p.setdefault("limit", 5000)
    r = requests.get(f"{REST}/rest/v1/{path}", params=p, headers=H, timeout=60)
    r.raise_for_status()
    out = r.json()
    return out if isinstance(out, list) else []


# ── Indian-style currency formatting ──────────────────────────────────

def format_inr_indian(rupees: float) -> str:
    """Indian comma grouping: 1,255,000,000 → '1,25,50,00,000.00'."""
    s = f"{rupees:.2f}"
    intp, decp = s.split(".")
    sign = ""
    if intp.startswith("-"):
        sign, intp = "-", intp[1:]
    if len(intp) <= 3:
        return f"{sign}{intp}.{decp}"
    last3 = intp[-3:]
    rest = intp[:-3]
    chunks: list[str] = []
    while len(rest) > 2:
        chunks.insert(0, rest[-2:])
        rest = rest[:-2]
    if rest:
        chunks.insert(0, rest)
    return f"{sign}{','.join(chunks)},{last3}.{decp}"


# ── AP class-of-bidders mapping (per AP-GO-094) ──────────────────────

def _ap_class_for_ecv(ecv_cr: float) -> str:
    ecv_lakh = ecv_cr * 100
    if ecv_cr > 10:    return "Special"
    if ecv_cr >= 2:    return "Class-I"
    if ecv_cr >= 1:    return "Class-II"
    if ecv_lakh >= 50: return "Class-III"
    if ecv_lakh >= 10: return "Class-IV"
    return "Class-V"


# ── NIT-number generator ──────────────────────────────────────────────

def auto_gen_nit_number(args: argparse.Namespace) -> str:
    """Heuristic NIT-No format mirroring the JA real document:
        '<seq>/<dept-acronym>/<purpose-code>/<seq2>/<year>'
    The format is hand-rolled because we don't have a sequence
    generator; the year is current. Procurement officers can
    override by passing --nit-number.
    """
    if args.nit_number:
        return args.nit_number
    today = date.today()
    seq1 = "100"   # placeholder
    dept = (args.department or "DEPT").upper()
    # Strip non-alphanumerics from dept for the slug
    dept_slug = re.sub(r"[^A-Z0-9]", "", dept) or "DEPT"
    seq2 = "1"
    year = today.year
    return f"{seq1}/PROC/{dept_slug}/{seq2}/{year}"


# ── STEP 1: tender_facts dict for condition_evaluator ─────────────────

def build_tender_facts(args: argparse.Namespace) -> dict:
    is_ap = bool(args.is_ap_tender)
    facts: dict[str, Any] = {
        "tender_type":    args.tender_type,
        "TenderType":     args.tender_type,
        "is_ap_tender":   is_ap,
        "TenderState":    "AndhraPradesh" if is_ap else "Other",
        "EstimatedValue": float(args.ecv_cr) * 1e7,
        "OriginalContractPeriodMonths": int(args.duration_months),
        "_estimated_value_cr":          float(args.ecv_cr),
        "ProcurementMethod":   "OpenTender",
        "ProcurementMode":     "OpenTender",
        "ContractType":        "EPC" if args.tender_type == "EPC" else None,
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
    if is_ap and args.tender_type in ("Works", "EPC"):
        facts["BidderClassRequired"] = _ap_class_for_ecv(args.ecv_cr)
    return facts


# ── STEP 2: Clause selection (rule-driven) ────────────────────────────

def fetch_drafting_clauses() -> list[dict]:
    return rest_get("clause_templates", {
        "clause_type": "eq.DRAFTING_CLAUSE",
        "select":      ("clause_id,title,text_english,parameters,"
                        "applicable_tender_types,mandatory,position_section,"
                        "position_order,rule_ids,cross_references"),
    })


def fetch_rules_by_id(rule_ids: list[str]) -> dict[str, dict]:
    if not rule_ids:
        return {}
    in_clause = "(" + ",".join(rule_ids) + ")"
    rows = rest_get("rules", {
        "rule_id": f"in.{in_clause}",
        "select":  "rule_id,natural_language,layer,severity,condition_when,defeats",
    })
    return {r["rule_id"]: r for r in rows}


def select_clauses(clauses: list[dict], facts: dict) -> list[dict]:
    all_rule_ids: set[str] = set()
    for c in clauses:
        for rid in (c.get("rule_ids") or []):
            all_rule_ids.add(rid)
    rules_by_id = fetch_rules_by_id(sorted(all_rule_ids))

    tt = facts.get("TenderType")
    out: list[dict] = []

    for c in clauses:
        att = c.get("applicable_tender_types") or []
        att_match = (not att) or (tt in att) or ("ANY" in att)
        if not att_match:
            out.append(dict(c, status="EXCLUDED",
                            _exclusion_reason=f"tender_type={tt!r} not in {att}",
                            rule_verdicts={}, firing_rules=[]))
            continue

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

        if firing_rules:
            status = "MANDATORY"
        elif unknown_rules and not skip_rules:
            status = "ADVISORY"
        elif (c.get("rule_ids") or []) and not (firing_rules or unknown_rules):
            status = "EXCLUDED"
        elif c.get("mandatory"):
            status = "MANDATORY-DEFAULT"
        else:
            status = "OPTIONAL"

        out.append(dict(c, status=status,
                        rule_verdicts=rule_verdicts, firing_rules=firing_rules))
    return out


# ── STEP 3: Parameter map ─────────────────────────────────────────────

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def build_parameter_map(args: argparse.Namespace, facts: dict) -> dict[str, Any]:
    """Global placeholder map. Indian numbering, AP regulatory defaults.

    The values here are anchored on the SAME baselines the 24 Tier-1
    validators read — so a draft using these defaults passes the
    validator's quality gate by construction.
    """
    ecv_cr   = float(args.ecv_cr)
    ecv_inr  = ecv_cr * 1e7
    is_ap    = bool(args.is_ap_tender)
    today    = date.today()

    # AP regulatory date conventions
    bid_open = today + timedelta(days=21)   # AP-GO-057 minimum 14-day window + buffer
    pre_bid  = today + timedelta(days=10)   # ~half-way to bid open
    fin_open = bid_open + timedelta(days=3)
    loa_date = bid_open + timedelta(days=17)

    contractor_class = _ap_class_for_ecv(ecv_cr) if is_ap else "ANY"
    department_full = (args.department_full_name
                       or args.department
                       or ("APCRDA" if is_ap else "Department"))
    # Prefer the short --department acronym when supplied; else parse a
    # parenthesised acronym from the full name; else strip caps.
    if args.department:
        department_acronym = args.department.upper()
    else:
        m = re.search(r"\(([A-Z][A-Z0-9]{1,15})\)", department_full)
        if m:
            department_acronym = m.group(1)
        else:
            words = re.findall(r"\b([A-Z])[A-Z]*", department_full)
            department_acronym = ("".join(words)[:6] if words else "DEPT")
    nit_number = auto_gen_nit_number(args)
    issue_date = today.strftime("%d/%m/%Y")

    # Compliance-anchored defaults — these match the 24 validators' expectations
    emd_stage1_pct = 1.0
    emd_stage2_pct = 1.5
    pbg_pct        = 10.0   # per AP-GO-175 (the 2.5% in JA was the L1 PBG-Shortfall violation)
    bid_validity_days = 90
    dlp_months     = 24
    abc_multiplier = 2

    pmap: dict[str, Any] = {
        # State + department
        "state_upper":           "ANDHRA PRADESH" if is_ap else "INDIA",
        "department_full_name":  department_full,
        "department_acronym":    department_acronym,
        "department_office":     args.department_office or f"{department_full} Office",
        # NIT identifiers
        "nit_number":            nit_number,
        "issue_date":            issue_date,
        # Project basics
        "project_name":          args.project_name,
        "tender_type":           args.tender_type,
        "tender_subject":        args.project_name,
        "duration_months":       str(args.duration_months),
        "completion_period":     f"{args.duration_months} months",
        "contract_duration":     f"{args.duration_months} months",
        "n_years":               str(max(1, round(args.duration_months / 12))),
        # Currency formats
        "ecv_cr":                f"{ecv_cr:.2f}",
        "ecv_rupees":            format_inr_indian(ecv_inr),
        "estimated_value":       f"Rs. {ecv_cr:.2f} Crore (Rs.{format_inr_indian(ecv_inr)})",
        "estimated_value_cr":    f"{ecv_cr:.2f}",
        "estimated_contract_value": f"Rs. {ecv_cr:.2f} Crore",
        "ecv":                   f"Rs. {ecv_cr:.2f} Crore",
        "contract_value":        f"Rs. {ecv_cr:.2f} Crore",
        # State + class
        "tender_state":          "Andhra Pradesh" if is_ap else "Other",
        "state":                 "Andhra Pradesh" if is_ap else "Other",
        "bidder_class":          contractor_class,
        "contractor_class":      contractor_class,
        # AP regulatory values (compliance-anchored)
        "emd_percentage":        f"{emd_stage1_pct + emd_stage2_pct}%",
        "emd_stage1_pct":        f"{emd_stage1_pct}",
        "emd_stage2_pct":        f"{emd_stage2_pct}",
        "emd_stage1_amount":     format_inr_indian(ecv_inr * emd_stage1_pct / 100),
        "emd_stage2_amount":     format_inr_indian(ecv_inr * emd_stage2_pct / 100),
        "emd_total_amount":      format_inr_indian(ecv_inr * (emd_stage1_pct + emd_stage2_pct) / 100),
        "emd_amount":            format_inr_indian(ecv_inr * emd_stage1_pct / 100),
        "pbg_percentage":        f"{pbg_pct}%",
        "pbg_pct":               f"{pbg_pct}",
        "pbg_amount":            format_inr_indian(ecv_inr * pbg_pct / 100),
        "bid_validity_days":     str(bid_validity_days),
        "bid_validity":          f"{bid_validity_days} days",
        "dlp_months":            str(dlp_months),
        "dlp_years":             str(max(1, dlp_months // 12)),
        "dlp":                   f"{dlp_months} months ({dlp_months // 12} years)",
        "defect_liability_period": f"{dlp_months} months from the date of completion of the work",
        "abc_multiplier":        str(abc_multiplier),
        "ma_percentage":         "10%",
        "mobilisation_advance":  "10% of contract value",
        # Solvency framework (per AP-GO-089)
        "solvency_threshold":    f"Rs. {ecv_cr * 0.10:.4f} Crore",
        "solvency_validity":     "1 year from date of issue",
        # Force Majeure
        "fm_notice_days":        "30",
        "fm_termination_window_days": "120",
        # LD
        "ld_rate_per_week":      "0.5%",
        "ld_cap_pct":            "10%",
        # Dates
        "today":                 today.strftime("%d/%m/%Y"),
        "tender_publication_date": today.strftime("%d/%m/%Y"),
        "prebid_date":           pre_bid.strftime("%d/%m/%Y"),
        "pre_bid_meeting_date":  pre_bid.strftime("%d/%m/%Y"),
        "bid_due_date":          bid_open.strftime("%d/%m/%Y"),
        "bid_submission_deadline": bid_open.strftime("%d/%m/%Y"),
        "tech_open_date":        bid_open.strftime("%d/%m/%Y"),
        "fin_open_date":         fin_open.strftime("%d/%m/%Y"),
        "loa_date":              loa_date.strftime("%d/%m/%Y"),
        # Officers
        "contact_officer":       args.contact_officer or "Chief Engineer",
        "contact_email":         args.contact_email or "proc@example.org",
        # AP regulatory anchors
        "ap_go_emd":             "GO Ms No 50 dt 12-04-2024",
        "ap_go_pbg":             "GO Ms No 175 dt 25-03-2024",
        "ap_go_dlp":             "GO Ms No 84 (AP Works DLP — 2 years)",
        "ap_go_abc":             "GO Ms No 62 (AP Works ABC formula M=2)",
        "ap_go_solvency":        "GO MS No 129 dt 05-10-2015",
        "ap_go_class":           "GO Ms No 94 dt 01-07-2003",
        "currency":              "INR",
    }
    return pmap


def substitute_placeholders(
    text: str,
    pmap: dict[str, Any],
    clause_params: list[dict] | None = None,
) -> tuple[str, list[str], list[str]]:
    """Resolve {{name}} placeholders. pmap first, then per-clause example, else [[FILL: name]]."""
    by_name = {p["name"]: p for p in (clause_params or [])
               if isinstance(p, dict) and p.get("name")}
    substituted: list[str] = []
    unresolved: list[str] = []

    def _replace(m: re.Match) -> str:
        nm = m.group(1)
        if nm in pmap:
            substituted.append(nm); return str(pmap[nm])
        if nm in by_name and by_name[nm].get("example"):
            substituted.append(f"{nm}(example)")
            return str(by_name[nm]["example"])
        unresolved.append(nm)
        return f"[[FILL: {nm}]]"

    return _PLACEHOLDER_RE.sub(_replace, text), substituted, unresolved


# ── STEP 4: Slot generators ───────────────────────────────────────────

def render_2col_table(rows: list[tuple[str, str]]) -> str:
    """Render rows as a markdown 2-column table. Newlines in cells → <br>."""
    if not rows:
        return "_(no rows)_\n"
    lines = ["| Field | Value |", "|---|---|"]
    for label, value in rows:
        clean_value = (str(value) or "").replace("\n", "<br>").replace("|", "\\|")
        clean_label = (str(label) or "").replace("|", "\\|")
        lines.append(f"| **{clean_label}** | {clean_value} |")
    return "\n".join(lines) + "\n"


def render_overrides_table(rows: list[tuple[str, str]],
                           left_header: str = "ITB Clause Ref",
                           right_header: str = "BDS Override") -> str:
    """Same shape as 2col but with custom headers — for BDS / PCC."""
    if not rows:
        return "_(no override rows)_\n"
    lines = [f"| {left_header} | {right_header} |", "|---|---|"]
    for label, value in rows:
        clean_value = (str(value) or "").replace("\n", "<br>").replace("|", "\\|")
        clean_label = (str(label) or "").replace("|", "\\|")
        lines.append(f"| **{clean_label}** | {clean_value} |")
    return "\n".join(lines) + "\n"


def render_clauses_as_table(
    clauses: list[dict],
    pmap: dict,
    left_header: str = "Clause",
    right_header: str = "Provision",
    drop_excluded: bool = True,
) -> str:
    """Render a list of selected clauses as a 2-column markdown table.
    Left column = clause title with status badge. Right column = parameter-
    substituted clause text."""
    rows: list[tuple[str, str]] = []
    for c in clauses:
        if drop_excluded and c.get("status") == "EXCLUDED":
            continue
        title = c.get("title") or c.get("clause_id") or "(untitled)"
        status = c.get("status", "OPTIONAL")
        firing = c.get("firing_rules") or []
        cite = (f" · firing: {', '.join(firing)}" if firing else "")
        text, _, unres = substitute_placeholders(
            c.get("text_english") or "",
            pmap, c.get("parameters") or [],
        )
        # Compress whitespace for readability inside table cells
        text = re.sub(r"\s+", " ", text).strip()
        label = f"`{c.get('clause_id', '')}` · {title} · _{status}_{cite}"
        rows.append((label, text))
    if not rows:
        return "_(no clauses applicable to this tender configuration)_\n"
    lines = [f"| {left_header} | {right_header} |", "|---|---|"]
    for label, value in rows:
        clean_value = (str(value) or "").replace("\n", "<br>").replace("|", "\\|")
        clean_label = (str(label) or "").replace("\n", " ").replace("|", "\\|")
        lines.append(f"| {clean_label} | {clean_value} |")
    return "\n".join(lines) + "\n"


def build_nit_body_rows(args: argparse.Namespace, facts: dict, pmap: dict) -> list[tuple[str, str]]:
    """Generate the canonical NIT body 2-column metadata table.
    Mirrors the JA real-document structure (L31-92): department,
    tender id, subject, ECV, duration, DLP, contract form, tender
    type, eligible class, bid validity, EMD, transaction fee, dates,
    contact officers."""
    ecv_cr = float(args.ecv_cr)
    contractor_class = _ap_class_for_ecv(ecv_cr) if args.is_ap_tender else "ANY"

    rows: list[tuple[str, str]] = [
        ("Department", pmap['department_full_name']),
        ("Tender Number", f"NIT No. {pmap['nit_number']}, Dt:{pmap['issue_date']}"),
        ("Tender Subject", pmap['project_name']),
        ("Estimated Contract Value (ECV)", f"Rs.{pmap['ecv_rupees']}"),
        ("Period of Completion of Work", f"{pmap['duration_months']} months"),
        ("Period of Defect Liability Period (DLP)",
         f"{pmap['dlp_months']} months from the date of completion of work."),
        ("Form of Contract", "Lumpsum (% Percentage Tender)"),
        ("Tender Type",
         f"National Competitive Tender through AP E-procurement portal "
         f"(https://apeprocurement.gov.in)"),
        ("Eligible Class of Bidders and additional references",
         f"1. {contractor_class} Class Civil registration with Government of "
         f"Andhra Pradesh, vide GO.MS. No.94, I&CAD (Dept.) dated 01-07-2003."),
        ("Category of Registration",
         f"{contractor_class} Class Civil registration with Government of Andhra Pradesh"),
        ("Bid Validity",
         f"{pmap['bid_validity_days']} days from the date of Bid submission"),
        ("Bid Security (EMD)",
         f"{pmap['emd_stage1_pct']}% of ECV at bid stage = Rs.{pmap['emd_stage1_amount']}; "
         f"additional {pmap['emd_stage2_pct']}% of ECV at agreement signing = "
         f"Rs.{pmap['emd_stage2_amount']} (per {pmap['ap_go_emd']})"),
        ("Transaction Fee",
         "0.03% of ECV (cap Rs.10,000/- for ECV ≤ Rs.50 Cr; "
         "Rs.25,000/- for ECV > Rs.50 Cr) plus applicable taxes."),
        ("BID Processing Fee",
         "Rs. 20,000/- payable online to the Department (Non-refundable)"),
        ("Bid Document Downloading Start Date", pmap['issue_date']),
        ("Bid Document Downloading Close Date",
         f"{pmap['bid_due_date']} @ 14:00 Hrs"),
        ("Pre-Bid Meeting Date",
         f"{pmap['prebid_date']} @ 11:30 Hrs @ {pmap['department_office']}"),
        ("Bid Submission Due Date and time",
         f"{pmap['bid_due_date']} @ 15:00 Hrs"),
        ("Opening of Technical Bid",
         f"{pmap['bid_due_date']} @ 16:00 Hrs"),
        ("Opening of Financial Bid",
         f"{pmap['fin_open_date']} @ 16:00 Hrs"),
        ("Probable date of Issue of LOA", pmap['loa_date']),
        ("Place of bid opening", pmap['department_office']),
        ("Officer Inviting Bids",
         f"MD, {pmap['department_acronym']}"),
        ("Address", pmap['department_office']),
        ("Contact Person until submission of bids",
         f"{pmap['contact_officer']}, {pmap['department_full_name']} "
         f"({pmap['contact_email']})"),
        ("Point of Contact (POC) for procurement-related grievances",
         f"Addl. Commissioner (Admin), {pmap['department_full_name']}"),
        ("Note",
         "The bidder shall upload the scanned copy of online transfer "
         "acknowledgement / Bank Guarantee / Insurance Surety Bond / EBG "
         "along with the bid."),
    ]
    return rows


def build_bds_overrides(args: argparse.Namespace, facts: dict, pmap: dict) -> list[tuple[str, str]]:
    """Generate the BDS (Section II) override rows.

    The BDS values here are anchored on the 24 validators' regulatory
    expectations — so a draft using these defaults passes by
    construction. In particular:
      ITB 4.1(a): Joint Venture: ALLOWED (NOT 'not allowed' which was
                 the L53 violation in JA/HC)
      ITB 19.1:  EMD = 1% of ECV (AP-GO-050 stage 1) + 1.5% additional
                 at agreement
      ITB 18.1:  Bid validity = 90 days (AP-GO-067)
      ITB 42.1:  PBG = 10% of contract value (AP-GO-175 — NOT 2.5%
                 which was the L1 PBG-Shortfall violation in 5 corpus
                 docs)
    """
    contractor_class = _ap_class_for_ecv(float(args.ecv_cr)) if args.is_ap_tender else "ANY"

    rows: list[tuple[str, str]] = [
        ("ITB 1.1",
         f"NIT No: {pmap['nit_number']}, Dt:{pmap['issue_date']}. "
         f"Name of Work: {pmap['project_name']} including DLP of "
         f"{pmap['dlp_years']} Years."),
        ("ITB 1.2",
         "Definitions added: \"ES\" = Environmental and Social; "
         "\"SEA\" = Sexual Exploitation and Abuse; \"SH\" = Sexual Harassment "
         "(per WB / ADB safeguards)."),
        ("ITB 4.1",
         f"The Bidder shall have a {contractor_class} Class Civil registration "
         f"with the Government of Andhra Pradesh per {pmap['ap_go_class']}."),
        ("ITB 4.1 (a)",
         f"**Joint Venture: Allowed**. Maximum number of members in the JV: 2. "
         f"All members shall be jointly and severally liable for execution "
         f"per ITB 4.1 (f). _(Compliant with MPG-279 — the bidding doc shall "
         f"not arbitrarily exclude eligible bidders.)_"),
        ("ITB 4.2 (f)",
         "Conflict of Interest — affiliates of consultants who prepared the "
         "design or technical specifications are debarred from bidding."),
        ("ITB 6.3",
         "Electronic procurement portal: AP eProcurement Portal — "
         "https://apeprocurement.gov.in"),
        ("ITB 7.1",
         f"Clarification address: {pmap['contact_officer']}, "
         f"{pmap['department_full_name']} ({pmap['contact_email']}). "
         f"Queries shall include Clause No., Clause text, and Query, and "
         f"shall be submitted in writing before 5PM of the date of pre-bid "
         f"meeting; submissions after the cut-off shall not be entertained."),
        ("ITB 7.4",
         f"Pre-Bid meeting: {pmap['prebid_date']} @ 11:30 Hrs @ "
         f"{pmap['department_office']}."),
        ("ITB 8.4",
         "Amendments / Corrigendum shall be published at "
         "https://apeprocurement.gov.in"),
        ("ITB 9.1",
         "Bid Transaction Fee: 0.03% of ECV (cap Rs.10,000 for ECV ≤ Rs.50 Cr; "
         "Rs.25,000 for ECV > Rs.50 Cr) + GST. Cost of Bid Processing Fee: "
         "Rs.20,000 (Non-refundable)."),
        ("ITB 10.1",
         "Language of the Bid: English. All correspondence shall be in English."),
        ("ITB 18.1",
         f"Bid validity period: **{pmap['bid_validity_days']} days** "
         f"(per AP-GO-067 — minimum 90 days from bid submission)."),
        ("ITB 19.1",
         f"**Bid Security (EMD): {pmap['emd_stage1_pct']}% of ECV = "
         f"Rs.{pmap['emd_stage1_amount']}** at bid stage. Additional "
         f"{pmap['emd_stage2_pct']}% of ECV = Rs.{pmap['emd_stage2_amount']} "
         f"at agreement signing (per {pmap['ap_go_emd']}). Acceptable forms: "
         f"NEFT/RTGS, irrevocable Bank Guarantee, Insurance Surety Bond, "
         f"or e-Bank Guarantee from any Government / Nationalised / Public "
         f"Sector / Scheduled Bank, valid for 180 days from last bid-submission date."),
        ("ITB 22.1",
         f"Bid Submission Due Date and time: {pmap['bid_due_date']} @ 15:00 Hrs."),
        ("ITB 25.1",
         f"Bid opening: {pmap['bid_due_date']} @ 16:00 Hrs at "
         f"{pmap['department_office']}."),
        ("ITB 30",
         "Non-Material and Non-Conformities **shall not be permitted**."),
        ("ITB 34",
         "Sub-contracting limit: total value of works to be awarded on "
         "sub-contracting **shall not exceed 50% of the contract value**. "
         "Sub-contracting any part requires written employer permission."),
        ("ITB 42.1",
         f"**Performance Security (PBG): {pmap['pbg_pct']}% of contract value = "
         f"Rs.{pmap['pbg_amount']}** (per {pmap['ap_go_pbg']}). The PBG shall "
         f"be valid until 60 days after the completion of the Defects "
         f"Liability Period."),
        ("ITB 43",
         f"Procurement-related Complaint procedure: complaints in writing to "
         f"Addl. Commissioner (Admin), {pmap['department_full_name']}. "
         f"Appellate Authority: Secretary, MA&UD, Government of Andhra Pradesh. "
         f"Appeal within 7 days of decision; written decision within 15 days "
         f"of hearing."),
    ]
    return rows


# ── STEP 5: Skeleton-driven rendering ─────────────────────────────────

def _load_skeleton() -> str:
    if not SKELETON_PATH.exists():
        raise FileNotFoundError(
            f"Skeleton template not found at {SKELETON_PATH}. "
            f"Run from repo root or check templates/ exists."
        )
    return SKELETON_PATH.read_text(encoding="utf-8")


_SLOT_RE = re.compile(r"<<SLOT:([a-zA-Z_][a-zA-Z0-9_]*)>>")


def render_with_skeleton(
    args: argparse.Namespace,
    facts: dict,
    selected: list[dict],
    pmap: dict,
) -> tuple[str, dict]:
    """Two-pass render:
      Pass 1: replace each <<SLOT:xxx>> with rendered content
      Pass 2: substitute remaining {{name}} placeholders globally from pmap
    """
    skeleton = _load_skeleton()

    # Group selected clauses by position_section for slot routing
    by_section: dict[str, list[dict]] = defaultdict(list)
    for c in selected:
        if c.get("status") == "EXCLUDED":
            continue
        by_section[c.get("position_section") or "(none)"].append(c)
    # Sort each section by position_order for stable output
    for k in by_section:
        by_section[k].sort(key=lambda c: (c.get("position_order") or 9999,
                                          c.get("clause_id") or ""))

    # Build slot contents
    slots: dict[str, str] = {}

    # NIT body — programmatic 25-row metadata table
    slots["nit_body_table"] = render_2col_table(
        build_nit_body_rows(args, facts, pmap)
    )

    # ITB body — clauses with position_section='Volume-I/Section-2/ITB'
    slots["itb_body"] = render_clauses_as_table(
        by_section.get("Volume-I/Section-2/ITB", []),
        pmap,
        left_header="ITB Clause",
        right_header="Standard Clause Body",
    )

    # BDS — programmatic override table (compliance-anchored values)
    slots["bds_table"] = render_overrides_table(
        build_bds_overrides(args, facts, pmap),
        left_header="ITB Clause Ref",
        right_header="BDS Override",
    )

    # Section III — Evaluation criteria (Volume-I/Section-4/Evaluation
    # + Volume-I/Section-3/Datasheet which has PQ-Datasheet content)
    eval_clauses = (by_section.get("Volume-I/Section-4/Evaluation", [])
                  + by_section.get("Volume-I/Section-3/Datasheet", []))
    slots["evaluation_criteria"] = render_clauses_as_table(
        eval_clauses, pmap,
        left_header="Criterion / Datasheet Ref",
        right_header="Specification",
    )

    # Section IV — Bidding Forms
    slots["bidding_forms"] = render_clauses_as_table(
        by_section.get("Volume-I/Section-5/Forms", []),
        pmap,
        left_header="Form",
        right_header="Form Specification / Template",
    )

    # Section V — Fraud and Corruption (no specific section in clause_templates;
    # standard WB/ADB framework boilerplate)
    slots["fraud_corruption"] = (
        "1. The Procuring Entity, the World Bank, and the Asian Development "
        "Bank require compliance with their respective Anti-Corruption "
        "Guidelines and prevailing sanctions policies and procedures.\n\n"
        "2. The Bidder shall not, directly or indirectly through its "
        "agents, subcontractors, sub-consultants, service providers, or "
        "suppliers, engage in any of the following: (a) corrupt practice; "
        "(b) fraudulent practice; (c) collusive practice; (d) coercive "
        "practice; or (e) obstructive practice — as defined in the Bank's "
        "Anti-Corruption Guidelines.\n\n"
        "3. The Bidder shall permit, and cause its agents and personnel "
        "to permit, the World Bank and the Asian Development Bank to "
        "inspect all accounts, records, and other documents relating to "
        "any prequalification process, bid submission, proposal submission, "
        "and contract performance, and to have them audited by auditors "
        "appointed by the Banks.\n"
    )

    # Section VI — Works' Requirements (Volume-II/Section-3/Scope +
    # Volume-II/Section-4/Specifications + Volume-II/Section-5/BOQ)
    works_clauses = (by_section.get("Volume-II/Section-3/Scope", [])
                   + by_section.get("Volume-II/Section-4/Specifications", [])
                   + by_section.get("Volume-II/Section-5/BOQ", []))
    slots["works_requirements"] = render_clauses_as_table(
        works_clauses, pmap,
        left_header="Scope / Specification",
        right_header="Description",
    )

    # Section VII — GCC body
    slots["gcc_body"] = render_clauses_as_table(
        by_section.get("Volume-II/Section-1/GCC", []),
        pmap,
        left_header="GCC Clause",
        right_header="Provision",
    )

    # Section VIII — PCC = SCC overrides
    slots["pcc_overrides"] = render_clauses_as_table(
        by_section.get("Volume-II/Section-2/SCC", []),
        pmap,
        left_header="GCC Clause Ref",
        right_header="PCC Override",
    )

    # Section IX — Contract Forms (NIT + Forms-shaped clauses)
    slots["contract_forms"] = render_clauses_as_table(
        by_section.get("Volume-I/Section-1/NIT", []),
        pmap,
        left_header="Form",
        right_header="Template",
    )

    # Pass 1: substitute slot markers
    def _slot_replace(m: re.Match) -> str:
        nm = m.group(1)
        return slots.get(nm, f"_(slot {nm} not implemented)_\n")
    body = _SLOT_RE.sub(_slot_replace, skeleton)

    # Add summary stats to pmap for the footer placeholder
    pmap = dict(pmap)
    pmap["n_clauses_total"]  = str(len(selected))
    pmap["n_mandatory"]      = str(sum(1 for c in selected if c["status"] == "MANDATORY"))
    pmap["n_advisory"]       = str(sum(1 for c in selected if c["status"] == "ADVISORY"))

    # Pass 2: substitute remaining {{name}} placeholders in skeleton-level text
    body, _, _ = substitute_placeholders(body, pmap, [])

    # Stats
    stats = {
        "slots_filled": len(slots),
        "by_section":   {k: len(v) for k, v in by_section.items()},
        "selected_count": sum(1 for c in selected if c.get("status") != "EXCLUDED"),
        "excluded_count": sum(1 for c in selected if c.get("status") == "EXCLUDED"),
    }
    return body, stats


# ── CLI ───────────────────────────────────────────────────────────────

def _bool(v: str) -> bool:
    return str(v).lower() in {"1", "true", "yes", "y", "t"}


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="procureAI Drafter — compose an AP Works tender doc",
    )
    ap.add_argument("--project-name", required=True)
    ap.add_argument("--tender-type",  required=True,
                    choices=["Works", "EPC", "PPP", "Goods", "Services", "Consultancy"])
    ap.add_argument("--is-ap-tender", required=True, type=_bool)
    ap.add_argument("--ecv-cr",       required=True, type=float,
                    help="Estimated Contract Value in crores")
    ap.add_argument("--duration-months", required=True, type=int)
    ap.add_argument("--department",   default=None,
                    help="Short department name / acronym (e.g. APCRDA)")
    ap.add_argument("--department-full-name", default=None,
                    help="Full department name (default: same as --department)")
    ap.add_argument("--department-office", default=None,
                    help="Department office address (for pre-bid meeting + bid opening)")
    ap.add_argument("--nit-number",   default=None,
                    help="Override the auto-generated NIT number")
    ap.add_argument("--contact-officer", default=None)
    ap.add_argument("--contact-email",   default=None)
    ap.add_argument("--output",       default="draft_output.md")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    timings: dict[str, float] = {}
    t_start = time.perf_counter()

    print("=" * 76)
    print("  procureAI Drafter (skeleton-driven, v2)")
    print("=" * 76)
    print(f"  project_name      : {args.project_name}")
    print(f"  tender_type       : {args.tender_type}")
    print(f"  is_ap_tender      : {args.is_ap_tender}")
    print(f"  estimated_value   : Rs. {args.ecv_cr:.2f} Crore "
          f"(Rs.{format_inr_indian(args.ecv_cr * 1e7)})")
    print(f"  duration_months   : {args.duration_months}")
    print(f"  department        : {args.department}")
    print(f"  output            : {args.output}")
    print()

    facts = build_tender_facts(args)
    print("── tender_facts ──")
    print(f"  TenderType={facts['TenderType']!r}, TenderState={facts['TenderState']!r}, "
          f"EstimatedValue=Rs.{facts['_estimated_value_cr']:.2f}Cr, "
          f"Period={facts['OriginalContractPeriodMonths']}mo")
    if facts.get("BidderClassRequired"):
        print(f"  BidderClassRequired={facts['BidderClassRequired']} (per AP-GO-094)")
    print()

    print("── clause selection ──")
    t = time.perf_counter()
    clauses = fetch_drafting_clauses()
    timings["fetch_clauses"] = time.perf_counter() - t
    print(f"  fetched {len(clauses)} DRAFTING_CLAUSE templates "
          f"({timings['fetch_clauses']*1000:.0f}ms)")
    t = time.perf_counter()
    selected = select_clauses(clauses, facts)
    timings["select_clauses"] = time.perf_counter() - t
    by_status: dict[str, int] = defaultdict(int)
    for c in selected:
        by_status[c["status"]] += 1
    for status in ["MANDATORY", "ADVISORY", "MANDATORY-DEFAULT", "OPTIONAL", "EXCLUDED"]:
        print(f"    {status:18s} {by_status.get(status, 0):4d}")
    print()

    print("── parameter map ──")
    pmap = build_parameter_map(args, facts)
    print(f"  {len(pmap)} keys")
    print(f"  ECV (rupees, Indian fmt) : Rs.{pmap['ecv_rupees']}")
    print(f"  contractor_class         : {pmap['contractor_class']}")
    print(f"  EMD stage 1              : {pmap['emd_stage1_pct']}% = Rs.{pmap['emd_stage1_amount']}")
    print(f"  PBG                      : {pmap['pbg_percentage']} = Rs.{pmap['pbg_amount']}")
    print(f"  Bid validity             : {pmap['bid_validity_days']} days")
    print(f"  DLP                      : {pmap['dlp_months']} months")
    print(f"  pre-bid meeting date     : {pmap['prebid_date']}")
    print(f"  bid submission date      : {pmap['bid_due_date']}")
    print()

    print("── render with skeleton ──")
    t = time.perf_counter()
    body, stats = render_with_skeleton(args, facts, selected, pmap)
    timings["render"] = time.perf_counter() - t
    print(f"  slots filled        : {stats['slots_filled']}")
    print(f"  clauses in sections : {stats['by_section']}")
    print(f"  rendered            : {len(body):,} chars / {body.count(chr(10))+1:,} lines "
          f"({timings['render']*1000:.0f}ms)")
    print()

    out_path = Path(args.output).resolve()
    out_path.write_text(body, encoding="utf-8")
    print(f"── Output ──")
    print(f"  {out_path}")
    print(f"  size: {out_path.stat().st_size:,} bytes")

    timings["total_wall"] = time.perf_counter() - t_start
    print(f"  wall: {timings['total_wall']:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
