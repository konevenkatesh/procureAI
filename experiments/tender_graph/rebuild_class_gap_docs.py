"""
experiments/tender_graph/rebuild_class_gap_docs.py

One-off rebuild for Vizag + Kakinada to close the kg_builder section
gaps that blocked Eligibility-Class-Mismatch (typology 13) findings:

  - Vizag NIT  (1_Volume_I_NIT_&_Bidding_Process.md):
      Preamble L3-12 → next section L415-565. Lines 13-414
      uncovered. L178 ("appropriate eligible class") falls in
      this gap.
  - Kakinada SBD (SBDPKG11Kakinadafinalrev.md):
      INSTRUCTIONS TO TENDERERS (part 1) ends L58 → next section
      L313-411. Lines 59-312 uncovered. L149 ("Class I Civil &
      above") falls in this gap.

L32 snapshot-and-restore preserves ValidationFinding nodes +
VIOLATES_RULE edges across the rebuild. The 36 prior findings
should survive; the section-graph itself is what changes.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
HERE = Path(__file__).resolve().parent     # experiments/tender_graph
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))              # so `kg_builder` and `_common` resolve

from kg_builder import build_kg


PROCESSED_MD = REPO / "source_documents" / "e_procurement" / "processed_md"


# Vizag — 5-volume EPC tender (matches _common.py SOURCE_FILES).
VIZAG_DOC_ID  = "vizag_ugss_exp_001"
VIZAG_NAME    = "Vizag UGSS Pkg-2"
VIZAG_SOURCES = [
    PROCESSED_MD / "1_Volume_I_NIT_&_Bidding_Process.md",
    PROCESSED_MD / "2 VOLUME II Scope of work.md",
    PROCESSED_MD / "3 Volume_III _GCC,_SCC.md",
    PROCESSED_MD / "3.3A_Schedules.md",
    PROCESSED_MD / "4 VOLUME IV Bill of Quantiites.md",
]


# Kakinada — single SBD doc.
KAKINADA_DOC_ID  = "kakinada_pkg11_exp_001"
KAKINADA_NAME    = "Kakinada Package-11 SBD"
KAKINADA_SOURCES = [
    PROCESSED_MD / "SBDPKG11Kakinadafinalrev.md",
]


JOBS = [
    (VIZAG_DOC_ID,    VIZAG_NAME,    VIZAG_SOURCES),
    (KAKINADA_DOC_ID, KAKINADA_NAME, KAKINADA_SOURCES),
]


def main() -> int:
    for doc_id, name, sources in JOBS:
        print("=" * 76)
        print(f"  Rebuilding KG: {doc_id}")
        print(f"                 {name}")
        print(f"  source files : {len(sources)}")
        for s in sources:
            ok = "✓" if s.exists() else "✗ MISSING"
            print(f"                 {ok} {s.name}")
        print("=" * 76)
        t0 = time.perf_counter()
        summary = build_kg(
            doc_id=doc_id,
            document=sources,
            document_name=name,
            clear_existing=True,
        )
        dt = time.perf_counter() - t0
        print()
        print(f"  → built in {dt:.1f}s")
        print(summary)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
