"""
engines/parameter_cascade.py

ParameterCascadeEngine — given high-level tender inputs (department, type,
value, duration, method, AP-flag, funding, MSE/Startup), computes ALL the
derived tender parameters needed to assemble or validate a tender document.

The cascade encodes documented procurement rules:

AP-state thresholds:
  GO Ms No 94/2003 (I&CAD)             — comprehensive AP works tender procedures
  GO Ms No 79/2020 (Finance)           — reverse-tendering MANDATORY ≥ Rs.1 cr
  GO Ms No 62/2021 (WR Reforms)        — price adjustment applicability + formulae
  GO Ms No 57/2024 (WR Reforms)        — mobilization advance restoration
  GO Ms No 258/2013 (Finance TFR)      — e-procurement for stores ≥ Rs.1 lakh
  GO Ms No 2/2014 (Finance W&P)        — e-procurement for works/material ≥ Rs.1 lakh
  AP Judicial Preview Act 2019         — judicial preview required ≥ Rs.100 cr
  AP Financial Code Vol-I Articles 122–129
                                       — Stores procurement, Open Tender ≥ Rs.5 lakh,
                                         PBG = 10% (Article 129)

Central thresholds:
  GFR 2017 Rule 154 (e-procurement) / Rule 161-162 (modes) / Rule 170 (EMD)
  CVC OM 2014                          — e-procurement ≥ Rs.2 lakh
  MPW 2022 §4.11/§4.12                 — EMD 2-5%, PBG 5-10%
  MPG 2022 §3.4                        — Open Tender Enquiry ≥ Rs.50 lakh
  CVC standard                         — Integrity Pact ≥ Rs.5 crore

MSE / Startup carve-outs:
  GFR Rule 170(iv) + MSME procurement policy — MSE EMD capped at Rs.2 lakh
  DPIIT Startup procurement policy           — startups exempted from EMD
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────────────────
# I/O models
# ─────────────────────────────────────────────────────────────────────────────

TenderType = Literal["Works", "Goods", "Consultancy", "EPC", "Services"]
ProcMethod = Literal["Open", "Limited", "Single", "Reverse"]


class TenderInputs(BaseModel):
    """High-level inputs to the cascade."""

    department: str
    tender_type: TenderType
    estimated_value: float = Field(..., gt=0, description="Estimated value in INR")
    duration_months: int = Field(..., ge=0)
    procurement_method: ProcMethod
    is_ap_tender: bool = True
    funding_source: str = "state"
    mse_participation: bool = False
    startup_participation: bool = False


class TenderParameters(BaseModel):
    """Complete derived parameter set for a tender."""

    # ── FINANCIAL ──
    emd_percentage: float
    emd_amount: float
    emd_mse_capped: bool
    pbg_percentage: float
    pbg_amount: float
    retention_percentage: float = 5.0
    mobilisation_advance_max_pct: float = 10.0
    mobilisation_advance_bg_pct: float = 110.0
    ld_per_week_pct: float = 0.5
    ld_cap_pct: float = 10.0

    # ── TEMPORAL ──
    bid_validity_days: int
    bg_validity_days: int
    solvency_cert_max_months: int = 3
    loa_acknowledgment_days: int = 7
    contract_execution_days: int = 30
    dlp_months: int
    price_adjustment_applicable: bool

    # ── MANDATORY FLAGS ──
    e_procurement_mandatory: bool
    reverse_tender_mandatory: bool
    judicial_preview_required: bool
    integrity_pact_required: bool
    open_tender_required: bool
    two_cover_required: bool
    anti_collusion_form_required: bool = True
    arbitration_allowed: bool


# ─────────────────────────────────────────────────────────────────────────────
# Engine
# ─────────────────────────────────────────────────────────────────────────────

class ParameterCascadeEngine:
    """Cascade tender inputs into a complete TenderParameters object.

    The engine is intentionally pure: no database calls, no I/O. All
    thresholds are class constants so they can be inspected, overridden,
    and audited.
    """

    # ── AP-state thresholds (INR) ──
    AP_EPROC_THRESHOLD                = 100_000          # Rs.1 lakh — GO 258/2013, GO 2/2014
    AP_REVERSE_TENDER_THRESHOLD       = 1_00_00_000      # Rs.1 crore — GO 79/2020
    AP_PRICE_ADJ_VALUE_THRESHOLD      = 40_00_000        # Rs.40 lakh — GO 62/2021
    AP_PRICE_ADJ_MIN_MONTHS           = 6                # GO 62/2021
    AP_OPEN_TENDER_GOODS              = 5_00_000         # Rs.5 lakh — AP FC Article 125 Rule III(8)
    AP_OPEN_TENDER_WORKS              = 2_500            # Rs.2,500 — AP FC Article 192
    AP_OPEN_TENDER_CONSULTANCY        = 50_00_000        # Rs.50 lakh — common practice
    AP_JUDICIAL_PREVIEW_THRESHOLD     = 100_00_00_000    # Rs.100 crore — AP JP Act 2019
    AP_TWO_COVER_WORKS_THRESHOLD      = 10_00_000        # Rs.10 lakh — AP GO 94/2003 Annex-I §12
    AP_EMD_PCT                        = 2.5              # AP GO 94/2003 Annex-I §5 (1% + 1.5%)
    AP_PBG_GOODS_PCT                  = 10.0             # AP FC Article 129
    AP_PBG_WORKS_PCT                  = 5.0              # MPW default; AP-2.5% departures handled at clause level

    # ── Central thresholds (INR) ──
    CENTRAL_EPROC_THRESHOLD           = 2_00_000         # Rs.2 lakh — CVC OM 2014
    CENTRAL_OPEN_TENDER_GOODS         = 50_00_000        # Rs.50 lakh — GFR / MPG
    CENTRAL_OPEN_TENDER_WORKS         = 5_00_000         # Rs.5 lakh — MPW (NIT mandate)
    CENTRAL_TWO_COVER_WORKS_THRESHOLD = 10_00_000        # Rs.10 lakh — common practice
    CENTRAL_EMD_PCT                   = 2.0              # GFR Rule 170
    CENTRAL_PBG_PCT                   = 5.0              # MPW §4.12 default
    CENTRAL_PRICE_ADJ_MIN_MONTHS      = 18               # MPW §6.2 default

    # ── Universal thresholds ──
    INTEGRITY_PACT_THRESHOLD          = 5_00_00_000      # Rs.5 crore — CVC standard

    # ── MSE / Startup carve-outs ──
    MSE_EMD_CAP                       = 2_00_000         # Rs.2 lakh — MSME procurement policy
    STARTUP_EMD_WAIVED                = True             # DPIIT startup policy

    # ─────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────

    def compute(self, inputs: TenderInputs) -> TenderParameters:
        """Cascade the inputs into a complete parameter set."""
        ev = inputs.estimated_value
        is_ap = inputs.is_ap_tender
        is_works_or_epc = inputs.tender_type in ("Works", "EPC")
        is_goods = inputs.tender_type == "Goods"
        is_consultancy = inputs.tender_type == "Consultancy"

        emd_pct, emd_amount, emd_mse_capped = self._compute_emd(
            ev, is_ap, inputs.mse_participation, inputs.startup_participation
        )
        pbg_pct, pbg_amount = self._compute_pbg(ev, is_ap, is_works_or_epc, is_goods)
        bid_validity = self._compute_bid_validity(inputs)
        bg_validity = bid_validity + 30  # GFR/MPW common practice
        dlp_months = self._compute_dlp_months(is_works_or_epc, is_consultancy)
        price_adj = self._compute_price_adjustment(
            ev, is_ap, is_works_or_epc, inputs.duration_months
        )

        flags = self._compute_flags(
            ev=ev, is_ap=is_ap,
            is_works_or_epc=is_works_or_epc,
            is_goods=is_goods,
            is_consultancy=is_consultancy,
        )

        return TenderParameters(
            # FINANCIAL
            emd_percentage=emd_pct,
            emd_amount=round(emd_amount, 2),
            emd_mse_capped=emd_mse_capped,
            pbg_percentage=pbg_pct,
            pbg_amount=round(pbg_amount, 2),
            # TEMPORAL
            bid_validity_days=bid_validity,
            bg_validity_days=bg_validity,
            dlp_months=dlp_months,
            price_adjustment_applicable=price_adj,
            # FLAGS
            **flags,
        )

    # ─────────────────────────────────────────────────────────────────────
    # Component helpers
    # ─────────────────────────────────────────────────────────────────────

    def _compute_emd(
        self,
        ev: float,
        is_ap: bool,
        mse: bool,
        startup: bool,
    ) -> tuple[float, float, bool]:
        """Return (emd_percentage, emd_amount, emd_mse_capped)."""
        if startup and self.STARTUP_EMD_WAIVED:
            return 0.0, 0.0, False

        emd_pct = self.AP_EMD_PCT if is_ap else self.CENTRAL_EMD_PCT
        emd_amount = ev * emd_pct / 100

        if mse:
            return emd_pct, min(emd_amount, self.MSE_EMD_CAP), True
        return emd_pct, emd_amount, False

    def _compute_pbg(
        self,
        ev: float,
        is_ap: bool,
        is_works_or_epc: bool,
        is_goods: bool,
    ) -> tuple[float, float]:
        """Return (pbg_percentage, pbg_amount)."""
        if is_ap and is_goods:
            pbg_pct = self.AP_PBG_GOODS_PCT
        elif is_works_or_epc:
            pbg_pct = self.AP_PBG_WORKS_PCT if is_ap else self.CENTRAL_PBG_PCT
        else:
            pbg_pct = self.CENTRAL_PBG_PCT
        return pbg_pct, ev * pbg_pct / 100

    def _compute_bid_validity(self, inputs: TenderInputs) -> int:
        """Return bid_validity_days based on tender type + procurement method."""
        if inputs.tender_type in ("Consultancy", "EPC"):
            return 180
        if inputs.procurement_method == "Open":
            return 90
        if inputs.procurement_method == "Limited":
            return 60
        if inputs.procurement_method == "Single":
            return 30
        return 90  # Reverse default

    def _compute_dlp_months(self, is_works_or_epc: bool, is_consultancy: bool) -> int:
        if is_works_or_epc:
            return 24      # AP-GO-084 / MPW default
        if is_consultancy:
            return 6
        return 12          # Goods default

    def _compute_price_adjustment(
        self,
        ev: float,
        is_ap: bool,
        is_works_or_epc: bool,
        duration_months: int,
    ) -> bool:
        if is_ap:
            return (
                ev >= self.AP_PRICE_ADJ_VALUE_THRESHOLD
                and duration_months >= self.AP_PRICE_ADJ_MIN_MONTHS
            )
        return is_works_or_epc and duration_months >= self.CENTRAL_PRICE_ADJ_MIN_MONTHS

    def _compute_flags(
        self,
        *,
        ev: float,
        is_ap: bool,
        is_works_or_epc: bool,
        is_goods: bool,
        is_consultancy: bool,
    ) -> dict:
        # E-procurement
        if is_ap:
            e_proc = ev >= self.AP_EPROC_THRESHOLD
        else:
            e_proc = ev >= self.CENTRAL_EPROC_THRESHOLD

        # Reverse tendering
        rev_mandatory = is_ap and ev >= self.AP_REVERSE_TENDER_THRESHOLD

        # Judicial preview (AP infrastructure ≥ Rs.100 cr)
        jp_required = (
            is_ap
            and is_works_or_epc
            and ev >= self.AP_JUDICIAL_PREVIEW_THRESHOLD
        )

        # Integrity pact
        ip_required = ev >= self.INTEGRITY_PACT_THRESHOLD

        # Open tender mandate
        if is_ap:
            if is_goods:
                open_required = ev >= self.AP_OPEN_TENDER_GOODS
            elif is_works_or_epc:
                open_required = ev >= self.AP_OPEN_TENDER_WORKS
            else:
                open_required = ev >= self.AP_OPEN_TENDER_CONSULTANCY
        else:
            if is_goods:
                open_required = ev >= self.CENTRAL_OPEN_TENDER_GOODS
            elif is_works_or_epc:
                open_required = ev >= self.CENTRAL_OPEN_TENDER_WORKS
            else:
                open_required = ev >= self.CENTRAL_OPEN_TENDER_GOODS

        # Two-cover sealed bid
        two_cover_threshold = (
            self.AP_TWO_COVER_WORKS_THRESHOLD if is_ap
            else self.CENTRAL_TWO_COVER_WORKS_THRESHOLD
        )
        two_cover = (
            (is_works_or_epc and ev >= two_cover_threshold)
            or is_consultancy  # QCBS always two-envelope
        )

        # Arbitration allowed?
        # AP works: civil court ladder per GO 94/2003 — arbitration NOT allowed for ≥ Rs.50K claims
        arbitration_allowed = not (is_ap and is_works_or_epc)

        return {
            "e_procurement_mandatory": e_proc,
            "reverse_tender_mandatory": rev_mandatory,
            "judicial_preview_required": jp_required,
            "integrity_pact_required": ip_required,
            "open_tender_required": open_required,
            "two_cover_required": two_cover,
            "arbitration_allowed": arbitration_allowed,
        }
