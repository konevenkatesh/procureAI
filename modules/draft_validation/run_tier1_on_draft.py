"""
modules/draft_validation/run_tier1_on_draft.py

Run the existing scripts/tier1_*_check.py runners against a draft
markdown file produced by the Drafter.

Pipeline:
  1. Ingest the draft into the KG via experiments/tender_graph/kg_builder
     .build_kg(doc_id="draft_<thread_id>", document=draft_path).
     This populates kg_nodes (TenderDocument + Section), kg_edges
     (HAS_SECTION), AND Qdrant points (Phase 6b auto-ingest).
  2. Override the TenderDocument node properties with the Drafter's
     known facts (tender_type, is_ap_tender, estimated_value_cr,
     OriginalContractPeriodMonths) so the tier1 checks' condition_when
     selectors fire correctly. This bypasses kg_builder Phase 6c's LLM
     extraction (already done in the Drafter's project_brief node).
  3. Subprocess-run each requested tier1 check with the draft doc_id
     as argv[1]. Each script writes ValidationFinding rows + (on
     violation) VIOLATES_RULE edges directly to the KG.
  4. Aggregate the results: query kg_nodes for ValidationFinding rows
     with `doc_id=draft_<thread_id>`, group by typology_code, classify
     each typology as COMPLIANT (no row) / GAP_VIOLATION (row +
     VIOLATES_RULE edge) / UNVERIFIED (row, no edge).
  5. Optionally clean up: delete kg_nodes/kg_edges and Qdrant points
     for the draft doc_id.

The 6 typologies checked map to the 6 most-cited Tier-1 validators
covering the BDS values the Drafter writes deterministically:
    PBG-Shortfall            (AP-GO-175 — 10% of contract value)
    EMD-Shortfall            (AP-GO-050 — 1% bid stage + 1.5% at agreement)
    Bid-Validity-Short       (AP-GO-067 — 90 days)
    Missing-LD-Clause        (GFR Rule 83 / MPW 2022 §6.4.4)
    MakeInIndia-LCC-Missing  (DPIIT Order 2017 — universal Tier-1 absence)
    Judicial-Preview-Bypass  (AP Judicial Preview Act 2019 — fires ≥Rs.100cr)

For the Kurnool 85-cr brief: JP-Bypass SKIPs at the rule layer
(EstimatedValue=8.5e8 < Rs.100cr threshold), so the actual check
list is 5/6 fires. For the JA 125.5-cr brief, all 6 fire.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import requests

REPO = Path(__file__).resolve().parent.parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from builder.config import settings


REST = settings.supabase_rest_url
H = {
    "apikey":        settings.supabase_anon_key,
    "Authorization": f"Bearer {settings.supabase_anon_key}",
    "Content-Type":  "application/json",
}

QDRANT_URL  = os.environ.get("QDRANT_URL", "http://localhost:6333")
QDRANT_COLL = "tender_sections"


# Drafter pipeline coverage: 19 typologies post-Bug-C arc.
#
# Original 6 (commit edc68bd) cover the BDS values the Drafter writes
# deterministically — PBG / EMD / BV / LD / MII / JP. The 13 added
# below are drafter-relevant typologies migrated to the Bug C contract
# during expansion Batches 1 + 2 (commits c968c61 + dcf7080).
#
# The 5 Batch-3 bidder-fact validators (Blacklist / Solvency /
# Turnover / Class-Mismatch / ABC — commit 412081e) are intentionally
# NOT wired here. They require bidder submission data which is
# Module 4 Evaluator scope, not drafter scope.
DEFAULT_CHECKS: list[tuple[str, str]] = [
    # (script_name, typology_code)
    # ── Original 6 (commit edc68bd) — BDS-deterministic ───────────
    ("tier1_pbg_check.py",                  "PBG-Shortfall"),
    ("tier1_emd_check.py",                  "EMD-Shortfall"),
    ("tier1_bid_validity_check.py",         "Bid-Validity-Short"),
    ("tier1_ld_check.py",                   "Missing-LD-Clause"),
    ("tier1_mii_check.py",                  "MakeInIndia-LCC-Missing"),
    ("tier1_jp_check.py",                   "Judicial-Preview-Bypass"),
    # ── Batch 1 (commit c968c61) — drafter-relevant numeric/threshold
    ("tier1_pvc_check.py",                  "Missing-PVC-Clause"),
    ("tier1_ma_check.py",                   "Mobilisation-Advance-Excess"),
    ("tier1_bg_validity_gap_check.py",      "BG-Validity-Gap"),
    ("tier1_dlp_check.py",                  "DLP-Period-Short"),
    ("tier1_force_majeure_check.py",        "Missing-Force-Majeure"),
    ("tier1_mandatory_fields_check.py",     "Works-Universal-Mandatory-Fields"),
    # ── Batch 2 (commit dcf7080) — drafter-relevant presence/structural
    ("tier1_integrity_pact_check.py",       "Missing-Integrity-Pact"),
    ("tier1_eproc_check.py",                "E-Procurement-Bypass"),
    ("tier1_arbitration_check.py",          "Arbitration-Clause-Violation"),
    ("tier1_geographic_restriction_check.py", "Geographic-Restriction"),
    ("tier1_prebid_check.py",               "Pre-Bid-Process-Unclear"),
    ("tier1_spec_tailoring_check.py",       "Spec-Tailoring"),
    ("tier1_crn_check.py",                  "Criteria-Restriction-Narrow"),
]


# ── KG helpers ────────────────────────────────────────────────────────

def _rest_patch(path: str, params: dict, body: dict) -> list[dict]:
    p = dict(params or {})
    r = requests.patch(
        f"{REST}/rest/v1/{path}",
        params=p,
        json=body,
        headers={**H, "Prefer": "return=representation"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def _rest_get(path: str, params: dict | None = None) -> list[dict]:
    p = dict(params or {})
    r = requests.get(f"{REST}/rest/v1/{path}", params=p, headers=H, timeout=30)
    r.raise_for_status()
    out = r.json()
    return out if isinstance(out, list) else []


def _rest_delete(path: str, params: dict) -> None:
    r = requests.delete(f"{REST}/rest/v1/{path}", params=params, headers=H, timeout=30)
    r.raise_for_status()


def _override_tender_document_facts(doc_id: str, facts: dict) -> dict:
    """Override TenderDocument node properties with the Drafter's
    known facts. This runs AFTER build_kg so kg_builder's Phase 6c
    LLM extraction can be cheaply overridden — we know better."""
    rows = _rest_get("kg_nodes", {
        "doc_id":    f"eq.{doc_id}",
        "node_type": "eq.TenderDocument",
        "select":    "node_id,properties",
    })
    if not rows:
        raise RuntimeError(f"No TenderDocument node for doc_id={doc_id} after build_kg")
    td = rows[0]
    new_props = dict(td.get("properties") or {})
    # Merge the Drafter's facts
    new_props["tender_type"]                  = facts.get("tender_type")
    new_props["is_ap_tender"]                 = bool(facts.get("is_ap_tender"))
    new_props["estimated_value_cr"]           = facts.get("ecv_cr")
    new_props["OriginalContractPeriodMonths"] = facts.get("duration_months")
    # Audit stamp
    new_props["facts_overridden_by_drafter"]  = True
    new_props["facts_overridden_at"]          = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return _rest_patch("kg_nodes",
                       {"node_id": f"eq.{td['node_id']}"},
                       {"properties": new_props})


# ── Tier-1 invocation ─────────────────────────────────────────────────

def _run_one_check(script_name: str, doc_id: str, *, timeout_s: int = 180) -> dict:
    """Run a single tier1_*_check.py script as a subprocess.

    The script reads doc_id from sys.argv[1] and writes ValidationFinding
    rows + (optional) VIOLATES_RULE edges directly to the KG. We capture
    stdout/stderr for diagnostic logging but don't parse them — the
    authoritative result is the KG state after the script finishes.
    """
    script_path = REPO / "scripts" / script_name
    if not script_path.exists():
        return {"script": script_name, "status": "MISSING_SCRIPT",
                "elapsed_s": 0, "stdout": "", "stderr": ""}
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            [sys.executable, str(script_path), doc_id],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env={**os.environ},
        )
        elapsed = time.perf_counter() - t0
        return {
            "script":    script_name,
            "rc":        proc.returncode,
            "elapsed_s": round(elapsed, 1),
            "stdout":    proc.stdout[-4000:],   # last 4KB
            "stderr":    proc.stderr[-2000:],
        }
    except subprocess.TimeoutExpired:
        return {"script": script_name, "status": "TIMEOUT",
                "elapsed_s": timeout_s, "stdout": "", "stderr": "Timed out"}
    except Exception as e:
        return {"script": script_name, "status": f"ERROR:{type(e).__name__}",
                "elapsed_s": time.perf_counter() - t0,
                "stdout": "", "stderr": str(e)}


# ── Result aggregation ────────────────────────────────────────────────

def _aggregate_findings(
    doc_id: str,
    typologies: list[str],
    subprocess_results: list[dict] | None = None,
) -> dict[str, dict]:
    """Query kg_nodes for ValidationFinding rows; classify each typology
    via the Bug-C verdict taxonomy (COMPLIANT_FIRED / SKIP_NOT_APPLICABLE
    / UNVERIFIED / GAP_VIOLATION / HARD_BLOCK).

    Reads `properties.verdict` first (Bug-C explicit verdicts). On
    empty rows for a typology, emits VALIDATOR_NOT_MIGRATED — a
    regression alarm, NOT a default to COMPLIANT. Pre-Bug-C the
    aggregator collapsed every empty-rows verdict to COMPLIANT,
    silently masking three different mechanisms. Post-Bug-C every
    typology must have at least one row per run.

    Empty-cell branching (Bug-C + (b)-prime crash resilience):

      • If `subprocess_results` carries this typology with rc≠0
        (subprocess crashed) AND no row arrived in the KG, emit a
        synthetic UNVERIFIED row tagged `failure_path=subprocess_crashed`
        and use it as the headline. The validator-side wrapper
        (`main_with_crash_resilience`) normally commits this row
        itself; the aggregator's emit is a fallback for the case
        where the wrapper never ran (e.g. import-time exception or
        SIGKILL before the except-block).

      • Otherwise (rc=0 OR rc not provided AND no row): emit
        VALIDATOR_NOT_MIGRATED. NOTE: this still lumps together
        "script not in registry" (true catalogue gap) with
        "rc=0 + in registry + no row" (silent_compliant_exit — a
        validator that returned 0 without emitting any verdict
        row). The latter is a real bug that this pass does not
        close; tracked separately. If we observe rc=0 cells on
        registered typologies post-(b)-prime, that's the signal
        to add the silent_compliant_exit aggregator branch.
    """
    rows = _rest_get("kg_nodes", {
        "doc_id":    f"eq.{doc_id}",
        "node_type": "eq.ValidationFinding",
        "select":    ("node_id,label,properties->>typology_code,"
                      "properties->>severity,properties->>status,"
                      "properties->>verdict,"
                      "properties->>violation_reason,"
                      "properties->>evidence,"
                      "properties->>evidence_quote,"
                      "properties->>evidence_section_heading,"
                      "properties->>evidence_line_no_local,"
                      "properties->>failure_path,"
                      "properties->>failed_condition,"
                      "properties->>skip_reason_human,"
                      "properties->>clause_id,"
                      "properties->>source_file"),
    })
    findings_by_typology: dict[str, list[dict]] = {}
    for r in rows:
        tc = r.get("typology_code") or "UNKNOWN"
        findings_by_typology.setdefault(tc, []).append(r)

    edges = _rest_get("kg_edges", {
        "doc_id":    f"eq.{doc_id}",
        "edge_type": "eq.VIOLATES_RULE",
        "select":    "edge_id,properties->>typology",
    })
    edges_by_typology: dict[str, int] = {}
    for e in edges:
        t = e.get("typology") or "UNKNOWN"
        edges_by_typology[t] = edges_by_typology.get(t, 0) + 1

    # Verdict severity rank for picking the headline row when multiple
    # exist for the same typology. Higher = more important.
    _RANK = {
        "HARD_BLOCK":          5,
        "GAP_VIOLATION":       4,
        "UNVERIFIED":          3,
        "SKIP_NOT_APPLICABLE": 2,
        "COMPLIANT_FIRED":     1,
    }

    def _verdict_of(r: dict) -> str:
        """Return Bug-C verdict from row, with fallback to legacy `status`
        for the 73 corpus rows that pre-date Bug C."""
        v = r.get("verdict")
        if v:
            return v
        s = (r.get("status") or "").upper()
        sev = (r.get("severity") or "").upper()
        if s == "OPEN" and sev == "HARD_BLOCK":  return "HARD_BLOCK"
        if s == "OPEN":                          return "GAP_VIOLATION"
        if s == "UNVERIFIED":                    return "UNVERIFIED"
        if s == "SKIP":                          return "SKIP_NOT_APPLICABLE"
        if s == "COMPLIANT":                     return "COMPLIANT_FIRED"
        return "UNVERIFIED"  # safest default

    def _mechanism_evidence(r: dict, verdict: str) -> str:
        """Compose a per-verdict mechanism-evidence blurb for the formatter."""
        if verdict == "COMPLIANT_FIRED":
            line = r.get("evidence_line_no_local")
            quote = (r.get("evidence_quote") or r.get("evidence") or "")[:200]
            heading = (r.get("evidence_section_heading") or "")[:80]
            cid = r.get("clause_id") or ""
            return (f"line {line} ({heading}): {quote}"
                    + (f" · clause={cid}" if cid else ""))
        if verdict == "SKIP_NOT_APPLICABLE":
            reason = r.get("skip_reason_human") or ""
            return reason[:240]
        if verdict == "UNVERIFIED":
            fp = r.get("failure_path") or "unspecified"
            return f"failure_path={fp}"
        if verdict in ("GAP_VIOLATION", "HARD_BLOCK"):
            line = r.get("evidence_line_no_local")
            quote = (r.get("evidence_quote") or r.get("evidence") or "")[:200]
            reason = r.get("violation_reason") or ""
            return (f"reason={reason}"
                    + (f" · evidence@line {line}: {quote}" if quote else ""))
        return ""

    # Index subprocess_results by typology for fast crash-classification.
    crashed_by_typology: dict[str, dict] = {}
    if subprocess_results:
        for sr in subprocess_results:
            t = sr.get("typology")
            rc = sr.get("rc")
            if t and rc is not None and rc != 0:
                crashed_by_typology[t] = sr

    report: dict[str, dict] = {}
    for typology in typologies:
        rows = findings_by_typology.get(typology, [])
        n_edges = edges_by_typology.get(typology, 0)
        if not rows:
            # (b)-prime crash resilience: distinguish a crashed
            # subprocess (rc≠0) from a true catalogue gap.
            crash_sr = crashed_by_typology.get(typology)
            if crash_sr is not None:
                # Validator's own wrapper should have committed an
                # UNVERIFIED subprocess_crashed row; if we still see
                # zero rows, emit one ourselves so the cell is never
                # silently empty. (See aggregator docstring.)
                stderr_tail = (crash_sr.get("stderr") or "")[-200:]
                try:
                    from modules.validation.verdict_emitter import (
                        emit_verdict_row,
                    )
                    emit_verdict_row(
                        doc_id=doc_id, typology=typology, rule_id=None,
                        verdict="UNVERIFIED",
                        failure_path="subprocess_crashed",
                        severity="ADVISORY",
                        evidence_quote=stderr_tail or
                            f"subprocess rc={crash_sr.get('rc')}",
                        retrieval_debug={
                            "rc":        crash_sr.get("rc"),
                            "elapsed_s": crash_sr.get("elapsed_s"),
                            "source":    "aggregator_fallback",
                        },
                    )
                except Exception:
                    pass
                report[typology] = {
                    "status":   "UNVERIFIED",
                    "verdict":  "UNVERIFIED",
                    "severity": "ADVISORY",
                    "detail":   (f"failure_path=subprocess_crashed "
                                 f"(rc={crash_sr.get('rc')}, "
                                 f"elapsed={crash_sr.get('elapsed_s')}s)"),
                    "mechanism_evidence":
                        f"failure_path=subprocess_crashed",
                    "n_findings": 0, "n_edges": 0,
                    "n_unverified": 1,
                }
                continue
            # No crash signal — Bug C regression alarm: post-migration
            # every registered validator MUST emit at least one row per
            # run. Empty-rows here is either:
            #   • a script not in DEFAULT_CHECKS (true catalogue gap), OR
            #   • silent_compliant_exit (rc=0, in registry, but the
            #     script returned 0 without emitting a row — a real bug
            #     this pass does not close).
            report[typology] = {
                "status":   "VALIDATOR_NOT_MIGRATED",
                "verdict":  "VALIDATOR_NOT_MIGRATED",
                "detail":   (f"Validator for {typology} did not emit a row. "
                             "This is a pre-Bug-C regression — flag for fix."),
                "n_findings": 0, "n_edges": 0,
                "mechanism_evidence": "",
            }
            continue
        # Sort rows by verdict severity rank — the highest-severity row
        # is the headline verdict for the typology.
        rows_with_verdict = [(r, _verdict_of(r)) for r in rows]
        rows_with_verdict.sort(
            key=lambda rv: _RANK.get(rv[1], 0), reverse=True,
        )
        primary, primary_verdict = rows_with_verdict[0]
        n_hb   = sum(1 for r, v in rows_with_verdict if v == "HARD_BLOCK")
        n_gap  = sum(1 for r, v in rows_with_verdict if v == "GAP_VIOLATION")
        n_unv  = sum(1 for r, v in rows_with_verdict if v == "UNVERIFIED")
        n_skip = sum(1 for r, v in rows_with_verdict if v == "SKIP_NOT_APPLICABLE")
        n_comp = sum(1 for r, v in rows_with_verdict if v == "COMPLIANT_FIRED")
        # `status` retained for back-compat with portal code that reads it
        status_legacy = primary.get("status") or _STATUS_LEGACY_MAP.get(primary_verdict, "")
        report[typology] = {
            "verdict":             primary_verdict,
            "status":              status_legacy,    # back-compat
            "detail":              _mechanism_evidence(primary, primary_verdict),
            "mechanism_evidence":  _mechanism_evidence(primary, primary_verdict),
            "severity":            primary.get("severity"),
            "n_findings":          len(rows),
            "n_edges":              n_edges,
            "n_hard_block":         n_hb,
            "n_gap_violation":      n_gap,
            "n_unverified":         n_unv,
            "n_skip_not_applicable": n_skip,
            "n_compliant_fired":    n_comp,
        }
    return report


# Verdict → legacy `status` for portal back-compat (pre-Bug-C consumers
# read .status; Bug-C consumers read .verdict). Mirror of the helper's
# _STATUS_MAP — duplicated locally so the aggregator can stay self-
# contained.
_STATUS_LEGACY_MAP = {
    "COMPLIANT_FIRED":      "COMPLIANT",
    "SKIP_NOT_APPLICABLE":  "SKIP",
    "UNVERIFIED":           "UNVERIFIED",
    "GAP_VIOLATION":        "OPEN",
    "HARD_BLOCK":           "OPEN",
    "VALIDATOR_NOT_MIGRATED": "VALIDATOR_NOT_MIGRATED",
}


# ── Cleanup ───────────────────────────────────────────────────────────

def cleanup_draft_doc(doc_id: str) -> dict:
    """Delete kg_nodes, kg_edges, and Qdrant points for a draft doc_id.
    Markdown files on disk are NOT touched — those are the user's draft
    output.

    Safe to call even if some artefacts don't exist; counts are
    approximate (Qdrant doesn't return deleted-count consistently)."""
    n_edges = 0
    n_nodes = 0
    try:
        edges = _rest_get("kg_edges", {"doc_id": f"eq.{doc_id}", "select": "edge_id"})
        for e in edges:
            _rest_delete("kg_edges", {"edge_id": f"eq.{e['edge_id']}"})
            n_edges += 1
    except Exception as e:
        return {"status": "edge_delete_failed", "error": str(e)}
    try:
        nodes = _rest_get("kg_nodes", {"doc_id": f"eq.{doc_id}", "select": "node_id"})
        for n in nodes:
            _rest_delete("kg_nodes", {"node_id": f"eq.{n['node_id']}"})
            n_nodes += 1
    except Exception as e:
        return {"status": "node_delete_failed", "error": str(e),
                "n_edges_deleted": n_edges}
    n_qdrant = 0
    try:
        # Qdrant filter-by-payload delete
        body = {"filter": {"must": [{"key": "doc_id", "match": {"value": doc_id}}]}}
        r = requests.post(
            f"{QDRANT_URL}/collections/{QDRANT_COLL}/points/delete",
            json=body, timeout=30,
        )
        if r.ok:
            j = r.json()
            n_qdrant = j.get("result", {}).get("operation_id") or 0
    except Exception:
        pass
    # Remove the staged markdown copy from sample_tenders/processed_md/.
    # The original /tmp/draft_X.md is preserved (caller's responsibility).
    staged_dir = REPO / "source_documents" / "sample_tenders" / "processed_md"
    n_staged_removed = 0
    for p in staged_dir.glob(f"draft_{doc_id.replace('draft_', '')}.md"):
        try:
            p.unlink(); n_staged_removed += 1
        except OSError:
            pass
    return {
        "status":            "ok",
        "doc_id":            doc_id,
        "n_nodes_deleted":   n_nodes,
        "n_edges_deleted":   n_edges,
        "qdrant_op_id":      n_qdrant,
        "n_staged_removed":  n_staged_removed,
    }


# ── Public API ────────────────────────────────────────────────────────

def run_tier1_on_draft(
    *,
    draft_path: str | Path,
    thread_id:  str,
    facts:      dict,
    checks:     list[tuple[str, str]] | None = None,
    timeout_per_check_s: int = 180,
    cleanup:    bool = False,
) -> dict:
    """End-to-end: ingest the draft, run tier1 checks, aggregate report.

    Args:
        draft_path: path to the draft markdown file
        thread_id:  drafter session thread_id (used to derive doc_id)
        facts:      Drafter's officer_facts dict (tender_type, ecv_cr,
                    duration_months, is_ap_tender, …)
        checks:     list of (script_name, typology_code) tuples;
                    DEFAULT_CHECKS if None
        timeout_per_check_s: per-script subprocess timeout
        cleanup:    if True, delete the draft doc_id's KG + Qdrant
                    artefacts after report is built. Markdown file is
                    preserved.

    Returns:
        {
          "doc_id":             "draft_<thread_id>",
          "kg_summary":         { …kg_builder.KGSummary as dict… },
          "subprocess_results": [ { script, rc, elapsed_s, stdout… }, … ],
          "validation_report":  { typology: { status, detail, … }, … },
          "summary":            { n_compliant, n_gap, n_unverified,
                                  total_elapsed_s },
          "cleanup":            { …if cleanup=True… }
        }
    """
    draft_path = Path(draft_path).resolve()
    if not draft_path.exists():
        raise FileNotFoundError(f"draft markdown not found: {draft_path}")

    doc_id = f"draft_{thread_id}"
    selected_checks = checks or DEFAULT_CHECKS

    t_total = time.perf_counter()

    # 1. Stage the draft into a processed_md root so the tier1 scripts'
    # _slice_source_file() (which scans PROCESSED_MD_ROOTS for the bare
    # filename recorded on each Section node) can find it. We use
    # source_documents/sample_tenders/processed_md/ — the kg_builder
    # already searches it as the second root.
    staged_dir = REPO / "source_documents" / "sample_tenders" / "processed_md"
    staged_dir.mkdir(parents=True, exist_ok=True)
    staged_path = staged_dir / draft_path.name
    if staged_path.resolve() != draft_path.resolve():
        staged_path.write_text(draft_path.read_text(encoding="utf-8"),
                               encoding="utf-8")
    print(f"[run_tier1_on_draft] phase 1/4: build_kg(doc_id={doc_id!r}) "
          f"from staged {staged_path}")
    sys.path.insert(0, str(REPO / "experiments" / "tender_graph"))
    from kg_builder import build_kg
    summary = build_kg(
        doc_id=doc_id,
        document=staged_path,
        document_name=f"DraftTender:{thread_id}",
        clear_existing=True,
    )
    kg_summary = {
        "doc_id":        summary.doc_id,
        "nodes_by_type": summary.nodes_by_type,
        "edges_by_type": summary.edges_by_type,
        "timing_ms":     summary.timing_ms,
        "staged_path":   str(staged_path),
    }

    # 2. Override TenderDocument facts with the Drafter's known values
    print(f"[run_tier1_on_draft] phase 2/4: override TenderDocument facts")
    _override_tender_document_facts(doc_id, facts)

    # 3. Run each requested tier1 check as a subprocess
    print(f"[run_tier1_on_draft] phase 3/4: run {len(selected_checks)} tier1 checks")
    subprocess_results: list[dict] = []
    for script_name, typology in selected_checks:
        print(f"  → {script_name} (typology={typology}) …", flush=True)
        result = _run_one_check(script_name, doc_id,
                                timeout_s=timeout_per_check_s)
        subprocess_results.append({**result, "typology": typology})
        rc = result.get("rc", "—")
        print(f"     rc={rc}  elapsed={result.get('elapsed_s')}s")

    # 4. Aggregate findings into validation_report. Pass
    #    subprocess_results so the aggregator can distinguish a crashed
    #    validator (rc≠0) from a true catalogue gap when zero rows
    #    arrived.
    print(f"[run_tier1_on_draft] phase 4/4: aggregate KG findings")
    typologies = [t for _, t in selected_checks]
    validation_report = _aggregate_findings(doc_id, typologies,
                                            subprocess_results=subprocess_results)

    n_comp = sum(1 for v in validation_report.values() if v["status"] == "COMPLIANT")
    n_gap  = sum(1 for v in validation_report.values() if v["status"] == "GAP_VIOLATION")
    n_unv  = sum(1 for v in validation_report.values() if v["status"] == "UNVERIFIED")

    cleanup_result = None
    if cleanup:
        print(f"[run_tier1_on_draft] cleanup: deleting draft KG artefacts")
        cleanup_result = cleanup_draft_doc(doc_id)

    total_elapsed = round(time.perf_counter() - t_total, 1)
    return {
        "doc_id":             doc_id,
        "kg_summary":         kg_summary,
        "subprocess_results": subprocess_results,
        "validation_report":  validation_report,
        "summary": {
            "n_compliant":       n_comp,
            "n_gap_violation":   n_gap,
            "n_unverified":      n_unv,
            "total_typologies":  len(typologies),
            "total_elapsed_s":   total_elapsed,
        },
        "cleanup": cleanup_result,
    }


# ── CLI ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import json
    ap = argparse.ArgumentParser()
    ap.add_argument("--draft-path", required=True)
    ap.add_argument("--thread-id",  required=True)
    ap.add_argument("--tender-type", default="Works")
    ap.add_argument("--is-ap-tender", default="true")
    ap.add_argument("--ecv-cr",     type=float, default=85.0)
    ap.add_argument("--duration-months", type=int, default=18)
    ap.add_argument("--cleanup",   action="store_true")
    a = ap.parse_args()
    facts = {
        "tender_type":     a.tender_type,
        "is_ap_tender":    a.is_ap_tender.lower() in ("true", "1", "yes"),
        "ecv_cr":          a.ecv_cr,
        "duration_months": a.duration_months,
    }
    out = run_tier1_on_draft(
        draft_path=a.draft_path,
        thread_id=a.thread_id,
        facts=facts,
        cleanup=a.cleanup,
    )
    print(json.dumps(out, indent=2, default=str))
