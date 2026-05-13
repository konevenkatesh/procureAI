"use client";

import { Suspense } from "react";
import KbListView from "@/components/knowledge/KbListView";

export default function RulesPage() {
  return (
    <Suspense fallback={<div className="text-xs text-ink-500">Loading rules…</div>}>
      <Inner />
    </Suspense>
  );
}

function Inner() {
  return (
    <KbListView
      endpoint="/api/kb/rules"
      detailEndpoint="/api/kb/rules"
      filterChips={[
        { label: "WARNING",   value: "WARNING",   param: "severity" },
        { label: "HARD_BLOCK", value: "HARD_BLOCK", param: "severity" },
        { label: "ADVISORY",  value: "ADVISORY",  param: "severity" },
        { label: "GFR",       value: "GFR",       param: "source" },
        { label: "AP-GO",     value: "AP-GO",     param: "source" },
        { label: "CVC",       value: "CVC",       param: "source" },
      ]}
      columns={[
        { key: "rule_id", header: "Rule ID", width: "180px", render: r =>
          <code className="text-[10px] text-ink-700">{r.properties?.rule_id || r.node_id.slice(0, 8)}</code>
        },
        { key: "label", header: "Title", render: r =>
          <span className="text-ink-900 line-clamp-2">{r.label}</span>
        },
        { key: "source", header: "Source", width: "120px", render: r =>
          <span className="text-ink-700 text-[11px]">{r.properties?.source || "—"}</span>
        },
        { key: "severity", header: "Severity", width: "100px", render: r => {
          const s = r.properties?.severity || "—";
          const color = s === "HARD_BLOCK" ? "text-red-700" : s === "WARNING" ? "text-amber-700" : "text-ink-500";
          return <span className={`text-[11px] font-semibold ${color}`}>{s}</span>;
        }},
        { key: "typology", header: "Typology", width: "160px", render: r =>
          <span className="text-ink-700 text-[11px] truncate">{r.properties?.typology_code || "—"}</span>
        },
      ]}
    />
  );
}
