"""R7.3 — Persist 72 TechSpecTemplate kg_nodes to Supabase."""
from __future__ import annotations

import sys
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "services" / "m1-drafter"))

from builder.config import settings  # noqa: E402
from app.tech_spec_templates import all_templates  # noqa: E402

REST = settings.supabase_rest_url
H = {
    "apikey": settings.supabase_anon_key,
    "Authorization": f"Bearer {settings.supabase_anon_key}",
    "Content-Type": "application/json",
}

SOURCE_REF = "module1:tech_spec_template_v1"


def _delete_prior() -> int:
    rows = requests.get(
        f"{REST}/rest/v1/kg_nodes",
        params={"select": "node_id", "node_type": "eq.TechSpecTemplate", "source_ref": f"eq.{SOURCE_REF}"},
        headers=H, timeout=30,
    ).json()
    for row in rows:
        requests.delete(f"{REST}/rest/v1/kg_nodes",
                        params={"node_id": f"eq.{row['node_id']}"}, headers=H, timeout=30)
    return len(rows)


def main() -> None:
    templates = all_templates()
    print(f"R7.3 — Persisting {len(templates)} TechSpecTemplate rows")

    n_cleaned = _delete_prior()
    if n_cleaned:
        print(f"  cleanup: removed {n_cleaned} prior rows")

    rows = []
    for t in templates:
        rows.append({
            "doc_id":    f"tech_spec_{t.template_id.replace('/', '_')}",
            "node_type": "TechSpecTemplate",
            "label":     f"TechSpec {t.discipline}/{t.sub_discipline}: {t.item_category} ({t.typical_short_desc})",
            "properties": {
                "template_id":            t.template_id,
                "discipline":             t.discipline,
                "sub_discipline":         t.sub_discipline,
                "item_category":          t.item_category,
                "work_type_label":        t.work_type_label,
                "typical_uom":            t.typical_uom,
                "typical_short_desc":     t.typical_short_desc,
                "sample_short_descs":     t.sample_short_descs,
                "expected_citations":     t.expected_citations,
                "expected_citation_count": len(t.expected_citations),
                "retrieval_query_template": t.retrieval_query_template,
                "llm_prompt_template":    t.llm_prompt_template,
                "validation_rules":       t.validation_rules,
                "llm_model":              t.llm_model,
                "expected_output_tokens": t.expected_output_tokens,
            },
            "source_ref": SOURCE_REF,
        })

    # Batch insert
    inserted = 0
    for start in range(0, len(rows), 20):
        batch = rows[start:start + 20]
        r = requests.post(f"{REST}/rest/v1/kg_nodes", json=batch,
                          headers={**H, "Prefer": "return=representation"}, timeout=60)
        r.raise_for_status()
        inserted += len(r.json())

    print(f"  ✓ Inserted {inserted} TechSpecTemplate rows")


if __name__ == "__main__":
    main()
