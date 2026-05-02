"""
modules/validator/combined_validator.py

CombinedValidator — orchestrates the three independent approaches:

    Stage 1: SHACL                 (P1 numeric rules, ~100 ms)
    Stage 2: Vector / BGE-M3       (P2 structural concepts, ~240 ms cached)
    Stage 3: Regex / cascade       (gap-fill, defeasibility, ~1 s)
    Stage 4: merge by typology_code, deduplicate, record `detected_by`
    Stage 5: apply AP-State defeasibility (downgrade to ADVISORY)
    Stage 6: score → PASS / CONDITIONAL / BLOCK

Produces a single CombinedValidationReport — one finding per typology with
the union of evidence across approaches plus an `approach_coverage` summary
that tells you exactly which validator caught which issue uniquely.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from engines.classifier import TenderClassification
from engines.parameter_cascade import TenderParameters
from modules.validator.rule_verification_engine import (
    RuleVerificationEngine, ValidationReport, RuleFinding,
)
from modules.validator.shacl_validator import SHACLValidator, SHACLViolation
from modules.validator.vector_checker import VectorChecker, VectorFinding


OverallStatus = Literal["PASS", "CONDITIONAL", "BLOCK"]
ApproachName  = Literal["SHACL", "Vector", "Regex"]


# Map vector concept_id → procurement typology used by regex / SHACL.
CONCEPT_TO_TYPOLOGY: dict[str, str] = {
    "integrity-pact":          "Missing-Integrity-Pact",
    "anti-collusion":          "Missing-Anti-Collusion",
    "price-variation-clause":  "Missing-PVC-Clause",
    "judicial-preview":        "Judicial-Preview-Bypass",
    "performance-security":    "PBG-Shortfall",
    "earnest-money":           "EMD-Shortfall",
    "reverse-tendering":       "Reverse-Tender-Evasion",
    "mobilisation-advance":    "Mobilisation-Advance-Excess",
}

_SEVERITY_ORDER = {"HARD_BLOCK": 3, "WARNING": 2, "ADVISORY": 1}


# ─────────────────────────────────────────────────────────────────────────
# Output models
# ─────────────────────────────────────────────────────────────────────────

class MergedFinding(BaseModel):
    typology_code: str
    severity: str
    detected_by: list[str]            # ["SHACL"] / ["Vector","Regex"] / etc
    primary_evidence: str
    source_clause: str
    defeated_by: list[str] = Field(default_factory=list)


class ApproachCoverage(BaseModel):
    shacl_findings: int
    vector_findings: int
    regex_findings: int
    unique_to_shacl: int
    unique_to_vector: int
    unique_to_regex: int
    caught_by_multiple: int


class CombinedValidationReport(BaseModel):
    document_name: str
    classification: TenderClassification
    parameters: TenderParameters
    overall_status: OverallStatus
    score: int
    findings: list[MergedFinding]
    approach_coverage: ApproachCoverage
    timing_ms: dict
    processing_time_ms: int
    timestamp: str


# ─────────────────────────────────────────────────────────────────────────
# Engine
# ─────────────────────────────────────────────────────────────────────────

class CombinedValidator:
    """Three-approach orchestrator."""

    def __init__(
        self,
        *,
        regex_engine: RuleVerificationEngine | None = None,
        shacl_validator: SHACLValidator | None = None,
        vector_checker: VectorChecker | None = None,
    ):
        self.regex_engine    = regex_engine    or RuleVerificationEngine()
        self.shacl_validator = shacl_validator or SHACLValidator()
        self.vector_checker  = vector_checker  or VectorChecker()

    # ── Internal helpers ──

    @staticmethod
    def _max_severity(severities: list[str]) -> str:
        return max(severities, key=lambda s: _SEVERITY_ORDER.get(s, 0)) if severities else "ADVISORY"

    @staticmethod
    def _coverage(by_typology: dict[str, dict]) -> ApproachCoverage:
        shacl_set  = {t for t, d in by_typology.items() if "SHACL"  in d["detected_by"]}
        vector_set = {t for t, d in by_typology.items() if "Vector" in d["detected_by"]}
        regex_set  = {t for t, d in by_typology.items() if "Regex"  in d["detected_by"]}
        u_shacl  = shacl_set  - vector_set - regex_set
        u_vector = vector_set - shacl_set  - regex_set
        u_regex  = regex_set  - shacl_set  - vector_set
        multiple = {t for t in by_typology
                    if len(by_typology[t]["detected_by"]) >= 2}
        return ApproachCoverage(
            shacl_findings=len(shacl_set),
            vector_findings=len(vector_set),
            regex_findings=len(regex_set),
            unique_to_shacl=len(u_shacl),
            unique_to_vector=len(u_vector),
            unique_to_regex=len(u_regex),
            caught_by_multiple=len(multiple),
        )

    # ── Stage adapters: convert each approach's findings into the
    # common (typology_code → bucket) form used by Stage 4 merge ──

    def _ingest_shacl(self, by_typ: dict, violations: list[SHACLViolation]) -> int:
        n = 0
        for v in violations:
            typ = v.typology_code or "Unknown"
            bucket = by_typ.setdefault(typ, _empty_bucket())
            if "SHACL" in bucket["detected_by"]:
                continue   # one SHACL contribution per typology is enough
            bucket["detected_by"].add("SHACL")
            bucket["severities"].append(v.severity.replace("Violation", "HARD_BLOCK")
                                         if v.severity == "Violation" else v.severity)
            bucket["evidences"].append(("SHACL", v.message[:280]))
            bucket["rule_ids"].add(v.rule_id)
            n += 1
        return n

    def _ingest_vector(self, by_typ: dict, vector_findings: list[VectorFinding]) -> int:
        n = 0
        for f in vector_findings:
            typ = CONCEPT_TO_TYPOLOGY.get(f.concept_id, f"Vector:{f.concept_id}")
            bucket = by_typ.setdefault(typ, _empty_bucket())
            bucket["detected_by"].add("Vector")
            bucket["severities"].append(f.severity)
            ev = (
                f"Vector concept '{f.concept_id}' ABSENT — max_sim={f.max_similarity:.4f} "
                f"(threshold {f.threshold:.4f}). Closest section: "
                f"'{f.top_matches[0]['heading'][:60]}' at score {f.top_matches[0]['score']:.4f}"
            ) if f.top_matches else f"Concept '{f.concept_id}' absent"
            bucket["evidences"].append(("Vector", ev))
            n += 1
        return n

    def _ingest_regex(self, by_typ: dict, report: ValidationReport) -> int:
        n = 0
        for f in report.hard_blocks + report.warnings + report.advisories:
            typ = f.typology_code
            bucket = by_typ.setdefault(typ, _empty_bucket())
            bucket["detected_by"].add("Regex")
            bucket["severities"].append(f.severity)
            bucket["evidences"].append(("Regex", f.evidence_text[:280]))
            bucket["rule_ids"].update(f.triggered_rule_ids or [f.rule_id])
            if f.source_clause and not bucket["source_clause"]:
                bucket["source_clause"] = f.source_clause
            for d in f.defeated_by or []:
                bucket["defeated_by"].add(d)
            n += 1
        return n

    # ── Public API ──

    def validate(
        self,
        document_text: str,
        document_name: str,
        estimated_value_override: float | None = None,
    ) -> CombinedValidationReport:
        t_start = time.perf_counter()
        timings: dict[str, int] = {}

        # ── Stage 1: SHACL ──
        t0 = time.perf_counter()
        shacl_violations = self.shacl_validator.validate(document_text)
        timings["shacl_ms"] = int((time.perf_counter() - t0) * 1000)

        # We need is_ap and estimated_value for VectorChecker. Use the
        # bundled classifier rather than re-running regex first.
        classification = self.regex_engine.classifier.classify(document_text)
        ev = estimated_value_override if estimated_value_override is not None \
             else (classification.estimated_value or 1_00_00_000)
        is_ap = classification.is_ap_tender

        # ── Stage 2: Vector ──
        t0 = time.perf_counter()
        vec_out = self.vector_checker.check_document(
            document_text=document_text,
            source_file=document_name,
            is_ap_tender=is_ap,
            estimated_value=ev,
            duration_months=classification.duration_months or 12,
        )
        vector_findings: list[VectorFinding] = vec_out["findings"]
        timings["vector_ms"] = int((time.perf_counter() - t0) * 1000)
        timings["vector_cache_hit"] = bool(vec_out["timing_ms"].get("cache_hit"))

        # ── Stage 3: Regex (also gives us classification + cascade params) ──
        t0 = time.perf_counter()
        regex_report = self.regex_engine.verify(
            document_text, document_name=document_name,
        )
        timings["regex_ms"] = int((time.perf_counter() - t0) * 1000)

        # Override estimated_value on the regex report's classification if caller supplied one
        if estimated_value_override is not None:
            regex_report.classification.estimated_value = estimated_value_override

        # ── Stage 4: merge ──
        by_typ: dict[str, dict] = {}
        n_shacl  = self._ingest_shacl(by_typ, shacl_violations)
        n_vector = self._ingest_vector(by_typ, vector_findings)
        n_regex  = self._ingest_regex(by_typ, regex_report)

        # ── Stage 5: defeasibility resolution ──
        # Regex already applied defeats; carry that signal across approaches.
        # If regex marked a typology as defeated, downgrade ALL contributions
        # for that typology to ADVISORY.
        for typ, bucket in by_typ.items():
            if bucket["defeated_by"]:
                bucket["severities"] = ["ADVISORY"] * len(bucket["severities"]) or ["ADVISORY"]

        # Build merged findings
        merged: list[MergedFinding] = []
        for typ, bucket in by_typ.items():
            sev = self._max_severity(bucket["severities"])
            # Choose primary evidence — prefer SHACL > Regex > Vector for crispness
            evid_by_source = {src: ev for src, ev in bucket["evidences"]}
            primary_evidence = (
                evid_by_source.get("SHACL")
                or evid_by_source.get("Regex")
                or evid_by_source.get("Vector")
                or ""
            )
            # Annotate which approaches detected this
            sources = sorted(bucket["detected_by"])
            evidence_full = (
                f"[Detected by: {', '.join(sources)}]  {primary_evidence}"
            )
            merged.append(MergedFinding(
                typology_code=typ,
                severity=sev,
                detected_by=sources,
                primary_evidence=evidence_full,
                source_clause=bucket["source_clause"],
                defeated_by=sorted(bucket["defeated_by"]),
            ))

        merged.sort(key=lambda f: (
            -_SEVERITY_ORDER.get(f.severity, 0), f.typology_code,
        ))

        # ── Stage 6: score ──
        hard_blocks = [m for m in merged if m.severity == "HARD_BLOCK"]
        warnings    = [m for m in merged if m.severity == "WARNING"]
        advisories  = [m for m in merged if m.severity == "ADVISORY"]
        if hard_blocks:
            score, status = 0, "BLOCK"
        else:
            score = max(0, 100 - 3 * len(warnings) - len(advisories))
            status = "PASS" if score >= 85 else "CONDITIONAL"

        coverage = self._coverage(by_typ)
        timings["total_ms"] = int((time.perf_counter() - t_start) * 1000)

        return CombinedValidationReport(
            document_name=document_name,
            classification=regex_report.classification,
            parameters=regex_report.parameters,
            overall_status=status,
            score=score,
            findings=merged,
            approach_coverage=coverage,
            timing_ms=timings,
            processing_time_ms=timings["total_ms"],
            timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )


# ─────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────

def _empty_bucket() -> dict:
    return {
        "detected_by":   set(),
        "severities":    [],
        "evidences":     [],          # list of (source, text)
        "rule_ids":      set(),
        "source_clause": "",
        "defeated_by":   set(),
    }
