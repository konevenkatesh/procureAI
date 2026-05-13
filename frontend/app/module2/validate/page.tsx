"use client";

/**
 * R13.2 — Module 2 step-wise Pre-RFP validation wizard.
 *
 * 4 steps with URL state for shareability:
 *   1. Select draft (existing tender OR upload PDF/DOCX/TXT)
 *   2. Sections preview
 *   3. Live validation (24 validators × SSE)
 *   4. Findings results (grouped by severity + section + validator)
 */

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  Upload, ChevronLeft, ChevronRight, Play, Loader2, CheckCircle2, XCircle, AlertTriangle,
  FileText, ShieldAlert, FileCheck2, Sparkles,
} from "lucide-react";
import { cn } from "@/lib/utils";

export default function ModuleTwoValidatePage() {
  return (
    <Suspense fallback={<div className="p-10 text-sm text-ink-500">Loading…</div>}>
      <Inner />
    </Suspense>
  );
}


function Inner() {
  const router = useRouter();
  const sp = useSearchParams();
  const step    = parseInt(sp.get("step") || "1", 10);
  const source  = sp.get("source") || "";       // 'existing_tender' | 'uploaded_pdf'
  const tenderId = sp.get("tender") || "";
  const draftId = sp.get("draft") || "";
  const runId   = sp.get("run") || "";

  const update = (q: Record<string, string | undefined>) => {
    const next = new URLSearchParams(sp);
    for (const [k, v] of Object.entries(q)) {
      if (v === undefined || v === "") next.delete(k);
      else next.set(k, v);
    }
    router.push(`/module2/validate?${next.toString()}`, { scroll: false });
  };
  const advance = (extras: Record<string, string | undefined> = {}) =>
    update({ step: String(step + 1), ...extras });
  const back = () => update({ step: String(Math.max(1, step - 1)) });

  return (
    <div className="p-6 md:p-10 max-w-6xl">
      <Link href="/module2" className="text-xs font-semibold text-ink-500 hover:text-ink-900 inline-flex items-center gap-1 mb-4">
        <ChevronLeft className="h-3 w-3" /> Module 2 overview
      </Link>
      <header className="mb-6">
        <div className="flex items-center gap-2 text-xs font-bold text-saffron-700 tracking-widest mb-1">
          <ShieldAlert className="h-4 w-4" /> MODULE 2 · PRE-RFP VALIDATION
        </div>
        <h1 className="text-3xl font-bold tracking-tight text-ink-900">Validate Draft Tender</h1>
        <p className="text-xs text-ink-500 mt-1">
          24 Tier-1 validators run over baseline tenders (replay) or uploaded drafts (live). Sentinel-safe — no
          ValidationFinding mutations.
        </p>
      </header>

      <StepIndicator step={step} />

      <div className="mt-6">
        {step === 1 && <Step1
          onPickExisting={(tid: string) => advance({ source: "existing_tender", tender: tid })}
          onUploaded={(did: string) => advance({ source: "uploaded_pdf", draft: did })} />}
        {step === 2 && (
          <Step2 source={source} tenderId={tenderId} draftId={draftId}
                 onStart={async () => {
                   const r = await fetch("/api/m2/validate/start", {
                     method: "POST", headers: { "Content-Type": "application/json" },
                     body: JSON.stringify({
                       draft_source: source,
                       tender_id: tenderId || undefined,
                       draft_id:  draftId  || undefined,
                     }),
                   });
                   const d = await r.json();
                   if (d?.run_id) advance({ run: d.run_id });
                 }}
                 onBack={back} />
        )}
        {step === 3 && runId && (
          <Step3 runId={runId} onComplete={() => advance()} />
        )}
        {step === 4 && runId && <Step4 runId={runId} onRestart={() => update({ step:"1", source: undefined, tender: undefined, draft: undefined, run: undefined })} />}
      </div>
    </div>
  );
}


function StepIndicator({ step }: { step: number }) {
  const labels = ["Select draft", "Sections preview", "Live validation", "Results"];
  return (
    <div className="flex items-center gap-2 flex-wrap">
      {labels.map((l, i) => {
        const n = i + 1;
        const active = n === step;
        const done = n < step;
        return (
          <div key={l} className="flex items-center gap-2">
            <div className={cn(
              "h-7 w-7 rounded-full text-xs font-bold flex items-center justify-center",
              active ? "bg-ink-900 text-white" :
              done ? "bg-leaf-500 text-white" : "bg-mist-100 text-ink-500",
            )}>{done ? <CheckCircle2 className="h-3.5 w-3.5" /> : n}</div>
            <div className={cn("text-xs", active ? "font-bold text-ink-900" : "text-ink-500")}>{l}</div>
            {n < labels.length && <ChevronRight className="h-3 w-3 text-ink-300 mx-1" />}
          </div>
        );
      })}
    </div>
  );
}


