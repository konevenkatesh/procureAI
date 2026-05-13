"use client";

import KbListView from "@/components/knowledge/KbListView";

export default function TemplatesPage() {
  return (
    <KbListView
      endpoint="/api/kb/templates"
      detailEndpoint="/api/kb/templates"
      filterChips={[
        { label: "TechSpec",   value: "tech", param: "kind" },
        { label: "SBD Section", value: "sbd",  param: "kind" },
        { label: "MEP",         value: "MEP",  param: "discipline" },
        { label: "Civil",       value: "Civil", param: "discipline" },
      ]}
      columns={[
        { key: "id", header: "Template", width: "240px", render: r =>
          <code className="text-[10px] text-ink-700">
            {r.properties?.template_id || r.properties?.section_id || r.node_id.slice(0, 12)}
          </code>
        },
        { key: "label", header: "Title", render: r =>
          <span className="text-ink-900 line-clamp-2">{r.label}</span>
        },
        { key: "kind", header: "Kind", width: "100px", render: r =>
          <span className="rounded bg-mist-100 px-2 py-0.5 text-[10px] uppercase tracking-wide">
            {(r as any).kind || (r.node_type === "TechSpecTemplate" ? "tech" : "sbd")}
          </span>
        },
        { key: "discipline", header: "Discipline", width: "120px", render: r =>
          <span className="text-ink-700 text-[11px]">
            {r.properties?.discipline || r.properties?.sub_discipline || "—"}
          </span>
        },
      ]}
    />
  );
}
