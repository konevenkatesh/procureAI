"""
modules/validation/section_router.py

Document-family-aware retrieval: pick the right `section_type` filter
for a given typology BEFORE running BGE-M3 + LLM rerank.

Why: the same content category (e.g. EMD / Bid Security) lives in
different `section_type` labels depending on the procurement document's
template family. Diagnostic results from L23 + the EMD-retrieval probe:

    APCRDA Works  (JA, HC)         → EMD content classified NIT
    SBD Format    (Kakinada)       → EMD content classified Evaluation
    NREDCAP PPP   (Tirupathi/Vja)  → EMD content classified NIT (and
                                     the percentage rerank already
                                     surfaces it via NIT)
    Vizag UGSS                     → EMD not stated in source (portal-
                                     implicit; gateway pays it)

A single global `section_type` filter cannot serve all three families
without dragging in PCC PBG reminders and ITB form-templates that
out-rank the actual EMD section. The router below picks the
section-type allowlist per (typology, family).

Family detection (cheap heuristic, kg_node-only — no LLM):

    SBD_Format       if the doc has a single dominant `Evaluation`-typed
                     section that holds most of the body
                     (Smart City SBDs put 80%+ of bid-process content
                     in one Evaluation block — see Kakinada)
    APCRDA_Works     if `GCC` is the dominant section_type AND
                     `is_ap_tender` is True AND tender_type is Works/EPC
                     (JA, HC, Vizag-Works pattern)
    NREDCAP_PPP      if tender_type is PPP/DBFOT (Tirupathi, Vijayawada)
    default          everything else — broadest filter so we don't
                     silently miss a typology on an unrecognised family

Family detection is INDEPENDENT of EMD — the same family label is
useful for other typologies (Integrity Pact lives in different
section types per family too). The router is keyed by both
(typology, family).

Public API:

    detect_family(doc_id) → str
    section_filter(typology: str, family: str) → list[str]
    family_for_doc_with_filter(doc_id, typology) → tuple[str, list[str]]
"""
from __future__ import annotations

from collections import Counter

import requests
from pathlib import Path
import sys


# Make sure the project root is importable when this module is loaded
# via `python -m modules.validation.section_router` or imported from
# scripts/.
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from builder.config import settings    # noqa: E402


REST = settings.supabase_rest_url
H = {"apikey":        settings.supabase_anon_key,
     "Authorization": f"Bearer {settings.supabase_anon_key}"}


# ── Section-type allowlists per (typology, family) ────────────────────
#
# Keyed by typology to keep the router open for future typologies
# (Integrity Pact, Judicial Preview, etc.) without re-organising the
# table. For now only EMD-Shortfall is wired in; PBG-Shortfall already
# uses a hardcoded list in tier1_pbg_check.py and will migrate later
# if needed.
EMD_SECTION_ROUTER: dict[str, list[str]] = {
    "APCRDA_Works":  ["NIT", "ITB"],
    "SBD_Format":    ["Evaluation", "ITB"],
    "NREDCAP_PPP":   ["NIT", "Forms"],
    "default":       ["NIT", "ITB", "Evaluation"],
}


# Bid-Validity-Short — bid-validity periods live in the same broad
# region as EMD (BDS / ITB), but PPP RFPs put it under NIT only
# ("14. VALIDITY OF THE BIDS" pattern in NREDCAP RFPs). No need for
# Forms (which carries bond templates, not the validity period).
BID_VALIDITY_SECTION_ROUTER: dict[str, list[str]] = {
    "APCRDA_Works":  ["ITB", "NIT"],
    "SBD_Format":    ["Evaluation", "ITB"],
    "NREDCAP_PPP":   ["NIT"],
    "default":       ["ITB", "NIT", "Evaluation"],
}


# Missing-PVC-Clause — Price Variation / Price Adjustment clauses are
# almost always in GCC or SCC (Vol-II) per the clause-template
# positions in the rules table. Heading search returns near-zero hits
# (Vizag: 1 stub heading match) — the actual clause body lives inside
# larger sections with generic headings, so the filter has to be
# permissive on section_type and the BGE-M3 + smart_truncate combo
# does the surfacing. PPP docs handle price variation through
# concession terms, not tender PVC, so AP-GO-019 / MPW-133 SKIP at
# the rule layer; the PPP filter still has GCC for completeness.
PVC_SECTION_ROUTER: dict[str, list[str]] = {
    "APCRDA_Works":  ["GCC", "SCC", "Specifications"],
    "SBD_Format":    ["GCC", "SCC", "Evaluation"],
    "NREDCAP_PPP":   ["GCC"],     # SKIP at rule layer; filter retained for completeness
    "default":       ["GCC", "SCC", "Specifications"],
}


