"""Verdict emission helper for Tier-1 validators (Bug C).

Five-state verdict taxonomy. Every Tier-1 validator emits exactly one
row per run unless that row is GAP_VIOLATION / HARD_BLOCK (still one
row plus VIOLATES_RULE edge). Schema extends `kg_nodes.properties`
JSONB with a new `verdict` field — no DDL. The legacy `status` field
is preserved for back-compat with the 73 corpus ValidationFindings
that pre-date Bug C.

Verdict semantics:

  COMPLIANT_FIRED       — validator ran, retrieved evidence, threshold
                          met. Row carries verbatim quote (≤30 words)
                          from the rendered draft at the cited line —
                          this IS the audit trail; an auditor must be
                          able to re-read it at evidence_line_no_local
                          in the named source_file.

  SKIP_NOT_APPLICABLE   — every candidate rule's condition_when
                          evaluated False against the tender's facts
                          (e.g. JP threshold ECV ≥ 100cr; tender at
                          85cr). Row carries failed_condition +
                          skip_reason_human + skip_trace.

                          NOTE: PBG-Shortfall is intentionally
                          asymmetric — its single rule (AP-GO-175)
                          has no condition gate, so PBG never legitimately
                          SKIPs. PBG extraction failures route to
                          UNVERIFIED, not synthetic SKIP.

  UNVERIFIED            — validator ran but extraction / retrieval
                          failed. Row carries `failure_path` (canonical
                          values listed below) so the root cause is
                          diagnosable.

  GAP_VIOLATION         — rule fires, evidence missing or wrong.
                          Severity ∈ ADVISORY / WARNING per the firing
                          rule. Pairs with a VIOLATES_RULE edge.

  HARD_BLOCK            — severe violation (PBG below threshold, EMD
                          missing, mandatory-field absent, etc.).
                          Severity is HARD_BLOCK per the firing rule.
                          Pairs with a VIOLATES_RULE edge.

═══════════════════════════════════════════════════════════════════
  CANONICAL `failure_path` taxonomy (UNVERIFIED rows)
═══════════════════════════════════════════════════════════════════

  no_candidate            — top-K retrieval found nothing matching the
                            target concept; LLM rerank returned
                            chosen_index=null OR found=false on every
                            candidate.

  chosen_oor              — LLM picked a chosen_index outside the
                            candidate list bounds, OR the extracted
                            value was out of expected range. Extraction
                            integrity failure.

  extraction_path_none    — neither percentage nor amount nor
                            equivalent extraction path produced a
                            usable value. The validator ran but had
                            no anchor to reason about.

  rule_lookup_missing     — rule referenced by `select_*_rule()` is
                            missing from the rules table. Knowledge-
                            layer integrity gap (see Bug A patch).

  L24_evidence_guard      — LLM extracted a value but the evidence
                            quote failed verbatim verification against
                            the chosen section's text (potential
                            hallucination caught).

  retrieval_coverage_gap  — BGE-M3 returned candidates but none had
                            sufficient coverage of the target concept;
                            grep fallback (L36 / L40) was promoted to
                            UNVERIFIED rather than emitting an absence
                            finding (added during Batch 1/2 migration).

  Future failure_paths must extend this canonical list; do NOT
  introduce ad-hoc strings. Add a new entry here, then thread it
  through every validator that can emit it.

═══════════════════════════════════════════════════════════════════
  CONTRACT: silent-COMPLIANT path for multi-rule validators
═══════════════════════════════════════════════════════════════════

Single-rule validators (rule selector returns `dict | None`) cover
the silent-compliant path implicitly via their `is_compliant` /
`not is_violation` branch — exactly one row per run is guaranteed.

Multi-rule validators (rule selector returns `list[dict]`) run a
loop over fired rules and emit one row per fired rule. The silent-
compliant path — when NO rule produces a violation row AND no
informational marker is emitted — must be made explicit by emitting
a final `COMPLIANT_FIRED` row at end-of-main:

    if not findings_emitted:
        emit_verdict_row(
            doc_id=DOC_ID, typology=TYPOLOGY,
            rule_id=(fired_rules[0]["rule_id"] if fired_rules else None),
            severity=(fired_rules[0].get("severity") if fired_rules else None),
            verdict="COMPLIANT_FIRED",
            evidence_quote=evidence,
            ...
            extra_props={"violation_reason": "compliant_…",
                         "rule_shape": "multi-rule"},
        )

Without this, the aggregator sees zero rows for that (doc, typology)
cell and emits VALIDATOR_NOT_MIGRATED.

The original Bug C migration (commit edc68bd, the first 6 validators)
did not need this because all 6 were single-rule. The pattern was
articulated explicitly only after the Batch-2 expansion surfaced
VALIDATOR_NOT_MIGRATED on Arbitration and Geographic-Restriction
(both multi-rule). Mandatory-Fields in Batch 1 happened to cover the
case via its sub-check rows — its loop emits one row per missing
sub-check, so the cell is always populated.

Future multi-rule validators must include the end-of-main silent-
COMPLIANT emit. Sub-check shapes that always emit at least one row
per cell (like Mandatory-Fields) are exempt.

═══════════════════════════════════════════════════════════════════

The aggregator at modules/draft_validation/run_tier1_on_draft.py
reads `properties.verdict` first; on empty rows it emits
VALIDATOR_NOT_MIGRATED (regression alarm) instead of defaulting to
COMPLIANT.
"""
from __future__ import annotations

