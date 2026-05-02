"""
experiments/tender_graph/step4_relationships.py

Step 4 — Wire cross-reference + rule relationships between clause_instances.

For each clause_instance in our doc:
    a. Fetch its clause_template's cross_references[].
       For each cross_ref clause_id that has at least one instance in
       our document, emit a clause_relationships row with type
       "cross_reference".
    b. Fetch its clause_template's rule_ids[].
       Look up each rule's typology_code. Run our existing regex
       validator on the document text → set of violated typologies.
       If the rule's typology is violated → "violatesRule"
       else                                → "satisfiesRule"

We do NOT create cross-reference relationships to non-existent instances,
so this surfaces only intra-document references that ARE realised in
the actual tender — exactly what we want for the graph.
"""
from __future__ import annotations

import time
from collections import Counter, defaultdict
from pathlib import Path

from _common import (
    DOC_ID, DOC_NAME, REPO, SOURCE_FILES,
    rest_select, rest_insert, rest_delete_doc,
)


def _fetch_violated_typologies() -> set[str]:
    """Run the regex validator on Vizag UGSS Pkg-2 (Vol I + Vol III) and
    return the set of typology_codes whose findings showed up.

    Note: the source set here is the same 2 files that step2 indexed; that
    means rules requiring data from Vol II / IV won't fire — caller should
    treat the result as 'satisfied based on what's visible' for missing
    typologies, not a global PASS."""
    from modules.validator.rule_verification_engine import RuleVerificationEngine

    text_blocks = []
    for f in SOURCE_FILES:
        text_blocks.append(f"\n\n[FILE: {f.name}]\n\n" + f.read_text(encoding="utf-8"))
    full_text = "\n\n".join(text_blocks)

    eng = RuleVerificationEngine()
    report = eng.verify(full_text, document_name=DOC_NAME)

    violated: set[str] = set()
    for f in (report.hard_blocks + report.warnings + report.advisories):
        if f.typology_code:
            violated.add(f.typology_code)
    return violated