# Missing-Integrity-Pact — IP applicability statements live in NIT
# (mandate clauses) and Forms (bidder declaration / pact proforma).
# Per the read-first scan of clause_templates: CLAUSE-IP-PREAMBLE-001
# (Forms), CLAUSE-INTEGRITY-PACT-MANDATE-001 (NIT), CLAUSE-IP-WORKS-001
# (NIT) — none in GCC/SCC/Specifications. The user-set router for this
# typology is intentionally [NIT, Forms] across every family because
# the IP anchors are stable across document templates (unlike PVC which
# moves between GCC and Evaluation per family).
#
# IMPORTANT (L30): APCRDA_Works documents under the Amaravati capital
# city program are funded by ADB ($788.8M) + World Bank ($800M), and
# therefore ship with the LENDER's anticorruption framework (ADB IPG,
# OAI sanctions, IEF, World Bank Sanctions Procedures, etc.). The
# multilateral-lender framework DOES NOT substitute for the regulated
# CVC Pre-bid Integrity Pact — Indian procurement law (CVC-086,
# MPS-022) requires the CVC IP regardless of funding source. The
# router stays at [NIT, Forms] for these docs and CVC-086 / MPS-022
# still fire; the LLM rerank prompt (in tier1_integrity_pact_check.py)
# distinguishes the two instruments via separate `adb_framework_detected`
# and `cvc_ip_detected` booleans, with `integrity_pact_present` locked
# to cvc_ip_detected. DO NOT add a multilateral-funding SKIP rule.
IP_SECTION_ROUTER: dict[str, list[str]] = {
    "APCRDA_Works":  ["NIT", "Forms"],
    "SBD_Format":    ["NIT", "Forms"],
    "NREDCAP_PPP":   ["NIT", "Forms"],
    "default":       ["NIT", "Forms"],
}


# Missing-LD-Clause — Liquidated Damages anchors live in GCC (most
# templates: CLAUSE-LD-001, CLAUSE-WORKS-LD-INCENTIVE-001, CLAUSE-LD-
# WAIVER-001) and SCC (Services per-day rate: CLAUSE-LD-PERFORMANCE-
# SERVICES-001). Same retrieval region as PVC, simpler than IP.
#
# Unlike PVC's NREDCAP_PPP entry which was kept "for completeness"
# while the rule layer SKIPped, GFR-083 actively fires on PPP/DBFOT
# (TenderType=ANY catch-all) — so the NREDCAP_PPP filter is real and
# the script will retrieve+rerank+materialise on Tirupathi/Vijayawada
# just like the Works docs. Concession Agreement DCAs DO carry GCC
# sections, so [GCC, SCC] is the correct retrieval region for them.
#
# SBD_Format includes Evaluation because Kakinada has zero GCC-typed
# sections (n_eval=15, n_gcc=0 — the same SBD pattern that drove the
# section_router threshold tuning in L28). The body lives in
# Evaluation blocks and the LD clause, where present, is in there.
LD_SECTION_ROUTER: dict[str, list[str]] = {
    "APCRDA_Works":  ["GCC", "SCC"],
    "SBD_Format":    ["GCC", "SCC", "Evaluation"],
    "NREDCAP_PPP":   ["GCC", "SCC"],
    "default":       ["GCC", "SCC", "Specifications"],
}


