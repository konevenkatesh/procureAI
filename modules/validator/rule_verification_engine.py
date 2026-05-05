"""
modules/validator/rule_verification_engine.py

RuleVerificationEngine — the heart of the Validator.

Pipeline:

    raw tender text
         │
         ▼
    ┌────────────────────┐
    │ Stage 1: Classify  │   TenderClassifier → TenderClassification
    │                    │   ParameterCascadeEngine → TenderParameters
    └────────────────────┘
         │
         ▼
    ┌────────────────────┐
    │ Stage 2: Select    │   Fetch TYPE_1_ACTIONABLE rules from Supabase
    │ rules              │   Filter by tender_type / value band / layer
    └────────────────────┘
         │
         ▼
    ┌────────────────────┐
    │ Stage 3: Pattern   │   For each rule, check the document for
    │ match (P1, P2)     │   numeric / presence evidence of violation
    └────────────────────┘
         │
         ▼
    ┌────────────────────┐
    │ Stage 4: Defeats   │   For each finding, check if any AP-State
    │ resolution         │   rule defeats it (and document is AP)
    └────────────────────┘
         │
         ▼
    ┌────────────────────┐
    │ Stage 5: Scoring   │   HARD_BLOCK → 0 / BLOCK
    │                    │   else 100 - 3·warnings - 1·advisories
    └────────────────────┘
         │
         ▼
    ValidationReport
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Literal

import requests
from pydantic import BaseModel, Field

from builder.config import settings
from engines.classifier import TenderClassifier, TenderClassification
from engines.parameter_cascade import (
    ParameterCascadeEngine,
    TenderInputs,
    TenderParameters,
)


# ─────────────────────────────────────────────────────────────────────────────
# Output models
# ─────────────────────────────────────────────────────────────────────────────

OverallStatus = Literal["PASS", "CONDITIONAL", "BLOCK"]


class RuleFinding(BaseModel):
    rule_id: str                                 # primary / representative rule
    rule_text: str
    typology_code: str
    severity: str                                # HARD_BLOCK / WARNING / ADVISORY
    evidence_text: str
    source_clause: str
    layer: str
    defeated_by: list[str] = Field(default_factory=list)
    triggered_rule_ids: list[str] = Field(default_factory=list)
    rules_fired: int = 1                         # how many rules contributed to this finding
    # Line number in the source document where the violating evidence was
    # detected. None for "missing X" violations (the doc *lacks* something
    # — there is no specific violating line). kg_builder uses this to
    # attach VIOLATES_RULE to the Section node containing this line, or
    # to the TenderDocument node when None.
    line_no: int | None = None


class ValidationReport(BaseModel):
    document_name: str
    classification: TenderClassification
    parameters: TenderParameters
    overall_status: OverallStatus
    score: int
    hard_blocks: list[RuleFinding]
    warnings: list[RuleFinding]
    advisories: list[RuleFinding]
    rules_checked: int
    rules_passed: int
    processing_time_ms: int
    timestamp: str


# ─────────────────────────────────────────────────────────────────────────────
# Pattern matchers — one per typology
#
# Each matcher returns a tuple:
#   (violated: bool, evidence_text: str)
# evidence_text is what in the document caused the violation (or "" on pass).
# ─────────────────────────────────────────────────────────────────────────────

# Markdown-escape stripper: pymupdf4llm output often has '2\.5%' (escaped period),
# 'Rs\.5 lakh', etc. Convert these to plain text before pattern matching.
_MD_ESCAPE = re.compile(r"\\([.,;:!?(){}\[\]<>~_*\-])")


def _clean_text(text: str) -> str:
    return _MD_ESCAPE.sub(r"\1", text)


# regex for percentage near a keyword: "EMD … 2.5%" / "2% EMD" / "2.5 percent"
_PCT_NEAR = lambda kw: re.compile(
    rf"(?:{kw})[^\n]{{0,80}}?(\d+(?:\.\d+)?)\s*%"
    rf"|(\d+(?:\.\d+)?)\s*%[^\n]{{0,40}}?(?:{kw})",
    re.IGNORECASE,
)
_DAYS_NEAR = lambda kw: re.compile(
    rf"(?:{kw})[^\n]{{0,80}}?(\d{{1,4}})\s*(?:days|day)",
    re.IGNORECASE,
)


def _char_offset_to_line(text: str, offset: int) -> int:
    """Convert a 0-indexed char offset into a 1-indexed line number."""
    if offset is None or offset < 0:
        return 1
    return text.count("\n", 0, offset) + 1


def _find_percentages_near(text: str, keyword_re: str) -> list[tuple[float, int]]:
    """Return [(value, char_offset), ...] for every percentage within 80
    chars of any keyword match, in document order. Caller can convert
    char_offset → line_no via _char_offset_to_line() to attribute the
    match to a Section node."""
    pat = _PCT_NEAR(keyword_re)
    out: list[tuple[float, int]] = []
    for m in pat.finditer(text):
        for grp in m.groups():
            if grp:
                try:
                    out.append((float(grp), m.start()))
                    break
                except ValueError:
                    continue
    return out


def _find_percentage_near(text: str, keyword_re: str) -> tuple[float, int] | None:
    """Return the (value, char_offset) of the LOWEST percentage near
    keyword. Lowest is the most-protective shortfall reading — if the
    doc says '5%' in one place and '2.5%' in another, the binding value
    is 2.5%. Returns None when no match."""
    vals = _find_percentages_near(text, keyword_re)
    if not vals:
        return None
    # Pick the (value, offset) with the smallest value
    return min(vals, key=lambda v: v[0])


def _find_days_near(text: str, keyword_re: str) -> tuple[int, int] | None:
    """Return (days, char_offset) of the first day-count near keyword."""
    pat = _DAYS_NEAR(keyword_re)
    for m in pat.finditer(text):
        try:
            return (int(m.group(1)), m.start())
        except (ValueError, IndexError):
            continue
    return None


def _first_match_line(text: str, pattern: str) -> int | None:
    """Return 1-indexed line number of the first match for `pattern`
    (case-insensitive). None if no match."""
    m = re.search(pattern, text, re.IGNORECASE)
    if m is None:
        return None
    return _char_offset_to_line(text, m.start())


# Each matcher returns: (violated: bool, evidence_text: str, line_no: int | None)
# line_no is the 1-indexed document line where the violating evidence sits.
# For "missing X" violations (the doc *lacks* something), line_no is None
# — the violation is doc-level, not section-level. kg_builder attaches
# those to the TenderDocument node instead of a Section node.

def _check_emd_shortfall(
    text: str, params: TenderParameters
) -> tuple[bool, str, int | None]:
    found = _find_percentage_near(text, r"emd|earnest\s+money|bid\s+security")
    if found is None:
        return False, "", None
    value, offset = found
    if value < params.emd_percentage:
        return (
            True,
            f"Document states EMD = {value}% (expected ≥ {params.emd_percentage}%)",
            _char_offset_to_line(text, offset),
        )
    return False, "", None


def _check_pbg_shortfall(
    text: str, params: TenderParameters
) -> tuple[bool, str, int | None]:
    found = _find_percentage_near(
        text, r"performance\s+(?:guarantee|security)|pbg"
    )
    if found is None:
        return False, "", None
    value, offset = found
    if value < params.pbg_percentage:
        return (
            True,
            f"Document states PBG = {value}% (expected ≥ {params.pbg_percentage}%)",
            _char_offset_to_line(text, offset),
        )
    return False, "", None


def _check_bid_validity_short(
    text: str, params: TenderParameters
) -> tuple[bool, str, int | None]:
    found = _find_days_near(text, r"bid\s+valid|validity\s+of\s+bid|tender\s+valid")
    if found is None:
        return False, "", None
    value, offset = found
    if value < params.bid_validity_days:
        return (
            True,
            f"Document states bid validity = {value} days (expected ≥ {params.bid_validity_days})",
            _char_offset_to_line(text, offset),
        )
    return False, "", None


def _check_missing_integrity_pact(
    text: str, params: TenderParameters
) -> tuple[bool, str, int | None]:
    if not params.integrity_pact_required:
        return False, "", None
    if re.search(r"integrity\s+pact|integrity-pact|\bIP\s+clause", text, re.IGNORECASE):
        return False, "", None
    # Missing-X violation — no specific line; doc-level
    return True, "No 'integrity pact' clause found in document", None


def _check_missing_anti_collusion(
    text: str, params: TenderParameters
) -> tuple[bool, str, int | None]:
    if re.search(
        r"anti[-\s]?collusion|form\s+3N|no[-\s]?collusion\s+(?:declaration|certificate)",
        text,
        re.IGNORECASE,
    ):
        return False, "", None
    return True, "No 'anti-collusion' / Form 3N declaration found", None


def _check_missing_pvc(
    text: str, params: TenderParameters
) -> tuple[bool, str, int | None]:
    if not params.price_adjustment_applicable:
        return False, "", None
    if re.search(
        r"price\s+(?:adjustment|variation)|pvc|escalation\s+formula",
        text,
        re.IGNORECASE,
    ):
        return False, "", None
    return True, "Price-adjustment clause missing despite value/duration triggering it", None


def _check_criteria_restriction_narrow(
    text: str, params: TenderParameters
) -> tuple[bool, str, int | None]:
    """Detect tender-instance JV / foreign-bidder bans without recorded justification.

    Sentence-level heuristic: within one sentence, an 'entity-being-banned'
    token must co-occur with a 'ban-word' token. Catches both 'No JV
    permitted' and 'Joint Venture ... is not allowed' phrasings.
    """
    JV_TOKENS = (
        "joint venture", " jv ", " jv,", " jv.", "consortium",
        " spv ", " spv,", " spv.", "special purpose vehicle",
    )
    FOREIGN_TOKENS = (
        "from abroad", "foreign contractor", "foreign bidder",
        "foreign firm", "foreign company",
    )
    BAN_TOKENS = (
        "not allowed", "not permitted", "prohibited", "disallowed",
        "barred", "excluded", "is not", "shall not",
        "no jv", "no consortium", "no spv", "no foreign",
    )
    bans: list[str] = []
    first_offset: int | None = None
    text_lower = text.lower()

    # Find first sentence offset that triggers a ban — used as line_no anchor
    for m in re.finditer(r"[^.\n]+", text_lower):
        s = m.group(0)
        if (any(j in s for j in JV_TOKENS) and any(b in s for b in BAN_TOKENS)) or \
           (any(f in s for f in FOREIGN_TOKENS) and any(b in s for b in BAN_TOKENS)):
            if first_offset is None:
                first_offset = m.start()
            if any(j in s for j in JV_TOKENS) and "JV / Consortium / SPV ban" not in bans:
                bans.append("JV / Consortium / SPV ban")
            if any(f in s for f in FOREIGN_TOKENS) and "Foreign-bidder ban" not in bans:
                bans.append("Foreign-bidder ban")

    if not bans:
        return False, "", None
    # NOTE: previous code suppressed when "GO Ms" appeared anywhere in the
    # doc — that fired on every AP-State tender (which all reference GO Ms
    # orders) and effectively neutered the check. Suppression removed.
    line_no = _char_offset_to_line(text, first_offset) if first_offset is not None else None
    return True, " + ".join(bans) + " without recorded justification", line_no


def _check_e_procurement_bypass(
    text: str, params: TenderParameters
) -> tuple[bool, str, int | None]:
    if not params.e_procurement_mandatory:
        return False, "", None
    if re.search(
        r"e[-\s]?procurement|apeprocurement\.gov\.in|gepnic|nicgov|cppp",
        text,
        re.IGNORECASE,
    ):
        return False, "", None
    return True, "E-procurement is mandatory at this value but no e-portal reference found", None


def _check_judicial_preview_bypass(
    text: str, params: TenderParameters
) -> tuple[bool, str, int | None]:
    if not params.judicial_preview_required:
        return False, "", None
    if re.search(r"judicial\s+preview|hon'?ble\s+judge|preview\s+committee", text, re.IGNORECASE):
        return False, "", None
    return True, "Project value triggers AP Judicial Preview (≥ Rs.100 cr) but no preview reference found", None


# Map typology → matcher
_PATTERN_MATCHERS = {
    "EMD-Shortfall":                _check_emd_shortfall,
    "PBG-Shortfall":                _check_pbg_shortfall,
    "Bid-Validity-Short":           _check_bid_validity_short,
    "Missing-Integrity-Pact":       _check_missing_integrity_pact,
    "Missing-Anti-Collusion":       _check_missing_anti_collusion,
    "Missing-PVC-Clause":           _check_missing_pvc,
    "Criteria-Restriction-Narrow":  _check_criteria_restriction_narrow,
    "E-Procurement-Bypass":         _check_e_procurement_bypass,
    "Judicial-Preview-Bypass":      _check_judicial_preview_bypass,
}


# ─────────────────────────────────────────────────────────────────────────────
# Engine
# ─────────────────────────────────────────────────────────────────────────────

class RuleVerificationEngine:
    """End-to-end tender verification pipeline."""

    REST = settings.supabase_rest_url
    HEADERS = {
        "apikey": settings.supabase_anon_key,
        "Authorization": f"Bearer {settings.supabase_anon_key}",
    }

    def __init__(
        self,
        *,
        classifier: TenderClassifier | None = None,
        cascade: ParameterCascadeEngine | None = None,
    ):
        self.classifier = classifier or TenderClassifier()
        self.cascade = cascade or ParameterCascadeEngine()
        self._rules_cache: list[dict] | None = None

    # ── Rule fetching ──

    def _fetch_rules(self) -> list[dict]:
        """Fetch all TYPE_1_ACTIONABLE rules from Supabase. Cached per instance."""
        if self._rules_cache is not None:
            return self._rules_cache
        out: list[dict] = []
        page = 1000
        fr = 0
        select = "rule_id,natural_language,verification_method,pattern_type,severity,typology_code,layer,source_clause,defeats,defeated_by,condition_when"
        while True:
            h = {**self.HEADERS, "Range-Unit": "items", "Range": f"{fr}-{fr + page - 1}"}
            url = f"{self.REST}/rest/v1/rules?select={select}&rule_type=eq.TYPE_1_ACTIONABLE"
            res = requests.get(url, headers=h, timeout=60)
            if res.status_code not in (200, 206):
                break
            chunk = res.json()
            out.extend(chunk)
            if len(chunk) < page:
                break
            fr += page
        self._rules_cache = out
        return out

    # ── Stage 2: rule selection ──

    def _select_rules(
        self,
        rules: list[dict],
        cls: TenderClassification,
        params: TenderParameters,
        tender_facts: dict | None = None,
    ) -> tuple[list[dict], dict[str, str]]:
        """Filter rules to those applicable to this tender.

        Returns:
            applicable: list of rules to pattern-match
            verdicts:   {rule_id → "FIRE"|"UNKNOWN"} — for rules that pass
                        the condition_when gate. UNKNOWN means findings
                        from this rule should be downgraded to ADVISORY.
                        SKIP rules are dropped entirely (not in dict).

        Rule selection:
          1. Layer filter — drop AP-State rules on non-AP tenders.
          2. Typology filter — drop rules whose typology has no matcher
             (the 33 silent typologies; can't be checked at all).
          3. condition_when gate — evaluate the rule's condition against
             tender_facts. SKIP → drop. FIRE → keep as actionable.
             UNKNOWN → keep but mark for downgrade-to-ADVISORY downstream.
        """
        from modules.validator.condition_evaluator import evaluate, Verdict

        is_ap = cls.is_ap_tender
        # Build the fact dict once. Caller may supply tender_facts (the
        # reliable v0.3 path); otherwise we synthesise from classifier
        # output (the unreliable fallback). Reliability is the caller's
        # responsibility to indicate by what it puts in the dict.
        facts: dict = dict(tender_facts or {})
        facts.setdefault("is_ap_tender", bool(is_ap))
        # Don't insert classifier-derived tender_type unless caller already
        # supplied it — classifier output is documented unreliable.

        applicable: list[dict] = []
        verdicts:   dict[str, str] = {}
        skipped_by_layer = skipped_by_typology = skipped_by_condition = 0
        unknown_count = 0
        for r in rules:
            layer = r.get("layer") or ""
            if not is_ap and layer == "AP-State":
                skipped_by_layer += 1
                continue
            if r.get("typology_code") not in _PATTERN_MATCHERS:
                skipped_by_typology += 1
                continue

            cond = (r.get("condition_when") or "").strip()
            if not cond:
                # No condition → vacuously applies (back-compat)
                applicable.append(r)
                verdicts[r["rule_id"]] = Verdict.FIRE.value
                continue
            res = evaluate(cond, facts)
            if res.verdict == Verdict.SKIP:
                skipped_by_condition += 1
                continue
            applicable.append(r)
            verdicts[r["rule_id"]] = res.verdict.value
            if res.verdict == Verdict.UNKNOWN:
                unknown_count += 1

        # Stash counters for the verify() summary log
        self._last_selection_stats = {
            "skipped_by_layer":     skipped_by_layer,
            "skipped_by_typology":  skipped_by_typology,
            "skipped_by_condition": skipped_by_condition,
            "applicable":           len(applicable),
            "applicable_unknown":   unknown_count,
        }
        return applicable, verdicts

    # ── Stage 3: pattern matching ──

    def _match_rule(
        self, rule: dict, text: str, params: TenderParameters
    ) -> RuleFinding | None:
        matcher = _PATTERN_MATCHERS.get(rule["typology_code"])
        if matcher is None:
            return None
        result = matcher(text, params)
        # Backwards-compat: matchers may still return 2-tuples; new ones return 3-tuples
        if len(result) == 2:
            violated, evidence = result
            line_no = None
        else:
            violated, evidence, line_no = result
        if not violated:
            return None
        return RuleFinding(
            rule_id=rule["rule_id"],
            rule_text=(rule.get("natural_language") or "")[:500],
            typology_code=rule["typology_code"],
            severity=rule.get("severity", "WARNING"),
            evidence_text=evidence,
            source_clause=rule.get("source_clause") or "",
            layer=rule.get("layer") or "",
            defeated_by=[],
            line_no=line_no,
        )

    # ── Stage 4: defeasibility resolution ──

    def _resolve_defeats(
        self, findings: list[RuleFinding], rules: list[dict], is_ap: bool
    ) -> list[RuleFinding]:
        """For each finding, check whether any AP-State rule lists this rule_id
        in its `defeats` array. If found AND document is AP, downgrade the
        finding to ADVISORY and record the override."""
        if not is_ap:
            return findings
        # Build {target_rule_id → [overriding_rule_ids]}
        override_map: dict[str, list[str]] = {}
        for r in rules:
            if r.get("layer") != "AP-State":
                continue
            for target in (r.get("defeats") or []):
                override_map.setdefault(target, []).append(r["rule_id"])

        resolved: list[RuleFinding] = []
        for f in findings:
            overriders = override_map.get(f.rule_id, [])
            if overriders:
                f.severity = "ADVISORY"
                f.defeated_by = overriders
                f.evidence_text = (
                    f"{f.evidence_text} — Overridden by AP-State rule(s) "
                    f"{', '.join(overriders)} (acceptable AP departure from central default)"
                )
            resolved.append(f)
        return resolved

    # ── Stage 4b: deduplicate by typology ──

    _SEVERITY_ORDER = {"HARD_BLOCK": 3, "WARNING": 2, "ADVISORY": 1}
    _LAYER_PRIORITY = {"CVC": 4, "GFR": 3, "Central": 2, "AP-State": 1}

    def _dedupe_findings(self, findings: list[RuleFinding]) -> list[RuleFinding]:
        """Group findings by typology_code only — one finding per typology.

        For each group:
          • severity        = highest severity in the group
          • rule_id         = primary (first found, by stable rule_id order)
          • triggered_rule_ids = all unique rule_ids in the group
          • rules_fired     = group size
          • evidence_text   = primary evidence + count + primary authority +
                              defeasibility note if any rule was overridden
        """
        if not findings:
            return findings

        # Bucket by typology only — single finding per typology
        buckets: dict[str, list[RuleFinding]] = {}
        for f in findings:
            buckets.setdefault(f.typology_code, []).append(f)

        merged: list[RuleFinding] = []
        for typology, group in buckets.items():
            # "Primary rule (first one found)" — first by alphabetical rule_id
            primary = sorted(group, key=lambda f: f.rule_id)[0]

            # Highest severity in the group
            top_severity = max(
                (f.severity for f in group),
                key=lambda s: self._SEVERITY_ORDER.get(s, 0),
            )

            triggered_ids = sorted({f.rule_id for f in group})
            all_defeats   = sorted({d for f in group for d in f.defeated_by})
            count         = len(group)
            any_defeated  = any(f.defeated_by for f in group)
            all_defeated  = all(f.defeated_by for f in group)

            # Synthesize a clean evidence text
            base_evidence = primary.evidence_text or "(no evidence text)"
            base_evidence = re.split(r" — Overridden by AP-State", base_evidence)[0]
            evidence_parts = [
                base_evidence,
                f"{count} rule{'s' if count > 1 else ''} require this.",
                f"Primary authority: {primary.source_clause or primary.rule_id}.",
                f"All triggering rules: [{', '.join(triggered_ids)}]",
            ]
            if all_defeated:
                evidence_parts.append(
                    f"All triggering rules overridden by AP-State rule(s): "
                    f"[{', '.join(all_defeats)}] — acceptable AP departure from central default."
                )
            elif any_defeated:
                evidence_parts.append(
                    f"Some triggering rules overridden by AP-State rule(s): "
                    f"[{', '.join(all_defeats)}] — review whether the override applies broadly."
                )
            merged_evidence = " ".join(evidence_parts)

            # Propagate line_no — pick the smallest non-None across the group
            # (deterministic; the earliest-line evidence is typically the
            # operative one for shortfall checks).
            line_nos = [f.line_no for f in group if f.line_no is not None]
            merged_line_no = min(line_nos) if line_nos else None

            merged.append(RuleFinding(
                rule_id=primary.rule_id,
                rule_text=primary.rule_text,
                typology_code=typology,
                severity=top_severity,
                evidence_text=merged_evidence,
                source_clause=primary.source_clause,
                layer=primary.layer,
                defeated_by=all_defeats,
                triggered_rule_ids=triggered_ids,
                rules_fired=count,
                line_no=merged_line_no,
            ))

        # Sort merged findings: severity desc, then typology
        merged.sort(key=lambda f: (
            -self._SEVERITY_ORDER.get(f.severity, 0),
            f.typology_code,
        ))
        return merged

    # ── Stage 5: scoring ──

    @staticmethod
    def _score(
        hard_blocks: list[RuleFinding],
        warnings: list[RuleFinding],
        advisories: list[RuleFinding],
    ) -> tuple[int, OverallStatus]:
        if hard_blocks:
            return 0, "BLOCK"
        score = 100 - (3 * len(warnings)) - (1 * len(advisories))
        score = max(0, score)
        if score >= 85:
            return score, "PASS"
        return score, "CONDITIONAL"

    # ── Public API ──

    def verify_bundle(
        self,
        file_paths: list[str],
        document_name: str = "<bundle>",
        estimated_value_override: float | None = None,
    ) -> ValidationReport:
        """Run a single verification across multiple tender volumes.

        A real-world Indian tender is typically split across several files
        (Volume I = NIT/ITB, Volume II = Scope, Volume III = GCC/SCC, etc.).
        Some clauses (e.g. PBG percentage) live in only one volume but apply
        to the whole tender. This method reads all files, concatenates their
        text with separating headers, and runs `verify` once on the combined
        text — producing a single ValidationReport for the whole bundle.

        Args:
          file_paths: list of file paths whose contents should be concatenated
            and verified as one tender.
          document_name: friendly name for the bundle in the report.
          estimated_value_override: pass when the contract value is known from
            external context (NIT page, AP procurement portal) but isn't
            confidently extractable from the bundled markdown — common when
            BoQ tables didn't survive PDF→markdown conversion.

        Files are read as UTF-8 text. Missing files are skipped with a note
        in the document_name.
        """
        from pathlib import Path
        chunks: list[str] = []
        skipped: list[str] = []
        for fp in file_paths:
            p = Path(fp)
            try:
                text = p.read_text(encoding="utf-8")
            except FileNotFoundError:
                skipped.append(p.name)
                continue
            chunks.append(f"\n\n===== FILE: {p.name} =====\n\n{text}")
        if not chunks:
            raise FileNotFoundError(
                f"None of the bundle files could be read: {file_paths}"
            )
        combined = "\n".join(chunks)

        # If caller supplied an estimated_value override, inject a synthetic
        # PREFER-labeled marker so downstream classifier picks it up. This
        # works because the classifier looks for "estimated contract value"
        # in the 50 chars before a Rs/INR/₹ amount.
        if estimated_value_override is not None:
            ev = estimated_value_override
            if ev >= 1_00_00_000:        # >= 1 crore
                marker = f"\n[BUNDLE METADATA] Estimated Contract Value: Rs.{ev/1e7:.2f} crore.\n"
            elif ev >= 1_00_000:         # >= 1 lakh
                marker = f"\n[BUNDLE METADATA] Estimated Contract Value: Rs.{ev/1e5:.2f} lakh.\n"
            else:
                marker = f"\n[BUNDLE METADATA] Estimated Contract Value: Rs.{ev:.0f}.\n"
            combined = marker + combined

        bundle_name = document_name + (f" (skipped: {', '.join(skipped)})" if skipped else "")
        return self.verify(combined, document_name=bundle_name)

    def verify(
        self,
        document_text: str,
        *,
        document_name: str = "<inline>",
        tender_facts: dict | None = None,
    ) -> ValidationReport:
        """Validate `document_text` and return a ValidationReport.

        `tender_facts`: optional dict of reliable facts for the
        condition_when gate. The v0.3-clean caller (kg_builder /
        validator_graph) loads this from the TenderDocument kg_node so
        only RELIABLE facts go in (e.g. tender_type from the OpenRouter
        extractor, is_ap_tender from the classifier). Keys missing from
        this dict resolve to UNKNOWN at the condition_evaluator stage,
        and findings whose rules failed to fully resolve get downgraded
        from HARD_BLOCK/WARNING to ADVISORY."""
        t0 = time.perf_counter()

        # Strip pymupdf4llm-style markdown escapes ('2\.5%' → '2.5%') so the
        # numeric pattern matchers see clean text. Classification can use either.
        document_text = _clean_text(document_text)

        # Stage 1: classify + cascade
        classification = self.classifier.classify(document_text)
        inputs = TenderInputs(
            department=(classification.department or "Unknown"),
            tender_type=(classification.primary_type
                         if classification.primary_type != "Unknown" else "Works"),
            estimated_value=(classification.estimated_value or 1.0),
            duration_months=(classification.duration_months or 12),
            procurement_method=(
                classification.procurement_method
                if classification.procurement_method in ("Open", "Limited", "Single", "Reverse")
                else "Open"
            ),
            is_ap_tender=classification.is_ap_tender,
        )
        params = self.cascade.compute(inputs)

        # Stage 2: select applicable rules — filter by layer + typology
        # AND condition_when. tender_facts overrides classifier-derived
        # values when provided.
        all_rules = self._fetch_rules()
        applicable, condition_verdicts = self._select_rules(
            all_rules, classification, params, tender_facts=tender_facts,
        )

        # Stage 3: pattern-match each applicable rule.
        # Findings from UNKNOWN-verdict rules are downgraded to ADVISORY:
        # the rule pattern fired, but we couldn't fully verify the
        # context the rule's condition_when expects, so we surface it
        # as advisory rather than confirmed.
        findings: list[RuleFinding] = []
        for rule in applicable:
            f = self._match_rule(rule, document_text, params)
            if f is None:
                continue
            if condition_verdicts.get(rule["rule_id"]) == "UNKNOWN":
                f.severity = "ADVISORY"
                f.evidence_text = (
                    f"{f.evidence_text} — Rule fires but a fact required by "
                    f"condition_when is unavailable; surfaced as ADVISORY."
                )
            findings.append(f)

        # Stage 4a: defeasibility resolution
        findings = self._resolve_defeats(findings, all_rules, classification.is_ap_tender)

        # Stage 4b: deduplicate findings by typology so reviewers see one finding
        # per issue rather than one finding per rule that fired the same issue.
        # rules_passed is computed BEFORE dedup so it reflects the underlying
        # rule count (not the typology-grouped count).
        rules_violated_count = len({f.rule_id for f in findings})
        findings = self._dedupe_findings(findings)

        # Stage 5: score
        hard_blocks  = [f for f in findings if f.severity == "HARD_BLOCK"]
        warnings     = [f for f in findings if f.severity == "WARNING"]
        advisories   = [f for f in findings if f.severity == "ADVISORY"]
        score, status = self._score(hard_blocks, warnings, advisories)

        return ValidationReport(
            document_name=document_name,
            classification=classification,
            parameters=params,
            overall_status=status,
            score=score,
            hard_blocks=hard_blocks,
            warnings=warnings,
            advisories=advisories,
            rules_checked=len(applicable),
            rules_passed=len(applicable) - rules_violated_count,
            processing_time_ms=int((time.perf_counter() - t0) * 1000),
            timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
