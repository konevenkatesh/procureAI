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


def _find_percentages_near(text: str, keyword_re: str) -> list[float]:
    """Return ALL percentage values found within 80 chars of any keyword match,
    in document order. Multi-volume tenders often have several percentage
    references (e.g. 'performance security can be increased to 20%' in one
    volume vs. '2.5% performance security' in another) — callers need to see
    all of them to decide what's the operative one."""
    pat = _PCT_NEAR(keyword_re)
    out: list[float] = []
    for m in pat.finditer(text):
        for grp in m.groups():
            if grp:
                try:
                    out.append(float(grp))
                    break
                except ValueError:
                    continue
    return out


def _find_percentage_near(text: str, keyword_re: str) -> float | None:
    """Backwards-compat: return the LOWEST percentage near keyword, since
    the most-protective clause is usually the operative one for shortfall checks."""
    vals = _find_percentages_near(text, keyword_re)
    return min(vals) if vals else None


def _find_days_near(text: str, keyword_re: str) -> int | None:
    pat = _DAYS_NEAR(keyword_re)
    for m in pat.finditer(text):
        try:
            return int(m.group(1))
        except (ValueError, IndexError):
            continue
    return None


def _check_emd_shortfall(
    text: str, params: TenderParameters
) -> tuple[bool, str]:
    found = _find_percentage_near(text, r"emd|earnest\s+money|bid\s+security")
    if found is None:
        return False, ""
    if found < params.emd_percentage:
        return (
            True,
            f"Document states EMD = {found}% (expected ≥ {params.emd_percentage}%)",
        )
    return False, ""


def _check_pbg_shortfall(
    text: str, params: TenderParameters
) -> tuple[bool, str]:
    found = _find_percentage_near(
        text, r"performance\s+(?:guarantee|security)|pbg"
    )
    if found is None:
        return False, ""
    if found < params.pbg_percentage:
        return (
            True,
            f"Document states PBG = {found}% (expected ≥ {params.pbg_percentage}%)",
        )
    return False, ""


def _check_bid_validity_short(
    text: str, params: TenderParameters
) -> tuple[bool, str]:
    found = _find_days_near(text, r"bid\s+valid|validity\s+of\s+bid|tender\s+valid")
    if found is None:
        return False, ""
    if found < params.bid_validity_days:
        return (
            True,
            f"Document states bid validity = {found} days (expected ≥ {params.bid_validity_days})",
        )
    return False, ""


def _check_missing_integrity_pact(
    text: str, params: TenderParameters
) -> tuple[bool, str]:
    if not params.integrity_pact_required:
        return False, ""
    if re.search(r"integrity\s+pact|integrity-pact|\bIP\s+clause", text, re.IGNORECASE):
        return False, ""
    return True, "No 'integrity pact' clause found in document"


def _check_missing_anti_collusion(
    text: str, params: TenderParameters
) -> tuple[bool, str]:
    if re.search(
        r"anti[-\s]?collusion|form\s+3N|no[-\s]?collusion\s+(?:declaration|certificate)",
        text,
        re.IGNORECASE,
    ):
        return False, ""
    return True, "No 'anti-collusion' / Form 3N declaration found"


def _check_missing_pvc(
    text: str, params: TenderParameters
) -> tuple[bool, str]:
    if not params.price_adjustment_applicable:
        return False, ""
    if re.search(
        r"price\s+(?:adjustment|variation)|pvc|escalation\s+formula",
        text,
        re.IGNORECASE,
    ):
        return False, ""
    return True, "Price-adjustment clause missing despite value/duration triggering it"


def _check_criteria_restriction_narrow(
    text: str, params: TenderParameters
) -> tuple[bool, str]:
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
    sentences = re.split(r"[.\n]+", text.lower())

    if any(any(j in s for j in JV_TOKENS) and any(b in s for b in BAN_TOKENS)
           for s in sentences):
        bans.append("JV / Consortium / SPV ban")

    if any(any(f in s for f in FOREIGN_TOKENS) and any(b in s for b in BAN_TOKENS)
           for s in sentences):
        bans.append("Foreign-bidder ban")

    if not bans:
        return False, ""
    if re.search(r"justification|recorded\s+rationale|special\s+order|GO\s+Ms",
                 text, re.IGNORECASE):
        return False, ""
    return True, " + ".join(bans) + " without recorded justification"


def _check_e_procurement_bypass(
    text: str, params: TenderParameters
) -> tuple[bool, str]:
    if not params.e_procurement_mandatory:
        return False, ""
    if re.search(
        r"e[-\s]?procurement|apeprocurement\.gov\.in|gepnic|nicgov|cppp",
        text,
        re.IGNORECASE,
    ):
        return False, ""
    return True, "E-procurement is mandatory at this value but no e-portal reference found"


def _check_judicial_preview_bypass(
    text: str, params: TenderParameters
) -> tuple[bool, str]:
    if not params.judicial_preview_required:
        return False, ""
    if re.search(r"judicial\s+preview|hon'?ble\s+judge|preview\s+committee", text, re.IGNORECASE):
        return False, ""
    return True, "Project value triggers AP Judicial Preview (≥ Rs.100 cr) but no preview reference found"


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
        self, rules: list[dict], cls: TenderClassification, params: TenderParameters
    ) -> list[dict]:
        """Filter rules to those applicable to this tender."""
        primary = cls.primary_type
        is_ap = cls.is_ap_tender
        applicable: list[dict] = []
        for r in rules:
            layer = r.get("layer") or ""
            # If AP tender, allow Central + CVC + AP-State; else exclude AP-State
            if not is_ap and layer == "AP-State":
                continue
            # If a matcher exists for this typology, the rule is checkable
            if r.get("typology_code") in _PATTERN_MATCHERS:
                applicable.append(r)
        return applicable

    # ── Stage 3: pattern matching ──

    def _match_rule(
        self, rule: dict, text: str, params: TenderParameters
    ) -> RuleFinding | None:
        matcher = _PATTERN_MATCHERS.get(rule["typology_code"])
        if matcher is None:
            return None
        violated, evidence = matcher(text, params)
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
        self, document_text: str, *, document_name: str = "<inline>"
    ) -> ValidationReport:
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

        # Stage 2: select applicable rules
        all_rules = self._fetch_rules()
        applicable = self._select_rules(all_rules, classification, params)

        # Stage 3: pattern-match each applicable rule
        findings: list[RuleFinding] = []
        for rule in applicable:
            f = self._match_rule(rule, document_text, params)
            if f is not None:
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