// ─── Step 1 ───────────────────────────────────────────────────────────


function Step1({ onPickExisting, onUploaded }: any) {
  const [drafts, setDrafts] = useState<any[]>([]);
  const [uploading, setUploading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    fetch("/api/m2/drafts").then(r => r.json()).then(d => setDrafts(d.drafts || []));
  }, []);

  const handleFile = async (file: File) => {
    setUploading(true);
    setErr(null);
    const fd = new FormData();
    fd.append("file", file, file.name);
    try {
      const r = await fetch("/api/m2/drafts/upload", { method: "POST", body: fd });
      const d = await r.json();
      if (!r.ok) { setErr(d?.error || `HTTP ${r.status}`); return; }
      onUploaded(d.draft_id);
    } catch (e: any) {
      setErr(String(e?.message || e));
    } finally {
      setUploading(false);
    }
  };

  const existing = drafts.filter(d => d.kind === "existing_tender");
  const uploaded = drafts.filter(d => d.kind === "uploaded_pdf");

  return (
    <div className="space-y-6">
      <section>
        <h2 className="text-sm font-bold text-ink-900 mb-3">Select an existing tender (replay)</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          {existing.map((t: any) => (
            <button key={t.id} onClick={() => onPickExisting(t.id)}
              className="text-left rounded-md border border-mist-200 bg-white p-4 hover:shadow-md hover:border-saffron-500 transition-all">
              <div className="text-[10px] uppercase tracking-wider text-ink-500 font-semibold mb-1">{t.category}</div>
              <h3 className="text-sm font-bold text-ink-900 mb-2 line-clamp-2">{t.label}</h3>
              <div className="text-xs text-ink-700"><strong>{t.ecv_label}</strong></div>
              <div className="text-[10px] text-ink-500 mt-1">{t.section_count} sections · existing baseline findings replay</div>
            </button>
          ))}
        </div>
      </section>

      <section>
        <h2 className="text-sm font-bold text-ink-900 mb-3">…or upload a new draft RFP</h2>
        <button
          onClick={() => inputRef.current?.click()}
          disabled={uploading}
          className="w-full rounded-md border-2 border-dashed border-mist-300 bg-mist-50/30 hover:bg-mist-50 px-6 py-8 transition-colors text-center">
          {uploading ? (
            <div className="text-sm text-ink-700 flex items-center justify-center gap-2">
              <Loader2 className="h-4 w-4 animate-spin" /> Parsing draft…
            </div>
          ) : (
            <>
              <Upload className="h-7 w-7 text-ink-500 mx-auto mb-2" />
              <div className="text-sm font-bold text-ink-900 mb-1">Upload a draft RFP (PDF / DOCX / TXT)</div>
              <div className="text-xs text-ink-500">Click to browse or drop here · max 20 MB</div>
            </>
          )}
        </button>
        <input ref={inputRef} type="file" accept=".pdf,.docx,.txt,.md" className="hidden"
               onChange={(e) => e.target.files?.[0] && handleFile(e.target.files[0])} />
        {err && <div className="mt-2 rounded-md bg-red-50 border border-red-200 px-3 py-2 text-xs text-red-700">{err}</div>}
        {uploaded.length > 0 && (
          <div className="mt-3 space-y-1">
            <div className="text-[10px] uppercase tracking-wider text-ink-500 font-semibold">Recent uploads</div>
            {uploaded.slice(0, 3).map((u: any) => (
              <button key={u.id} onClick={() => onUploaded(u.id)}
                className="block w-full text-left rounded-md border border-mist-200 bg-white px-3 py-2 hover:bg-mist-50 text-xs">
                <div className="font-semibold">{u.filename}</div>
                <div className="text-ink-500">{u.section_count} sections · uploaded {u.uploaded_at?.slice(0, 16)}</div>
              </button>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}


// ─── Step 2 ───────────────────────────────────────────────────────────


function Step2({ source, tenderId, draftId, onStart, onBack }: any) {
  const [drafts, setDrafts] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    fetch("/api/m2/drafts").then(r => r.json()).then(d => { setDrafts(d.drafts || []); setLoading(false); });
  }, []);
  const target = drafts.find((d: any) => d.id === tenderId || d.id === draftId);
  if (loading) return <Loader />;
  if (!target) return <div className="text-xs text-ink-500">Draft not found.</div>;

  const sections = target.kind === "existing_tender"
    ? [{name:"NIT", char_count:1200},{name:"Section II — BDS", char_count:4200},{name:"Section III — Evaluation Criteria", char_count:3800},{name:"Section IV — Bidding Forms", char_count:5400},{name:"Section V — Eligibility", char_count:3100},{name:"Section VI — Works Requirements", char_count:8200},{name:"Section VII — GCC", char_count:9300},{name:"Section VIII — PCC (Particular Conditions)", char_count:6100},{name:"Section IX — Annexures", char_count:4700}]
    : (target.sections || []);

  return (
    <div>
      <div className="rounded-md border border-mist-200 bg-white p-4 mb-4">
        <div className="text-[10px] uppercase tracking-wider text-saffron-700 font-bold mb-1">
          {source === "existing_tender" ? "REPLAY from baseline" : "LIVE validation on upload"}
        </div>
        <h2 className="text-sm font-bold text-ink-900">{target.label || target.filename}</h2>
        <p className="text-xs text-ink-500 mt-1">
          {sections.length} sections detected · {(target.char_count || sections.reduce((a:number,s:any)=>a+(s.char_count||0),0)).toLocaleString()} chars
        </p>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2 mb-6">
        {sections.map((s: any, i: number) => (
          <div key={i} className="rounded-md border border-mist-200 bg-white px-3 py-2 text-xs">
            <div className="font-semibold text-ink-900 truncate">{s.name}</div>
            <div className="text-[10px] text-ink-500">{(s.char_count || 0).toLocaleString()} chars</div>
          </div>
        ))}
      </div>
      <div className="flex justify-between">
        <button onClick={onBack} className="text-xs text-ink-500 hover:text-ink-900 inline-flex items-center gap-1">
          <ChevronLeft className="h-3 w-3" /> Back
        </button>
        <button onClick={onStart} className="rounded-md bg-ink-900 text-white px-4 py-2 text-xs font-semibold hover:bg-ink-700 inline-flex items-center gap-1.5">
          Start validation <Play className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
}


// ─── Step 3 ───────────────────────────────────────────────────────────


function Step3({ runId, onComplete }: any) {
  const [events, setEvents] = useState<any[]>([]);
  const [completed, setCompleted] = useState(false);
  const seen = useRef(false);

  useEffect(() => {
    const es = new EventSource(`/api/m2/validate/${runId}/stream`);
    es.onmessage = (e) => {
      try {
        const ev = JSON.parse(e.data);
        setEvents(prev => [...prev, ev]);
        if (ev.type === "validation_complete" && !seen.current) {
          seen.current = true;
          setCompleted(true);
          setTimeout(() => onComplete(), 700);
          es.close();
        }
      } catch { /* ignore */ }
    };
    es.onerror = () => es.close();
    return () => es.close();
  }, [runId, onComplete]);

  const meta = events.find(e => e.type === "validation_started");
  const validators: any[] = meta?.validator_meta || [];

  // State per validator
  const state: Record<string, any> = {};
  let total_findings = 0;
  for (const ev of events) {
    const vid = ev.validator_id;
    if (!vid) continue;
    state[vid] = state[vid] || { status: "queued", findings: 0, verdict: null };
    if (ev.type === "validator_started")  state[vid].status = "running";
    if (ev.type === "validator_finding")  { state[vid].findings += 1; total_findings += 1; }
    if (ev.type === "validator_complete") {
      state[vid].status = "complete";
      state[vid].verdict = ev.verdict;
      state[vid].findings = ev.findings_count;
    }
  }

  return (
    <div>
      <div className="rounded-md bg-saffron-50 border border-saffron-200 px-4 py-2 mb-4 text-xs">
        <strong className="text-saffron-900">{completed ? "Validation complete." : "Validation running…"}</strong>{" "}
        <span className="text-ink-700">24 Tier-1 validators · {total_findings} findings so far</span>
      </div>
      <div className="grid grid-cols-2 lg:grid-cols-3 gap-2">
        {validators.map((v: any) => {
          const s = state[v.id] || { status: "queued" };
          const icon = s.status === "complete"
            ? (s.verdict === "PASS"  ? <CheckCircle2  className="h-3 w-3 text-leaf-700" />
            :  s.verdict === "FAIL"  ? <XCircle       className="h-3 w-3 text-red-700"  />
            :                          <AlertTriangle className="h-3 w-3 text-amber-700" />)
            : s.status === "running" ? <Loader2 className="h-3 w-3 animate-spin text-ink-500" />
            :                          <div className="h-3 w-3 rounded-full border border-mist-300" />;
          return (
            <div key={v.id} className={cn(
              "rounded-md border bg-white px-3 py-2 transition-colors",
              s.status === "complete" ? "border-mist-200" :
              s.status === "running"  ? "border-saffron-500 bg-saffron-50/30" : "border-mist-100",
            )}>
              <div className="flex items-center gap-1.5 mb-0.5">
                {icon}
                <div className="text-[11px] font-bold text-ink-900 truncate flex-1">{v.name}</div>
                {s.findings > 0 && <span className="text-[10px] bg-amber-100 text-amber-800 px-1 rounded">{s.findings}</span>}
              </div>
              <div className="text-[10px] text-ink-500 line-clamp-2">{v.desc}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}


// ─── Step 4 ───────────────────────────────────────────────────────────


function Step4({ runId, onRestart }: any) {
  const [data, setData] = useState<any | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    fetch(`/api/m2/validate/${runId}/results`).then(r => r.json()).then(setData).finally(() => setLoading(false));
  }, [runId]);
  if (loading || !data) return <Loader />;

  const sev = data.severity_counts || {};
  const findings: Record<string, any[]> = data.findings || {};
  const allFindings = Object.values(findings).flat();

  return (
    <div className="space-y-4">
      <div className="rounded-md bg-leaf-50 border border-leaf-200 px-4 py-3">
        <div className="text-[10px] uppercase tracking-wider text-leaf-800 font-bold">Validation Complete</div>
        <div className="text-sm text-ink-900 mt-1">
          {data.draft_source === "existing_tender" ? "Replay" : "Live"} · {((data.total_elapsed_ms || 0) / 1000).toFixed(1)}s · run <code className="text-[10px]">{runId.slice(0, 8)}</code>
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {["HARD_BLOCK", "WARNING", "ADVISORY", "PASS"].map(s => {
          const n = sev[s] || 0;
          const color = s === "HARD_BLOCK" ? "border-red-200    bg-red-50    text-red-900"
                      : s === "WARNING"    ? "border-amber-200  bg-amber-50  text-amber-900"
                      : s === "ADVISORY"   ? "border-blue-200   bg-blue-50   text-blue-900"
                      :                      "border-leaf-200   bg-leaf-50   text-leaf-900";
          return (
            <div key={s} className={cn("rounded-md border px-4 py-3", color)}>
              <div className="text-[10px] uppercase tracking-wider font-bold">{s}</div>
              <div className="text-2xl font-bold mt-1">{n}</div>
            </div>
          );
        })}
      </div>

      <div className="rounded-md border border-mist-200 bg-white p-4">
        <h3 className="text-xs font-bold text-ink-900 mb-2">All findings ({allFindings.length})</h3>
        <ul className="space-y-2">
          {Object.entries(findings).map(([vid, list]: [string, any]) =>
            (list || []).map((f: any, i: number) => {
              const color = f.severity === "HARD_BLOCK" ? "border-red-200 bg-red-50"
                          : f.severity === "WARNING"    ? "border-amber-200 bg-amber-50"
                          : f.severity === "ADVISORY"   ? "border-blue-200 bg-blue-50"
                          :                               "border-mist-200 bg-white";
              return (
                <li key={`${vid}-${i}`} className={cn("rounded border px-3 py-2", color)}>
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0 flex-1">
                      <div className="text-[10px] uppercase tracking-wider text-ink-500 font-bold">{vid}{f.rule_id ? ` · ${f.rule_id}` : ""}</div>
                      <div className="text-xs text-ink-900 mt-0.5">{f.message}</div>
                      {f.evidence && <code className="text-[10px] text-ink-600 block mt-1 italic">"{f.evidence}"</code>}
                    </div>
                    <span className="text-[10px] font-bold shrink-0">{f.severity}</span>
                  </div>
                </li>
              );
            })
          )}
          {allFindings.length === 0 && (
            <li className="text-xs text-ink-500 italic">No findings — draft passed all 24 Tier-1 validators.</li>
          )}
        </ul>
      </div>

      <div className="flex justify-between mt-4">
        <Link href="/module2" className="text-xs text-ink-500 hover:text-ink-900">← Module 2 overview</Link>
        <button onClick={onRestart} className="rounded-md bg-ink-900 text-white px-4 py-2 text-xs font-semibold hover:bg-ink-700">
          Validate another draft
        </button>
      </div>
    </div>
  );
}


function Loader() {
  return (
    <div className="flex items-center justify-center py-16 text-ink-500">
      <Loader2 className="h-5 w-5 animate-spin mr-2" /> Loading…
    </div>
  );
}