# Mobilisation-Advance-Excess — same retrieval region as LD/PVC.
# MA anchors live in:
#   GCC : CLAUSE-MOBILISATION-ADVANCE-WORKS-001 (MPW 2022 §6.5.3 baseline)
#   SCC : CLAUSE-AP-MOBILIZATION-RESTORED-001 (AP Works 10% per GO 94/2003 + GO 57/2024)
#         CLAUSE-AP-EPC-MOBILIZATION-001       (AP EPC 5% override)
#
# SBD_Format includes Evaluation because Kakinada has zero GCC-typed
# sections (n_eval=15, n_gcc=0 — same SBD pattern as LD/PVC).
#
# NREDCAP_PPP: rule layer SKIPs (no rule in the 23-rule set fires on
# PPP/DBFOT). Filter retained for completeness so the script can still
# run if a future PPP MA rule lands. Tirupathi/Vijayawada currently
# return zero candidates from rule selection — the script exits before
# retrieval.
MA_SECTION_ROUTER: dict[str, list[str]] = {
    "APCRDA_Works":  ["GCC", "SCC"],
    "SBD_Format":    ["GCC", "SCC", "Evaluation"],
    "NREDCAP_PPP":   ["GCC", "SCC"],
    "default":       ["GCC", "SCC", "Specifications"],
}


# E-Procurement-Bypass — mandate clauses live in NIT (AP-GO-012
# stores/works mandate, EPROC-PROCESS-001) and ITB (DSC requirement,
# AP RFP single-stage e-procurement, AP two-cover residual).
#
# CLAUSE-AP-EPROCUREMENT-WORKS-1LAKH-001 (NIT) is the primary anchor
# for AP corpus docs; CLAUSE-EPROC-PROCESS-001 (NIT) carries the DSC
# language; CLAUSE-AP-RFP-DIGITAL-SIGNATURE-001 (ITB) carries the
# e-portal upload requirements; CLAUSE-AP-RFP-SINGLE-STAGE-TWO-SUBSTAGE
# -001 (ITB) explicitly says "Bid Process shall be conducted by way
# of E-PROCUREMENT".
#
# CLAUSE-AP-TWO-COVER-SYSTEM-001 (ITB) is residual two-cover language
# that lives alongside e-procurement on AP docs — NOT a bypass signal
# by itself; the LLM prompt distinguishes "doc mandates e-procurement"
# from "doc has legacy two-cover language alongside e-procurement".
#
# SBD_Format includes Evaluation because Kakinada has zero NIT-typed
# body sections beyond the title (ITT body lives in Evaluation per
# the L28 SBD pattern).
EPROC_SECTION_ROUTER: dict[str, list[str]] = {
    "APCRDA_Works":  ["NIT", "ITB"],
    "SBD_Format":    ["NIT", "ITB", "Evaluation"],
    "NREDCAP_PPP":   ["NIT", "ITB"],
    "default":       ["NIT", "ITB"],
}


# Blacklist-Not-Checked — bidder declaration + buyer verification
# anchors live in ITB (eligibility, debarment, sanctions clauses)
# and Forms (bidder self-declaration / disclosure forms).
#
# Per the read-first scan of clause_templates:
#   ITB   : CLAUSE-DEBARMENT-GFR-RULE-151-001 (Rule 151 conviction-based)
#           CLAUSE-DEBARMENT-ALLIED-001 (allied-firm coverage)
#           CLAUSE-AP-OTHER-REQUIREMENTS-001 (AP blacklist self-cert)
#           CLAUSE-WB-ADB-DEBARMENT-001 (multilateral lender sanctions)
#           CLAUSE-DEBARMENT-GRADES-001 (holiday listing / banning)
#           CLAUSE-HOLIDAY-LISTING-001 (temporary debarment)
#   Forms : CLAUSE-BLACKLIST-DISCLOSURE-FORM-001 (bidder self-decl)
#   GCC   : CLAUSE-AP-CONTRACTOR-BLACKLIST-001 (AP contractor mgmt)
#
# GCC is intentionally NOT in the filter — the AP contractor
# blacklisting clause is operational (post-execution) management,
# not a bid-stage eligibility check. The Tier-1 question is whether
# the doc requires bidders to declare past debarments or commits
# the buyer to checking, which lives in ITB + Forms.
#
# SBD_Format includes Evaluation because Kakinada's SBD body lives
# in Evaluation blocks (per L28 SBD pattern).
BLACKLIST_SECTION_ROUTER: dict[str, list[str]] = {
    "APCRDA_Works":  ["ITB", "Forms"],
    "SBD_Format":    ["ITB", "Forms", "Evaluation"],
    "NREDCAP_PPP":   ["ITB", "Forms"],
    "default":       ["ITB", "Forms"],
}


