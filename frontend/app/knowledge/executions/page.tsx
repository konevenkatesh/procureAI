"use client";

import { useEffect, useState } from "react";
import { Loader2, Activity, TrendingUp } from "lucide-react";
import { KbDetailModal } from "@/components/knowledge/KbDetailModal";

interface TopRule {
  rule_id: string;
  firing_count: number;
  verdict_mix: Record<string, number>;
  tender_count: number;
  latest_at: string;
}

export default function ExecutionsPage() {
  const [rules, setRules] = useState<TopRule[]>([]);
  const [totalFirings, setTotalFirings] = useState(0);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/kb/recent-executions")
      .then(r => r.ok ? r.json() : Promise.reject())
      .then((d) => {
        setRules(d.top_rules || []);
        setTotalFirings(d.total_firings || 0);
      })
      .catch(() => setRules([]))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <div className="rounded-md border border-mist-200 bg-white px-4 py-3">
          <div className="text-[10px] uppercase tracking-wider text-ink-500 font-semibold mb-1">Total firings sampled</div>
          <div className="text-2xl font-bold text-ink-900">{totalFirings.toLocaleString()}</div>
        </div>
        <div className="rounded-md border border-mist-200 bg-white px-4 py-3">
          <div className="text-[10px] uppercase tracking-wider text-ink-500 font-semibold mb-1">Distinct rules fired</div>
          <div className="text-2xl font-bold text-ink-900">{rules.length}</div>
        </div>
        <div className="rounded-md border border-mist-200 bg-white px-4 py-3">
          <div className="text-[10px] uppercase tracking-wider text-ink-500 font-semibold mb-1">Tenders touched (top-50)</div>
          <div className="text-2xl font-bold text-ink-900">
            {rules.reduce((a, r) => Math.max(a, r.tender_count), 0)}
          </div>
        </div>
      </div>

      <p className="text-xs text-ink-500 flex items-center gap-1.5">
        <TrendingUp className="h-3 w-3" /> Most-fired rules across all recent validation runs. Click a row to see the rule definition.
      </p>

      {loading && (
        <div className="flex items-center justify-center py-12 text-ink-500">
          <Loader2 className="h-5 w-5 animate-spin mr-2" /> Loading…
        </div>
      )}

      <div className="rounded-md border border-mist-200 bg-white overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="bg-mist-50">
            <tr className="text-left text-ink-700">
              <th className="px-3 py-2 w-12">#</th>
              <th className="px-3 py-2">Rule ID</th>
              <th className="px-3 py-2 w-20 text-right">Firings</th>
              <th className="px-3 py-2 w-20 text-right">Tenders</th>
              <th className="px-3 py-2">Verdict mix</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-mist-100">
            {rules.length === 0 && !loading && (
              <tr><td colSpan={5} className="px-3 py-8 text-center text-ink-500 italic">No firings yet.</td></tr>
            )}
            {rules.map((r, i) => (
              <tr
                key={r.rule_id}
                className="hover:bg-mist-50/40 cursor-pointer"
                onClick={() => setSelectedId(r.rule_id)}
              >
                <td className="px-3 py-2 text-ink-500 tabular-nums">{i + 1}</td>
                <td className="px-3 py-2"><code className="text-[10px]">{r.rule_id}</code></td>
                <td className="px-3 py-2 text-right font-semibold tabular-nums">{r.firing_count}</td>
                <td className="px-3 py-2 text-right tabular-nums">{r.tender_count}</td>
                <td className="px-3 py-2">
                  <div className="flex gap-1 flex-wrap">
                    {Object.entries(r.verdict_mix).map(([v, n]) => (
                      <span key={v} className="rounded bg-mist-100 px-2 py-0.5 text-[10px] text-ink-700">
                        {v}: {String(n)}
                      </span>
                    ))}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {selectedId && (
        <KbDetailModal
          id={selectedId}
          endpoint="/api/kb/rules"
          onClose={() => setSelectedId(null)}
        />
      )}
    </div>
  );
}
