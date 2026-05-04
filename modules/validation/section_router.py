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


SECTION_ROUTERS: dict[str, dict[str, list[str]]] = {
    "EMD-Shortfall":      EMD_SECTION_ROUTER,
    "Bid-Validity-Short": BID_VALIDITY_SECTION_ROUTER,
    "Missing-PVC-Clause": PVC_SECTION_ROUTER,
    "Missing-Integrity-Pact": IP_SECTION_ROUTER,
    "Missing-LD-Clause":   LD_SECTION_ROUTER,
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
