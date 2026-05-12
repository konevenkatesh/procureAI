"""R8.3 — HOD Towers capital-scale smoke (~3000 MEP BoQ rows).

The wow moment: full ₹743cr MEP tender end-to-end with parallel Flash batches.
~200 batches / max_concurrent=10 = 20 waves × ~35s = ~12 min for BoQ alone.

Cost estimate: ~₹38-42 (extrapolated from R8.2 mid: 800 rows = ₹10.27).
Wall-clock budget: 1200s (20 min) — capital-scale headroom.

Synthetic HOD Towers MEP modelled after the AGICL APCRDA reference NIT
135/PROC/MAU61-USI0HB(OTH)/122/2026-HB, ECV ₹743.02 Cr, 18-month period,
Lumpsum % Tender, MEP disciplines.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "services" / "m1-drafter"))

os.environ.setdefault("M1_DRAFTER_WORKFLOW_V2", "1")
os.environ.setdefault("M1_BOQ_MAX_CONCURRENT", "10")

from scripts.run8.smoke_banaganapalli import run_smoke
from app.boq_generator import BoQSkeletonRow
from app.schemas import (
    BiddingType, Classification, ConsortiumJV, DisplayRank, Evaluation,
    EvaluationCriteria, EvaluationType, EnquiryParticulars, Financial,
    FormOfContract, GateName, Geography, TenderCategory, TenderDates,
    TenderDraftState, TenderType, now_iso,
)


def build_state() -> TenderDraftState:
    ts = now_iso()
    return TenderDraftState(
        draft_id="r83_smoke_hod_towers",
        enquiry_particulars=EnquiryParticulars(
            department_name="AGICL",
            circle_division="Andhra Pradesh General Infrastructure Corporation Limited",
            officer_inviting_bids="Chief Procurement Officer, AGICL",
            bid_opening_authority="MD, AGICL",
            address="AGICL Tower, Amaravati, AP-522237",
            contact_details="+91-863-2330500",
            email="cpo@agicl.gov.in",
            name_of_project="Amaravati HOD Towers MEP",
            name_of_work=(
                "MEP works for HOD (Heads of Department) Towers including "
                "HVAC, Electrical, Fire-fighting, Lifts, Public Address, "
                "Building Management System, HSD storage, and Plumbing"
            ),
        ),
        classification=Classification(
            tender_category=TenderCategory.WORKS,
            type_of_work="MEP",
            tender_type=TenderType.OPEN_ICB,
            bidding_type=BiddingType.OPEN,
            form_of_contract=FormOfContract.PERCENTAGE,
            consortium_joint_venture=ConsortiumJV.APPLICABLE,
            bid_call_numbers=1,
        ),
        financial=Financial(
            estimated_contract_value_inr=7_430_281_792,
            estimated_contract_value_words=(
                "Rupees Seven Hundred Forty-Three Crore Two Lakh Eighty-One "
                "Thousand Seven Hundred Ninety-Two Only"
            ),
            period_of_completion_months=18,
            bid_validity_days=180,
            bid_security_percent=1.0,
            bid_security_inr=74_302_817,
            bid_security_in_favour_of="MD, AGICL Amaravati",
            mode_of_payment="DD/BG drawn on a Scheduled Commercial Bank",
        ),
        geography=Geography(
            state="Andhra Pradesh", district="Guntur", mandal="Thullur",
            assembly="Mangalagiri", parliament="Guntur",
        ),
        evaluation=Evaluation(
            evaluation_type=EvaluationType.PERCENTAGE,
            evaluation_criteria=EvaluationCriteria.BASED_ON_PRICE,
            display_rank=DisplayRank.LOWEST,
        ),
        dates=TenderDates(start_date=ts, end_date=ts, closing_date=ts),
        current_gate=GateName.AI_GENERATION,
        created_by="DEALING_OFFICER:r83_smoke",
        created_at=ts, last_updated_at=ts,
    )


# ─── 8-discipline MEP skeleton (~3000 rows) ──────────────────────────


def _seed(label):
    if label == "HVAC":
        return [
            ("AHU 8000 CFM double-skin panel construction CW coil", "No", 12.0),
            ("AHU 6000 CFM double-skin panel construction CW coil", "No", 18.0),
            ("AHU 4000 CFM single-skin panel construction CW coil", "No", 24.0),
            ("FCU 600 CFM 4-pipe ceiling-suspended", "No", 320.0),
            ("FCU 800 CFM 4-pipe ceiling-suspended", "No", 240.0),
            ("Chiller water-cooled centrifugal 500 TR", "No", 4.0),
            ("Chiller water-cooled centrifugal 300 TR", "No", 6.0),
            ("Cooling tower induced-draught counter-flow 500 TR", "No", 4.0),
            ("VRF outdoor unit 16 HP heat-recovery", "No", 60.0),
            ("VRF indoor unit cassette 2 HP", "No", 480.0),
            ("Chilled water pump primary 90 kW", "No", 6.0),
            ("Condenser water pump 75 kW", "No", 6.0),
            ("MS pipe 250mm dia 8mm thick Sch 40 for chilled water", "RM", 1200.0),
            ("MS pipe 150mm dia 5.5mm thick for chilled water", "RM", 2400.0),
            ("MS pipe 100mm dia 4.5mm thick for chilled water", "RM", 3600.0),
            ("Butterfly valve PN16 flanged 250mm dia", "No", 80.0),
            ("Butterfly valve PN16 flanged 150mm dia", "No", 240.0),
            ("Y-strainer 150mm dia CI body 40 mesh", "No", 165.0),
            ("Ball valve brass 50mm dia thread-end", "No", 480.0),
            ("Duct GI 24G rectangular 800x400", "m2", 4800.0),
            ("Duct GI 22G rectangular 1200x600", "m2", 2400.0),
            ("Duct insulation nitrile rubber 19mm thick", "m2", 7200.0),
            ("VAV box single-duct 800 CFM with reheat coil", "No", 220.0),
            ("Grille linear bar 1200mm with damper", "m", 480.0),
            ("Diffuser swirl 600x600 4-way", "No", 1200.0),
            ("Damper fire 1.5-hr rating 600x600", "No", 480.0),
            ("Damper motorised volume 800x400", "No", 320.0),
            ("Exhaust fan inline duct 4000 CFM", "No", 80.0),
            ("Refrigerant copper pipe 7/8 inch nitrogen-flushed", "RM", 3600.0),
            ("Heat recovery wheel 1500 CMH", "No", 48.0),
        ] * 27   # ~810 items
    if label == "Electrical":
        return [
            ("HT switchgear 11kV vacuum circuit breaker 1600A", "No", 8.0),
            ("HT switchgear 11kV LBS panel 630A", "No", 12.0),
            ("Transformer dry-type 2500 kVA 11/0.433 kV Dyn11", "No", 6.0),
            ("Transformer dry-type 1600 kVA 11/0.433 kV Dyn11", "No", 12.0),
            ("LT panel ACB 4000A 65kA 5-tier with metering", "No", 6.0),
            ("LT panel ACB 2500A 50kA 4-tier with metering", "No", 12.0),
            ("MCC 1600A 50kA 4-tier with VFDs", "No", 18.0),
            ("MCC 800A 35kA 3-tier", "No", 24.0),
            ("Sub-MDB 400A TPN", "No", 60.0),
            ("Final DB 8-way SP+N MCB", "No", 480.0),
            ("Final DB 12-way TP+N MCB", "No", 240.0),
            ("LT cable XLPE 4Cx400 sqmm Al armoured", "m", 2400.0),
            ("LT cable XLPE 4Cx240 sqmm Al armoured", "m", 4800.0),
            ("LT cable XLPE 4Cx95 sqmm Al armoured", "m", 7200.0),
            ("LT cable XLPE 4Cx16 sqmm Cu armoured", "m", 9600.0),
            ("Control cable 12C x 1.5sqmm Cu armoured", "m", 4800.0),
            ("Bus duct sandwich 4000A IP54", "RM", 240.0),
            ("Cable tray ladder 600mm GI hot-dip", "m", 4800.0),
            ("Cable tray perforated 300mm GI hot-dip", "m", 7200.0),
            ("Conduit GI 32mm with bends + accessories", "m", 7200.0),
            ("LED ceiling light 36W TPa 110lm/W", "No", 1440.0),
            ("LED tube light 18W T5", "No", 2880.0),
            ("LED emergency exit signage 3-hr backup", "No", 480.0),
            ("Earthing pit copper electrode 50mm dia + chamber", "No", 36.0),
            ("Lightning arrestor ESE 30m radius", "No", 12.0),
            ("UPS 200 kVA online double-conversion 30-min", "No", 6.0),
            ("DG set 1500 kVA prime power acoustic enclosure", "No", 4.0),
            ("DG set 750 kVA prime power", "No", 6.0),
            ("Capacitor bank APFC 800 kVAr 12-step", "No", 6.0),
        ] * 28   # ~812 items
    if label == "Fire":
        return [
            ("Fire pump electric main 4500 LPM 70m head", "No", 4.0),
            ("Fire pump diesel jockey 180 LPM 70m head", "No", 4.0),
            ("Fire pump electric standby 4500 LPM", "No", 4.0),
            ("Sprinkler head pendant K=5.6 brass", "No", 4800.0),
            ("Sprinkler head upright K=5.6 brass", "No", 1200.0),
            ("Hydrant valve oblique 65mm dia GM body", "No", 240.0),
            ("Hose reel cabinet with 30m rubber hose 25mm", "No", 240.0),
            ("Hose box internal with 2x15m hose + branch pipe", "No", 240.0),
            ("Smoke detector photoelectric addressable", "No", 1200.0),
            ("Heat detector rate-of-rise addressable", "No", 480.0),
            ("Manual call point break-glass addressable", "No", 240.0),
            ("Fire alarm panel 4-loop addressable 500 devices/loop", "No", 6.0),
            ("Fire damper 1.5-hr 60-min 800x400", "No", 480.0),
            ("Fire extinguisher portable ABC 6 kg", "No", 480.0),
            ("Fire extinguisher portable CO2 4.5 kg", "No", 240.0),
            ("GI pipe medium class 150mm dia for hydrant", "RM", 2400.0),
            ("GI pipe medium class 100mm dia for sprinkler", "RM", 3600.0),
        ] * 24   # ~408 items
    if label == "Lifts":
        return [
            ("Lift passenger 13-pax 1.75 m/s MRL", "No", 8.0),
            ("Lift passenger 20-pax 2.5 m/s gearless", "No", 12.0),
            ("Lift freight 2000kg 1.0 m/s", "No", 6.0),
            ("Escalator 600mm step 30-degree 5m vertical rise", "No", 12.0),
            ("Lift control panel destination-control system", "No", 36.0),
        ] * 32   # ~160 items
    if label == "Plumbing":
        return [
            ("CPVC pipe 25mm dia SDR-11 thread-end", "RM", 4800.0),
            ("CPVC pipe 32mm dia SDR-11 thread-end", "RM", 2400.0),
            ("uPVC pipe SWR 110mm dia SN8", "RM", 3600.0),
            ("uPVC pipe SWR 75mm dia SN8", "RM", 2400.0),
            ("Water closet wall-hung dual-flush", "No", 480.0),
            ("Wash basin under-counter porcelain", "No", 480.0),
            ("Urinal wall-hung sensor-flush", "No", 240.0),
            ("Kitchen sink double-bowl SS304", "No", 120.0),
            ("Water meter 100mm dia battery-powered electromagnetic", "No", 18.0),
            ("Pressure-reducing valve 50mm dia PN16", "No", 60.0),
            ("Ball valve brass 25mm dia full-bore", "No", 480.0),
            ("Float valve 50mm dia float-cup PN10", "No", 36.0),
            ("Sand filter 30 m3/hr capacity", "No", 6.0),
            ("Carbon filter 30 m3/hr capacity", "No", 6.0),
        ] * 30   # ~420 items
    if label == "PA":
        return [
            ("PA amplifier 240W 4-zone matrix", "No", 36.0),
            ("Ceiling loudspeaker 6W 100V line", "No", 720.0),
            ("Volume control 6W 100V wall-mount", "No", 240.0),
            ("Microphone paging desktop with PTT", "No", 24.0),
            ("Audio source DSP digital signage", "No", 18.0),
        ] * 41   # ~205 items
    if label == "BMS":
        return [
            ("BMS workstation HP Z440 with 24-inch monitor", "No", 6.0),
            ("BMS server rack-mount Xeon with redundancy", "No", 6.0),
            ("DDC controller 32-point BACnet", "No", 48.0),
            ("Field device temperature RTD Pt-100 duct", "No", 240.0),
            ("Field device humidity RH 0-100% duct", "No", 240.0),
            ("Field device pressure differential DPS 0-500Pa", "No", 240.0),
        ] * 25   # ~150 items
    if label == "HSD":
        return [
            ("HSD bulk tank UL-142 5000 L double-wall", "No", 4.0),
            ("HSD day tank 1000 L galvanised", "No", 8.0),
            ("Transfer pump self-priming 200 LPH", "No", 8.0),
            ("Level transmitter capacitive 0-100% with relay", "No", 16.0),
            ("Flame arrestor 25mm dia in-line", "No", 16.0),
            ("Fuel filter 25 micron cartridge in-line", "No", 16.0),
        ] * 17   # ~102 items
    return []


def build_skeleton(target_n: int = 3000):
    rows = []
    sno = 1
    for disc in ("HVAC", "Electrical", "Fire", "Lifts", "Plumbing", "PA", "BMS", "HSD"):
        for (item, unit, qty) in _seed(disc):
            rows.append(BoQSkeletonRow(s_no=sno, item_name=item, qty=qty, unit=unit))
            sno += 1
            if len(rows) >= target_n:
                return rows
    return rows


def main():
    state = build_state()
    skeleton = build_skeleton(target_n=3000)
    print(f"R8.3 — HOD Towers capital smoke target={len(skeleton)} rows")
    result, boq_rows = run_smoke(
        "HOD Towers capital", state, skeleton,
        cost_budget=60.0,
        wall_budget=1200.0,
        citation_threshold=0.85,
        spec_threshold=0.95,
    )
    Path("/tmp/r83_smoke_result.json").write_text(json.dumps(result, indent=2, default=str))
    Path("/tmp/r83_boq_sample.json").write_text(json.dumps(boq_rows[:50], indent=2, default=str))
    return 0 if result["all_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
