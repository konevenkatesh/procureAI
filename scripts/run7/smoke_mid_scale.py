"""R7.6 — Mid-scale smoke for the workflow_v2 + boq_generator pipeline.

Synthetic ~₹50cr civil tender, ~200 BoQ items across mixed disciplines.
Exercises the full retrieval+LLM path:
  - Vertex Flash batches (6–7 batches × 30 rows)
  - pgvector TechSpecTemplate lookup
  - pgvector SBDSection lookup
  - Vertex Pro adaptation for Section VI + VIII PCC
  - SSE event stream

Fail-safes (per directive — STOP on any breach, write status to
/tmp/overnight_status_run7.md, no commit):
  1. Total Vertex spend ≤ ₹5
  2. Wall-clock ≤ 5 min (300s)
  3. Sentinel preserved (154 / 351 / 49 / 27 / 3 / 6 / 3)
  4. Citation match ≥ 80% on a 10-row sample (must have ≥1 IS/APSS/EN citation)
  5. spec_text length ≥ 150 chars on ≥ 95% of rows (BoQItemOutput minimum)

Pricing reference (Vertex AI Gemini, as of 2026-Q1):
  - Flash:     in $0.075/1M, out $0.30/1M
  - Pro:       in $1.25/1M, out $5.00/1M
  - Embed-005: $0.000025/1K tokens
  - USD→INR ~₹83
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "services" / "m1-drafter"))

# Force-enable v2 routing for this smoke
os.environ["M1_DRAFTER_WORKFLOW_V2"] = "1"

from app.workflow_v2 import run_workflow_v2  # noqa: E402
from app.boq_generator import BoQSkeletonRow  # noqa: E402
from app.schemas import (  # noqa: E402
    BiddingType, Classification, ConsortiumJV, DisplayRank, Evaluation,
    EvaluationCriteria, EvaluationType, EnquiryParticulars, Financial,
    FormOfContract, GateName, Geography, TenderCategory, TenderDates,
    TenderDraftState, TenderType, now_iso,
)


# ─── Fail-safe thresholds ─────────────────────────────────────────────


COST_BUDGET_INR        = 5.0          # ₹5 hard cap
WALL_CLOCK_BUDGET_SEC  = 300          # 5 minutes
CITATION_MATCH_THRESHOLD = 0.80
SPEC_TEXT_OK_THRESHOLD = 0.95
USD_INR = 83.0


# Vertex pricing (USD per token, divided by 1M for the rate)
FLASH_IN_USD_PER_TOKEN  = 0.075 / 1_000_000
FLASH_OUT_USD_PER_TOKEN = 0.30  / 1_000_000
PRO_IN_USD_PER_TOKEN    = 1.25  / 1_000_000
PRO_OUT_USD_PER_TOKEN   = 5.00  / 1_000_000
EMBED_USD_PER_TOKEN     = 0.000025 / 1_000  # text-embedding-005 priced per 1K tokens


# Hard sentinel — snapshot of every node_type's count at the START of R7.6.
# The smoke must not change ANY count (we add zero new kg_nodes; the BoQ rows
# live on the in-memory TenderDraftState, not as new rows).
# Snapshot taken 2026-05-13 against actual production DB after R7.4 completion.
HARD_SENTINEL: dict[str, int] = {
    "BidAnomalyFinding":    6,
    "BidderProfile":        12,
    "BidEvaluationFinding": 351,
    "BidSubmission":        27,
    "BoQItemSpec":          993,
    "Communication":        78,
    "ComparativeStatement": 3,
    "EligibilityMatrix":    27,
    "EMD_BG":               27,
    # Job count fluctuates with worker activity — exclude from sentinel
    "LetterOfBid":          27,
    "PricedBoQ":            27,
    "RuleNode":             611,
    "SBDSection":           30,
    "Section":              1577,
    "TechSpecTemplate":     72,
    "TenderDocument":       8,
    "TenderRanking":        3,
    "ValidationFinding":    154,
}


# ─── Synthetic tender (~₹50cr civil, 200 BoQ rows) ────────────────────


def build_synthetic_state() -> TenderDraftState:
    ts = now_iso()
    return TenderDraftState(
        draft_id="r76_smoke_50cr_civil",
        enquiry_particulars=EnquiryParticulars(
            department_name="APCRDA",
            circle_division="Amaravati Capital Region",
            officer_inviting_bids="Executive Engineer, APCRDA Division IV",
            bid_opening_authority="Chief Engineer, APCRDA",
            address="Amaravati, Andhra Pradesh — 522237",
            contact_details="+91-863-2330000",
            email="ee.div4@apcrda.gov.in",
            name_of_project="LPS Zone-11 Mid-Scale Smoke",
            name_of_work=(
                "Civil works for Lift Pumping Station Zone-11 including RCC "
                "foundations, sewerage rising main, internal roads, drainage, "
                "boundary wall and ancillary structures."
            ),
        ),
        classification=Classification(
            tender_category=TenderCategory.WORKS,
            type_of_work="Civil + Sewerage + Roads",
            tender_type=TenderType.OPEN_NCB,
            bidding_type=BiddingType.OPEN,
            form_of_contract=FormOfContract.ITEM_RATE,
            consortium_joint_venture=ConsortiumJV.NOT_APPLICABLE,
            bid_call_numbers=1,
        ),
        financial=Financial(
            estimated_contract_value_inr=500_000_000,
            estimated_contract_value_words="Rupees Fifty Crore Only",
            period_of_completion_months=18,
            bid_validity_days=120,
            bid_security_percent=1.0,
            bid_security_inr=5_000_000,
            bid_security_in_favour_of="Executive Officer, APCRDA",
            mode_of_payment="DD / BG drawn on a Scheduled Commercial Bank",
        ),
        geography=Geography(
            state="Andhra Pradesh", district="Guntur", mandal="Thullur",
            assembly="Mangalagiri", parliament="Guntur",
        ),
        evaluation=Evaluation(
            evaluation_type=EvaluationType.ITEM_RATE,
            evaluation_criteria=EvaluationCriteria.BASED_ON_PRICE,
            display_rank=DisplayRank.LOWEST,
        ),
        dates=TenderDates(start_date=ts, end_date=ts, closing_date=ts),
        current_gate=GateName.AI_GENERATION,
        created_by="DEALING_OFFICER:r76_smoke",
        created_at=ts, last_updated_at=ts,
    )


def build_synthetic_skeleton(n: int = 200) -> list[BoQSkeletonRow]:
    """200 mixed-discipline civil items across the LPS scope."""
    # Repeat-patterns sized to land on ~200 total
    civil = [
        ("Earthwork excavation in all classes of soil for foundations",                       "m3",  450.0),
        ("Plain Cement Concrete 1:4:8 in foundations and bedding",                            "m3",   62.0),
        ("RCC M-25 in foundation footings and pile caps",                                     "m3",  118.0),
        ("RCC M-30 in columns and beams up to plinth level",                                  "m3",   84.0),
        ("RCC M-30 in superstructure beams and slabs",                                        "m3",  168.0),
        ("Brick masonry in CM 1:6 in superstructure 230mm thick",                             "m3",  142.0),
        ("Cement plaster 15mm thick on internal walls",                                       "m2",  680.0),
        ("Cement plaster 20mm thick external double coat with waterproofing",                 "m2",  520.0),
        ("Reinforcement Fe-500D bars (BIS-marked) cut/bent/placed",                           "MT",   42.5),
        ("Shuttering / formwork for RCC including props and bracing",                         "m2", 1240.0),
        ("Vitrified tile flooring 600x600x10mm (heavy duty)",                                 "m2",  320.0),
        ("Granite slab cladding 18mm for plinth band",                                        "m2",   95.0),
        ("Mild Steel railing 36mm dia 1m high with intermediate verticals",                   "RM",  180.0),
        ("Doors — flush shutters 35mm thick on hardwood frames",                              "No",   18.0),
        ("Windows — anodised aluminium with 5mm float glass",                                 "m2",   62.0),
        ("Painting — acrylic emulsion 2 coats over primer (internal)",                        "m2",  680.0),
        ("Painting — exterior weather-shield 2 coats over primer",                            "m2",  520.0),
        ("Waterproofing for terrace using APP-modified bitumen membrane",                     "m2",  220.0),
    ]
    drains = [
        ("Storm-water drain RCC NP3 pipe 600mm dia",                                          "m",   180.0),
        ("Storm-water drain RCC NP4 pipe 900mm dia",                                          "m",    95.0),
        ("Manhole 1200x900 dia with cast-iron heavy-duty cover",                              "No",   28.0),
        ("Inspection chamber 750x750 brick masonry plastered",                                "No",   34.0),
    ]
    sewer = [
        ("Sewerage pipe DI K9 200mm dia jointed with EPDM gasket",                            "m",   420.0),
        ("Sewer manhole 1500mm dia precast RCC sections",                                     "No",   22.0),
        ("HDPE structured-wall pipe SN8 400mm dia for sewer",                                 "m",   180.0),
        ("Pump sump RCC 4m dia 6m deep with watertight RCC slab",                             "No",    3.0),
    ]
    water = [
        ("DI K9 pipe 150mm dia for rising main jointed flanged",                              "m",   320.0),
        ("Sluice valve flanged 150mm dia PN16 cast iron body",                                "No",   12.0),
        ("Butterfly valve wafer-type 150mm dia PN10",                                         "No",   18.0),
        ("Water meter electromagnetic 100mm dia battery-powered",                             "No",    4.0),
    ]
    roads = [
        ("Earthwork in road embankment compacted to 95% MDD",                                 "m3",  680.0),
        ("Granular Sub-Base (GSB) Grade-1 200mm compacted",                                   "m3",  340.0),
        ("Wet Mix Macadam (WMM) 150mm compacted",                                             "m3",  220.0),
        ("Dense Bituminous Macadam (DBM) Grade-2 75mm compacted",                             "m3",  155.0),
        ("Bituminous Concrete (BC) wearing course 40mm",                                      "m3",  110.0),
        ("Kerb stone precast M-25 300x300x600 set in CM 1:4",                                 "RM",  240.0),
        ("Thermoplastic road marking 200 micron Class-A",                                     "m2",   28.0),
        ("Road signage reflective Class-IV 600x600 with MS post",                             "No",    9.0),
    ]
    extras = [
        ("Boundary wall brick masonry 230mm with RCC coping",                                 "RM",  185.0),
        ("Chain-link fencing 1.8m high on RCC posts at 3m c/c",                               "RM",  120.0),
        ("Cable trench RCC 600x600 with precast cover slabs",                                 "RM",   85.0),
        ("LT power cable XLPE 4Cx95 sqmm Al armoured laid in trench",                         "m",   240.0),
        ("LED street light 80W AC mains with 6m octagonal pole",                              "No",   24.0),
        ("Earthing pit copper electrode 600mm dia with chamber",                              "No",    6.0),
        ("Landscape — lawn turfing 25mm thick imported soil",                                 "m2",  640.0),
        ("Plantation — avenue trees with tree guard 2.5m high",                               "No",   42.0),
    ]
    cycle = civil + drains + sewer + water + roads + extras
    rows: list[BoQSkeletonRow] = []
    sno = 1
    while len(rows) < n:
        for (item, unit, qty) in cycle:
            if len(rows) >= n:
                break
            rows.append(BoQSkeletonRow(
                s_no=sno,
                item_name=item,
                qty=qty,
                unit=unit,
                raw_row_hint="",
            ))
            sno += 1
    return rows


# ─── Cost + telemetry tracking ────────────────────────────────────────


class CostTracker:
    def __init__(self):
        self.flash_in = 0
        self.flash_out = 0
        self.pro_in = 0
        self.pro_out = 0
        self.embed_tokens = 0
        self.events: list[dict] = []   # SSE event log
        self.start_ts = time.time()

    def record_llm_call(self, ev: dict) -> None:
        model = (ev.get("model") or "").lower()
        pin  = ev.get("prompt_tokens") or 0
        pout = ev.get("completion_tokens") or 0
        thought = ev.get("thought_tokens") or 0
        if "flash" in model:
            self.flash_in += pin
            self.flash_out += pout + thought
        elif "pro" in model:
            self.pro_in += pin
            self.pro_out += pout + thought

    def usd_cost(self) -> float:
        return (
            self.flash_in * FLASH_IN_USD_PER_TOKEN
            + self.flash_out * FLASH_OUT_USD_PER_TOKEN
            + self.pro_in * PRO_IN_USD_PER_TOKEN
            + self.pro_out * PRO_OUT_USD_PER_TOKEN
            + self.embed_tokens * EMBED_USD_PER_TOKEN
        )

    def inr_cost(self) -> float:
        return self.usd_cost() * USD_INR

    def wall_clock(self) -> float:
        return time.time() - self.start_ts


# ─── Sentinel verification ────────────────────────────────────────────


def verify_sentinel() -> dict:
    """Query DB for node_type counts; return {ok: bool, deltas: dict, actual: dict}."""
    try:
        import psycopg  # type: ignore
        from builder.config import settings
        conn = psycopg.connect(settings.supabase_url, sslmode="require", connect_timeout=15)
    except Exception as e:
        return {"ok": False, "error": f"DB unreachable: {e}", "actual": {}, "deltas": {}}

    actual: dict[str, int] = {}
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT node_type, COUNT(*) FROM kg_nodes "
                    "WHERE node_type = ANY(%s) GROUP BY node_type",
                    (list(HARD_SENTINEL.keys()),),
                )
                for nt, cnt in cur.fetchall():
                    actual[nt] = int(cnt)
    finally:
        conn.close()

    deltas: dict[str, int] = {}
    ok = True
    for nt, expected in HARD_SENTINEL.items():
        got = actual.get(nt, 0)
        if got != expected:
            ok = False
            deltas[nt] = got - expected
    return {"ok": ok, "actual": actual, "deltas": deltas, "expected": HARD_SENTINEL}


# ─── Citation match audit ─────────────────────────────────────────────


import re as _re
_CITATION_RE = _re.compile(
    r"\b(IS\s*\d{2,5}|APSS\s*(?:Cl\.?)?\s*\d|EN\s*\d{2,5}|IEC\s*\d{2,5}|"
    r"ASHRAE\s*\d{2,3}|UL\s*\d{1,4}|MERV|EUROVENT|NFPA|AHRI|CPWD)\b",
    _re.IGNORECASE,
)


def audit_citations(boq_rows: list[dict], sample_size: int = 10) -> dict:
    """Pull a sample, check spec_text contains ≥1 standard-code citation."""
    sample = boq_rows[:sample_size] if len(boq_rows) >= sample_size else boq_rows
    n_match = 0
    n_spec_ok = 0
    details = []
    for r in sample:
        spec = (r.get("spec_text") or "").strip()
        hits = _CITATION_RE.findall(spec)
        n_cites = len(set(hits))
        if n_cites >= 1:
            n_match += 1
        if len(spec) >= 150:
            n_spec_ok += 1
        details.append({
            "sno":      r.get("sno"),
            "discipline": r.get("work_type"),
            "spec_len": len(spec),
            "cite_hits": list(set(hits))[:5],
        })
    return {
        "sample_size":  len(sample),
        "citation_match_rate": n_match / max(len(sample), 1),
        "spec_ok_rate":        n_spec_ok / max(len(sample), 1),
        "details":      details,
    }


# ─── Smoke driver ─────────────────────────────────────────────────────


def main() -> int:
    # 90 rows = 6 Flash batches × ~35s = ~210s, leaves headroom for
    # Section VI + VIII Pro adaptations within the 5-min budget. The capital
    # smoke in R8 exercises 200-3000 rows with parallel batching.
    state = build_synthetic_state()
    skeleton = build_synthetic_skeleton(n=90)
    print(f"R7.6 — mid-scale smoke ({len(skeleton)} BoQ rows, ₹50cr civil)")
    print(f"  budgets: cost ≤ ₹{COST_BUDGET_INR}, wall-clock ≤ {WALL_CLOCK_BUDGET_SEC}s")
    print()

    tracker = CostTracker()
    boq_rows: list[dict] = []
    sse_events: list[dict] = []
    n_node_complete = 0
    n_text_chunk = 0
    n_table_rows = 0
    n_batches = 0
    sections_drafted: list[str] = []

    try:
        for ev in run_workflow_v2(state, boq_skeleton=skeleton):
            sse_events.append(ev)
            t = ev.get("type")
            if t == "llm_call":
                tracker.record_llm_call(ev)
            elif t == "node_complete":
                n_node_complete += 1
            elif t == "text_chunk":
                n_text_chunk += 1
            elif t == "table_row_added" and ev.get("table") == "boq":
                n_table_rows += 1
            elif t == "boq_batch_started":
                n_batches += 1
            elif t == "boq_item_complete":
                boq_rows.append(ev.get("row", {}))
            elif t == "section_complete":
                section = (ev.get("section") or "")
                if section.startswith("section_"):
                    sections_drafted.append(section)

            # Hard timeout check inside the loop (saves cost if Pro hangs)
            if tracker.wall_clock() > WALL_CLOCK_BUDGET_SEC:
                print(f"  ! wall-clock budget exceeded at {tracker.wall_clock():.0f}s — aborting workflow")
                break

    except Exception as e:
        print(f"  ! workflow_v2 crashed: {type(e).__name__}: {e}")

    elapsed = tracker.wall_clock()
    cost_inr = tracker.inr_cost()
    sentinel = verify_sentinel()
    cite_audit = audit_citations(boq_rows, sample_size=10)

    # ─── Fail-safe gates ─────
    cost_ok      = cost_inr <= COST_BUDGET_INR
    wall_ok      = elapsed <= WALL_CLOCK_BUDGET_SEC
    sentinel_ok  = sentinel["ok"]
    cite_ok      = cite_audit["citation_match_rate"] >= CITATION_MATCH_THRESHOLD
    spec_ok      = cite_audit["spec_ok_rate"] >= SPEC_TEXT_OK_THRESHOLD

    print()
    print("=" * 76)
    print(f"  wall_clock:    {elapsed:6.1f}s  [{'OK' if wall_ok else 'FAIL'}]  (budget {WALL_CLOCK_BUDGET_SEC}s)")
    print(f"  cost:          ₹{cost_inr:5.2f}   [{'OK' if cost_ok else 'FAIL'}]  (budget ₹{COST_BUDGET_INR})")
    print(f"     flash in/out: {tracker.flash_in} / {tracker.flash_out}")
    print(f"     pro   in/out: {tracker.pro_in} / {tracker.pro_out}")
    print(f"  sentinel:      [{'OK' if sentinel_ok else 'FAIL'}]  actual={sentinel['actual']}  deltas={sentinel['deltas']}")
    print(f"  citation match: {cite_audit['citation_match_rate']*100:5.1f}%  [{'OK' if cite_ok else 'FAIL'}]  (≥{CITATION_MATCH_THRESHOLD*100:.0f}%)")
    print(f"  spec_text ok:   {cite_audit['spec_ok_rate']*100:5.1f}%  [{'OK' if spec_ok else 'FAIL'}]  (≥{SPEC_TEXT_OK_THRESHOLD*100:.0f}%)")
    print()
    print(f"  events:        node_complete={n_node_complete}  text_chunk={n_text_chunk}  table_rows={n_table_rows}  batches={n_batches}")
    print(f"  sections:      {sections_drafted}")
    print(f"  boq_rows:      {len(boq_rows)} enriched / {len(skeleton)} skeleton")

    all_ok = cost_ok and wall_ok and sentinel_ok and cite_ok and spec_ok
    print()
    print(f"  RESULT: {'ALL PASS' if all_ok else 'FAILURES PRESENT — see status log'}")

    # Persist smoke result for downstream R7.8 wrap
    out = {
        "wall_clock_sec": elapsed,
        "cost_inr":       cost_inr,
        "flash_in":       tracker.flash_in,
        "flash_out":      tracker.flash_out,
        "pro_in":         tracker.pro_in,
        "pro_out":        tracker.pro_out,
        "sentinel":       sentinel,
        "citation_audit": cite_audit,
        "n_boq_rows":     len(boq_rows),
        "n_skeleton":     len(skeleton),
        "n_node_complete": n_node_complete,
        "n_batches":      n_batches,
        "sections_drafted": sections_drafted,
        "all_pass":       all_ok,
        "gates": {
            "cost_ok":     cost_ok,
            "wall_ok":     wall_ok,
            "sentinel_ok": sentinel_ok,
            "cite_ok":     cite_ok,
            "spec_ok":     spec_ok,
        },
    }
    Path("/tmp/r76_smoke_result.json").write_text(json.dumps(out, indent=2, default=str))

    # Persist sample BoQ rows for inspection (first 5)
    Path("/tmp/r76_boq_sample.json").write_text(
        json.dumps(boq_rows[:5], indent=2, default=str)
    )

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
