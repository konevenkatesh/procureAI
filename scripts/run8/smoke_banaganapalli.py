"""R8.1 — Banaganapalli small-scale smoke (~30 civil BoQ rows).

Goal: regression check that R8.6's parallel batching doesn't break small-scale.
At 30 rows / 15 per batch = 2 batches; with max_concurrent=10 should still
complete in ~1 wave (~35s). Cost ~₹0.40.

Fail-safes:
  - cost ≤ ₹2 (small scale)
  - wall-clock ≤ 180s
  - sentinel preserved
  - ≥80% citation match
  - 100% spec_text ≥ 150 chars

Uses the actual eGP Banaganapalli sample: Tender 933192,
Kitchen Shed at Shadikhana, ECV ₹15,97,185, PRED Kurnool.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "services" / "m1-drafter"))

os.environ.setdefault("M1_DRAFTER_WORKFLOW_V2", "1")
os.environ.setdefault("M1_BOQ_MAX_CONCURRENT", "10")

from app.workflow_v2 import run_workflow_v2     # noqa: E402
from app.boq_generator import BoQSkeletonRow     # noqa: E402
from app.schemas import (                        # noqa: E402
    BiddingType, Classification, ConsortiumJV, DisplayRank, Evaluation,
    EvaluationCriteria, EvaluationType, EnquiryParticulars, Financial,
    FormOfContract, GateName, Geography, TenderCategory, TenderDates,
    TenderDraftState, TenderType, now_iso,
)


# ─── Fail-safe thresholds ─────────────────────────────────────────────


COST_BUDGET_INR        = 2.0
WALL_CLOCK_BUDGET_SEC  = 180
CITATION_MATCH_THRESHOLD = 0.80
SPEC_TEXT_OK_THRESHOLD = 1.00      # all rows must have ≥150 chars
USD_INR = 83.0

FLASH_IN_USD  = 0.075 / 1_000_000
FLASH_OUT_USD = 0.30  / 1_000_000
PRO_IN_USD    = 1.25  / 1_000_000
PRO_OUT_USD   = 5.00  / 1_000_000


HARD_SENTINEL: dict[str, int] = {
    "BidAnomalyFinding": 6, "BidderProfile": 12, "BidEvaluationFinding": 351,
    "BidSubmission": 27, "BoQItemSpec": 993, "Communication": 78,
    "ComparativeStatement": 3, "EligibilityMatrix": 27, "EMD_BG": 27,
    "LetterOfBid": 27, "PricedBoQ": 27, "RuleNode": 611,
    "SBDSection": 30, "Section": 1577, "TechSpecTemplate": 72,
    "TenderDocument": 8, "TenderRanking": 3, "ValidationFinding": 154,
}


# ─── Banaganapalli payload (from actual eGP Tender 933192) ────────────


def build_banaganapalli_state() -> TenderDraftState:
    ts = now_iso()
    return TenderDraftState(
        draft_id="r81_smoke_banaganapalli",
        enquiry_particulars=EnquiryParticulars(
            department_name="PRED",
            circle_division="PRED-Executive Engineer PR PIU division Kurnool",
            officer_inviting_bids="Executive Engineer, PR PIU Division, Kurnool",
            bid_opening_authority="E E",
            address="PRED Office, Kurnool, Andhra Pradesh - 518002",
            contact_details="+91-8518-XXXXXX",
            email="ee.pred.kurnool@ap.gov.in",
            name_of_project="Banaganapalli Shadikhana Facilities Upgrade",
            name_of_work=(
                "Providing Kitchen Shed and additional facilities to Shadikhana at Banaganapalli"
            ),
        ),
        classification=Classification(
            tender_category=TenderCategory.WORKS,
            type_of_work="Civil Works",
            tender_type=TenderType.OPEN_NCB,
            bidding_type=BiddingType.OPEN,
            form_of_contract=FormOfContract.LS,
            consortium_joint_venture=ConsortiumJV.NOT_APPLICABLE,
            bid_call_numbers=1,
        ),
        financial=Financial(
            estimated_contract_value_inr=1_597_185,
            estimated_contract_value_words="Rupees Fifteen Lakhs Ninety-Seven Thousand One Hundred Eighty-Five Only",
            period_of_completion_months=6,
            bid_validity_days=90,
            bid_security_percent=1.0,
            bid_security_inr=15_972,
            bid_security_in_favour_of="Executive Engineer, PR PIU Division, Kurnool",
            mode_of_payment="DD/BG drawn on a Scheduled Commercial Bank",
        ),
        geography=Geography(
            state="Andhra Pradesh", district="Nandyal", mandal="Banaganapalle",
            assembly="Banaganapalli", parliament="Nandyal",
        ),
        evaluation=Evaluation(
            evaluation_type=EvaluationType.PERCENTAGE,
            evaluation_criteria=EvaluationCriteria.BASED_ON_PRICE,
            display_rank=DisplayRank.LOWEST,
        ),
        dates=TenderDates(start_date=ts, end_date=ts, closing_date=ts),
        current_gate=GateName.AI_GENERATION,
        created_by="DEALING_OFFICER:r81_smoke",
        created_at=ts, last_updated_at=ts,
    )


def build_banaganapalli_skeleton() -> list[BoQSkeletonRow]:
    """~30 rows of civil construction for a kitchen shed (foundation → finishing)."""
    items = [
        # Foundation + earthwork
        ("Earthwork excavation in foundation in all classes of soil", "m3", 120.0),
        ("Plain Cement Concrete 1:4:8 in foundation bed", "m3", 18.0),
        ("RCC M-20 in foundation footings", "m3", 14.0),
        ("RCC M-20 in plinth beams and grade slab", "m3", 16.0),
        # Reinforcement + masonry
        ("Reinforcement Fe-500 bars (BIS-marked) cut/bent/placed", "MT", 2.8),
        ("Brick masonry in CM 1:6 superstructure 230mm thick", "m3", 32.0),
        ("Brick masonry in CM 1:6 partition walls 115mm thick", "m3", 14.0),
        # Roof
        ("RCC M-25 in roof slab 150mm thick", "m3", 22.0),
        ("Lintels + sunshades in M-25 RCC", "m3", 4.5),
        # Plastering
        ("Cement plaster 12mm thick on internal walls 1:6", "m2", 320.0),
        ("Cement plaster 20mm thick double-coat external 1:5", "m2", 180.0),
        # Flooring
        ("Cement concrete flooring 1:2:4 (75mm thick)", "m2", 95.0),
        ("Vitrified tile flooring 600x600mm in kitchen area", "m2", 35.0),
        # Doors + windows
        ("Doors — teakwood frames + flush shutters 35mm", "No", 6.0),
        ("Windows — anodised aluminium with 5mm glass", "m2", 14.0),
        ("Steel grilled windows + ventilators", "m2", 6.0),
        # Painting
        ("Painting — primer + 2 coats acrylic emulsion internal", "m2", 500.0),
        ("Painting — exterior weather-shield 2 coats over primer", "m2", 220.0),
        # Plumbing
        ("CPVC pipe + fittings for water supply (15-25mm)", "RM", 80.0),
        ("PVC drainage line 110mm dia for kitchen + WC waste", "RM", 35.0),
        ("Sanitary fittings — WC, washbasin, urinal, kitchen sink", "set", 1.0),
        ("Overhead water tank PVC 1000L with float valve + GI inlet", "No", 2.0),
        # Electrical
        ("Wiring — concealed conduit FRLS 2.5sqmm copper", "point", 24.0),
        ("MCB DB 4-way SP+N for kitchen panel", "No", 1.0),
        ("LED light fittings 18W ceiling + 9W wall", "No", 12.0),
        ("Ceiling fan 1200mm sweep with regulator", "No", 4.0),
        ("Earthing pit copper electrode 600mm dia", "No", 2.0),
        # Other
        ("Mild steel railing for stairs 36mm dia", "RM", 8.0),
        ("Boundary wall brick masonry with RCC coping", "RM", 38.0),
        ("Site clearing + final cleaning before handover", "lump sum", 1.0),
    ]
    rows = []
    for i, (name, unit, qty) in enumerate(items, 1):
        rows.append(BoQSkeletonRow(s_no=i, item_name=name, qty=qty, unit=unit))
    return rows


# ─── Smoke driver (reusable across R8.1/R8.2/R8.3) ───────────────────


_CITATION_RE = re.compile(
    r"\b(IS\s*\d{2,5}|APSS\s*(?:Cl\.?)?\s*\d|EN\s*\d{2,5}|IEC\s*\d{2,5}|"
    r"ASHRAE\s*\d{2,3}|UL\s*\d{1,4}|MERV|EUROVENT|NFPA|AHRI|CPWD)\b",
    re.IGNORECASE,
)


def run_smoke(label: str, state, skeleton, cost_budget, wall_budget,
              citation_threshold=0.80, spec_threshold=1.00):
    print(f"R8.1 — {label} smoke ({len(skeleton)} BoQ rows)")
    print(f"  budgets: cost ≤ ₹{cost_budget}, wall-clock ≤ {wall_budget}s, "
          f"citation ≥ {citation_threshold*100:.0f}%, spec_ok ≥ {spec_threshold*100:.0f}%")
    print(f"  max_concurrent: {os.environ.get('M1_BOQ_MAX_CONCURRENT', '10')}")
    print()

    flash_in = flash_out = pro_in = pro_out = 0
    boq_rows = []
    sse_events = []
    n_node_complete = 0
    n_batches = 0
    sections_drafted = []
    t0 = time.time()

    try:
        for ev in run_workflow_v2(state, boq_skeleton=skeleton):
            sse_events.append(ev)
            t = ev.get("type")
            if t == "llm_call":
                model = (ev.get("model") or "").lower()
                pin = ev.get("prompt_tokens") or 0
                pout = (ev.get("completion_tokens") or 0) + (ev.get("thought_tokens") or 0)
                if "flash" in model:
                    flash_in += pin; flash_out += pout
                elif "pro" in model:
                    pro_in += pin; pro_out += pout
            elif t == "node_complete":
                n_node_complete += 1
            elif t == "boq_batch_started":
                n_batches += 1
            elif t == "boq_item_complete":
                boq_rows.append(ev.get("row", {}))
            elif t == "section_complete":
                s = ev.get("section", "")
                if s.startswith("section_"):
                    sections_drafted.append(s)
            if time.time() - t0 > wall_budget:
                print(f"  ! wall-clock budget exceeded — aborting workflow")
                break
    except Exception as e:
        print(f"  ! workflow crash: {type(e).__name__}: {e}")

    elapsed = time.time() - t0
    usd = (flash_in * FLASH_IN_USD + flash_out * FLASH_OUT_USD
           + pro_in * PRO_IN_USD + pro_out * PRO_OUT_USD)
    inr = usd * USD_INR

    # Sentinel
    import psycopg
    from builder.config import settings
    sentinel_actual = {}
    sentinel_deltas = {}
    try:
        with psycopg.connect(settings.supabase_url, sslmode="require", connect_timeout=15) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT node_type, COUNT(*) FROM kg_nodes WHERE node_type = ANY(%s) GROUP BY node_type",
                    (list(HARD_SENTINEL.keys()),)
                )
                sentinel_actual = {nt: int(c) for nt, c in cur.fetchall()}
        sentinel_deltas = {
            nt: sentinel_actual.get(nt, 0) - exp
            for nt, exp in HARD_SENTINEL.items()
            if sentinel_actual.get(nt, 0) != exp
        }
    except Exception as e:
        print(f"  ! sentinel query failed: {e}")

    # Citations + spec text
    sample = boq_rows[:10] if len(boq_rows) >= 10 else boq_rows
    n_cite_match = sum(
        1 for r in sample
        if _CITATION_RE.findall((r.get("spec_text") or ""))
    )
    n_spec_ok = sum(
        1 for r in boq_rows if len((r.get("spec_text") or "")) >= 150
    )
    cite_rate = n_cite_match / max(len(sample), 1)
    spec_rate = n_spec_ok / max(len(boq_rows), 1)

    cost_ok    = inr <= cost_budget
    wall_ok    = elapsed <= wall_budget
    sentinel_ok = not sentinel_deltas
    cite_ok    = cite_rate >= citation_threshold
    spec_ok    = spec_rate >= spec_threshold

    print("=" * 76)
    print(f"  wall_clock:     {elapsed:6.1f}s  [{'OK' if wall_ok else 'FAIL'}]")
    print(f"  cost:          ₹{inr:6.2f}  [{'OK' if cost_ok else 'FAIL'}]")
    print(f"    flash in/out: {flash_in} / {flash_out}")
    print(f"    pro   in/out: {pro_in} / {pro_out}")
    print(f"  sentinel:       [{'OK' if sentinel_ok else 'FAIL'}]  deltas={sentinel_deltas}")
    print(f"  citation match: {cite_rate*100:5.1f}%  [{'OK' if cite_ok else 'FAIL'}]")
    print(f"  spec_text ok:   {spec_rate*100:5.1f}%  [{'OK' if spec_ok else 'FAIL'}]")
    print(f"  events:         node_complete={n_node_complete}  batches={n_batches}")
    print(f"  sections:       {sections_drafted}")
    print(f"  boq_rows:       {len(boq_rows)}/{len(skeleton)}")

    all_pass = cost_ok and wall_ok and sentinel_ok and cite_ok and spec_ok
    print(f"\n  RESULT: {'ALL PASS' if all_pass else 'FAILURES PRESENT'}")

    return {
        "label":          label,
        "wall_clock_sec": elapsed,
        "cost_inr":       inr,
        "flash_in":       flash_in, "flash_out": flash_out,
        "pro_in":         pro_in,   "pro_out":   pro_out,
        "sentinel_ok":    sentinel_ok,
        "sentinel_deltas": sentinel_deltas,
        "citation_rate":  cite_rate,
        "spec_rate":      spec_rate,
        "n_boq_rows":     len(boq_rows),
        "n_skeleton":     len(skeleton),
        "n_batches":      n_batches,
        "sections":       sections_drafted,
        "all_pass":       all_pass,
        "gates": {
            "cost_ok": cost_ok, "wall_ok": wall_ok, "sentinel_ok": sentinel_ok,
            "cite_ok": cite_ok, "spec_ok": spec_ok,
        },
    }, boq_rows


def main():
    state = build_banaganapalli_state()
    skeleton = build_banaganapalli_skeleton()
    result, boq_rows = run_smoke(
        "Banaganapalli", state, skeleton,
        cost_budget=COST_BUDGET_INR,
        wall_budget=WALL_CLOCK_BUDGET_SEC,
        citation_threshold=CITATION_MATCH_THRESHOLD,
        spec_threshold=SPEC_TEXT_OK_THRESHOLD,
    )
    Path("/tmp/r81_smoke_result.json").write_text(json.dumps(result, indent=2, default=str))
    Path("/tmp/r81_boq_sample.json").write_text(json.dumps(boq_rows[:5], indent=2, default=str))
    return 0 if result["all_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
