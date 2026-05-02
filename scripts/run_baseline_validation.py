"""
scripts/run_baseline_validation.py

Run the regex-only validator against the full e_procurement corpus and save
a structured results.json plus a summary table.

This is the v0 BASELINE — the pure-regex pipeline (no SHACL, no vector
search). Every number recorded here is the benchmark against which future
classifier / matcher / defeasibility refinements will be measured.

Usage:
    python scripts/run_baseline_validation.py
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from modules.validator.rule_verification_engine import (
    RuleVerificationEngine,
    ValidationReport,
)


REPO = Path(__file__).resolve().parent.parent
MD = REPO / "source_documents" / "e_procurement" / "processed_md"
OUT_DIR = REPO / "data" / "validation_tests" / "regex_baseline"


# ─── Test plan ──────────────────────────────────────────────────────────────

VIZAG_BUNDLE = {
    "id":   "vizag_ugss_pkg_2",
    "name": "Vizag UGSS Pkg-2 (5-volume bundle)",
    "kind": "bundle",
    "files": [
        "1_Volume_I_NIT_&_Bidding_Process.md",
        "2 VOLUME II Scope of work.md",
        "3 Volume_III _GCC,_SCC.md",
        "3.3A_Schedules.md",
        "4 VOLUME IV Bill of Quantiites.md",
    ],
    "estimated_value_override": 350_00_00_000,   # Rs.350 cr from external NIT page
    "ground_truth": {
        "expected_type":     ["EPC", "Works"],
        "expected_is_ap":    True,
        "known_issues":      ["PBG-Shortfall (2.5%)", "Missing-Integrity-Pact"],
        "ap_acceptable":     ["Civil-court arbitration (defeated by AP-GO-229)"],
    },
}

SINGLES = [
    {
        "id":   "sbdpkg11_kakinada",
        "name": "SBDPKG11Kakinadafinalrev.md",
        "file": "SBDPKG11Kakinadafinalrev.md",
        "ground_truth": {"expected_type": ["Works", "EPC"], "expected_is_ap": True},
    },
    {
        "id":   "high_court",
        "name": "High Court bid document",
        "file": "High court  bid document.md",
        "ground_truth": {"expected_type": ["EPC", "Works"], "expected_is_ap": True},
    },
    {
        "id":   "judicial_academy",
        "name": "Bid Document of Judicial Academy",
        "file": "Bid Document of Judicial Academy.md",
        "ground_truth": {"expected_type": ["EPC", "Works"], "expected_is_ap": True},
    },
    {
        "id":   "rfp_vijayawada",
        "name": "RFP_Vijayawada_NITI_01042026 (15 MW WtE PPP)",
        "file": "RFP_Vijayawada_NITI_01042026.md",
        "ground_truth": {"expected_type": ["EPC"], "expected_is_ap": True},
    },
    {
        "id":   "rfp_tirupathi",
        "name": "RFP_Tirupathi_NITI_01042026 (12 MW WtE PPP)",
        "file": "RFP_Tirupathi_NITI_01042026.md",
        "ground_truth": {"expected_type": ["EPC"], "expected_is_ap": True},
    },
]


# ─── Helpers ────────────────────────────────────────────────────────────────

def _summarise_findings(findings) -> list[dict]:
    return [
        {
            "typology_code":      f.typology_code,
            "severity":           f.severity,
            "primary_rule_id":    f.rule_id,
            "rules_fired":        f.rules_fired,
            "triggered_rule_ids": f.triggered_rule_ids,
            "defeated_by":        f.defeated_by,
            "evidence":           f.evidence_text[:300] + ("…" if len(f.evidence_text) > 300 else ""),
            "source_clause":      f.source_clause,
            "layer":              f.layer,
        }
        for f in findings
    ]


def _record_one(report: ValidationReport, test_meta: dict, error: str | None = None) -> dict:
    if error or report is None:
        return {
            "id":                 test_meta["id"],
            "name":               test_meta["name"],
            "ground_truth":       test_meta.get("ground_truth", {}),
            "error":              error,
        }
    c = report.classification
    p = report.parameters
    return {
        "id":                     test_meta["id"],
        "name":                   test_meta["name"],
        "ground_truth":           test_meta.get("ground_truth", {}),
        "document_name":          report.document_name,
        "timestamp":              report.timestamp,
        "processing_time_ms":     report.processing_time_ms,
        "classification": {
            "primary_type":         c.primary_type,
            "procurement_method":   c.procurement_method,
            "cover_system":         c.cover_system,
            "estimated_value":      c.estimated_value,
            "duration_months":      c.duration_months,
            "department":           c.department,
            "is_ap_tender":         c.is_ap_tender,
            "funding_source":       c.funding_source,
            "special_flags":        c.special_flags,
            "confidence":           c.confidence,
            "needs_human_confirm":  c.needs_human_confirmation,
        },
        "parameters": {
            "emd_percentage":              p.emd_percentage,
            "pbg_percentage":              p.pbg_percentage,
            "bid_validity_days":           p.bid_validity_days,
            "dlp_months":                  p.dlp_months,
            "integrity_pact_required":     p.integrity_pact_required,
            "reverse_tender_mandatory":    p.reverse_tender_mandatory,
            "judicial_preview_required":   p.judicial_preview_required,
            "e_procurement_mandatory":     p.e_procurement_mandatory,
            "open_tender_required":        p.open_tender_required,
            "two_cover_required":          p.two_cover_required,
            "arbitration_allowed":         p.arbitration_allowed,
            "price_adjustment_applicable": p.price_adjustment_applicable,
        },
        "verdict": {
            "overall_status":   report.overall_status,
            "score":            report.score,
            "rules_checked":    report.rules_checked,
            "rules_passed":     report.rules_passed,
            "hard_blocks":      _summarise_findings(report.hard_blocks),
            "warnings":         _summarise_findings(report.warnings),
            "advisories":       _summarise_findings(report.advisories),
        },
    }


# ─── Annotation: false positives, false negatives, classification errors ──

def _annotate(record: dict) -> dict:
    """Compare against `ground_truth` to flag false positives, false negatives,
    and classification errors. The annotations are deliberately conservative —
    only things we can identify with high confidence are flagged."""
    if record.get("error"):
        return {
            "false_positives":  [],
            "false_negatives":  [],
            "classification_errors": ["Run errored before producing report"],
        }

    gt = record.get("ground_truth") or {}
    cls = record.get("classification") or {}
    verdict = record.get("verdict") or {}
    fps: list[str] = []
    fns: list[str] = []
    cls_errors: list[str] = []

    # Classification errors
    expected_types = gt.get("expected_type")
    if expected_types and cls.get("primary_type") not in expected_types:
        cls_errors.append(
            f"Type misclassified: expected one of {expected_types}, "
            f"got {cls.get('primary_type')}"
        )
    if "expected_is_ap" in gt and cls.get("is_ap_tender") != gt["expected_is_ap"]:
        cls_errors.append(
            f"AP-tender flag wrong: expected {gt['expected_is_ap']}, "
            f"got {cls.get('is_ap_tender')}"
        )

    # False negatives — "known issues" not in findings
    found_typologies = {f["typology_code"] for f in (
        verdict.get("hard_blocks", []) + verdict.get("warnings", []) + verdict.get("advisories", [])
    )}
    for known in gt.get("known_issues", []):
        # known is a label like "PBG-Shortfall (2.5%)" — match by typology prefix
        if not any(known.startswith(t) or t in known for t in found_typologies):
            fns.append(f"Missed: {known}")

    # AP-acceptable departures should appear as ADVISORY [defeated_by ...]
    for accepted in gt.get("ap_acceptable", []):
        adv = verdict.get("advisories", [])
        if not any(a.get("defeated_by") for a in adv):
            fns.append(
                f"AP-acceptable case not surfaced as defeated advisory: {accepted}"
            )

    # False positives — look for findings on patterns clearly not present
    # (e.g. JV ban flagged when document explicitly permits JVs).
    for f in verdict.get("hard_blocks", []) + verdict.get("warnings", []):
        # If a typology relates to JV ban but document mentions "joint venture
        # permitted", that's a likely FP. (Heuristic; only flagged when high-conf.)
        # In v0 we don't have access to the document text here — leave to manual.
        pass

    return {
        "false_positives":       fps,
        "false_negatives":       fns,
        "classification_errors": cls_errors,
    }


# ─── Main ───────────────────────────────────────────────────────────────────

def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    engine = RuleVerificationEngine()
    results: list[dict] = []

    # 1. Bundle
    bundle = VIZAG_BUNDLE
    print(f"\n>>> [1/{len(SINGLES)+1}] {bundle['name']}")
    file_paths = [str(MD / f) for f in bundle["files"]]
    try:
        report = engine.verify_bundle(
            file_paths,
            document_name=bundle["name"],
            estimated_value_override=bundle.get("estimated_value_override"),
        )
        rec = _record_one(report, bundle)
    except Exception as e:
        traceback.print_exc()
        rec = _record_one(None, bundle, error=f"{type(e).__name__}: {e}")
    rec["annotations"] = _annotate(rec)
    results.append(rec)

    # 2. Singles
    for i, s in enumerate(SINGLES, start=2):
        print(f"\n>>> [{i}/{len(SINGLES)+1}] {s['name']}")
        path = MD / s["file"]
        if not path.exists():
            rec = _record_one(None, s, error=f"File not found: {path}")
        else:
            try:
                t0 = time.perf_counter()
                report = engine.verify(path.read_text(), document_name=s["file"])
                rec = _record_one(report, s)
            except Exception as e:
                traceback.print_exc()
                rec = _record_one(None, s, error=f"{type(e).__name__}: {e}")
        rec["annotations"] = _annotate(rec)
        results.append(rec)

    # ── Save full results ──
    out_file = OUT_DIR / "results.json"
    bundle_meta = {
        "generated_at":     datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "validator_version": "v0-regex-baseline",
        "rule_corpus_size":  results[0].get("verdict", {}).get("rules_checked", "unknown"),
        "tests_total":       len(results),
        "results":           results,
    }
    out_file.write_text(json.dumps(bundle_meta, indent=2, ensure_ascii=False))
    print(f"\nFull results written to: {out_file.relative_to(REPO)}")

    # ── Print summary table ──
    print()
    print("=" * 132)
    print(f"{'#':>2}  {'Document':<48}{'Type':<14}{'Value':>15}{'Status':<14}{'Score':>7}{'HB':>4}{'W':>4}{'A':>4}{'Time':>9}")
    print("-" * 132)
    for i, r in enumerate(results, start=1):
        if r.get("error"):
            print(f"{i:>2}  {r['name'][:47]:<48}ERROR: {r['error'][:80]}")
            continue
        c = r["classification"]; v = r["verdict"]
        val = c.get("estimated_value")
        val_s = "—" if val is None else (f"₹{val/1e7:.1f}cr" if val >= 1e7 else f"₹{val/1e5:.1f}L")
        print(
            f"{i:>2}  {r['name'][:47]:<48}"
            f"{c.get('primary_type','?'):<14}"
            f"{val_s:>15}"
            f"{v.get('overall_status',''):<14}"
            f"{v.get('score','—'):>7}"
            f"{len(v.get('hard_blocks',[])):>4}"
            f"{len(v.get('warnings',[])):>4}"
            f"{len(v.get('advisories',[])):>4}"
            f"{r.get('processing_time_ms','—'):>7}ms"
        )
    print("=" * 132)

    # ── Print per-test findings & annotations ──
    print("\n=== PER-DOCUMENT FINDINGS ===")
    for r in results:
        print(f"\n● {r['name']}")
        if r.get("error"):
            print(f"  ERROR: {r['error']}")
            continue
        v = r["verdict"]
        for label, items in (
            ("HARD_BLOCK", v["hard_blocks"]),
            ("WARNING",    v["warnings"]),
            ("ADVISORY",   v["advisories"]),
        ):
            for f in items:
                defeat = f" [defeated_by={','.join(f['defeated_by'])}]" if f["defeated_by"] else ""
                print(
                    f"  · {label:10s} {f['typology_code']:30s} primary={f['primary_rule_id']:<14s} "
                    f"rules_fired={f['rules_fired']:<3d}{defeat}"
                )
        ann = r["annotations"]
        if ann["false_positives"]:
            print(f"  ⚠  FALSE POSITIVES:        {ann['false_positives']}")
        if ann["false_negatives"]:
            print(f"  ⚠  FALSE NEGATIVES:        {ann['false_negatives']}")
        if ann["classification_errors"]:
            print(f"  ⚠  CLASSIFICATION ERRORS:  {ann['classification_errors']}")

    # ── Aggregate FP/FN/ClsErr counts ──
    total_fp = sum(len(r["annotations"]["false_positives"]) for r in results)
    total_fn = sum(len(r["annotations"]["false_negatives"]) for r in results)
    total_ce = sum(len(r["annotations"]["classification_errors"]) for r in results)
    print(f"\n=== AGGREGATE BENCHMARK ===")
    print(f"  documents tested:        {len(results)}")
    print(f"  total false positives:   {total_fp}")
    print(f"  total false negatives:   {total_fn}")
    print(f"  classification errors:   {total_ce}")
    print(f"  results saved to:        {out_file.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
