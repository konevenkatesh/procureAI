"use client";

import { Suspense } from "react";
import KbListView from "@/components/knowledge/KbListView";

export default function ClausesPage() {
  return (
    <Suspense fallback={<div className="text-xs text-ink-500">Loading clauses…</div>}>
      <Inner />
    </Suspense>
  );
}

function Inner() {
  return (
    <KbListView
      endpoint="/api/kb/clauses"
      detailEndpoint="/api/kb/clauses"
      columns={[
        { key: "id", header: "Clause", width: "200px", render: r =>
          <code className="text-[10px] text-ink-700">{r.node_id.slice(0, 12)}</code>
        },
        { key: "label", header: "Text", render: r =>
          <span className="text-ink-900 line-clamp-2">{r.label}</span>
        },
        { key: "type", header: "Type", width: "120px", render: r =>
          <span className="text-ink-700 text-[11px]">{r.properties?.clause_type || r.properties?.section_type || "—"}</span>
        },
        { key: "source", header: "Source", width: "140px", render: r =>
          <span className="text-ink-700 text-[11px] truncate">{r.source_ref || "—"}</span>
        },
      ]}
    />
  );
}
