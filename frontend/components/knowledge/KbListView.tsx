"use client";

/**
 * Shared list-view component for Knowledge Layer tabs.
 *
 * Hooks into /api/kb/{rules|clauses|templates} via a generic endpoint param.
 * Owns: search debouncing, pagination, row-click → detail modal.
 */

import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { ChevronLeft, ChevronRight, Search, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { KbDetailModal } from "./KbDetailModal";

export interface KbRow {
  node_id: string;
  node_type: string;
  label: string;
  properties: Record<string, any>;
  kind?: string;
  source_ref?: string | null;
  doc_id?: string | null;
  created_at?: string;
}

interface Column<T = KbRow> {
  key: string;
  header: string;
  width?: string;
  render: (row: T) => React.ReactNode;
}

interface Props {
  endpoint: string;                     // e.g. "/api/kb/rules"
  detailEndpoint: string;               // e.g. "/api/kb/rules"  (we append /:id)
  columns: Column[];
  filterChips?: { label: string; value: string; param: string }[];
  emptyMessage?: string;
}

export default function KbListView({
  endpoint, detailEndpoint, columns, filterChips, emptyMessage = "No rows match your filters.",
}: Props) {
  const [rows, setRows] = useState<KbRow[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [pageSize] = useState(25);
  const [search, setSearch] = useState("");
  const [searchDebounced, setSearchDebounced] = useState("");
  const [activeChip, setActiveChip] = useState<{ param: string; value: string } | null>(null);
  const [loading, setLoading] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const sp = useSearchParams();

  // R10.4 — deep-link from BOT chat citations: /knowledge/{tab}?detail=ID
  // auto-opens the detail modal so citations work as expected.
  useEffect(() => {
    const detail = sp?.get("detail");
    if (detail) setSelectedId(detail);
  }, [sp]);

  // Debounce search
  useEffect(() => {
    const t = setTimeout(() => setSearchDebounced(search), 300);
    return () => clearTimeout(t);
  }, [search]);

  const fetchRows = useCallback(async () => {
    setLoading(true);
    const sp = new URLSearchParams({ page: String(page), pageSize: String(pageSize) });
    if (searchDebounced) sp.set("search", searchDebounced);
    if (activeChip) sp.set(activeChip.param, activeChip.value);
    try {
      const res = await fetch(`${endpoint}?${sp}`);
      if (!res.ok) {
        setRows([]); setTotal(0);
        return;
      }
      const data = await res.json();
      setRows(data.rows || []);
      setTotal(data.total || 0);
    } finally {
      setLoading(false);
    }
  }, [endpoint, page, pageSize, searchDebounced, activeChip]);

  useEffect(() => { fetchRows(); }, [fetchRows]);

  const maxPage = Math.max(0, Math.ceil(total / pageSize) - 1);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[240px] max-w-md">
          <Search className="absolute left-3 top-2.5 h-4 w-4 text-ink-400 pointer-events-none" />
          <input
            type="text"
            placeholder="Search…"
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(0); }}
            className="w-full rounded-md border border-mist-200 bg-white pl-9 pr-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-saffron-100 focus:border-saffron-500"
          />
        </div>
        {filterChips && (
          <div className="flex gap-1.5 flex-wrap">
            {filterChips.map(chip => {
              const active = activeChip?.param === chip.param && activeChip?.value === chip.value;
              return (
                <button
                  key={`${chip.param}=${chip.value}`}
                  onClick={() => {
                    setActiveChip(active ? null : { param: chip.param, value: chip.value });
                    setPage(0);
                  }}
                  className={cn(
                    "rounded-full border px-3 py-1 text-xs transition-colors",
                    active
                      ? "bg-ink-900 text-white border-ink-900"
                      : "bg-white text-ink-700 border-mist-200 hover:bg-mist-50"
                  )}
                >
                  {chip.label}
                </button>
              );
            })}
          </div>
        )}
        <div className="text-xs text-ink-500 ml-auto">
          {loading ? <Loader2 className="inline h-3 w-3 animate-spin" /> :
            <>Total: <span className="font-semibold text-ink-700">{total.toLocaleString()}</span></>
          }
        </div>
      </div>

      <div className="rounded-md border border-mist-200 bg-white overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="bg-mist-50">
            <tr className="text-left text-ink-700">
              {columns.map(c => (
                <th key={c.key} className="px-3 py-2" style={c.width ? { width: c.width } : undefined}>
                  {c.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-mist-100">
            {rows.length === 0 && !loading && (
              <tr><td colSpan={columns.length} className="px-3 py-8 text-center text-ink-500 italic">{emptyMessage}</td></tr>
            )}
            {rows.map(row => (
              <tr
                key={row.node_id}
                className="hover:bg-mist-50/40 cursor-pointer"
                onClick={() => setSelectedId(row.node_id)}
              >
                {columns.map(c => (
                  <td key={c.key} className="px-3 py-2 align-top">{c.render(row)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between text-xs text-ink-500">
        <div>
          Page {page + 1} of {maxPage + 1} ({(page * pageSize) + 1}–{Math.min((page + 1) * pageSize, total)} of {total})
        </div>
        <div className="flex gap-1">
          <button
            onClick={() => setPage(Math.max(0, page - 1))}
            disabled={page === 0 || loading}
            className="rounded-md border border-mist-200 px-3 py-1 disabled:opacity-50 hover:bg-mist-50"
          >
            <ChevronLeft className="h-3.5 w-3.5" />
          </button>
          <button
            onClick={() => setPage(Math.min(maxPage, page + 1))}
            disabled={page >= maxPage || loading}
            className="rounded-md border border-mist-200 px-3 py-1 disabled:opacity-50 hover:bg-mist-50"
          >
            <ChevronRight className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      {selectedId && (
        <KbDetailModal
          id={selectedId}
          endpoint={detailEndpoint}
          onClose={() => setSelectedId(null)}
        />
      )}
    </div>
  );
}