def main() -> int:
    print("=" * 70)
    print(f"STEP 4 — Wire relationships for {DOC_ID}")
    print("=" * 70)

    t_step = time.perf_counter()

    print("\nClearing prior clause_relationships...")
    n_rel = rest_delete_doc("clause_relationships", DOC_ID)
    print(f"  deleted: {n_rel}")

    # 1. Fetch all clause_instances for this doc
    instances = rest_select(
        "clause_instances",
        params={"select": "id,clause_template_id,section_id",
                "doc_id": f"eq.{DOC_ID}",
                "order":  "id.asc"},
    )
    print(f"\nclause_instances in doc: {len(instances)}")

    # Index by template_id → list of instance_ids (a template may have
    # multiple instances if multiple sections matched it).
    template_to_instances: dict[str, list[int]] = defaultdict(list)
    for inst in instances:
        template_to_instances[inst["clause_template_id"]].append(inst["id"])
    distinct_templates = list(template_to_instances.keys())
    print(f"distinct templates realised: {len(distinct_templates)}")

    # 2. Fetch ALL templates we touch (their xrefs + rule_ids)
    # Supabase REST `in.()` filter — chunked because URL length is bounded.
    templates_data: dict[str, dict] = {}
    CHUNK = 60
    for i in range(0, len(distinct_templates), CHUNK):
        ids = distinct_templates[i:i + CHUNK]
        ids_quoted = ",".join(f'"{x}"' for x in ids)
        rows = rest_select(
            "clause_templates",
            params={"select": "clause_id,cross_references,rule_ids",
                    "clause_id": f"in.({ids_quoted})"},
        )
        for r in rows:
            templates_data[r["clause_id"]] = r
    print(f"fetched template metadata for {len(templates_data)} templates")

    # 3. Fetch rule → typology lookup for every rule_id we care about
    all_rule_ids: set[str] = set()
    for t in templates_data.values():
        for rid in (t.get("rule_ids") or []):
            all_rule_ids.add(rid)
    print(f"distinct rule_ids referenced by templates: {len(all_rule_ids)}")

    rule_typology: dict[str, str] = {}
    rid_list = sorted(all_rule_ids)
    for i in range(0, len(rid_list), CHUNK):
        ids = rid_list[i:i + CHUNK]
        ids_quoted = ",".join(f'"{x}"' for x in ids)
        rows = rest_select(
            "rules",
            params={"select": "rule_id,typology_code",
                    "rule_id": f"in.({ids_quoted})"},
        )
        for r in rows:
            rule_typology[r["rule_id"]] = r["typology_code"]
    print(f"resolved typology for {len(rule_typology)}/{len(all_rule_ids)} rules")

    # 4. Run the regex validator → violated typologies
    print("\nRunning RuleVerificationEngine on doc text...")
    t0 = time.perf_counter()
    violated_typologies = _fetch_violated_typologies()
    val_ms = int((time.perf_counter() - t0) * 1000)
    print(f"  validator wall: {val_ms} ms")
    print(f"  violated typologies ({len(violated_typologies)}): {sorted(violated_typologies)}")

    # 5. Build relationship rows
    rel_rows: list[dict] = []

    for inst in instances:
        from_id  = inst["id"]
        tpl_id   = inst["clause_template_id"]
        tpl      = templates_data.get(tpl_id)
        if not tpl:
            continue

        # 5a. cross_reference relationships
        for xref_clause_id in (tpl.get("cross_references") or []):
            for to_id in template_to_instances.get(xref_clause_id, []):
                if to_id == from_id:
                    continue
                rel_rows.append({
                    "doc_id":           DOC_ID,
                    "from_instance_id": from_id,
                    "to_instance_id":   to_id,
                    "relationship_type": "cross_reference",
                })

        # 5b. violatesRule / satisfiesRule (self-loop reflecting validator state)
        # We model "rule satisfaction" as a relationship from the instance to ITSELF
        # tagged with the rule_id, because there is no "rule instance" node — the
        # rule is metadata. Using a self-loop keeps the schema homogeneous.
        for rid in (tpl.get("rule_ids") or []):
            typology = rule_typology.get(rid)
            if not typology:
                continue
            rel_type = "violatesRule" if typology in violated_typologies else "satisfiesRule"
            rel_rows.append({
                "doc_id":           DOC_ID,
                "from_instance_id": from_id,
                "to_instance_id":   from_id,
                "relationship_type": f"{rel_type}:{rid}",
            })

    # 6. Insert in batches
    print(f"\nInserting {len(rel_rows)} relationships...")
    BATCH = 100
    inserted = 0
    for i in range(0, len(rel_rows), BATCH):
        chunk = rel_rows[i:i + BATCH]
        out = rest_insert("clause_relationships", chunk)
        inserted += len(out)
    print(f"  inserted: {inserted}")

    # 7. Reporting
    print("\n" + "=" * 70)
    print("STEP 4 — RESULTS")
    print("=" * 70)
    print(f"Total relationships:        {inserted}")

    # Type breakdown — collapse `violatesRule:RID` family into one bucket
    type_buckets = Counter()
    for r in rel_rows:
        rt = r["relationship_type"]
        if   rt.startswith("violatesRule:"):  type_buckets["violatesRule"] += 1
        elif rt.startswith("satisfiesRule:"): type_buckets["satisfiesRule"] += 1
        else:                                  type_buckets[rt] += 1
    print("\nRelationship type breakdown:")
    for rt, n in type_buckets.most_common():
        print(f"  {rt:18s} {n}")

    # 5 example xrefs in plain English
    print("\n5 example cross-reference relationships:")
    inst_to_template = {inst["id"]: inst["clause_template_id"] for inst in instances}
    xrefs = [r for r in rel_rows if r["relationship_type"] == "cross_reference"]
    seen_pairs = set()
    examples = 0
    for r in xrefs:
        ft = inst_to_template[r["from_instance_id"]]
        tt = inst_to_template[r["to_instance_id"]]
        key = (ft, tt)
        if key in seen_pairs or ft == tt:
            continue
        seen_pairs.add(key)
        print(f"  {ft} cross_references {tt}")
        examples += 1
        if examples >= 5:
            break

    elapsed = int((time.perf_counter() - t_step) * 1000)
    print(f"\nStep 4 wall time: {elapsed} ms ({elapsed/1000:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
