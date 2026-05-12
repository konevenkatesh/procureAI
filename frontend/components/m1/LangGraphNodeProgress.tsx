"use client";

import { useState } from "react";
import { cn } from "@/lib/utils";
import type { NodeStatus } from "@/hooks/useSSEDraftStream";
import {
  CheckCircle2, Loader2, Circle, AlertCircle, ChevronDown, ChevronRight,
} from "lucide-react";

const NODE_LABELS: Record<string, string> = {
  analyze_inputs:         "Analyze inputs",
  classify_tender_type:   "Classify tender type",
  retrieve_templates:     "Retrieve templates",
  retrieve_clauses:       "Retrieve clauses (RAG)",
  draft_NIT:              "Draft NIT identifier",
  draft_ITB:              "Draft Instructions to Bidders",
  draft_eligibility:      "Draft eligibility criteria",
  draft_BoQ_skeleton:     "Draft BoQ skeleton",
  draft_legal_terms:      "Draft legal terms",
  draft_evaluation_form:  "Draft evaluation form",
  assemble_document:      "Assemble document",
  render_DOCX:            "Finalise for review",
};

export function LangGraphNodeProgress({
  nodes,
  totalElapsedMs,
}: {
  nodes: NodeStatus[];
  totalElapsedMs?: number;
}) {
  const done = nodes.filter((n) => n.status === "done").length;
  const total = nodes.length;
  const pct = Math.round((done / total) * 100);

  return (
    <div className="rounded-lg border border-mist-200 bg-white shadow-card sticky top-6 max-h-[calc(100vh-3rem)] overflow-y-auto">
      <div className="px-4 py-3 border-b border-mist-200 bg-mist-50/40">
        <div className="text-xs font-bold text-ink-900">LangGraph Workflow</div>
        <div className="mt-1.5 text-xs text-ink-500 flex items-baseline justify-between">
          <span>
            <strong className="text-ink-900 tabular-nums">{done}/{total}</strong> nodes
          </span>
          {totalElapsedMs && (
            <span className="tabular-nums">{(totalElapsedMs / 1000).toFixed(1)}s total</span>
          )}
        </div>
        <div className="mt-2 h-1.5 rounded-full bg-mist-100 overflow-hidden">
          <div
            className="h-full bg-saffron-500 transition-all duration-200"
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>

      <ol className="p-2 space-y-0.5">
        {nodes.map((n, i) => (
          <NodeRow key={n.node} node={n} isLast={i === nodes.length - 1} />
        ))}
      </ol>
    </div>
  );
}

function NodeRow({ node, isLast }: { node: NodeStatus; isLast: boolean }) {
  const [open, setOpen] = useState(false);
  const Icon =
    node.status === "done" ? CheckCircle2 :
    node.status === "running" ? Loader2 :
    node.status === "error" ? AlertCircle :
    Circle;
  const iconClass =
    node.status === "done" ? "text-leaf-700" :
    node.status === "running" ? "text-saffron-700 animate-spin" :
    node.status === "error" ? "text-red-700" :
    "text-mist-200";

  return (
    <li className="relative">
      {!isLast && (
        <div className="absolute left-[15px] top-7 bottom-0 w-px bg-mist-200" />
      )}
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-start gap-2 px-2 py-1.5 rounded hover:bg-mist-50 text-left transition-colors"
      >
        <Icon className={cn("h-4 w-4 mt-0.5 shrink-0 relative z-10 bg-white rounded-full", iconClass)} />
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline justify-between gap-2">
            <span className={cn(
              "text-xs font-semibold truncate",
              node.status === "done" ? "text-ink-900" :
              node.status === "running" ? "text-saffron-700" :
              "text-ink-500",
            )}>
              {node.index}. {NODE_LABELS[node.node] || node.node}
            </span>
            {node.elapsed_ms != null && (
              <span className="text-[10px] text-ink-500 tabular-nums shrink-0">
                {node.elapsed_ms < 1000 ? `${node.elapsed_ms}ms` : `${(node.elapsed_ms / 1000).toFixed(1)}s`}
              </span>
            )}
          </div>
          {node.error_message && (
            <div className="text-[10px] text-red-700 mt-0.5">{node.error_message}</div>
          )}
        </div>
        {node.citations && node.citations.sources?.length > 0 && (
          open ? <ChevronDown className="h-3 w-3 text-ink-500 mt-1" /> : <ChevronRight className="h-3 w-3 text-ink-500 mt-1" />
        )}
      </button>
      {open && node.citations && node.citations.sources?.length > 0 && (
        <div className="ml-7 mb-1 px-2 py-2 text-[10px] text-ink-500 border-l-2 border-mist-200">
          <div className="font-semibold text-ink-700 mb-1">Citations ({node.citations.sources.length})</div>
          {node.citations.sources.map((s: any, i: number) => (
            <div key={i} className="mb-1 last:mb-0">
              <code className="text-[10px] text-ink-700">{s.node_id}</code>
              <div className="text-ink-500 italic">{s.quote_excerpt}</div>
            </div>
          ))}
        </div>
      )}
    </li>
  );
}
