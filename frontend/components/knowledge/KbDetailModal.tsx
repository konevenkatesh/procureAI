"use client";

/**
 * Shared detail modal for any kg_node.
 *
 * Renders specialised, descriptive layouts per node_type:
 *   - RuleNode        → rule statement card with source/layer/severity badges,
 *                       verification method block, defeats chain
 *   - Section/Clause  → clause heading + body + source-file provenance
 *   - SBDSection      → section heading + content_md preview
 *   - TechSpecTemplate → discipline + standards + sample short descriptions
 *   - generic fallback (TenderDraft etc.) → properties dict
 *
 * Plus shared sections: linked entities, recent firings, sample BoQ items.
 */

import { useEffect, useState } from "react";
import { X, Loader2, FileText, ShieldAlert, ShieldCheck, Layers, GitBranch, Activity, BookOpen } from "lucide-react";

interface Props {
  id: string;
  endpoint: string;
  onClose: () => void;
}


// ─── Severity / layer badges ──────────────────────────────────────────


function SeverityBadge({ severity }: { severity?: string }) {
  if (!severity) return null;
  const cls = severity === "HARD_BLOCK"
    ? "bg-red-100 text-red-800 border-red-200"
    : severity === "WARNING"
    ? "bg-amber-100 text-amber-900 border-amber-200"
    : severity === "ADVISORY"
    ? "bg-blue-100 text-blue-800 border-blue-200"
    : "bg-mist-100 text-ink-700 border-mist-200";
  return (
    <span className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-wider ${cls}`}>
      {severity === "HARD_BLOCK" ? <ShieldAlert className="h-3 w-3" /> : <ShieldCheck className="h-3 w-3" />}
      {severity}
    </span>
  );
}

function LayerBadge({ layer }: { layer?: string }) {
  if (!layer) return null;
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-saffron-100 text-saffron-900 border border-saffron-200 px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-wider">
      <Layers className="h-3 w-3" /> {layer}
    </span>
  );
}


// ─── Rule view ────────────────────────────────────────────────────────


function RuleView({ rule, linked_clauses, recent_firings }: any) {
  const p = rule?.properties || {};
  const ruleId = p.rule_id || rule?.node_id?.slice(0, 8);
  const statement = rule?.label || "—";

  return (
    <div className="space-y-5">
      <section>
        <div className="flex flex-wrap items-center gap-2 mb-3">
          <code className="text-xs font-bold text-ink-900 bg-mist-100 px-2 py-1 rounded">{ruleId}</code>
          <SeverityBadge severity={p.severity} />
          <LayerBadge layer={p.layer} />
          {p.typology_code && (
            <span className="inline-flex items-center gap-1 rounded-full bg-leaf-100 text-leaf-900 border border-leaf-200 px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-wider">
              {p.typology_code}
            </span>
          )}
        </div>
        <h3 className="text-[10px] uppercase tracking-wider text-ink-500 font-semibold mb-1.5">Rule statement</h3>
        <p className="text-sm text-ink-900 leading-relaxed whitespace-pre-wrap">
          {statement.includes(":") ? statement.substring(statement.indexOf(":") + 1).trim() : statement}
        </p>
      </section>

      {p.verification_method && (
        <section>
          <h3 className="text-[10px] uppercase tracking-wider text-ink-500 font-semibold mb-1.5 flex items-center gap-1">
            <ShieldCheck className="h-3 w-3" /> Verification method
          </h3>
          <div className="text-sm text-ink-900 leading-relaxed bg-mist-50/60 border border-mist-200 rounded-md px-4 py-3 whitespace-pre-wrap">
            {p.verification_method}
          </div>
        </section>
      )}

      {(p.pattern_type || p.rule_type) && (
        <section>
          <h3 className="text-[10px] uppercase tracking-wider text-ink-500 font-semibold mb-1.5">Classification</h3>
          <dl className="grid grid-cols-1 md:grid-cols-2 gap-2 text-xs">
            {p.pattern_type && (
              <div><dt className="text-ink-500 inline">Pattern: </dt><dd className="inline font-mono text-ink-900">{p.pattern_type}</dd></div>
            )}
            {p.rule_type && (
              <div><dt className="text-ink-500 inline">Rule type: </dt><dd className="inline font-mono text-ink-900">{p.rule_type}</dd></div>
            )}
          </dl>
        </section>
      )}

      {Array.isArray(p.defeats) && p.defeats.length > 0 && (
        <section>
          <h3 className="text-[10px] uppercase tracking-wider text-ink-500 font-semibold mb-1.5 flex items-center gap-1">
            <GitBranch className="h-3 w-3" /> Defeats (supersedes)
          </h3>
          <div className="flex flex-wrap gap-1.5">
            {p.defeats.map((d: string) => (
              <code key={d} className="text-[10px] bg-saffron-50 text-saffron-900 border border-saffron-200 px-2 py-0.5 rounded">{d}</code>
            ))}
          </div>
        </section>
      )}

      {linked_clauses && linked_clauses.length > 0 && (
        <section>
          <h3 className="text-[10px] uppercase tracking-wider text-ink-500 font-semibold mb-1.5 flex items-center gap-1">
            <BookOpen className="h-3 w-3" /> Linked clauses ({linked_clauses.length})
          </h3>
          <ul className="space-y-1">
            {linked_clauses.slice(0, 12).map((c: any) => (
              <li key={c.to_id} className="text-xs rounded border border-mist-200 bg-white px-3 py-1.5">
                <code className="text-[10px] text-ink-500">{c.to_id}</code>
              </li>
            ))}
          </ul>
        </section>
      )}

      {recent_firings && recent_firings.length > 0 && (
        <section>
          <h3 className="text-[10px] uppercase tracking-wider text-ink-500 font-semibold mb-1.5 flex items-center gap-1">
            <Activity className="h-3 w-3" /> Recent firings ({recent_firings.length})
          </h3>
          <ul className="space-y-1">
            {recent_firings.slice(0, 10).map((f: any) => (
              <li key={f.node_id} className="text-xs rounded border border-mist-200 bg-white px-3 py-2">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="font-semibold text-ink-900 line-clamp-2">{f.label}</div>
                    {f.doc_id && <code className="text-[10px] text-ink-500">doc: {f.doc_id}</code>}
                  </div>
                  {f.properties?.verdict && (
                    <span className="shrink-0 rounded bg-mist-100 text-ink-700 px-2 py-0.5 text-[10px] font-mono">
                      {f.properties.verdict}
                    </span>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}


// ─── Clause / Section view ────────────────────────────────────────────


function ClauseView({ clause, linked_rules }: any) {
  const p = clause?.properties || {};
  const heading = p.heading || clause?.label || "—";
  // Section type label can come from properties.section_type
  return (
    <div className="space-y-5">
      <section>
        <div className="flex flex-wrap items-center gap-2 mb-3">
          {p.section_type && (
            <span className="inline-flex items-center gap-1 rounded-full bg-leaf-100 text-leaf-900 border border-leaf-200 px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-wider">
              {p.section_type}
            </span>
          )}
          {p.word_count && (
            <span className="text-[10px] text-ink-500 font-mono">{p.word_count} words</span>
          )}
        </div>
        <h3 className="text-[10px] uppercase tracking-wider text-ink-500 font-semibold mb-1.5">Clause heading</h3>
        <p className="text-base text-ink-900 leading-relaxed font-semibold whitespace-pre-wrap">{heading}</p>
        {clause?.label && clause.label !== heading && (
          <p className="mt-2 text-sm text-ink-700 leading-relaxed whitespace-pre-wrap">{clause.label}</p>
        )}
      </section>

      {p.source_file && (
        <section>
          <h3 className="text-[10px] uppercase tracking-wider text-ink-500 font-semibold mb-1.5 flex items-center gap-1">
            <FileText className="h-3 w-3" /> Source
          </h3>
          <div className="text-xs text-ink-700 bg-mist-50/60 border border-mist-200 rounded-md px-3 py-2">
            <code className="text-[11px]">{p.source_file}</code>
            {(p.line_start || p.line_end) && (
              <span className="ml-2 text-ink-500">lines {p.line_start ?? "?"}–{p.line_end ?? "?"}</span>
            )}
          </div>
        </section>
      )}

      {linked_rules && linked_rules.length > 0 && (
        <section>
          <h3 className="text-[10px] uppercase tracking-wider text-ink-500 font-semibold mb-1.5">Linked rules ({linked_rules.length})</h3>
          <ul className="space-y-1">
            {linked_rules.slice(0, 12).map((r: any) => (
              <li key={r.from_id} className="text-xs rounded border border-mist-200 bg-white px-3 py-1.5">
                <code className="text-[10px] text-ink-500">{r.from_id}</code>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}


// ─── Template view ────────────────────────────────────────────────────


function TemplateView({ template, samples }: any) {
  const p = template?.properties || {};
  const isTechSpec = template?.node_type === "TechSpecTemplate";
  const isSBD = template?.node_type === "SBDSection";

  return (
    <div className="space-y-5">
      <section>
        <div className="flex flex-wrap items-center gap-2 mb-3">
          <span className="inline-flex items-center gap-1 rounded-full bg-saffron-100 text-saffron-900 border border-saffron-200 px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-wider">
            {isTechSpec ? "TECH SPEC" : isSBD ? "SBD SECTION" : "TEMPLATE"}
          </span>
          {p.discipline && (
            <span className="inline-flex items-center gap-1 rounded-full bg-leaf-100 text-leaf-900 border border-leaf-200 px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-wider">
              {p.discipline}{p.sub_discipline ? ` / ${p.sub_discipline}` : ""}
            </span>
          )}
          {p.section_id && (
            <code className="text-[10px] font-bold bg-mist-100 px-2 py-1 rounded">Section {p.section_id}</code>
          )}
        </div>
        <h3 className="text-[10px] uppercase tracking-wider text-ink-500 font-semibold mb-1.5">Title</h3>
        <p className="text-sm text-ink-900 leading-relaxed">{template?.label || "—"}</p>
      </section>

      {isTechSpec && (
        <>
          {p.typical_short_desc && (
            <section>
              <h3 className="text-[10px] uppercase tracking-wider text-ink-500 font-semibold mb-1.5">Typical short description</h3>
              <p className="text-sm text-ink-900 leading-relaxed">{p.typical_short_desc}</p>
            </section>
          )}
          {(Array.isArray(p.sample_short_descs) && p.sample_short_descs.length > 0) && (
            <section>
              <h3 className="text-[10px] uppercase tracking-wider text-ink-500 font-semibold mb-1.5">Sample descriptions</h3>
              <ul className="text-xs space-y-0.5 text-ink-700 list-disc list-inside">
                {p.sample_short_descs.slice(0, 8).map((s: string, i: number) => (
                  <li key={i}>{s}</li>
                ))}
              </ul>
            </section>
          )}
          {(Array.isArray(p.expected_citations) && p.expected_citations.length > 0) && (
            <section>
              <h3 className="text-[10px] uppercase tracking-wider text-ink-500 font-semibold mb-1.5">Expected standards</h3>
              <div className="flex flex-wrap gap-1.5">
                {p.expected_citations.map((c: string) => (
                  <code key={c} className="text-[10px] bg-saffron-50 text-saffron-900 border border-saffron-200 px-2 py-0.5 rounded">{c}</code>
                ))}
              </div>
            </section>
          )}
        </>
      )}

      {isSBD && (p.content_md || p.placeholders || p.sub_blocks) && (
        <>
          {Array.isArray(p.placeholders) && p.placeholders.length > 0 && (
            <section>
              <h3 className="text-[10px] uppercase tracking-wider text-ink-500 font-semibold mb-1.5">Placeholders</h3>
              <div className="flex flex-wrap gap-1.5">
                {p.placeholders.slice(0, 16).map((ph: string) => (
                  <code key={ph} className="text-[10px] bg-mist-100 px-2 py-0.5 rounded">{ph}</code>
                ))}
              </div>
            </section>
          )}
          {p.content_md && (
            <section>
              <h3 className="text-[10px] uppercase tracking-wider text-ink-500 font-semibold mb-1.5">Content preview</h3>
              <pre className="text-xs text-ink-900 leading-relaxed bg-mist-50/60 border border-mist-200 rounded-md px-4 py-3 whitespace-pre-wrap max-h-72 overflow-y-auto font-sans">
                {String(p.content_md).slice(0, 2400)}
                {String(p.content_md).length > 2400 && "…"}
              </pre>
            </section>
          )}
        </>
      )}

      {samples && samples.length > 0 && (
        <section>
          <h3 className="text-[10px] uppercase tracking-wider text-ink-500 font-semibold mb-1.5">Sample BoQ items ({samples.length})</h3>
          <ul className="space-y-1">
            {samples.map((s: any) => (
              <li key={s.node_id} className="text-xs rounded border border-mist-200 bg-white px-3 py-1.5">
                {s.short_desc || s.label}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}


// ─── Typology view ────────────────────────────────────────────────────


function TypologyView({ typology_code, rules, recent_firings, verdict_mix, rule_count, firing_count }: any) {
  return (
    <div className="space-y-5">
      <section>
        <div className="flex flex-wrap items-center gap-2 mb-3">
          <span className="inline-flex items-center gap-1 rounded-full bg-leaf-100 text-leaf-900 border border-leaf-200 px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-wider">
            <Layers className="h-3 w-3" /> {typology_code}
          </span>
          <span className="text-[10px] text-ink-500 font-mono">{rule_count} rules · {firing_count} firings</span>
        </div>
      </section>

      {verdict_mix && Object.keys(verdict_mix).length > 0 && (
        <section>
          <h3 className="text-[10px] uppercase tracking-wider text-ink-500 font-semibold mb-1.5">Verdict mix (recent)</h3>
          <div className="flex flex-wrap gap-1.5">
            {Object.entries(verdict_mix).map(([v, n]) => (
              <span key={v} className="rounded bg-mist-100 text-ink-700 px-2.5 py-1 text-[11px]">
                <code className="font-mono">{v}</code>: <span className="font-bold">{String(n)}</span>
              </span>
            ))}
          </div>
        </section>
      )}

      {rules && rules.length > 0 && (
        <section>
          <h3 className="text-[10px] uppercase tracking-wider text-ink-500 font-semibold mb-1.5">Rules in this typology</h3>
          <ul className="space-y-1 max-h-96 overflow-y-auto pr-1">
            {rules.slice(0, 50).map((r: any) => (
              <li key={r.node_id} className="text-xs rounded border border-mist-200 bg-white px-3 py-2">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <code className="text-[10px] font-bold text-ink-900">{r.properties?.rule_id || r.node_id?.slice(0, 8)}</code>
                    <p className="text-ink-700 line-clamp-2 mt-0.5">{r.label}</p>
                  </div>
                  <SeverityBadge severity={r.properties?.severity} />
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}

      {recent_firings && recent_firings.length > 0 && (
        <section>
          <h3 className="text-[10px] uppercase tracking-wider text-ink-500 font-semibold mb-1.5 flex items-center gap-1">
            <Activity className="h-3 w-3" /> Recent firings ({recent_firings.length})
          </h3>
          <ul className="space-y-1 max-h-60 overflow-y-auto">
            {recent_firings.slice(0, 25).map((f: any) => (
              <li key={f.node_id} className="text-xs rounded border border-mist-200 bg-white px-3 py-1.5">
                <div className="flex justify-between gap-2">
                  <div className="truncate">{f.label}</div>
                  <span className="shrink-0 text-[10px] text-ink-500 font-mono">{f.properties?.verdict || "—"}</span>
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}


// ─── Generic dict view (fallback) ─────────────────────────────────────


function GenericView({ node }: any) {
  const p = node?.properties || {};
  return (
    <div className="space-y-3">
      <div>
        <div className="text-[10px] uppercase tracking-wider text-ink-500 font-semibold mb-1">Label</div>
        <p className="text-sm text-ink-900 whitespace-pre-wrap">{node?.label}</p>
      </div>
      <div>
        <div className="text-[10px] uppercase tracking-wider text-ink-500 font-semibold mb-1">Properties</div>
        <dl className="rounded-md border border-mist-200 bg-mist-50/40 p-3 text-xs space-y-1.5">
          {Object.entries(p).slice(0, 40).map(([k, v]) => {
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
      </div>
    </div>
  );
}


// ─── Main modal ───────────────────────────────────────────────────────


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

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  // Pick the right view based on what the API returned
  const node = data?.rule || data?.clause || data?.template || data;
  const nodeType = node?.node_type;
  const isTypology = !!(data?.typology_code && data?.rules);
  const headerType = isTypology ? "Typology"
                   : nodeType === "RuleNode"        ? "Procurement Rule"
                   : nodeType === "Section"         ? "Clause / Section"
                   : nodeType === "SBDSection"      ? "Bidding-Document Template"
                   : nodeType === "TechSpecTemplate" ? "Technical-Spec Template"
                   : nodeType || "Loading…";

  return (
    <div className="fixed inset-0 z-50 bg-ink-900/60 flex items-center justify-center p-4" onClick={onClose}>
      <div
        className="bg-white rounded-md shadow-xl max-w-3xl w-full max-h-[88vh] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="px-6 py-3 border-b border-mist-200 flex items-center justify-between bg-gradient-to-r from-mist-50/60 to-white">
          <div className="min-w-0">
            <div className="text-[10px] uppercase tracking-widest text-saffron-700 font-bold">
              {headerType}
            </div>
            <div className="text-sm font-bold text-ink-900 truncate mt-0.5">
              {node?.label || (isTypology ? data?.typology_code : id)}
            </div>
          </div>
          <button onClick={onClose} className="text-ink-500 hover:text-ink-900 p-1 rounded hover:bg-mist-100">
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="flex-1 overflow-y-auto px-6 py-5 text-sm">
          {loading && (
            <div className="flex items-center justify-center py-16 text-ink-500">
              <Loader2 className="h-5 w-5 animate-spin mr-2" /> Loading…
            </div>
          )}
          {err && (
            <div className="rounded-md bg-red-50 border border-red-200 px-3 py-2 text-red-700">{err}</div>
          )}
          {!loading && !err && data && (
            <>
              {isTypology
                ? <TypologyView {...data} />
                : nodeType === "RuleNode"
                  ? <RuleView rule={node} linked_clauses={data.linked_clauses} recent_firings={data.recent_firings} />
                : nodeType === "Section"
                  ? <ClauseView clause={node} linked_rules={data.linked_rules} />
                : (nodeType === "TechSpecTemplate" || nodeType === "SBDSection")
                  ? <TemplateView template={node} samples={data.samples} />
                : <GenericView node={node} />}
            </>
          )}
        </div>

        <footer className="px-6 py-2 border-t border-mist-200 flex items-center justify-between text-[10px] text-ink-500 font-mono">
          <span>node_id: {node?.node_id ? node.node_id.slice(0, 16) : id}</span>
          {node?.source_ref && <span>{node.source_ref}</span>}
        </footer>
      </div>
    </div>
  );
}