# BG-Validity-Gap — Bank Guarantee validity-duration anchors live
# in GCC (Performance Bank Guarantee clauses, AP EMD validity, Warranty
# BG specifications) and Forms (PBG proforma templates with the
# validity-period field).
#
# Per the read-first scan of clause_templates:
#   GCC   : CLAUSE-WORKS-PBG-001 (PBG amount + structure for Works)
#           CLAUSE-WBG-001 (Warranty BG: "60 days beyond warranty period")
#           CLAUSE-AP-EMD-VALIDITY-DLP-001 (AP Works EMD validity beyond DLP)
#           CLAUSE-CONTRACT-CLOSURE-001 (pre-PBG-release checks)
#   Forms : PBG / BG proforma templates with embedded validity field
#   ITB   : CLAUSE-BG-VERIFICATION-001 (issuer + format rules — NOT validity)
#
# ITB's BG-format clauses (must be irrevocable, scheduled commercial
# bank only) do NOT carry the validity-duration spec — they live
# alongside the PBG/BG validity clause in GCC. Default family adds
# ITB only as a safety net for non-canonical docs.
#
# SBD_Format includes Evaluation per the L28 SBD pattern (Kakinada
# n_gcc=0; the validity language sits in long Evaluation/ITT blocks).
#
# NREDCAP_PPP is retained for completeness even though all 9 typology
# rules SKIP on TenderType=PPP — the ConcessionAgreement DCAs DO carry
# Performance Security validity tied to Concession Period + DLP, but
# no rule in the typology table currently models PPP BG validity.
# Forward applicability item flagged in L37.
BG_VALIDITY_SECTION_ROUTER: dict[str, list[str]] = {
    "APCRDA_Works":  ["GCC", "Forms"],
    "SBD_Format":    ["GCC", "Forms", "Evaluation"],
    "NREDCAP_PPP":   ["GCC", "Forms"],
    "default":       ["GCC", "Forms", "ITB"],
}


# Judicial-Preview-Bypass — JP mandate citations live exclusively
# in NIT per the read-first scan of clause_templates:
#   NIT : CLAUSE-AP-JUDICIAL-PREVIEW-MANDATE-001  (the mandate clause)
#         CLAUSE-AP-JUDICIAL-PREVIEW-PROCESS-001  (7+8+15 day windows)
#         CLAUSE-AP-JUDICIAL-PREVIEW-SECTORS-001  (25-sector list)
#
# The cited regulatory anchor is "AP Judicial Preview Act 2019" (with
# prior framework GO Ms No 38/2018). The Judicial Preview Authority
# (APJPA) is a sitting/former High Court Judge's office.
#
# IMPORTANT corpus distinction: "Judicial Academy" (the procuring
# entity for the JA doc) is NOT the same as "Judicial Preview"
# (the regulatory framework). The LLM and grep keywords must be
# phrase-precise — "Judicial Preview" / "APJPA" / "Preview Authority",
# never bare "judicial".
#
# ITB is included as backup for non-canonical docs (e.g. SBD with
# JP citations in tendering instructions). SBD_Format adds
# Evaluation per the L28 SBD pattern.
JP_SECTION_ROUTER: dict[str, list[str]] = {
    "APCRDA_Works":  ["NIT", "ITB"],
    "SBD_Format":    ["NIT", "ITB", "Evaluation"],
    "NREDCAP_PPP":   ["NIT", "ITB"],
    "default":       ["NIT", "ITB"],
}


