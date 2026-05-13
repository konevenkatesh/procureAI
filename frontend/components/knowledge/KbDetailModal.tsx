"use client";

/**
 * Shared detail modal for any kg_node. Renders the full content + linked entities.
 * Used by all 4 KB tabs (rules / clauses / templates / typologies).
 */

import { useEffect, useState } from "react";
import { X, Loader2 } from "lucide-react";

interface Props {
  id: string;
  endpoint: string;            // base endpoint, e.g. "/api/kb/rules"
  onClose: () => void;
}

export function KbDetailModal({ id, endpoint, onClose }: Props) {
  const [data, setData] = useState<any | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    fetch(`${endpoint}/${encodeURIComponent(id)}`)
      .then(r => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
      .then(setData)
      .catch((e) => setErr(String(e)))
      .finally(() => setLoading(false));
  }, [endpoint, id]);

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  const node = data?.rule || data?.clause || data?.template || data;
  const properties = node?.properties || {};

  return (
    <div
      className="fixed inset-0 z-50 bg-ink-900/60 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-md shadow-xl max-w-3xl w-full max-h-[85vh] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="px-5 py-3 border-b border-mist-200 flex items-center justify-between">
          <div className="min-w-0">
            <div className="text-[10px] uppercase tracking-wider text-ink-500 font-semibold">
              {node?.node_type || "Loading…"}
            </div>
            <div className="text-sm font-bold text-ink-900 truncate">
              {node?.label || id}
            </div>
          </div>
          <button onClick={onClose} className="text-ink-500 hover:text-ink-900 p-1">
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="flex-1 overflow-y-auto px-5 py-4 text-sm">
          {loading && (
            <div className="flex items-center justify-center py-12 text-ink-500">
              <Loader2 className="h-5 w-5 animate-spin mr-2" /> Loading…
            </div>
          )}
          {err && (
            <div className="rounded-md bg-red-50 border border-red-200 px-3 py-2 text-red-700">
              {err}
            </div>
          )}
          {!loading && !err && node && (
            <div className="space-y-4">
              {/* Identifiers */}
              <section>
                <h3 className="text-xs font-bold text-ink-700 mb-1">Identifiers</h3>
                <div className="grid grid-cols-2 gap-2 text-xs">
                  <div><span className="text-ink-500">node_id:</span> <code className="text-[11px]">{node.node_id}</code></div>
                  <div><span className="text-ink-500">node_type:</span> <code className="text-[11px]">{node.node_type}</code></div>
                  {node.source_ref && <div><span className="text-ink-500">source:</span> <code className="text-[11px]">{node.source_ref}</code></div>}
                  {node.doc_id && <div><span className="text-ink-500">doc_id:</span> <code className="text-[11px]">{node.doc_id}</code></div>}
                </div>
              </section>

              {/* Key properties */}
              <section>
                <h3 className="text-xs font-bold text-ink-700 mb-1">Properties</h3>
                <dl className="rounded-md border border-mist-200 bg-mist-50/40 p-3 text-xs space-y-1.5">
                  {Object.entries(properties).slice(0, 40).map(([k, v]) => {
                    const str = typeof v === "string" ? v :
                                Array.isArray(v) ? v.slice(0, 5).join(", ") :
                                JSON.stringify(v);
                    return (
                      <div key={k} className="grid grid-cols-[140px_1fr] gap-2">
                        <dt className="text-ink-500 font-mono">{k}</dt>
                        <dd className="text-ink-900 break-words whitespace-pre-wrap line-clamp-6">
                          {str || <em className="text-ink-400">∅</em>}
                        </dd>
                      </div>
                    );
                  })}
                </dl>
              </section>

              {/* Linked entities */}
              {data?.linked_clauses && data.linked_clauses.length > 0 && (
                <section>
                  <h3 className="text-xs font-bold text-ink-700 mb-1">Linked clauses ({data.linked_clauses.length})</h3>
                  <ul className="text-xs space-y-1">
                    {data.linked_clauses.slice(0, 10).map((c: any) => (
                      <li key={c.to_id} className="rounded border border-mist-200 px-3 py-1.5 bg-white">
                        <code className="text-[11px]">{c.to_id}</code>
                      </li>
                    ))}
                  </ul>
                </section>
              )}
              {data?.linked_rules && data.linked_rules.length > 0 && (
                <section>
                  <h3 className="text-xs font-bold text-ink-700 mb-1">Linked rules ({data.linked_rules.length})</h3>
                  <ul className="text-xs space-y-1">
                    {data.linked_rules.slice(0, 10).map((r: any) => (
                      <li key={r.from_id} className="rounded border border-mist-200 px-3 py-1.5 bg-white">
                        <code className="text-[11px]">{r.from_id}</code>
                      </li>
                    ))}
                  </ul>
                </section>
              )}
              {data?.recent_firings && data.recent_firings.length > 0 && (
                <section>
                  <h3 className="text-xs font-bold text-ink-700 mb-1">Recent firings ({data.recent_firings.length})</h3>
                  <ul className="text-xs space-y-1">
                    {data.recent_firings.slice(0, 10).map((f: any) => (
                      <li key={f.node_id} className="rounded border border-mist-200 px-3 py-1.5 bg-white">
                        <div className="flex justify-between gap-2">
                          <code className="text-[11px] truncate">{f.label}</code>
                          <span className="text-[10px] text-ink-500 shrink-0">
                            {(f.properties?.verdict || "—")}
                          </span>
                        </div>
                      </li>
                    ))}
                  </ul>
                </section>
              )}
              {data?.samples && data.samples.length > 0 && (
                <section>
                  <h3 className="text-xs font-bold text-ink-700 mb-1">Sample items ({data.samples.length})</h3>
                  <ul className="text-xs space-y-1">
                    {data.samples.map((s: any) => (
                      <li key={s.node_id} className="rounded border border-mist-200 px-3 py-1.5 bg-white">
                        <code className="text-[11px] truncate block">{s.short_desc || s.label}</code>
                      </li>
                    ))}
                  </ul>
                </section>
              )}
              {data?.rules && data.rules.length > 0 && (
                <section>
                  <h3 className="text-xs font-bold text-ink-700 mb-1">Rules in this typology ({data.rule_count})</h3>
                  <ul className="text-xs space-y-1 max-h-60 overflow-y-auto">
                    {data.rules.slice(0, 30).map((r: any) => (
                      <li key={r.node_id} className="rounded border border-mist-200 px-3 py-1.5 bg-white">
                        <div className="font-mono text-[10px] text-ink-500">{r.properties?.rule_id || r.node_id}</div>
                        <div className="truncate">{r.label}</div>
                      </li>
                    ))}
                  </ul>
                  {data.verdict_mix && Object.keys(data.verdict_mix).length > 0 && (
                    <div className="mt-2 text-xs text-ink-700">
                      <span className="font-semibold">Verdict mix:</span>{" "}
                      {Object.entries(data.verdict_mix).map(([v, n]) => (
                        <span key={v} className="ml-2"><code className="text-[10px]">{v}</code>: {String(n)}</span>
                      ))}
                    </div>
                  )}
                </section>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
