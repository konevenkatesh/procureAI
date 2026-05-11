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

# ── Phase 2.4: fixed-skeleton feature flags ───────────────────────────
#
# When True, the corresponding section's slot in render_with_skeleton()
# loads a fixed-text skeleton from templates/sections/ instead of
# assembling clause fragments. The skeletons are extracted from the
# Standard Bidding Document (Zone-11 SBD canonical, Bid+document
# cross-validated) and capture the SBD's fixed legal text — the parts
# that don't change between AP Works tenders.
#
# Defaults are False so the existing clause-fragment assembly path
# remains the live behaviour. Override via env var for testing:
#   USE_FIXED_SKELETON_ITB=1 python3 scripts/draft_tender.py …
#   USE_FIXED_SKELETON_GCC=1 python3 scripts/draft_tender.py …
#   USE_FIXED_SKELETON_FRAUD=1 python3 scripts/draft_tender.py …
#
# Phase 2.6 will run a side-by-side validator comparison; only after
# all 6 Tier-1 validators pass will the defaults be flipped to True.
def _flag(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None: return default
    return v.lower() in {"1", "true", "yes", "y", "t"}

# Defaults flipped to True after Phase 2.6 side-by-side validator
# verification (2026-05-08): 5 validators unchanged, 1 improved
# (Judicial-Preview-Bypass), 0 regressed. PBG-Shortfall canary stayed
# COMPLIANT under both paths. To revert any individual section to the
# legacy clause-fragment assembly path, set the env var to "0":
#   USE_FIXED_SKELETON_ITB=0 python3 scripts/draft_tender.py …
USE_FIXED_SKELETON_ITB   = _flag("USE_FIXED_SKELETON_ITB",   True)
USE_FIXED_SKELETON_GCC   = _flag("USE_FIXED_SKELETON_GCC",   True)
USE_FIXED_SKELETON_FRAUD = _flag("USE_FIXED_SKELETON_FRAUD", True)

SKELETONS_DIR = REPO / "templates" / "sections"

# funding_source values that mean "World-Bank-funded" (toggles
# DELETED_IF_DOMESTIC blocks ON and selects fraud_wb_funded.md).
WB_FUNDED_SOURCES = {"MDB"}

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
    # Step-1 PART C: procurement_mode flows from CLI / extractor.
    # Default OTE for AP Works > Rs.25 lakh per APSS. Tracked in two
    # canonical names ("OTE" + "OpenTender") so both rule conditions
    # and the new clause-id skiplist key off the same source.
    procurement_mode = (
        (getattr(args, "procurement_mode", None) or "OTE").upper()
    )
    facts: dict[str, Any] = {
        "tender_type":    args.tender_type,
        "TenderType":     args.tender_type,
        "is_ap_tender":   is_ap,
        "TenderState":    "AndhraPradesh" if is_ap else "Other",
        "EstimatedValue": float(args.ecv_cr) * 1e7,
        "OriginalContractPeriodMonths": int(args.duration_months),
        "_estimated_value_cr":          float(args.ecv_cr),
        "ProcurementMethod":   procurement_mode,
        "ProcurementMode":     procurement_mode,
        "procurement_mode":    procurement_mode,
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
                        "position_order,rule_ids,cross_references,"
                        "project_scope_filter"),
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
    procurement_mode = (facts.get("ProcurementMode") or "OTE").upper()
    out: list[dict] = []

    # ── Step-1 PART C: procurement-mode hard skiplist ─────────────────
    # When procurement_mode=OTE (the default for AP Works tenders > Rs.25 lakh),
    # exclude clauses whose entire purpose is to govern a different mode
    # (Special Limited / Global Tender Enquiry / Two-Stage EoI / Pre-
    # Qualification Bidding). Even though their condition_when may
    # evaluate UNKNOWN against an OTE facts dict and the existing
    # selector would downgrade them to ADVISORY → INCLUDED, the result
    # is a self-contradicting tender (SLTE clauses inside an OTE doc).
    # The skiplist below is keyed on substrings in clause_id, not
    # rule_ids, since these clauses are mode-specific by construction.
    OTE_SKIP_SUBSTRINGS = (
        "SLTE",                      # Special Limited Tender Enquiry
        "GTE-T-AND-C",               # Global Tender Enquiry T&Cs
        "GLOBAL-TENDER",
        "EOI-TWOSTAGE",              # Two-stage EoI (process)
        "TWO-STAGE-EOI",
        "EOI-WORKS",                 # EoI for Works
        "2-STAGE-EOI-WORKS",
        "PQB-DOCUMENT",              # Pre-Qualification Bidding doc
        "PQB-WORKS",                 # PQB for Works
    )
    OTE_SKIP_EXACT = {
        "CLAUSE-SLTE-001",
        "CLAUSE-SLTE-CERT-001",
        "CLAUSE-GTE-T-AND-C-001",
        "CLAUSE-EOI-TWOSTAGE-001",
        "CLAUSE-2-STAGE-EOI-WORKS-001",
        "CLAUSE-PQB-DOCUMENT-001",
        "CLAUSE-PQB-WORKS-001",
    }

    # ── Step-2: AP_STATE_DEFEATS — central clauses that an AP-State
    # authoritative clause supersedes for AP tenders.
    #
    # The defeasibility relationship belongs in `rules.defeated_by`,
    # but a curated subset is missing from the seed data. Until that
    # gap is closed (separate knowledge-layer task), enforce the
    # defeat at the selector level so the validator sees only the
    # canonical AP-State anchor.
    #
    # Keyed by (is_ap_tender, tender_type) → set of clause_ids that
    # the AP-State variant supersedes.
    #
    # Initial entry (PBG): CLAUSE-AP-CONTRACTOR-SECURITY-DEPOSIT-001
    # (linked to AP-GO-175/216/217 — clean 10% statement) supersedes
    # CLAUSE-WORKS-PBG-001 (linked to MPW-072, which is itself a
    # mis-linked rule about corrigendum amendments — not PBG).
    AP_STATE_DEFEATS: dict[tuple[bool, str], set[str]] = {
        (True, "Works"): {"CLAUSE-WORKS-PBG-001"},
        (True, "EPC"):   {"CLAUSE-WORKS-PBG-001"},
    }
    is_ap = bool(facts.get("is_ap_tender"))
    ap_state_skip = AP_STATE_DEFEATS.get((is_ap, str(tt or "")), set())

    # ── Phase 2.5: fixed-skeleton exclusions ─────────────────────────
    # When the corresponding feature flag is on, the section's clauses
    # are rendered from a canonical SBD skeleton in templates/sections/
    # and the clause-template fragments are redundant — exclude them
    # to prevent double-rendering. This runs BEFORE the rule evaluator
    # because the skeleton-replaced clauses don't need rule evaluation.
    #
    # Note: there is no FRAUD exclusion here because the existing
    # `fraud_corruption` slot is filled by an inline boilerplate string
    # in render_with_skeleton() — not from clause_templates. The
    # USE_FIXED_SKELETON_FRAUD flag (2.4) swaps the inline string for
    # the loaded skeleton file directly; no clause_template change
    # needed.
    SKELETON_REPLACED_SECTIONS: set[str] = set()
    if USE_FIXED_SKELETON_ITB: SKELETON_REPLACED_SECTIONS.add("Volume-I/Section-2/ITB")
    if USE_FIXED_SKELETON_GCC: SKELETON_REPLACED_SECTIONS.add("Volume-II/Section-1/GCC")

    for c in clauses:
        cid = c.get("clause_id") or ""

        # Phase 2.5: fixed-skeleton replaces this whole section
        if c.get("position_section") in SKELETON_REPLACED_SECTIONS:
            out.append(dict(c, status="EXCLUDED",
                            _exclusion_reason=(
                                f"section {c.get('position_section')!r} replaced by "
                                f"fixed skeleton (templates/sections/)"),
                            rule_verdicts={}, firing_rules=[]))
            continue

        # Procurement-mode hard skip — runs BEFORE the rule evaluator
        if procurement_mode == "OTE" and (
            cid in OTE_SKIP_EXACT
            or any(s in cid for s in OTE_SKIP_SUBSTRINGS)
        ):
            out.append(dict(c, status="EXCLUDED",
                            _exclusion_reason=f"procurement_mode={procurement_mode!r} excludes mode-specific clause {cid!r}",
                            rule_verdicts={}, firing_rules=[]))
            continue

        # Step-2: AP-State authoritative clause defeats central variant
        if cid in ap_state_skip:
            out.append(dict(c, status="EXCLUDED",
                            _exclusion_reason=(
                                f"AP-State authoritative clause supersedes central variant "
                                f"({cid!r}) for is_ap_tender={is_ap}, tender_type={tt!r}"),
                            rule_verdicts={}, firing_rules=[]))
            continue

        att = c.get("applicable_tender_types") or []
        att_match = (not att) or (tt in att) or ("ANY" in att)
        if not att_match:
            out.append(dict(c, status="EXCLUDED",
                            _exclusion_reason=f"tender_type={tt!r} not in {att}",
                            rule_verdicts={}, firing_rules=[]))
            continue

        # Phase 1 binary disable: clauses with non-null project_scope_filter
        # are project-type-specific (Sewerage / WaterTreatment / Storage /
        # Buildings* / RoadHighway / etc.) and disabled in the universal
        # AP Works compliance scope. Phase 2 — when corpus expands across
        # project types — will introduce a tender ProjectScope facet and
        # match clause.project_scope_filter against tender.ProjectScope.
        # See clause_templates.project_scope_filter column comment.
        if c.get("project_scope_filter"):
            out.append(dict(c, status="EXCLUDED",
                            _exclusion_reason=(
                                "project-specific clause disabled in "
                                "Phase 1 scope (universal compliance only)"),
                            rule_verdicts={}, firing_rules=[]))
            continue

        rule_verdicts: dict[str, str] = {}
        firing_rules: list[str] = []
        unknown_rules: list[str] = []
        skip_rules: list[str] = []
        missing_rules: list[str] = []
        for rid in (c.get("rule_ids") or []):
            r = rules_by_id.get(rid)
            if not r:
                rule_verdicts[rid] = "MISSING"
                missing_rules.append(rid)
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

        # Bug A patch (Phase 2.7): when EVERY rule_id resolves to a row
        # missing from the `rules` table (knowledge-layer integrity
        # gap, not a facts-driven SKIP), the clause used to fall into
        # the `(rule_ids) and not (firing or unknown) → EXCLUDED`
        # branch — silently dropping mandatory clauses like
        # CLAUSE-MAKE-IN-INDIA-PPP2017-001 (rule MPS22-005 unseeded).
        # Promote based on the `mandatory` flag instead, and tag the
        # clause with `_audit_warning` so the gap surfaces in stats /
        # logs rather than vanishing. Mixed MISSING+SKIP cases still
        # fall to EXCLUDED (some rule says "doesn't apply"; conservative).
        all_rule_ids = c.get("rule_ids") or []
        all_missing  = bool(all_rule_ids) and len(missing_rules) == len(all_rule_ids)
        # Bug A patch upgraded to structured dict (was a flat string) so
        # renderers can consume the missing_rule_ids list directly without
        # parsing the string. Schema:
        #   {"missing_rule_ids": [str, …],
        #    "reason":           "<human-readable explanation>"}
        audit_warning: dict | None = None

        if firing_rules:
            status = "MANDATORY"
        elif unknown_rules and not skip_rules:
            status = "ADVISORY"
        elif all_missing:
            audit_warning = {
                "missing_rule_ids": list(missing_rules),
                "reason": (
                    f"all {len(missing_rules)} rule(s) referenced by this "
                    f"clause are missing from the rules table: "
                    f"{missing_rules}. Promoted via mandatory-flag fallback."
                ),
            }
            status = "MANDATORY-DEFAULT" if c.get("mandatory") else "ADVISORY"
        elif (c.get("rule_ids") or []) and not (firing_rules or unknown_rules):
            status = "EXCLUDED"
        elif c.get("mandatory"):
            status = "MANDATORY-DEFAULT"
        else:
            status = "OPTIONAL"

        out.append(dict(c, status=status,
                        rule_verdicts=rule_verdicts, firing_rules=firing_rules,
                        _audit_warning=audit_warning))
    return out


# ── Audit-warning marker rendering ────────────────────────────────────

def _format_audit_warning_marker(audit_warning: dict | None) -> str:
    """Format the Bug-A `_audit_warning` dict as a single-line italic
    marker for inline placement above a clause body.

    Returns "" when audit_warning is None or empty (nothing to render).
    Otherwise returns one line:
        _[⚠ rules pending seed: <id1>, <id2>, <id3> +N more]_

    Truncates to first 3 rule_ids; appends "+N more" when more exist."""
    if not audit_warning:
        return ""
    ids = list(audit_warning.get("missing_rule_ids") or [])
    if not ids:
        return ""
    shown = ids[:3]
    extra = len(ids) - 3
    suffix = f" +{extra} more" if extra > 0 else ""
    return f"_[⚠ rules pending seed: {', '.join(shown)}{suffix}]_"


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

    # ── Step-1 PART B: pmap convention ────────────────────────────────
    # pmap values carry RAW data only — no "Rs." prefix, no "%" suffix.
    # The clause templates (and slot builders) attach their own currency
    # / percent symbols. Keeping pmap raw eliminates the double-prefix
    # bugs ("Rs.Rs. 85.00 Crore", "2.5%%") that came from clauses
    # that wrote "Rs.{{estimated_value}}" or "{{emd_percentage}}%"
    # against pmap values that already had the prefix.
    ecv_rupees_raw = format_inr_indian(ecv_inr)            # "85,00,00,000.00"
    pmap: dict[str, Any] = {
        # State + department
        "state_upper":           "ANDHRA PRADESH" if is_ap else "INDIA",
        "department":            department_acronym,
        "department_full_name":  department_full,
        "department_acronym":    department_acronym,
        "department_office":     args.department_office or f"{department_full} Office",
        # PART A — param-name aliases that the seeded clauses use.
        # Resolves CLAUSE-BG-CUSTODY, REFUND, BOC, ARB-APPEAL,
        # CIPP, MAF, COI-DECLARATION, SEALING-MARKING, TWO-COVER, etc.
        "procuring_entity":          department_acronym,
        "procuring_entity_name":     department_full,
        "procuring_entity_full":     department_full,
        "procuring_entity_acronym":  department_acronym,
        # NIT identifiers
        "nit_number":            nit_number,
        "tender_no":             nit_number,           # PART A alias
        "tender_number":         nit_number,
        "tender_id":             nit_number,
        "issue_date":            issue_date,
        "declaration_date":      issue_date,           # PART A alias
        "publication_date":      issue_date,
        # Project basics
        "project_name":          args.project_name,
        "tender_type":           args.tender_type,
        "tender_subject":        args.project_name,
        "tender_subject_brief":  args.project_name,
        "description_of_procurement": args.project_name,   # PART A alias
        "subject_of_procurement":     args.project_name,
        "scope_of_work":             args.project_name,
        "name_of_work":              args.project_name,
        "duration_months":       str(args.duration_months),
        "completion_period":     f"{args.duration_months} months",
        "contract_duration":     f"{args.duration_months} months",
        "n_years":               str(max(1, round(args.duration_months / 12))),
        # ── Currency formats (PART B: raw values; clauses add Rs.) ────
        "ecv_cr":                f"{ecv_cr:.2f}",
        "ecv_rupees":            ecv_rupees_raw,            # raw, no Rs.
        "estimated_value":       ecv_rupees_raw,            # PART B: raw (was "Rs. 85.00 Crore (Rs.85,...)")
        "estimated_value_cr":    f"{ecv_cr:.2f}",           # raw number e.g. "85.00"
        "estimated_value_crore": f"{ecv_cr:.2f}",
        "estimated_contract_value": ecv_rupees_raw,         # PART B: raw
        "ecv":                   ecv_rupees_raw,            # PART B: raw
        "contract_value":        ecv_rupees_raw,            # PART B: raw
        # State + class
        "tender_state":          "Andhra Pradesh" if is_ap else "Other",
        "state":                 "Andhra Pradesh" if is_ap else "Other",
        "bidder_class":          contractor_class,
        "contractor_class":      contractor_class,
        # ── AP regulatory values (PART B: raw % values, no '%' suffix) ─
        "emd_percentage":        f"{emd_stage1_pct + emd_stage2_pct}",   # "2.5"
        "emd_pct":               f"{emd_stage1_pct + emd_stage2_pct}",   # "2.5"
        "emd_stage1_pct":        f"{emd_stage1_pct}",
        "emd_stage2_pct":        f"{emd_stage2_pct}",
        "emd_stage1_amount":     format_inr_indian(ecv_inr * emd_stage1_pct / 100),
        "emd_stage2_amount":     format_inr_indian(ecv_inr * emd_stage2_pct / 100),
        "emd_total_amount":      format_inr_indian(ecv_inr * (emd_stage1_pct + emd_stage2_pct) / 100),
        "emd_amount":            format_inr_indian(ecv_inr * emd_stage1_pct / 100),
        "pbg_percentage":        f"{pbg_pct}",                  # PART B: raw "10"
        "pbg_pct":               f"{pbg_pct}",                  # raw "10"
        "pbg_amount":            format_inr_indian(ecv_inr * pbg_pct / 100),
        "bid_validity_days":     str(bid_validity_days),
        "bid_validity":          f"{bid_validity_days} days",
        "dlp_months":            str(dlp_months),
        "dlp_years":             str(max(1, dlp_months // 12)),
        "dlp":                   f"{dlp_months} months ({dlp_months // 12} years)",
        "defect_liability_period": f"{dlp_months} months from the date of completion of the work",
        "abc_multiplier":        str(abc_multiplier),
        "ma_percentage":         "10",                          # PART B: raw "10"
        "ma_pct":                "10",
        "mobilisation_advance":  "10",                          # PART B: raw "10"
        # Solvency framework (per AP-GO-089)
        "solvency_threshold":    f"{ecv_cr * 0.10:.4f}",        # PART B: raw crore figure
        "solvency_validity":     "1 year from date of issue",
        # Force Majeure
        "fm_notice_days":        "30",
        "fm_termination_window_days": "120",
        # LD
        "ld_rate_per_week":      "0.5",                         # PART B: raw "0.5"
        "ld_rate_pct":           "0.5",
        "ld_cap_pct":            "10",                          # PART B: raw "10"
        # Dates
        "today":                 today.strftime("%d/%m/%Y"),
        "tender_publication_date": today.strftime("%d/%m/%Y"),
        "prebid_date":           pre_bid.strftime("%d/%m/%Y"),
        "pre_bid_meeting_date":  pre_bid.strftime("%d/%m/%Y"),
        "clarification_deadline": pre_bid.strftime("%d/%m/%Y"),  # PART A alias
        "bid_due_date":          bid_open.strftime("%d/%m/%Y"),
        "bid_submission_deadline": bid_open.strftime("%d/%m/%Y"),
        "tech_open_date":        bid_open.strftime("%d/%m/%Y"),
        "fin_open_date":         fin_open.strftime("%d/%m/%Y"),
        "financial_bid_opening_date": fin_open.strftime("%d/%m/%Y"),
        "loa_date":              loa_date.strftime("%d/%m/%Y"),
        # Officers
        "contact_officer":       args.contact_officer or "The Officer",
        "contact_email":         args.contact_email or "[contact email]",
        # PART C — procurement_mode (default OTE; selector uses this)
        "procurement_mode":      getattr(args, "procurement_mode", None) or "OTE",
        # AP regulatory anchors
        "ap_go_emd":             "GO Ms No 50 dt 12-04-2024",
        "ap_go_pbg":             "GO Ms No 175 dt 25-03-2024",
        "ap_go_dlp":             "GO Ms No 84 (AP Works DLP — 2 years)",
        "ap_go_abc":             "GO Ms No 62 (AP Works ABC formula M=2)",
        "ap_go_solvency":        "GO MS No 129 dt 05-10-2015",
        "ap_go_class":           "GO Ms No 94 dt 01-07-2003",
        "currency":              "INR",
        # ── Phase 2.7 COMMIT 1: AP-State procurement constants ──────
        # Project-invariant AP-State values that the gap-analysis
        # surface as MISSING_BLOCKING. Filling them here moves them
        # out of the [TO BE SPECIFIED] fallback path.
        # Source: GO Ms No 94/2003, AP-GO-001 to AP-GO-100, MPW 2022.
        "tender_premium_ceiling_pct":    "10",
        "discount_acceptance_pct":       "15",
        "discount_tender_threshold_pct": "15",
        "retention_threshold_pct":       "85",
        "deadband_pct":                  "5",
        "judicial_preview_threshold_cr": "100",
        "lead_member_min_equity_pct":    "26",
        "min_consortium_lead_equity_pct":"26",
        "tender_threshold":              "2,500",
        "eproc_works_threshold":         "1,00,000",
        "eproc_material_threshold":      "1,00,000",
        "lte_threshold":                 "5,00,000",
        "lte_threshold_value":           "5,00,000",
        "ma_threshold":                  "1,00,00,000",     # Rs.1 cr
        "mobilisation_advance_threshold_cr": "1",
        "mobilisation_advance_pct":      "10",
        "nac_pct":                       "0.10",
        "reverse_tender_threshold_cr":   "1",
        "mclr_rate_pct":                 "9",
        "performance_security_validity_days": "60",
        "bid_security_validity_days":    "180",
        "contract_signing_days":         "14",
        "works_ip_threshold":            "50,00,00,000",    # Rs.50 cr
        "experience_years":              "10",
        "past_perf_years":               "7",
        "turnover_lookback_years":       "5",
        "turnover_multiplier":           "2",
        "construction_share_pct":        "50",
        "two_proj_pct":                  "50",
        "three_proj_pct":                "40",
        "alb_trigger_pct":               "80",
        "validity_months":               "3",
        "large_contract_min_days":       "30",
        "large_contract_min_multiplier": "1.5",
        "representation_window_days":    "7",
        "pol_component_pct":             "15",
        "pig_iron_factor":               "0.96",
        "pig_iron_factor_default":       "0.96",
        "pa_threshold_lakh":             "40",
        "pa_min_months":                 "6",
        # CCI / regulatory references
        "cci_registration_no":           "Not applicable",
        "cartel_debarred":               "None — clean tender",
        # Officer-default-to-NIL fields (officer overrides if applicable)
        "coi_disclosure":                "NIL",
        "previous_transgression_list":   "NIL",
        "download_fee_status":           "NIL — no cost charged for downloaded documents",
        # Department-derived references
        "tender_authority":              department_full,
        "authority_office":              args.department_office or f"Managing Director, {department_acronym}, {('Amaravati' if is_ap else department_full)}",
        # NIT-number aliases used by some seeded clauses
        "bid_ref":                       nit_number,
        "bid_reference":                 nit_number,
        # Date aliases (already-computed values — exposing under the
        # names the seeded clauses use)
        "bid_opening_date":              bid_open.strftime("%d/%m/%Y"),
        "tech_opening_date":             bid_open.strftime("%d/%m/%Y"),
        # ── Phase 2.7 COMMIT 1b: additional AP-State defaults found
        # by gap-analysis after the first round (12 more constants).
        # Source: APSS, MPW 2022, AP-FC standard penalty schedule.
        "lte_invitation_count":          "5",                # APSS default LTE bidder list size
        "nit_district_threshold_lakh":   "50",               # district-publication threshold
        "labour_default_penalty":        "500",              # per-day labour-law non-compliance penalty
        "minor_min_penalty":             "10,000",
        "minor_max_penalty":             "10,00,000",
        "major_min_penalty":             "10,00,000",
        "major_max_penalty":             "50,00,000",
        "rectification_max_days":        "15",
        "program_review_days":           "15",
        "forward_window_months":         "3",
        "photo_min_count":               "10",               # site-progress photo minimum
        "video_min_minutes":             "5",
    }
    return pmap


def substitute_placeholders(
    text: str,
    pmap: dict[str, Any],
    clause_params: list[dict] | None = None,
    *,
    section: str = "",
) -> tuple[str, list[str], list[str]]:
    """Resolve ``{{name}}`` placeholders.

    Resolution order (Step-1 PART A change, 2026-05-08):
      1. pmap[name] — wired from facts via build_parameter_map()
      2. (REMOVED) per-clause `parameters[].example` fallback —
         this was the root cause of "Department of XYZ", "Mr. ABC",
         "PROC/2025/W/001", "M/s ABC Pvt Ltd" leaking into rendered
         documents. Those strings were *example* values for human
         template authors, not substitution defaults.
      3. Section-aware blank fallback:
           - Forms section (``Volume-I/Section-5/Forms``):
             render an underline placeholder ``___________________``
             so bidders can fill the form by hand or in Word.
           - Anywhere else: render ``[TO BE SPECIFIED]`` so a
             procurement officer reviewing the draft sees the gap
             plainly and can correct it before publication.

    The legacy ``[[FILL: name]]`` marker is retained internally on
    the ``unresolved`` list for telemetry only — it no longer
    appears in rendered text.
    """
    is_forms = "Forms" in (section or "")
    by_name  = {p["name"]: p for p in (clause_params or [])
                if isinstance(p, dict) and p.get("name")}
    substituted: list[str] = []
    unresolved:  list[str] = []

    def _replace(m: re.Match) -> str:
        nm = m.group(1)
        if nm in pmap:
            substituted.append(nm); return str(pmap[nm])
        # Param exists on the clause but no fact-driven mapping →
        # blank for the officer / bidder to fill, NOT the seeded
        # example value.
        unresolved.append(nm)
        if nm in by_name:
            return "_" * 25 if is_forms else "[TO BE SPECIFIED]"
        # Truly unknown placeholder — same blank treatment, with
        # the name retained as a hint via [[FILL]] in dev mode.
        return "_" * 25 if is_forms else "[TO BE SPECIFIED]"

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
    Left column = clause title (clean, no internal metadata). Right
    column = parameter-substituted clause text.

    Step-1a fix (2026-05-08): the prior version emitted
    ``\\`{cid}\\` · {title} · _{status}_ · firing: {rule_ids}`` in the
    left column. That metadata is internal — auditors and procurement-
    officer reviewers should never see clause IDs, status tags, or
    firing-rule references in the published tender. The audit trail
    still lives on each ValidationFinding row in the KG; the rendered
    document is now clean.
    """
    rows: list[tuple[str, str]] = []
    for c in clauses:
        if drop_excluded and c.get("status") == "EXCLUDED":
            continue
        title = c.get("title") or "(untitled)"
        text, _, unres = substitute_placeholders(
            c.get("text_english") or "",
            pmap, c.get("parameters") or [],
            section=c.get("position_section") or "",
        )
        # Compress whitespace for readability inside table cells
        text = re.sub(r"\s+", " ", text).strip()
        # Bug-A audit-warning marker (closing sub-finding from 63d8e7f):
        # in table cells we can't use blank-line separation, so prepend
        # the italic marker followed by a hard line break (`<br>`) so it
        # renders as its own visual line above the body inside the cell.
        marker = _format_audit_warning_marker(c.get("_audit_warning"))
        if marker:
            text = f"{marker}<br><br>{text}"
        # Title only — no clause_id, no status pill, no firing rules.
        rows.append((title, text))
    if not rows:
        return "_(no clauses applicable to this tender configuration)_\n"
    lines = [f"| {left_header} | {right_header} |", "|---|---|"]
    for label, value in rows:
        clean_value = (str(value) or "").replace("\n", "<br>").replace("|", "\\|")
        clean_label = (str(label) or "").replace("\n", " ").replace("|", "\\|")
        lines.append(f"| {clean_label} | {clean_value} |")
    return "\n".join(lines) + "\n"


def render_clauses_as_sections(
    clauses: list[dict],
    pmap: dict,
    drop_excluded: bool = True,
    heading_level: int = 3,
) -> str:
    """Render clauses as standalone H{heading_level} sections preserving
    paragraph / prose structure of the source clause text.

    Used for GCC, SCC, and Scope sections where Tier-1 validators
    (LD, PVC, IP, MA) expect paragraph-form text — table-cell rendering
    collapses whitespace and breaks the BGE-M3 retrieval expectations
    encoded in those validators (paragraphs, not table rows).

    Step-1a fix (2026-05-08): the prior version emitted a
    ``\\`{cid}\\` · _{status}_ · firing: {rule_ids}`` meta line above
    every clause body. That metadata is internal — auditors and
    procurement-officer reviewers should never see clause IDs, status
    tags, or firing-rule references in the published tender.
    The audit trail still lives on each ValidationFinding row in the
    KG; the rendered document is now clean prose only."""
    if not clauses:
        return "_(no clauses applicable to this tender configuration)_\n"
    blocks: list[str] = []
    hashes = "#" * heading_level
    visible = 0
    for c in clauses:
        if drop_excluded and c.get("status") == "EXCLUDED":
            continue
        visible += 1
        title  = c.get("title") or c.get("clause_id") or "(untitled)"
        text, _, _ = substitute_placeholders(
            c.get("text_english") or "",
            pmap, c.get("parameters") or [],
            section=c.get("position_section") or "",
        )
        # Preserve paragraph structure — only normalise CRLF + trim trailing
        text = (text or "").replace("\r\n", "\n").rstrip()
        # Bug-A audit-warning marker (closing sub-finding from 63d8e7f):
        # if the clause was conservatively included via the
        # all-MISSING-rule_ids fallback, surface a single-line italic
        # marker between the heading and the body so an officer
        # reviewing the rendered DOCX sees which rule_ids are pending
        # seed. Plain markdown italic; blank-line separation on both
        # sides for clean rendering.
        marker = _format_audit_warning_marker(c.get("_audit_warning"))
        if marker:
            blocks.append(f"{hashes} {title}\n\n{marker}\n\n{text}\n")
        else:
            # Heading + body only. No clause_id, no status pill, no
            # firing rules. Title is human-readable; that's all the
            # document needs to expose.
            blocks.append(f"{hashes} {title}\n\n{text}\n")
    if visible == 0:
        return "_(no clauses applicable to this tender configuration)_\n"
    return "\n".join(blocks) + "\n"


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


def render_bds_thematic(args: argparse.Namespace, facts: dict, pmap: dict) -> str:
    """Render the BDS (Section II) as themed H2 sub-sections matching real
    AP corpus structure (e.g. real JA's BDS at L435–545).

    Pattern (mirroring real JA):
      ## **A. General**                    — table for simple meta rows
      ## **B. Eligibility and Bid Preparation** — table for eligibility/format rows
      ## **The clause shall be read as**   — prose paragraphs for the
                                              complex clause re-statements
                                              (BV / EMD / PBG). Each prose
                                              paragraph carries an explicit
                                              "ITB X.Y — …" reference so
                                              the kg_builder content-based
                                              section classifier reads ITB
                                              and the validators retrieve
                                              the numeric anchor at the
                                              cited line.
      ## **C. Submission and Opening of Bids** — table for date/venue rows
      ## **D. Award of Contract**           — table for award-related rows

    Replaces the prior single-table render that pooled all ~21 rows into
    one Section node typed `Datasheet`. See LESSONS_LEARNED L57.
    """
    contractor_class = (
        _ap_class_for_ecv(float(args.ecv_cr)) if args.is_ap_tender else "ANY"
    )

    # ── A. General — simple meta rows ────────────────────────────
    a_general: list[tuple[str, str]] = [
        ("ITB 1.1",
         f"NIT No: {pmap['nit_number']}, Dt:{pmap['issue_date']}. "
         f"Name of Work: {pmap['project_name']} including DLP of "
         f"{pmap['dlp_years']} Years."),
        ("ITB 1.2",
         "Definitions added: \"ES\" = Environmental and Social; "
         "\"SEA\" = Sexual Exploitation and Abuse; \"SH\" = Sexual Harassment "
         "(per WB / ADB safeguards)."),
    ]

    # ── B. Eligibility and Bid Preparation ───────────────────────
    b_eligibility: list[tuple[str, str]] = [
        ("ITB 4.1",
         f"The Bidder shall have a {contractor_class} Class Civil registration "
         f"with the Government of Andhra Pradesh per {pmap['ap_go_class']}."),
        ("ITB 4.1 (a)",
         "**Joint Venture: Allowed**. Maximum number of members in the JV: 2. "
         "All members shall be jointly and severally liable for execution "
         "per ITB 4.1 (f). _(Compliant with MPG-279 — the bidding doc shall "
         "not arbitrarily exclude eligible bidders.)_"),
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
    ]

    # ── The clause shall be read as — prose, complex clauses ──────
    bv_prose = (
        f"The clause shall be read as: ITB 18.1 — Bid validity period shall be "
        f"{pmap['bid_validity_days']} days from the date of bid submission, "
        f"per AP-GO-067 (minimum 90 days for AP Works tenders)."
    )
    emd_prose = (
        f"The clause shall be read as: ITB 19.1 — Bid Security (EMD): "
        f"{pmap['emd_stage1_pct']}% of ECV = Rs.{pmap['emd_stage1_amount']} "
        f"at bid stage; additional {pmap['emd_stage2_pct']}% of ECV = "
        f"Rs.{pmap['emd_stage2_amount']} at agreement signing, per "
        f"{pmap['ap_go_emd']}. Acceptable forms: NEFT/RTGS, irrevocable "
        f"Bank Guarantee, Insurance Surety Bond, or e-Bank Guarantee from "
        f"any Government / Nationalised / Public Sector / Scheduled Bank, "
        f"valid for 180 days from the last bid-submission date."
    )
    pbg_prose = (
        f"The clause shall be read as: ITB 42.1 — Performance Security (PBG): "
        f"{pmap['pbg_pct']}% of contract value = Rs.{pmap['pbg_amount']}, "
        f"per {pmap['ap_go_pbg']}. The PBG shall be valid until 60 days "
        f"after the completion of the Defects Liability Period."
    )

    # ── C. Submission and Opening of Bids ─────────────────────────
    c_submission: list[tuple[str, str]] = [
        ("ITB 22.1",
         f"Bid Submission Due Date and time: {pmap['bid_due_date']} @ 15:00 Hrs."),
        ("ITB 25.1",
         f"Bid opening: {pmap['bid_due_date']} @ 16:00 Hrs at "
         f"{pmap['department_office']}."),
    ]

    # ── D. Award of Contract ──────────────────────────────────────
    d_award: list[tuple[str, str]] = [
        ("ITB 30",
         "Non-Material and Non-Conformities **shall not be permitted**."),
        ("ITB 34",
         "Sub-contracting limit: total value of works to be awarded on "
         "sub-contracting **shall not exceed 50% of the contract value**. "
         "Sub-contracting any part requires written employer permission."),
        ("ITB 43",
         f"Procurement-related Complaint procedure: complaints in writing to "
         f"Addl. Commissioner (Admin), {pmap['department_full_name']}. "
         f"Appellate Authority: Secretary, MA&UD, Government of Andhra Pradesh. "
         f"Appeal within 7 days of decision; written decision within 15 days "
         f"of hearing."),
    ]

    def _table_block(title: str, rows: list[tuple[str, str]]) -> str:
        if not rows:
            return ""
        out: list[str] = [f"## **{title}**", "",
                          "| ITB Clause Ref | BDS Override |", "|---|---|"]
        for k, v in rows:
            out.append(f"| **{k}** | {v} |")
        out.append("")
        return "\n".join(out)

    parts: list[str] = []
    parts.append(_table_block("A. General", a_general))
    parts.append(_table_block("B. Eligibility and Bid Preparation", b_eligibility))
    parts.append("## **The clause shall be read as**")
    parts.append("")
    parts.append(bv_prose)
    parts.append("")
    parts.append(emd_prose)
    parts.append("")
    parts.append(pbg_prose)
    parts.append("")
    parts.append(_table_block("C. Submission and Opening of Bids", c_submission))
    parts.append(_table_block("D. Award of Contract", d_award))
    return "\n".join(p for p in parts if p) + "\n"


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


# ── Phase 2.4: fixed-skeleton loader + marker substitution ───────────

# {{DELETED_IF_DOMESTIC}}…{{/DELETED_IF_DOMESTIC}} blocks. When the
# project is NOT WB-funded, the entire body between these markers is
# replaced with the literal "DELETED" (the AP-State convention for
# clauses that don't apply to a specific tender). When WB-funded, only
# the markers themselves are stripped — content is kept.
_DELETED_IF_DOMESTIC_RE = re.compile(
    r"\{\{DELETED_IF_DOMESTIC\}\}.*?\{\{/DELETED_IF_DOMESTIC\}\}",
    re.DOTALL,
)
_PROC_AUTH_RE     = re.compile(r"\{\{procuring_authority\}\}")
_HTML_COMMENT_RE  = re.compile(r"<!--.*?-->", re.DOTALL)
# The skeleton's own `## Section — ITB` / `## Section — GCC` H2 banner
# is redundant once the slot is embedded inside the parent skeleton
# (which already supplies the section heading). Strip it.
_SECTION_BANNER_RE = re.compile(r"^##\s*Section\s*—\s*\w+\s*$", re.M)


def _load_fixed_skeleton(filename: str, pmap: dict, *, is_wb_funded: bool) -> str:
    """Load a fixed-skeleton file from templates/sections/ and apply
    marker substitutions:

      • {{DELETED_IF_DOMESTIC}}…{{/DELETED_IF_DOMESTIC}} blocks →
        replaced with "DELETED" if NOT WB-funded; otherwise contents
        kept (markers stripped).
      • {{procuring_authority}} → pmap['department'] (with safe fallback
        to department_full_name or "[Department]").
      • <!-- … --> HTML comments → stripped.
      • Top-level "## Section — XXX" banner → stripped (the parent
        skeleton already supplies the section heading).

    Returns the cleaned markdown ready to embed as a slot's body.
    """
    path = SKELETONS_DIR / filename
    if not path.exists():
        return f"_(fixed-skeleton file missing: {filename})_\n"
    text = path.read_text(encoding="utf-8")

    # 1) DELETED_IF_DOMESTIC blocks
    if is_wb_funded:
        text = text.replace("{{DELETED_IF_DOMESTIC}}",  "")
        text = text.replace("{{/DELETED_IF_DOMESTIC}}", "")
    else:
        text = _DELETED_IF_DOMESTIC_RE.sub("DELETED", text)

    # 2) procuring_authority slot
    dept = (pmap.get("department")
            or pmap.get("department_acronym")
            or pmap.get("department_full_name")
            or "[Department]")
    text = _PROC_AUTH_RE.sub(str(dept), text)

    # 3) HTML comments
    text = _HTML_COMMENT_RE.sub("", text)

    # 4) Strip the section banner the skeleton starts with
    text = _SECTION_BANNER_RE.sub("", text)

    # 5) Tidy excess whitespace caused by the substitutions
    text = re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"
    return text


# ── Step-1d: bid-validity literal override ────────────────────────────
#
# The placeholder leakage that motivated the original _post_substitute
# table (Department of XYZ, Mr. ABC, PROC/2025/W/001, M/s ABC Pvt Ltd,
# 2.5%%, Rs.Rs., …) is now fixed at source — see PART A of
# substitute_placeholders() and PART B of build_parameter_map().
#
# This residual pass handles the ONE case that genuinely lives in
# clause_templates.text_english and not in parameters[].example:
# CLAUSE-WORKS-BID-VALIDITY-001's hardcoded "120 days" sentence,
# which contradicts the regulator-anchored 90-day BDS row. Until that
# clause's text_english gets recurated, this swap keeps the document
# internally consistent.
def _post_substitute(body: str, pmap: dict) -> str:
    bid_validity_days = pmap.get("bid_validity_days") or "90"
    procurement_mode  = pmap.get("procurement_mode")  or "OTE"

    pairs: list[tuple[str, str]] = [
        # Bid validity contradiction (longer first)
        (
            "Bid validity for this tender: 120 days; type: OTE — extended to 120 days due to multi-package coordination",
            f"Bid validity for this tender: {bid_validity_days} days; type: {procurement_mode}",
        ),
        (
            "Bid validity for this tender: 120 days",
            f"Bid validity for this tender: {bid_validity_days} days",
        ),
        (
            "type: OTE — extended to 120 days due to multi-package coordination",
            f"type: {procurement_mode}",
        ),
        # Defence-in-depth: if anything still slips Rs.Rs. or %% past
        # the source-level fixes (e.g. a clause body literal we missed),
        # collapse the duplicates so the document never ships with
        # those typos.
        ("Rs.Rs.",  "Rs."),
        ("Rs. Rs.", "Rs."),
        ("%%",      "%"),
    ]

    out = body
    for needle, repl in pairs:
        if needle in out:
            out = out.replace(needle, repl)
    return out


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
    # Stash args inside pmap so slot generators can pick up CLI flags
    # like --scope-description / --scope-file. Removed from pmap before
    # the {{name}} substitution pass.
    pmap = dict(pmap, __args=args)
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
    # Phase 2.4: when USE_FIXED_SKELETON_ITB is on, use the canonical
    # SBD ITB skeleton (templates/sections/itb_fixed.md) instead of the
    # clause-fragment table — gives proper numbered hierarchy 1./1.1/(a).
    is_wb_funded = pmap.get("funding_source") in WB_FUNDED_SOURCES
    if USE_FIXED_SKELETON_ITB:
        slots["itb_body"] = _load_fixed_skeleton(
            "itb_fixed.md", pmap, is_wb_funded=is_wb_funded,
        )
    else:
        slots["itb_body"] = render_clauses_as_table(
            by_section.get("Volume-I/Section-2/ITB", []),
            pmap,
            left_header="ITB Clause",
            right_header="Standard Clause Body",
        )

    # BDS — themed H2 sub-sections + prose for complex clauses (Change B,
    # see render_bds_thematic). Replaces the prior single-table render
    # that pooled all rows into one Datasheet-classified Section node.
    # build_bds_overrides remains exported for back-compat / external
    # callers but is no longer called from the slot.
    slots["bds_table"] = render_bds_thematic(args, facts, pmap)

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

    # Section V — Fraud and Corruption
    # Phase 2.4: when USE_FIXED_SKELETON_FRAUD is on, pick the
    # canonical SBD Section V text by funding_source — wb_funded.md
    # (long, includes WB Anti-Corruption Guidelines verbatim) for MDB
    # tenders, or domestic.md (short Indian-domestic version) for
    # State/Central/PPP/Mixed-funded ones.
    if USE_FIXED_SKELETON_FRAUD:
        fraud_filename = ("fraud_wb_funded.md" if is_wb_funded
                          else "fraud_domestic.md")
        slots["fraud_corruption"] = _load_fixed_skeleton(
            fraud_filename, pmap, is_wb_funded=is_wb_funded,
        )
    else:
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

    # Section VI — Works' Requirements
    # Resolution chain:
    #   (a) --scope-file path → file contents verbatim
    #   (b) --scope-description CLI string → verbatim
    #   (c) placeholder + Tier-1 framework clauses (Scope / Spec / BOQ)
    args_obj = pmap.get("__args")  # passed in via render_with_skeleton wrapper
    scope_text_blocks: list[str] = []
    scope_file = getattr(args_obj, "scope_file", None) if args_obj else None
    scope_desc = getattr(args_obj, "scope_description", None) if args_obj else None
    if scope_file:
        try:
            scope_text_blocks.append(Path(scope_file).read_text(encoding="utf-8").strip())
        except FileNotFoundError:
            scope_text_blocks.append(f"_(scope-file not found: {scope_file})_")
    elif scope_desc:
        scope_text_blocks.append(scope_desc.strip())
    else:
        scope_text_blocks.append(
            "**[SCOPE OF WORK TO BE SPECIFIED BY PROCUREMENT OFFICER]**\n\n"
            "_The procurement officer shall describe here the technical scope of "
            "the Works to be performed under this Contract — including the buildings "
            "/ structures / facilities to be constructed, the volumes (built-up area, "
            "earthwork quantity, concrete grade, structural steel tonnage, etc.), "
            "the architectural and engineering services included (civil, structural, "
            "MEP, finishes, external development, landscaping), and any "
            "project-specific constraints (site conditions, ground-water table, "
            "seismic zone, fire-rating requirements, accessibility / Green "
            "Building / IGBC compliance, etc.). Pass via `--scope-description` "
            "or `--scope-file` to the drafter to populate this section automatically._\n"
        )
    # Append Tier-1 framework clauses (statutory permits, repair caps,
    # BOQ tender-percentage rule) so the section is not just a single
    # placeholder
    works_clauses = (by_section.get("Volume-II/Section-3/Scope", [])
                   + by_section.get("Volume-II/Section-4/Specifications", [])
                   + by_section.get("Volume-II/Section-5/BOQ", []))
    # Statutory Framework clauses rendered as paragraph sections (not
    # table rows) so that prose-form retrieval by Tier-1 validators
    # works against the original whitespace structure.
    framework_sections = render_clauses_as_sections(
        works_clauses, pmap, heading_level=4,
    )
    slots["works_requirements"] = (
        "### Project Scope\n\n"
        + "\n\n".join(scope_text_blocks) + "\n\n"
        "### Statutory Framework (applicable Tier-1 clauses)\n\n"
        + framework_sections
    )

    # Section VII — GCC body
    # Phase 2.4: when USE_FIXED_SKELETON_GCC is on, use the canonical
    # SBD GCC skeleton (templates/sections/gcc_fixed.md) — proper
    # numbered hierarchy, {{procuring_authority}} substituted.
    # Validators continue to work because the skeleton text preserves
    # all the clauses they retrieve (PBG, EMD, LD, MA, etc.).
    if USE_FIXED_SKELETON_GCC:
        slots["gcc_body"] = _load_fixed_skeleton(
            "gcc_fixed.md", pmap, is_wb_funded=is_wb_funded,
        )
    else:
        # Render GCC clauses as paragraph sections (LD / PVC / IP / MA
        # validators expect paragraph-form prose; table-cell whitespace
        # compression breaks BGE-M3 retrieval).
        slots["gcc_body"] = render_clauses_as_sections(
            by_section.get("Volume-II/Section-1/GCC", []),
            pmap, heading_level=3,
        )

    # Section VIII — PCC = SCC overrides
    # Same paragraph-form treatment as GCC — SCC overrides reference
    # parent GCC clauses by number, so prose preservation matters.
    slots["pcc_overrides"] = render_clauses_as_sections(
        by_section.get("Volume-II/Section-2/SCC", []),
        pmap, heading_level=3,
    )

    # Section IX — Contract Forms (Phase 2.7 COMMIT 3)
    #
    # In a real AP Standard Bidding Document, Section IX is a 7-form
    # INDEX TABLE — not a policy-clause dump. The actual form bodies
    # (Letter of Acceptance, PBG Proforma, Contract Agreement) live
    # in Section IV Bidding Forms where they already render correctly.
    # Section IX just enumerates them.
    #
    # Previously this slot was hardwired to render
    # `by_section.get("Volume-I/Section-1/NIT", [])`, which mistakenly
    # routed 17 NIT-section policy clauses (AP Judicial Preview,
    # Mandatory CPPP Publication, AP Reverse Tendering, RTI
    # Disclosure, Mode of Procurement, etc.) into Section IX as a
    # policy dump. None of those belong in Section IX of a bid
    # document — they're internal procurement-officer reference
    # material. Dropping them from the rendered output (they remain
    # selectable in clause_templates for officer reference if a
    # future schedule/annex needs them).
    slots["contract_forms"] = (
        "The forms listed below shall be used for the contractual instruments noted. "
        "Each form's body is provided in Section IV — Bidding Forms.\n\n"
        "| S.No | Form Name |\n"
        "|---|---|\n"
        "| 1.   | Letter of Acceptance |\n"
        "| 2.   | Contract Agreement |\n"
        "| 3.   | Performance Bank Guarantee — Option 1: Bank Guarantee |\n"
        "| 4.   | Performance Bank Guarantee — Option 2: Insurance Surety Bond |\n"
        "| 5.   | Environmental & Social Performance Security Form |\n"
        "| 6.   | Appendix — Code of Conduct (E&S) |\n"
        "| 7.   | Anti-Corruption Guidelines |\n"
    )

    # Pass 1: substitute slot markers
    def _slot_replace(m: re.Match) -> str:
        nm = m.group(1)
        return slots.get(nm, f"_(slot {nm} not implemented)_\n")
    body = _SLOT_RE.sub(_slot_replace, skeleton)

    # Post-pass: NIT "Procurement Policy References" subsection
    #
    # Bug B (Phase 2.7): COMMIT 3 deleted the slot reader for
    # Volume-I/Section-1/NIT clauses without re-routing them. Any
    # selected clause whose position_section starts with
    # "Volume-I/Section-1/NIT" — including the 3 mandatory AP Judicial
    # Preview clauses for tenders ≥ Rs.100cr — was selected but never
    # rendered, producing a silent gap.
    #
    # Real AP SBDs treat these as a "Procurement Policy References"
    # annexure inside the NIT block — statutory framework citations
    # (JP mandate, RTI, AP reverse tendering, e-proc thresholds,
    # MII/IP framework refs, mode-of-procurement). They sit AFTER the
    # NIT body 25-row metadata table and BEFORE PART 1 (ITB).
    #
    # This is a true post-pass: it runs after slot substitution and
    # introduces no new <<SLOT:…>> marker — preserving the slot dict
    # as a section-level placement abstraction.
    nit_policy_refs = [c for c in selected
                       if c.get("status") in ("MANDATORY", "MANDATORY-DEFAULT", "ADVISORY")
                       and (c.get("position_section") or "").startswith(
                           "Volume-I/Section-1/NIT")]
    if nit_policy_refs:
        nit_policy_refs.sort(key=lambda c: (c.get("position_order") or 9999,
                                            c.get("clause_id") or ""))
        # Render each clause as a numbered H4 (A.1, A.2, …) with prose body.
        # Bug-A audit-warning marker propagated here too — 4 of the 16
        # all-MISSING-rule_ids victims (MII PPP2017, MII Reciprocity,
        # MII Existing-Policy-Precedence, MDB-Funded-Exemption) render
        # in this loop.
        ref_blocks: list[str] = []
        for i, c in enumerate(nit_policy_refs, start=1):
            title = c.get("title") or c.get("clause_id") or "(untitled)"
            text, _, _ = substitute_placeholders(
                c.get("text_english") or "",
                pmap, c.get("parameters") or [],
                section=c.get("position_section") or "",
            )
            text = (text or "").replace("\r\n", "\n").rstrip()
            marker = _format_audit_warning_marker(c.get("_audit_warning"))
            if marker:
                ref_blocks.append(f"#### A.{i}  {title}\n\n{marker}\n\n{text}\n")
            else:
                ref_blocks.append(f"#### A.{i}  {title}\n\n{text}\n")
        refs_md = "\n".join(ref_blocks)
        block = (
            "\n### Procurement Policy References\n\n"
            "The clauses below are the statutory framework references "
            "applicable to this tender — reproduced here so bidders, "
            "auditors, and the procurement officer have a single anchor "
            "point for every policy mandate cited above. Each reference "
            "carries the rule number(s) it derives from in its body.\n\n"
            + refs_md
            + "\n"
        )
        # Anchor: insert before "## **PART 1 – BIDDING PROCEDURES**".
        # Falls back to a no-op append if the skeleton ever changes its
        # part-1 header — surfaces visibly rather than silently dropping.
        anchor = "## **PART 1 – BIDDING PROCEDURES**"
        if anchor in body:
            body = body.replace(anchor, block + "\n" + anchor, 1)
        else:
            body = body + "\n" + block

    # Add summary stats and strip the __args stash before pass-2.
    pmap = dict(pmap)
    pmap.pop("__args", None)
    pmap["n_clauses_total"]  = str(len(selected))
    pmap["n_mandatory"]      = str(sum(1 for c in selected if c["status"] == "MANDATORY"))
    pmap["n_advisory"]       = str(sum(1 for c in selected if c["status"] == "ADVISORY"))

    # Pass 2: substitute remaining {{name}} placeholders in skeleton-level text
    body, _, _ = substitute_placeholders(body, pmap, [])

    # Pass 3 — Step-1b/1d post-substitute pass.
    # Many seeded clause_templates carry hardcoded example values
    # ("Department of XYZ", "Mr. ABC", "PROC/2025/W/001",
    #  "M/s ABC Pvt Ltd", "Rs.Rs.", "2.5%%", "Bid validity for this
    #  tender: 120 days") that the {{param}} substitution layer does
    # not touch because they are not Jinja markers.
    # Curating the seed text_english is the long-term fix; this pass
    # is a deterministic band-aid so the generated document is clean
    # this week.
    body = _post_substitute(body, pmap)

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
    ap.add_argument("--scope-description", default=None,
                    help="Project-specific scope-of-work description for Section VI. "
                         "Multi-line allowed. If omitted, a placeholder 'TO BE "
                         "SPECIFIED' marker is rendered for the procurement "
                         "officer to fill.")
    ap.add_argument("--scope-file", default=None,
                    help="Path to a markdown file whose contents replace the scope "
                         "description. Useful for long scopes that don't fit on the CLI.")
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
    print(f"  PBG                      : {pmap['pbg_percentage']}% = Rs.{pmap['pbg_amount']}")
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