# Turnover-Threshold-Excess — PQ turnover requirements live in
# Volume-I tendering machinery (NIT / ITB / Datasheet) for SBD-style
# docs and in RFP Volume-I Clause 4.2(b) (ITB-shaped) for NREDCAP PPP
# DCAs. Two PQ shapes coexist in the AP corpus:
#   1. Bid-Capacity formula (3AN-B / 2AN-B) — Vizag, JA, HC, Kakinada.
#      No fixed INR threshold to test against the ≤2× cap.
#   2. Fixed INR turnover floor — Tirupathi, Vijayawada (PPP DCAs).
#      Avg 3-yr turnover ≥ INR X cr. This is the typology-firing shape.
#
# Corpus discovery (typology-12 read-first): in HC, Tirupathi and
# Vijayawada the PQ Financial section is classified as
# `section_type='Evaluation'` (heading "Technical Requirements" /
# "4.2 Financial Criteria"), NOT NIT or ITB. The first run with
# `[NIT, ITB]`-only filters produced 3 false UNVERIFIED findings via
# L36 grep fallback. Fix: include `Evaluation` for every family — the
# PQ-Financial heading has no canonical Volume placement across the
# AP corpus's mixed family shapes (APCRDA SBD, NREDCAP PPP RFP,
# Kakinada SBD), so the filter has to be the union of the three.
TURNOVER_SECTION_ROUTER: dict[str, list[str]] = {
    "APCRDA_Works":  ["NIT", "ITB", "Evaluation"],
    "SBD_Format":    ["NIT", "ITB", "Evaluation"],
    "NREDCAP_PPP":   ["NIT", "ITB", "Evaluation"],
    "default":       ["NIT", "ITB", "Evaluation"],
}


# Eligibility-Class-Mismatch — AP-GO-092 (HARD_BLOCK) tests whether
# the doc's "Eligible Class of Bidders" text demands the correct
# contractor monetary class for its ECV band per GO Ms No 94/2003:
#   Special >  Rs.10 cr          (int=6)
#   Class-I  Rs.2 cr  – Rs.10 cr (int=5)
#   Class-II Rs.1 cr  – Rs.2 cr  (int=4)
#   Class-III Rs.50 L – Rs.1 cr  (int=3)
#   Class-IV Rs.10 L  – Rs.50 L  (int=2)
#   Class-V  ≤ Rs.10 L           (int=1)
#
# Eligibility text lives in NIT (eligibility headers — JA line 57, HC
# line 53, Kakinada line 149, Vizag NIT line 178) and ITB BDS rewrites
# (JA "ITB 4.1" line 444, HC "ITB 4.1" line 298). SBD_Format adds
# Evaluation per L28. NREDCAP_PPP gets [] because AP-GO-092 SKIPs on
# TenderType=PPP — rule selector exits before the router is queried;
# the empty list is purely defensive.
# NOTE — `Forms` added to every family after L41 gap-filler discovery:
# kg_builder gap-fills (synthetic Section nodes covering line ranges
# the deterministic splitter misses) frequently get classified as
# section_type='Forms' by the heading-content classifier because
# their tabular eNIT content lacks distinctive heading anchors. The
# Class-of-Bidders text in Kakinada (L149) and Vizag (L178) both
# land in `Forms` after the L41 fix. Without `Forms` in the router,
# the gap-fill content is still excluded at Qdrant retrieval even
# after gap-closure. Forward-applicable: any typology reaching into
# tabular eNIT content should include `Forms`.
ELIGIBILITY_CLASS_SECTION_ROUTER: dict[str, list[str]] = {
    "APCRDA_Works":  ["NIT", "ITB", "Forms"],
    "SBD_Format":    ["NIT", "Evaluation", "Forms"],
    "NREDCAP_PPP":   [],
    "default":       ["NIT", "ITB", "Evaluation", "Forms"],
}


# Arbitration-Clause-Violation — arbitration / dispute-resolution
# clauses live primarily in Vol-II GCC (per the read-first scan of
# clause_templates: CLAUSE-DISPUTE-RESOLUTION-001, CLAUSE-ARBITRATION-
# 001, CLAUSE-ARBITRATION-AGREEMENT-001, CLAUSE-FOREIGN-ARBITRATION-
# 001, CLAUSE-DRB-WORKS-001, CLAUSE-AP-CIVIL-COURT-50K-001, CLAUSE-AP-
# 3-ARBITRATOR-ICA-001 — all in Volume-II/Section-1/GCC). SCC may
# carry AP-State overrides (e.g. the civil-court ladder per APSS
# Clause 61). SBD_Format adds Evaluation per the L28 SBD pattern;
# default adds Forms because L41 gap-fills frequently land there
# (e.g. Annexure-17 arbitrator-declaration formats).
ARBITRATION_SECTION_ROUTER: dict[str, list[str]] = {
    "APCRDA_Works":  ["GCC", "SCC"],
    "SBD_Format":    ["GCC", "SCC", "Evaluation"],
    "NREDCAP_PPP":   ["GCC", "SCC"],
    "default":       ["GCC", "SCC", "Forms"],
}


