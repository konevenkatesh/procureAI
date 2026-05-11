"""
scripts/run_eligibility_matrix.py

═══════════════════════════════════════════════════════════════════
  Sub-block 4 — EligibilityMatrix Aggregator
═══════════════════════════════════════════════════════════════════
Reads all 90 BidEvaluationFinding rows (10 Tier-2 validators × 9
synthetic bids), groups by (bidder_profile_id, tender_id), and
emits one EligibilityMatrix kg_node per (bidder, tender) pair
with aggregate verdict + citation drilldown.

Aggregation precedence (per approved diagnose):
    HARD_BLOCK > WARNING > GAP > QUALIFIED
  - DISQUALIFIED  if ANY criterion INELIGIBLE+HARD_BLOCK
  - FLAGGED_FOR_COMMITTEE_REVIEW  if ANY INELIGIBLE+WARNING (and no HARD_BLOCK)
  - MARK_FOR_DOCUMENTATION_REVIEW if ANY GAP_INSUFFICIENT_DATA  (and no above)
  - QUALIFIED     else
  SKIP_NOT_APPLICABLE outcomes are neutral; surfaced in skip_criteria[]
  for audit completeness.

Pure aggregator — emits no edges. Citation drilldown via:
  - finding_node_ids[]              : array of underlying finding UUIDs
  - finding_typology_to_node_id     : O(1) typology→finding_id lookup dict

Idempotency: _delete_prior_eligibility_matrix_rows() filters on
source_ref="sub_block_4:eligibility_matrix_aggregator_v1" before
re-emit. Same delete-then-emit pattern as Tier-2 validators.

Crash resilience: single-batch main_with_crash_resilience wrapper
with synthetic doc_id="eligibility_matrix_aggregator_v1" and
typology="EligibilityMatrix". Wrapper's DeferredCleanup is harmless
(no prior ValidationFindings under that doc_id).

Vocabulary coverage gap on synthetic corpus (per L66 queue):
  - FLAGGED_FOR_COMMITTEE_REVIEW never fires today (every B3 bid
    with WARNING-litigation also carries HARD_BLOCK failures, so
    precedence pushes to DISQUALIFIED)
  - MARK_FOR_DOCUMENTATION_REVIEW never fires today (zero GAP
    outcomes across all 90 findings)
  Aggregator implements all 4 states for forward-compatibility;
  L66 extends synthetic seed to exercise the missing 2.
═══════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import os
import sys
import time
import requests
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from builder.config import settings


# ── Constants ─────────────────────────────────────────────────────────

TYPOLOGY = "EligibilityMatrix"  # for crash-resilience wrapper
AGGREGATOR_DOC_ID = "eligibility_matrix_aggregator_v1"
SOURCE_REF = "sub_block_4:eligibility_matrix_aggregator_v1"


REST = settings.supabase_rest_url
H = {"apikey": settings.supabase_anon_key,
     "Authorization": f"Bearer {settings.supabase_anon_key}"}


def rest_get(path, params=None, range_header=None):
    headers = {**H}
    if range_header:
        headers["Range"] = range_header
    r = requests.get(f"{REST}/rest/v1/{path}", params=params or {},
                     headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()


def rest_post(path, body):
    r = requests.post(
        f"{REST}/rest/v1/{path}", json=body,
        headers={**H, "Content-Type": "application/json",
                 "Prefer": "return=representation"}, timeout=30,
    )
    r.raise_for_status()
    return r.json()


def rest_delete(path, params=None):
    r = requests.delete(f"{REST}/rest/v1/{path}", params=params or {},
                        headers=H, timeout=30)
    r.raise_for_status()


def rest_count(path, params=None):
    """Return total row count via Prefer: count=exact."""
    r = requests.get(
        f"{REST}/rest/v1/{path}",
        params={**(params or {}), "select": "doc_id" if path == "kg_nodes" else "edge_id"},
        headers={**H, "Prefer": "count=exact", "Range": "0-0"},
        timeout=30,
    )
    cr = r.headers.get("Content-Range") or ""
    try:
        return int(cr.split("/")[-1])
    except ValueError:
        return -1


# ── Paginated read of all BidEvaluationFinding rows ──────────────────

PAGE_SIZE = 100


def fetch_all_bid_evaluation_findings() -> list[dict]:
    """Paginated read via PostgREST Range header. PostgREST page-caps at
    1000 by default; we use 100 to stay safely under any deployment-side
    caps and keep request size sane."""
    out: list[dict] = []
    page = 0
    while True:
        start = page * PAGE_SIZE
        end = start + PAGE_SIZE - 1
        rows = rest_get("kg_nodes", {
            "select":    "node_id,doc_id,label,properties",
            "node_type": "eq.BidEvaluationFinding",
            "order":     "doc_id.asc,properties->>typology_code.asc",
        }, range_header=f"{start}-{end}")
        out.extend(rows)
        if len(rows) < PAGE_SIZE:
            break
        page += 1
    return out


# ── Aggregation logic ────────────────────────────────────────────────

def aggregate_verdict(findings: list[dict]) -> str:
    """4-state precedence: HARD_BLOCK > WARNING > GAP > QUALIFIED.
    findings: list of property dicts (one per Tier-2 criterion for a
    given (bidder, tender) pair)."""
    has_hard_block = any(
        f.get("verdict") == "INELIGIBLE"
        and f.get("evaluation_consequence") == "HARD_BLOCK"
        for f in findings
    )
    has_warning = any(
        f.get("verdict") == "INELIGIBLE"
        and f.get("evaluation_consequence") == "WARNING"
        for f in findings
    )
    has_gap = any(f.get("verdict") == "GAP_INSUFFICIENT_DATA" for f in findings)
    if has_hard_block:
        return "DISQUALIFIED"
    if has_warning:
        return "FLAGGED_FOR_COMMITTEE_REVIEW"
    if has_gap:
        return "MARK_FOR_DOCUMENTATION_REVIEW"
    return "QUALIFIED"


def classify_outcomes(findings_with_ids: list[tuple[str, dict]]) -> dict:
    """Bucket the 10 findings for a pair into outcome-class lists.
    Returns dict with qualified/hard_block/warning/gap/skip typology
    lists and a typology→node_id dict for drilldown."""
    qualified, hard_block, warning, gap, skip = [], [], [], [], []
    typo_to_node = {}
    for node_id, p in findings_with_ids:
        typo = p.get("typology_code")
        typo_to_node[typo] = node_id
        v = p.get("verdict")
        c = p.get("evaluation_consequence")
        if v == "QUALIFIED":
            qualified.append(typo)
        elif v == "INELIGIBLE" and c == "HARD_BLOCK":
            hard_block.append(typo)
        elif v == "INELIGIBLE" and c == "WARNING":
            warning.append(typo)
        elif v == "GAP_INSUFFICIENT_DATA":
            gap.append(typo)
        elif v == "SKIP_NOT_APPLICABLE":
            skip.append(typo)
    return {
        "qualified_criteria":  sorted(qualified),
        "hard_block_criteria": sorted(hard_block),
        "warning_criteria":    sorted(warning),
        "gap_criteria":        sorted(gap),
        "skip_criteria":       sorted(skip),
        "finding_typology_to_node_id": typo_to_node,
    }


def compose_aggregate_reasoning(verdict: str, classification: dict) -> str:
    """Template-composed text summarising the aggregate verdict."""
    hb = classification["hard_block_criteria"]
    w  = classification["warning_criteria"]
    g  = classification["gap_criteria"]
    q  = classification["qualified_criteria"]
    if verdict == "QUALIFIED":
        return (f"QUALIFIED — all {len(q)} applicable criteria PASS "
                f"({', '.join(q)}). No INELIGIBLE, WARNING, or GAP findings.")
    if verdict == "DISQUALIFIED":
        msg = (f"DISQUALIFIED — {len(hb)} HARD_BLOCK criterion failure(s) "
               f"({', '.join(hb)})")
        if w:
            msg += (f" override {len(w)} WARNING (committee-review) "
                    f"finding(s) ({', '.join(w)})")
        if g:
            msg += f"; {len(g)} GAP finding(s) ({', '.join(g)})"
        return msg + "."
    if verdict == "FLAGGED_FOR_COMMITTEE_REVIEW":
        return (f"FLAGGED_FOR_COMMITTEE_REVIEW — {len(w)} WARNING "
                f"finding(s) ({', '.join(w)}) require evaluation-committee "
                f"discretion. No HARD_BLOCK disqualifiers.")
    if verdict == "MARK_FOR_DOCUMENTATION_REVIEW":
        return (f"MARK_FOR_DOCUMENTATION_REVIEW — {len(g)} GAP finding(s) "
                f"({', '.join(g)}) need bidder to supply missing "
                f"documentation. No HARD_BLOCK or WARNING failures.")
    return f"{verdict} — see counts."


# ── Idempotent cleanup ────────────────────────────────────────────────

def _delete_prior_eligibility_matrix_rows() -> int:
    """Remove all prior EligibilityMatrix rows tagged with our source_ref."""
    rows = rest_get("kg_nodes", {
        "select":     "node_id",
        "node_type":  "eq.EligibilityMatrix",
        "source_ref": f"eq.{SOURCE_REF}",
    })
    for row in rows:
        rest_delete("kg_nodes", {"node_id": f"eq.{row['node_id']}"})
    return len(rows)


# ── Main ──────────────────────────────────────────────────────────────

def main() -> int:
    t_start = time.perf_counter()
    print("=" * 76)
    print(f"  Sub-block 4 — EligibilityMatrix Aggregator")
    print(f"  source_ref : {SOURCE_REF}")
    print("=" * 76)

    # 1. Idempotent cleanup
    n_prior = _delete_prior_eligibility_matrix_rows()
    if n_prior:
        print(f"  cleanup: removed {n_prior} prior EligibilityMatrix row(s)")

    # 2. Sentinel snapshot — verify upstream untouched after run
    sentinel_pre = {
        "ValidationFinding":    rest_count("kg_nodes", {"node_type": "eq.ValidationFinding"}),
        "BidEvaluationFinding": rest_count("kg_nodes", {"node_type": "eq.BidEvaluationFinding"}),
        "BIDDER_VIOLATES_RULE": rest_count("kg_edges", {"edge_type": "eq.BIDDER_VIOLATES_RULE"}),
    }
    print(f"\n── Sentinel snapshot (pre) ──")
    for k, v in sentinel_pre.items():
        print(f"  {k:24s} : {v}")

    # 3. Fetch all 90 findings via paginated read
    print(f"\n── Fetch BidEvaluationFinding rows (paginated, page_size={PAGE_SIZE}) ──")
    findings = fetch_all_bid_evaluation_findings()
    print(f"  fetched {len(findings)} finding(s)")
    if len(findings) != sentinel_pre["BidEvaluationFinding"]:
        print(f"  ⚠ pagination mismatch: fetched={len(findings)} count={sentinel_pre['BidEvaluationFinding']}")

    # 4. Group by (bidder_profile_id, tender_id)
    groups: dict[tuple[str, str], list[tuple[str, dict]]] = defaultdict(list)
    for f in findings:
        p = f.get("properties") or {}
        key = (p.get("bidder_profile_id"), p.get("tender_id"))
        if not all(key):
            print(f"  ⚠ skipping finding {f['node_id']} — missing bidder_profile_id or tender_id")
            continue
        groups[key].append((f["node_id"], p))
    print(f"\n── Grouped into {len(groups)} (bidder, tender) pairs ──")

    # 5. Aggregate per group + emit EligibilityMatrix rows
    emitted: list[dict] = []
    print()
    print(f"  {'bid_id':28s} {'verdict':30s} Q HB W G S")
    for (bidder_profile_id, tender_id), group in sorted(groups.items()):
        props_list = [p for _, p in group]
        verdict = aggregate_verdict(props_list)
        classification = classify_outcomes(group)
        reasoning = compose_aggregate_reasoning(verdict, classification)

        # Pull common identity fields from the first finding in the group
        first = props_list[0]
        bidder_name           = first.get("bidder_name")
        bid_submission_id     = first.get("bid_submission_id")
        bid_submission_node_id = first.get("bid_submission_node_id")
        bidder_profile_node_id = first.get("bidder_profile_node_id")
        tender_nit_no         = first.get("tender_nit_no")

        # Per-verdict counts
        n_q  = len(classification["qualified_criteria"])
        n_hb = len(classification["hard_block_criteria"])
        n_w  = len(classification["warning_criteria"])
        n_g  = len(classification["gap_criteria"])
        n_s  = len(classification["skip_criteria"])
        total_criteria = n_q + n_hb + n_w + n_g + n_s

        print(f"  {bid_submission_id:28s} {verdict:30s} {n_q} {n_hb:2d} {n_w} {n_g} {n_s}")

        finding_node_ids = [nid for nid, _ in group]

        # Label is human-readable, shape: "EligibilityMatrix: {bidder} → {tender} — {verdict} ({n_hb} HB + {n_w} W)"
        tender_short = (tender_id or "?").replace("tender_synth_", "")
        if verdict == "QUALIFIED":
            label = (f"EligibilityMatrix: {bidder_name} → {tender_short} "
                     f"— QUALIFIED ({n_q}/{total_criteria} criteria PASS)")
        elif verdict == "DISQUALIFIED":
            label = (f"EligibilityMatrix: {bidder_name} → {tender_short} "
                     f"— DISQUALIFIED ({n_hb} HB"
                     + (f" + {n_w} W" if n_w else "") + ")")
        elif verdict == "FLAGGED_FOR_COMMITTEE_REVIEW":
            label = (f"EligibilityMatrix: {bidder_name} → {tender_short} "
                     f"— FLAGGED ({n_w} W committee-review)")
        elif verdict == "MARK_FOR_DOCUMENTATION_REVIEW":
            label = (f"EligibilityMatrix: {bidder_name} → {tender_short} "
                     f"— DOC_REVIEW ({n_g} GAP)")
        else:
            label = f"EligibilityMatrix: {bidder_name} → {tender_short} — {verdict}"

        em_props = {
            # identity
            "bid_submission_id":       bid_submission_id,
            "bid_submission_node_id":  bid_submission_node_id,
            "bidder_profile_id":       bidder_profile_id,
            "bidder_profile_node_id":  bidder_profile_node_id,
            "bidder_name":             bidder_name,
            "tender_id":               tender_id,
            "tender_nit_no":           tender_nit_no,

            # aggregate verdict
            "aggregate_verdict":       verdict,
            "aggregate_reasoning":     reasoning,

            # per-verdict counts
            "criteria_total":              total_criteria,
            "count_qualified":             n_q,
            "count_ineligible_hard_block": n_hb,
            "count_ineligible_warning":    n_w,
            "count_gap":                   n_g,
            "count_skip":                  n_s,

            # typology lists (drilldown by outcome class)
            **{k: classification[k] for k in (
                "qualified_criteria", "hard_block_criteria",
                "warning_criteria", "gap_criteria", "skip_criteria")},

            # citation drilldown
            "finding_node_ids":              finding_node_ids,
            "finding_typology_to_node_id":   classification["finding_typology_to_node_id"],

            # extraction metadata
            "extracted_by":                  "sub_block_4:eligibility_matrix_aggregator_v1",
            "aggregation_precedence":        "HARD_BLOCK > WARNING > GAP > QUALIFIED",
            "source_findings_count":         len(group),
            "tier":                          4,
        }

        # Parent doc = the BidSubmission's doc_id (so the row is scoped to
        # the bid for downstream per-bid query patterns)
        inserted = rest_post("kg_nodes", [{
            "doc_id":     bid_submission_id,
            "node_type":  "EligibilityMatrix",
            "label":      label,
            "properties": em_props,
            "source_ref": SOURCE_REF,
        }])[0]
        emitted.append(inserted)

    # 6. Distribution summary
    dist: dict[str, int] = defaultdict(int)
    for row in emitted:
        dist[row["properties"]["aggregate_verdict"]] += 1
    print(f"\n── Aggregate verdict distribution ──")
    for v in ["QUALIFIED", "FLAGGED_FOR_COMMITTEE_REVIEW",
              "MARK_FOR_DOCUMENTATION_REVIEW", "DISQUALIFIED"]:
        print(f"  {v:34s} : {dist.get(v, 0)}")

    # 7. Sentinel snapshot — post — must be unchanged
    sentinel_post = {
        "ValidationFinding":    rest_count("kg_nodes", {"node_type": "eq.ValidationFinding"}),
        "BidEvaluationFinding": rest_count("kg_nodes", {"node_type": "eq.BidEvaluationFinding"}),
        "BIDDER_VIOLATES_RULE": rest_count("kg_edges", {"edge_type": "eq.BIDDER_VIOLATES_RULE"}),
        "EligibilityMatrix":    rest_count("kg_nodes", {"node_type": "eq.EligibilityMatrix"}),
    }
    print(f"\n── Sentinel snapshot (post) ──")
    drift = False
    for k, v in sentinel_post.items():
        pre_v = sentinel_pre.get(k)
        marker = ""
        if pre_v is not None and pre_v != v:
            marker = f"  ⚠ DRIFT (was {pre_v})"
            drift = True
        print(f"  {k:24s} : {v}{marker}")
    if drift:
        print(f"  ✗ sentinel drift — upstream tables were modified during aggregator run")
        return 2

    # 8. Total
    wall = time.perf_counter() - t_start
    print()
    print("=" * 76)
    print(f"  EligibilityMatrix aggregator complete — emitted {len(emitted)} row(s) "
          f"in {wall:.2f}s")
    print("=" * 76)
    return 0


if __name__ == "__main__":
    from modules.validation.verdict_emitter import main_with_crash_resilience
    raise SystemExit(main_with_crash_resilience(
        main, doc_id=AGGREGATOR_DOC_ID, typology=TYPOLOGY))
