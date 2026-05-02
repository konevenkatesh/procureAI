"""
experiments/tender_graph/step3_clauses.py

Step 3 — Two-pass clause template matching against document_sections.

For each section we narrow the 700 templates by section_type, then score
each candidate via difflib.SequenceMatcher on (section heading vs template
title). Matches above MATCH_THRESHOLD become clause_instances rows.

The match_confidence is the SequenceMatcher ratio in [0, 1].
"""
from __future__ import annotations

import time
from collections import Counter, defaultdict
from difflib import SequenceMatcher

from _common import (
    DOC_ID,
    rest_select, rest_insert, rest_delete_doc,
)


MATCH_THRESHOLD = 0.40   # Up from 0.35 default. 0.50 was too strict —
                         # all 6 PBG-Shortfall templates dropped, so the
                         # cascade-violation Q4 returned no results. 0.40
                         # keeps Q4 demonstrable while still cutting the
                         # original 2,880-instance flood by ~4×.


# Map our section_type taxonomy → substring(s) that should appear in
# clause_templates.position_section. Pass-1 narrows the 700 templates
# to those whose position_section contains any of the given substrings.
SECTION_TO_POSITION: dict[str, list[str]] = {
    "NIT":            ["/NIT"],
    "ITB":            ["/ITB"],
    "Datasheet":      ["/Datasheet"],
    "Evaluation":     ["/Evaluation"],
    "Forms":          ["/Forms"],
    "GCC":            ["/GCC"],
    "SCC":            ["/SCC"],
    "Scope":          ["/Scope"],
    "Specifications": ["/Specifications"],
    "BOQ":            ["/BOQ"],
    # "Other" — we don't run pass-1 narrowing; will match against all
    # templates and rely on similarity threshold to filter.
}


def _normalise(text: str) -> str:
    """Lowercase, strip markdown, collapse whitespace, drop part suffix."""
    import re
    t = (text or "").lower()
    t = re.sub(r"\\\(([^)]*)\\\)", r"\1", t)         # \(foo\) → foo
    t = re.sub(r"<a\s+id=\"[^\"]*\">\s*</a>", "", t)  # <a id="..."></a>
    t = re.sub(r"\s*\(part\s+\d+\)\s*$", "", t)      # "(part 2)" tail
    t = re.sub(r"[*_#`|\\]", " ", t)                  # markdown chars
    t = re.sub(r"\s+", " ", t).strip()
    return t


def main() -> int:
    print("=" * 70)
    print(f"STEP 3 — Clause-template matching for {DOC_ID}")
    print(f"   threshold = {MATCH_THRESHOLD}")
    print("=" * 70)

    t_step = time.perf_counter()

    # Idempotent rerun
    print("\nClearing prior clause_instances + relationships...")
    n_rel = rest_delete_doc("clause_relationships", DOC_ID)
    n_inst = rest_delete_doc("clause_instances", DOC_ID)
    print(f"  deleted: {n_rel} relationships, {n_inst} instances")

    # 1. Fetch all 700 templates
    t0 = time.perf_counter()
    templates = rest_select(
        "clause_templates",
        params={"select": "clause_id,title,position_section,mandatory,cross_references,rule_ids"},
    )
    fetch_ms = int((time.perf_counter() - t0) * 1000)
    print(f"\nFetched {len(templates)} clause_templates in {fetch_ms} ms")

    # Pre-bucket templates by their position_section for fast pass-1 lookup
    by_position: dict[str, list[dict]] = defaultdict(list)
    for t in templates:
        by_position[t["position_section"]].append(t)
    print(f"position_section buckets: {dict((k, len(v)) for k, v in by_position.items())}")

    # 2. Fetch this doc's sections
    sections = rest_select(
        "document_sections",
        params={"select": "id,section_type,heading,full_text",
                "doc_id": f"eq.{DOC_ID}",
                "order":  "id.asc"},
    )
    print(f"\nDoc has {len(sections)} sections to score")

    # 3. Two-pass match
    instances: list[dict] = []
    sections_with_zero_matches = 0
    candidates_per_section_total = 0

    for sec in sections:
        sec_id   = sec["id"]
        sec_type = sec["section_type"]
        heading_norm = _normalise(sec["heading"])

        # Pass 1 — narrow by section_type
        substrs = SECTION_TO_POSITION.get(sec_type, [])
        if substrs:
            candidates: list[dict] = []
            for pos_key, lst in by_position.items():
                if any(s in pos_key for s in substrs):
                    candidates.extend(lst)
        else:
            # "Other" → fall back to entire template set
            candidates = templates

        candidates_per_section_total += len(candidates)

        # Pass 2 — heading similarity within candidates
        any_hit = False
        for tpl in candidates:
            title_norm = _normalise(tpl["title"])
            ratio = SequenceMatcher(None, heading_norm, title_norm).ratio()
            if ratio >= MATCH_THRESHOLD:
                any_hit = True
                instances.append({
                    "doc_id":             DOC_ID,
                    "section_id":         sec_id,
                    "clause_template_id": tpl["clause_id"],
                    "clause_title":       tpl["title"],
                    "match_confidence":   round(ratio, 4),
                    "extracted_variables": {},
                    "source_text":        sec["full_text"][:800],
                    "line_start":         None,
                    "line_end":           None,
                })

        if not any_hit:
            sections_with_zero_matches += 1

    avg_candidates = candidates_per_section_total / max(len(sections), 1)
    print(f"Pass-1 average candidate pool per section: {avg_candidates:.1f}  "
          f"(was 700 without narrowing)")

    # 4. Insert in batches of 50 (each instance ≤ 1KB after source_text trim)
    print(f"\nInserting {len(instances)} clause_instances...")
    BATCH = 50
    inserted = 0
    for i in range(0, len(instances), BATCH):
        chunk = instances[i:i + BATCH]
        out = rest_insert("clause_instances", chunk)
        inserted += len(out)
    print(f"  inserted: {inserted}")

    # 5. Reporting
    print("\n" + "=" * 70)
    print("STEP 3 — RESULTS")
    print("=" * 70)
    print(f"Total clause instances:               {inserted}")
    print(f"Distinct templates that matched:      {len({i['clause_template_id'] for i in instances})}")
    match_rate = 100.0 * len({i['clause_template_id'] for i in instances}) / len(templates)
    print(f"Match rate (distinct/700):            {match_rate:.1f}%")
    print(f"Sections with zero matches:           {sections_with_zero_matches}/{len(sections)}")

    # Top-10 highest confidence matches
    print("\nTop 10 highest-confidence matches:")
    instances_sorted = sorted(instances, key=lambda i: -i["match_confidence"])
    for i, m in enumerate(instances_sorted[:10], 1):
        print(f"  {i:2d}. score={m['match_confidence']:.3f}  "
              f"{m['clause_template_id']:38s} → {m['clause_title'][:55]}")

    # Distribution of matches per section_type
    by_st = Counter()
    sec_lookup = {s["id"]: s["section_type"] for s in sections}
    for m in instances:
        by_st[sec_lookup[m["section_id"]]] += 1
    print("\nInstances by section_type:")
    for st, n in by_st.most_common():
        print(f"  {st:20s} {n}")

    elapsed = int((time.perf_counter() - t_step) * 1000)
    print(f"\nStep 3 wall time: {elapsed} ms ({elapsed/1000:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