# Geographic-Restriction — anchor clauses live in:
#   - Volume-I/Section-2/ITB:  Land-border-country eligibility clause
#                              (CLAUSE-AP-LAND-BORDER-COUNTRY-RESTRICTION-001,
#                              CLAUSE-ELIGIBILITY-LANDBORDER-001, CLAUSE-
#                              LANDBORDER-VALIDITY-TIMING-001, CLAUSE-WORKS-
#                              SUBCONTRACT-LANDBORDER-001), AP-NO-JV-CONSORTIUM-
#                              001 anti-pattern, ITB 4.4 nationality
#   - Volume-I/Section-5/Forms: Bidder compliance certificates per MPS-213 /
#                              MPW-180 (CLAUSE-LAND-BORDER-WORKS-CERT-001,
#                              CLAUSE-LAND-BORDER-CERT-GEM-001)
#   - Volume-I/Section-1/NIT:  GTE clauses + MDB-funded GTE-exemption
#                              (CLAUSE-MDB-FUNDED-EXEMPTION-001, CLAUSE-
#                              WORKS-GTE-001)
# Forms is essential everywhere because bidder certificates (the MPS-213 /
# MPW-180 PRESENCE rules) live there. Evaluation is essential too: JA's
# "Any contractor from abroad not be permitted" anti-pattern at L878
# falls in section_type='Evaluation' "General Requirements" L870-899
# — a recurring corpus pattern where foreign-bidder bans are in BDS-
# rewrite-style Evaluation regions, not the canonical ITB. Including
# Evaluation in every family closes the retrieval-coverage gap.
GEOGRAPHIC_SECTION_ROUTER: dict[str, list[str]] = {
    "APCRDA_Works":  ["NIT", "ITB", "Evaluation", "Forms"],
    "SBD_Format":    ["NIT", "ITB", "Evaluation", "Forms"],
    "NREDCAP_PPP":   ["NIT", "ITB", "Evaluation", "Forms"],
    "default":       ["NIT", "ITB", "Evaluation", "Forms"],
}


SECTION_ROUTERS: dict[str, dict[str, list[str]]] = {
    "EMD-Shortfall":               EMD_SECTION_ROUTER,
    "Bid-Validity-Short":          BID_VALIDITY_SECTION_ROUTER,
    "Missing-PVC-Clause":          PVC_SECTION_ROUTER,
    "Missing-Integrity-Pact":      IP_SECTION_ROUTER,
    "Missing-LD-Clause":           LD_SECTION_ROUTER,
    "Mobilisation-Advance-Excess": MA_SECTION_ROUTER,
    "E-Procurement-Bypass":        EPROC_SECTION_ROUTER,
    "Blacklist-Not-Checked":       BLACKLIST_SECTION_ROUTER,
    "BG-Validity-Gap":             BG_VALIDITY_SECTION_ROUTER,
    "Judicial-Preview-Bypass":     JP_SECTION_ROUTER,
    "Turnover-Threshold-Excess":   TURNOVER_SECTION_ROUTER,
    "Eligibility-Class-Mismatch":  ELIGIBILITY_CLASS_SECTION_ROUTER,
    "Arbitration-Clause-Violation": ARBITRATION_SECTION_ROUTER,
    "Geographic-Restriction":      GEOGRAPHIC_SECTION_ROUTER,
    # Future typologies plug in here.
}


# ── Family detection ──────────────────────────────────────────────────

def _fetch_doc(doc_id: str) -> tuple[dict, list[dict]]:
    """Pull TenderDocument properties + every Section node's properties
    for one doc_id. Returns ({}, []) if the doc isn't in the KG."""
    td = requests.get(f"{REST}/rest/v1/kg_nodes",
                      params={"select":"properties","doc_id":f"eq.{doc_id}",
                              "node_type":"eq.TenderDocument"},
                      headers=H, timeout=30).json()
    secs = requests.get(f"{REST}/rest/v1/kg_nodes",
                        params={"select":"properties","doc_id":f"eq.{doc_id}",
                                "node_type":"eq.Section"},
                        headers=H, timeout=30).json()
    td_props = (td[0].get("properties") or {}) if td else {}
    sec_props = [(s.get("properties") or {}) for s in secs]
    return td_props, sec_props