import json
import requests

from builder.config import settings


REST = settings.supabase_rest_url
H = {"apikey": settings.supabase_anon_key,
     "Authorization": f"Bearer {settings.supabase_anon_key}"}


VERDICTS = {
    "COMPLIANT_FIRED",
    "SKIP_NOT_APPLICABLE",
    "UNVERIFIED",
    "GAP_VIOLATION",
    "HARD_BLOCK",
}

# Map verdict → legacy `status` field (preserves back-compat with
# pre-Bug-C corpus rows and the existing aggregator's status reads).
_STATUS_MAP = {
    "COMPLIANT_FIRED":      "COMPLIANT",
    "SKIP_NOT_APPLICABLE":  "SKIP",
    "UNVERIFIED":           "UNVERIFIED",
    "GAP_VIOLATION":        "OPEN",
    "HARD_BLOCK":           "OPEN",
}


# ── Quote handling ────────────────────────────────────────────────────

def truncate_evidence_quote(quote: str, max_words: int = 30) -> str:
    """Truncate a verbatim quote to ≤ max_words. Returns the
    word-truncated string with an ellipsis suffix when shortened.
    Preserves leading/trailing whitespace stripping but otherwise
    leaves the quote verbatim — DO NOT reformat or paraphrase."""
    if not quote:
        return ""
    quote = quote.strip()
    words = quote.split()
    if len(words) <= max_words:
        return quote
    return " ".join(words[:max_words]) + "…"


# ── SKIP trace helper ─────────────────────────────────────────────────

def compose_skip_trace(rule_candidates: list[dict],
                       facts: dict) -> tuple[str, list[dict], str]:
    """Re-evaluate each rule candidate's condition_when against the
    given facts. Returns:
        (failed_condition_string, skip_trace_list, skip_reason_human)

    Used at silent rule=None sites to give the SKIP_NOT_APPLICABLE
    row carriage data so the verdict is auditable. Cheap — one rules
    table lookup per candidate (the same lookups the rule selector
    just performed and printed to stdout)."""
    # Lazy import to avoid pulling the evaluator at module load
    from modules.validator.condition_evaluator import (
        evaluate as evaluate_when, Verdict,
    )

    trace_struct: list[dict] = []
    trace_lines: list[str] = []
    for cand in rule_candidates:
        rid = cand.get("rule_id") or "?"
        try:
            r = requests.get(
                f"{REST}/rest/v1/rules",
                params={"rule_id": f"eq.{rid}",
                        "select":  "rule_id,condition_when"},
                headers=H, timeout=15,
            )
            rows = r.json() if r.ok else []
        except Exception:
            rows = []
        if not rows:
            cw = "(rule not in rules table)"
            v_str = "MISSING"
        else:
            cw = rows[0].get("condition_when") or ""
            v_str = evaluate_when(cw, facts).verdict.value
        trace_struct.append({"rule_id": rid, "condition_when": cw,
                             "verdict": v_str})
        trace_lines.append(f"[{rid}] when={cw!r} verdict={v_str}")

    # Compose a one-line human reason from the strongest signal.
    # Look for a value-threshold-style condition for the headline.
    skip_reason_human = "no rule applies for these tender facts"
    facts_summary = (
        f"tender_type={facts.get('TenderType') or facts.get('tender_type')!r}, "
        f"is_ap={facts.get('TenderState') == 'AndhraPradesh' or facts.get('is_ap_tender')}, "
        f"ECV={facts.get('EstimatedValue') or facts.get('estimated_value_cr')}"
    )
    skip_reason_human = f"no candidate rule fired (facts: {facts_summary})"

    return ("\n  ".join(trace_lines), trace_struct, skip_reason_human)


# ── Single-row emit ───────────────────────────────────────────────────

def _post_node(doc_id: str, label: str, properties: dict,
               source_ref: str) -> dict:
    r = requests.post(
        f"{REST}/rest/v1/kg_nodes",
        headers={**H, "Content-Type": "application/json",
                 "Prefer": "return=representation"},
        json=[{"doc_id":     doc_id,
               "node_type":  "ValidationFinding",
               "label":      label,
               "properties": properties,
               "source_ref": source_ref}],
        timeout=30,
    )
    r.raise_for_status()
    return r.json()[0]


