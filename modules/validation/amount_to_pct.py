"""
modules/validation/amount_to_pct.py

Shared helper for typology checks where a percentage-based rule
(e.g. "EMD must be 2-5% of estimated contract value") meets a
document that states the value as a fixed INR amount instead
(e.g. NREDCAP PPP RFPs: "Bid Security INR 2.57 crore"). Computes
the implied percentage by dividing the amount by the contract
value stored on the TenderDocument kg_node.

The same code path was duplicated inline in scripts/tier1_pbg_check.py
(FIX C, L20). EMD on PPP docs needs the identical conversion. Lifted
here so any future typology with a percentage-based rule (Integrity
Pact threshold, Judicial Preview value cutoff, retention-money
percentages, etc.) can call it without re-implementing.

Public API — exactly one function:

    compute_implied_pct(doc_id, amount_cr, source) → dict

Returns:
    {
      "implied_pct":           float | None,    # amount_cr / value_cr * 100, rounded 4dp
      "amount_cr":             float,           # echo (for the caller's audit log)
      "contract_value_cr":     float | None,
      "contract_value_source": str,             # see below
      "needs_contract_value":  bool,            # True iff caller should emit
                                                # status=PENDING_VALUE and skip
                                                # the VIOLATES_RULE edge
      "source":                str,             # echo of the input ('emd' | 'pbg')
    }

`contract_value_source` lookup order (mirrors the original FIX C code
so existing PBG findings keep their attribution):

    1. `estimated_value_cr` (LLM-extracted via tender_facts_extractor)
       → source = "llm_extracted"
    2. `estimated_value_cr_classified` AND `estimated_value_reliable=True`
       → source = "regex_classifier_reliable"
    3. `estimated_value_cr_classified` non-zero, reliable=False
       → source = "regex_classifier_unreliable"
    4. nothing usable
       → source = "missing"  (or "no_tender_document" if the node
                              doesn't exist; caller treats both as
                              needs_contract_value=True)

The `source` parameter ("emd" | "pbg") doesn't change the math today
— it's recorded in the return dict so a downstream auditor can trace
which typology triggered the conversion. Could in future drive
typology-specific lookups (e.g. EMD might prefer a different field
than PBG if the rules diverge).
"""
from __future__ import annotations

from pathlib import Path
import sys

import requests


# Importable from any caller; avoid hard-coding sys.path order
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from builder.config import settings    # noqa: E402


REST = settings.supabase_rest_url
H = {"apikey":        settings.supabase_anon_key,
     "Authorization": f"Bearer {settings.supabase_anon_key}"}


def _fetch_contract_value_cr(doc_id: str) -> tuple[float | None, str]:
    """Same lookup order as the original FIX C code in tier1_pbg_check.py.

    Returns (value_or_None, source_label). Caller decides whether to
    use a `regex_classifier_unreliable` value or treat it as missing.
    """
    rows = requests.get(f"{REST}/rest/v1/kg_nodes",
                        params={"select":"properties","doc_id":f"eq.{doc_id}",
                                "node_type":"eq.TenderDocument"},
                        headers=H, timeout=30).json()
    if not rows:
        return None, "no_tender_document"
    p = rows[0].get("properties") or {}

    # 1) LLM-extracted (preferred)
    v = p.get("estimated_value_cr")
    if v is not None:
        try:
            return float(v), "llm_extracted"
        except (TypeError, ValueError):
            pass

    # 2/3) regex-classifier value, with reliability flag
    v = p.get("estimated_value_cr_classified")
    reliable = bool(p.get("estimated_value_reliable"))
    if v is not None:
        try:
            vf = float(v)
            if vf > 0:
                return vf, ("regex_classifier_reliable"
                            if reliable else "regex_classifier_unreliable")
        except (TypeError, ValueError):
            pass

    return None, "missing"


def compute_implied_pct(
    doc_id: str,
    amount_cr: float,
    source: str,
) -> dict:
    """Convert a fixed INR amount (in crores) into an implied
    percentage of the doc's contract value.

    Args:
        doc_id:   the TenderDocument we're checking
        amount_cr: the fixed amount stated in the doc, normalised to crores
        source:   "emd" | "pbg" (typology that triggered the conversion)

    Returns:
        dict with keys:
          implied_pct, amount_cr, contract_value_cr, contract_value_source,
          needs_contract_value, source

        - implied_pct is None iff contract_value_cr is None or zero;
          in that case needs_contract_value=True and the caller should
          emit a finding with status='PENDING_VALUE' and NO
          VIOLATES_RULE edge until a reliable contract value lands.

        - implied_pct is rounded to 4 decimal places to match the
          original FIX C / L20 audit format.
    """
    if amount_cr is None or amount_cr <= 0:
        return {
            "implied_pct":           None,
            "amount_cr":             amount_cr,
            "contract_value_cr":     None,
            "contract_value_source": "no_amount",
            "needs_contract_value":  False,
            "source":                source,
        }

    cv, cv_source = _fetch_contract_value_cr(doc_id)
    if cv is None or cv <= 0:
        return {
            "implied_pct":           None,
            "amount_cr":             float(amount_cr),
            "contract_value_cr":     None,
            "contract_value_source": cv_source,    # "missing" or "no_tender_document"
            "needs_contract_value":  True,
            "source":                source,
        }

    implied_pct = round((float(amount_cr) / float(cv)) * 100, 4)
    return {
        "implied_pct":           implied_pct,
        "amount_cr":             float(amount_cr),
        "contract_value_cr":     float(cv),
        "contract_value_source": cv_source,
        "needs_contract_value":  False,
        "source":                source,
    }