def _section_type_counts(secs: list[dict]) -> Counter:
    """Count how many Section nodes carry each `section_type` label."""
    return Counter((s.get("section_type") or "(none)") for s in secs)


def detect_family(doc_id: str) -> str:
    """Cheap kg_node-only family detector.

    Heuristic order (first match wins):

        SBD_Format     if Evaluation >= 10 AND GCC == 0
                       (the SBD pattern: body sits in Evaluation blocks,
                        zero GCC-typed sections — Kakinada n_eval=15,
                        n_gcc=0 fits this; was missed by the prior >20
                        threshold and routed to `default`, whose filter
                        `[GCC, SCC, Specifications]` matched zero
                        candidates and broke retrieval entirely.)
        APCRDA_Works   if GCC > 50 sections AND is_ap_tender AND tender_type Works/EPC
        NREDCAP_PPP    if tender_type=PPP
        default        otherwise

    The SBD_Format threshold was lowered (>20 → >=10 with the n_gcc==0
    co-condition) after the Kakinada PVC re-run found zero candidates
    in the default filter. The n_gcc==0 guard prevents APCRDA_Works
    docs (which have BOTH high GCC and some Evaluation sections) from
    being mis-routed to SBD_Format. APCRDA_Works docs have at least
    one GCC section, by definition.
    """
    td_props, sec_props = _fetch_doc(doc_id)
    if not td_props:
        return "default"

    counts = _section_type_counts(sec_props)
    n_total = sum(counts.values())
    n_eval  = counts.get("Evaluation", 0)
    n_gcc   = counts.get("GCC", 0)

    is_ap       = bool(td_props.get("is_ap_tender"))
    tender_type = td_props.get("tender_type")

    # Order matters — most specific first.
    if n_eval >= 10 and n_gcc == 0:
        return "SBD_Format"
    if n_gcc > 50 and is_ap and tender_type in ("Works", "EPC"):
        return "APCRDA_Works"
    if tender_type in ("PPP", "DBFOT"):
        return "NREDCAP_PPP"
    return "default"


def section_filter(typology: str, family: str) -> list[str]:
    """Return the section_type allowlist for (typology, family).

    Falls back to `default` if (a) the typology isn't routed yet, or
    (b) the family isn't recognised for that typology. Never returns
    an empty list — that would silently exclude all candidates.
    """
    router = SECTION_ROUTERS.get(typology)
    if not router:
        # Unknown typology — broad allowlist (same as EMD's default)
        return EMD_SECTION_ROUTER["default"]
    return router.get(family) or router.get("default") or EMD_SECTION_ROUTER["default"]


def family_for_doc_with_filter(doc_id: str, typology: str) -> tuple[str, list[str]]:
    """Convenience wrapper used by Tier-1 callers:

        family, allowlist = family_for_doc_with_filter(doc_id, "EMD-Shortfall")
        points = qdrant_topk(qvec, doc_id, k=10, section_types=allowlist)
    """
    family = detect_family(doc_id)
    flt    = section_filter(typology, family)
    return family, flt


# ── CLI: print family + filter for every doc in the KG ───────────────

def _cli() -> int:
    """Read all distinct doc_ids from kg_nodes and print
    family + EMD section_filter per doc. Read-only."""
    docs = requests.get(f"{REST}/rest/v1/kg_nodes",
                        params={"select":"doc_id","node_type":"eq.TenderDocument"},
                        headers=H, timeout=30).json()
    doc_ids = sorted({d["doc_id"] for d in docs})
    print(f"=== Family detection on {len(doc_ids)} doc(s) (typology=EMD-Shortfall) ===\n")
    print(f"{'doc_id':32s} {'tender_type':12s} {'is_ap':6s} {'family':16s} section_filter")
    print("-" * 100)
    for d in doc_ids:
        td, secs = _fetch_doc(d)
        counts = _section_type_counts(secs)
        family, flt = family_for_doc_with_filter(d, "EMD-Shortfall")
        print(f"  {d:30s} {str(td.get('tender_type')):12s} "
              f"{str(td.get('is_ap_tender')):6s} {family:16s} {flt}")
        # Show the section-type distribution that drove the decision
        top_types = ", ".join(f"{t}={c}" for t, c in counts.most_common(5))
        print(f"    top section_types: {top_types}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