def emit_verdict_row(
    *,
    doc_id: str,
    typology: str,
    rule_id: str | None,
    verdict: str,
    severity: str | None = None,
    # COMPLIANT_FIRED ----------------------------------------
    clause_id: str | None = None,
    evidence_quote: str | None = None,
    evidence_section_heading: str | None = None,
    evidence_line_no_local: int | None = None,
    section_node_id: str | None = None,
    source_file: str | None = None,
    qdrant_similarity: float | None = None,
    passed_threshold: bool | None = None,
    value_extracted=None,
    # SKIP_NOT_APPLICABLE ------------------------------------
    failed_condition: str | None = None,
    skip_reason_human: str | None = None,
    skip_trace: list[dict] | None = None,
    # UNVERIFIED ---------------------------------------------
    failure_path: str | None = None,   # one of: no_candidate / chosen_oor /
                                       # extraction_path_none /
                                       # rule_lookup_missing /
                                       # L24_evidence_guard
    retrieval_debug: dict | None = None,
    what_was_searched: str | None = None,
    # GAP_VIOLATION / HARD_BLOCK -----------------------------
    expected: str | None = None,
    found: str | None = None,
    violation_reason: str | None = None,
    # Free-form passthrough (for validator-specific audit fields)
    extra_props: dict | None = None,
) -> dict:
    """Single source of truth for verdict-row emission.

    Caller is responsible for emitting the VIOLATES_RULE edge on
    GAP_VIOLATION / HARD_BLOCK (the rule_node_id lookup is validator-
    local). This helper writes only the ValidationFinding kg_node.

    Returns the inserted node dict (with `node_id` for edge linkage).
    """
    if verdict not in VERDICTS:
        raise ValueError(
            f"unknown verdict: {verdict!r}; expected one of {sorted(VERDICTS)}")

    quote = truncate_evidence_quote(evidence_quote or "", max_words=30)

    # Compose label per verdict — keeps the label dataset auditor-readable.
    label_parts: list[str] = [typology, verdict]
    if verdict == "COMPLIANT_FIRED" and quote:
        line_anchor = f"line {evidence_line_no_local}" if evidence_line_no_local else "line ?"
        label_parts.append(f"{line_anchor}: {quote[:80]}")
    elif verdict == "SKIP_NOT_APPLICABLE" and skip_reason_human:
        label_parts.append(skip_reason_human[:120])
    elif verdict == "UNVERIFIED" and failure_path:
        label_parts.append(f"failure_path={failure_path}")
    elif verdict in ("GAP_VIOLATION", "HARD_BLOCK") and (expected or found):
        label_parts.append(f"expected={expected!r} found={found!r}")
    label = " · ".join(p for p in label_parts if p)

    # Common props
    props: dict = {
        "verdict":        verdict,
        "status":         _STATUS_MAP[verdict],
        "typology_code":  typology,
        "rule_id":        rule_id,
        "tier":           1,
        "severity":       severity,
    }

    if verdict == "COMPLIANT_FIRED":
        props.update({
            "clause_id":                clause_id,
            "evidence_quote":           quote,
            "evidence":                 quote,   # legacy alias
            "evidence_section_heading": evidence_section_heading,
            "evidence_line_no_local":   evidence_line_no_local,
            "section_node_id":          section_node_id,
            "source_file":              source_file,
            "qdrant_similarity":        qdrant_similarity,
            "passed_threshold":         passed_threshold,
            "value_extracted":          value_extracted,
        })
    elif verdict == "SKIP_NOT_APPLICABLE":
        props.update({
            "failed_condition":   failed_condition,
            "skip_reason_human":  skip_reason_human,
            "skip_trace":         skip_trace,
        })
    elif verdict == "UNVERIFIED":
        props.update({
            "failure_path":             failure_path,
            "retrieval_debug":          retrieval_debug,
            "what_was_searched":        what_was_searched,
            "section_node_id":          section_node_id,
            "evidence_section_heading": evidence_section_heading,
            "evidence_line_no_local":   evidence_line_no_local,
            "evidence_quote":           quote if quote else None,
            "evidence":                 quote if quote else None,  # legacy
            "qdrant_similarity":        qdrant_similarity,
            "source_file":              source_file,
        })
    elif verdict in ("GAP_VIOLATION", "HARD_BLOCK"):
        props.update({
            "expected":                 expected,
            "found":                    found,
            "violation_reason":         violation_reason,
            "evidence_quote":           quote if quote else None,
            "evidence":                 quote if quote else None,  # legacy
            "evidence_section_heading": evidence_section_heading,
            "evidence_line_no_local":   evidence_line_no_local,
            "section_node_id":          section_node_id,
            "source_file":              source_file,
            "qdrant_similarity":        qdrant_similarity,
        })

    if extra_props:
        # extra_props takes lower precedence — explicit kwargs win.
        merged = {**extra_props, **props}
        props = merged

    # Strip None values so the JSONB stays compact.
    props = {k: v for k, v in props.items() if v is not None}

    return _post_node(
        doc_id=doc_id,
        label=label,
        properties=props,
        source_ref=f"tier1:{typology}:{rule_id or 'no_rule'}",
    )
