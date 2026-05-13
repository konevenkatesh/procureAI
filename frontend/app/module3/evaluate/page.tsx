"use client";

/**
 * R11.2 — Module 3 step-wise evaluation wizard.
 *
 * Single-page state machine: 5 steps with URL-state for shareability.
 * SSE stream consumed inline for Step 4 live evaluation.
 *
 * Steps:
 *   1. Select tender (3 demo tenders)
 *   2. Select bidder(s) (B1-B9)
 *   3. View bid (BoQ + LetterOfBid + EMD-BG cards)
 *   4. Live evaluation (14 validators × N bidders, SSE-driven)
 *   5. Results (eligibility matrix delta + ranking + drilldown)
 */

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  Play, ChevronRight, ChevronLeft, Loader2, CheckCircle2, XCircle, AlertTriangle,
  FileText, Receipt, ShieldCheck, Building2, Sparkles,
} from "lucide-react";
import { cn } from "@/lib/utils";

export default function ModuleThreeEvaluatePage() {
  return (
    <Suspense fallback={<div className="p-10 text-sm text-ink-500">Loading wizard…</div>}>
      <Inner />
    </Suspense>
  );
}

function Inner() {
  const router = useRouter();
  const sp = useSearchParams();
  const step = parseInt(sp.get("step") || "1", 10);
  const tenderId = sp.get("tender") || "";
  const bidderIds = (sp.get("bidders") || "").split(",").filter(Boolean);
  const runId = sp.get("run") || "";

  const update = (q: Record<string, string | undefined>) => {
    const next = new URLSearchParams(sp);
    for (const [k, v] of Object.entries(q)) {
      if (v === undefined || v === "") next.delete(k);
      else next.set(k, v);
    }
    router.push(`/module3/evaluate?${next.toString()}`, { scroll: false });
  };

  const advance = (extras: Record<string, string | undefined> = {}) =>
    update({ step: String(step + 1), ...extras });
  const back = () => update({ step: String(Math.max(1, step - 1)) });

  return (
    <div className="p-6 md:p-10 max-w-6xl">
      <div className="mb-4">
        <Link href="/module3" className="text-xs font-semibold text-ink-500 hover:text-ink-900 inline-flex items-center gap-1">
          <ChevronLeft className="h-3 w-3" /> Back to Module 3 overview
        </Link>
      </div>
      <header className="mb-6">
        <div className="flex items-center gap-2 text-xs font-bold text-saffron-700 tracking-widest mb-1">
          <Sparkles className="h-4 w-4" /> MODULE 3 · STEP-WISE EVALUATION
        </div>
        <h1 className="text-3xl font-bold tracking-tight text-ink-900">Evaluate Bidders</h1>
        <p className="text-xs text-ink-500 mt-1">
          5-step ephemeral evaluation. Reads existing validations + emits live SSE progress.
          Sentinel-safe (no writes to ValidationFinding/EligibilityMatrix).
        </p>
      </header>

      <StepIndicator step={step} />

      <div className="mt-6">
        {step === 1 && <Step1 onChoose={(tid) => advance({ tender: tid })} />}
        {step === 2 && tenderId && (
          <Step2 tenderId={tenderId}
                 selected={bidderIds}
                 onToggle={(bids: string[]) => update({ bidders: bids.join(",") })}
                 onNext={() => advance()}
                 onBack={back} />
        )}
        {step === 3 && tenderId && bidderIds.length > 0 && (
          <Step3 tenderId={tenderId} bidderIds={bidderIds}
                 onStart={async () => {
                   const r = await fetch("/api/m3/evaluate/start", {
                     method: "POST",
                     headers: { "Content-Type": "application/json" },
                     body: JSON.stringify({ tender_id: tenderId, bidder_ids: bidderIds }),
                   });
                   const data = await r.json();
                   if (data?.run_id) advance({ run: data.run_id });
                 }}
                 onBack={back} />
        )}
        {step === 4 && runId && (
          <Step4 runId={runId} tenderId={tenderId} bidderIds={bidderIds}
                 onComplete={() => advance()} />
        )}
        {step === 5 && runId && <Step5 runId={runId} onRestart={() => update({ step: "1", tender: undefined, bidders: undefined, run: undefined })} />}
      </div>
    </div>
  );
}


// ─── Indicator ────────────────────────────────────────────────────────


function StepIndicator({ step }: { step: number }) {
  const labels = ["Tender", "Bidders", "View Bid", "Evaluation", "Results"];
  return (
    <div className="flex items-center gap-2">
      {labels.map((l, i) => {
        const n = i + 1;
        const active = n === step;
        const done = n < step;
        return (
          <div key={l} className="flex items-center gap-2">
            <div className={cn(
              "h-7 w-7 rounded-full text-xs font-bold flex items-center justify-center",
              active ? "bg-ink-900 text-white" :
              done ? "bg-leaf-500 text-white" : "bg-mist-100 text-ink-500"
            )}>
              {done ? <CheckCircle2 className="h-3.5 w-3.5" /> : n}
            </div>
            <div className={cn("text-xs", active ? "font-bold text-ink-900" : "text-ink-500")}>{l}</div>
            {n < labels.length && <ChevronRight className="h-3 w-3 text-ink-300 mx-1" />}
          </div>
        );
      })}
    </div>
  );
}


// ─── Step 1: Choose tender ────────────────────────────────────────────


function Step1({ onChoose }: { onChoose: (tid: string) => void }) {
  const [tenders, setTenders] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    fetch("/api/m3/tenders").then(r => r.json()).then(d => setTenders(d.tenders || [])).finally(() => setLoading(false));
  }, []);
  if (loading) return <Loader />;
  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
      {tenders.map(t => (
        <button key={t.tender_id} onClick={() => onChoose(t.tender_id)}
          className="text-left rounded-md border border-mist-200 bg-white p-5 hover:shadow-md hover:border-saffron-500 transition-all">
          <div className="flex items-center gap-2 text-[10px] uppercase tracking-wider text-ink-500 font-semibold mb-2">
            <Building2 className="h-3 w-3" /> {t.category}
          </div>
          <h3 className="text-sm font-bold text-ink-900 mb-2 line-clamp-2">{t.name}</h3>
          <div className="space-y-1 text-xs text-ink-700">
            <div><span className="text-ink-500">ECV:</span> <strong>{t.ecv_label}</strong></div>
            <div><span className="text-ink-500">Period:</span> {t.period_months} months</div>
            <div><span className="text-ink-500">Scope:</span> {t.discipline}</div>
            <div><span className="text-ink-500">Bidders:</span> {t.bidder_count}</div>
            <div className="text-[10px] text-ink-500 italic mt-2">{t.issued_by}</div>
          </div>
        </button>
      ))}
    </div>
  );
}


// ─── Step 2: Choose bidders ───────────────────────────────────────────


function Step2({ tenderId, selected, onToggle, onNext, onBack }: any) {
  const [bidders, setBidders] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    fetch(`/api/m3/tenders/${tenderId}/bidders`).then(r => r.json()).then(d => setBidders(d.bidders || [])).finally(() => setLoading(false));
  }, [tenderId]);
  if (loading) return <Loader />;

  const toggle = (bid: string) => {
    onToggle(selected.includes(bid) ? selected.filter((x: string) => x !== bid) : [...selected, bid]);
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-4 text-xs">
        <div className="text-ink-500">Select bidders to evaluate ({selected.length} selected)</div>
        <div className="flex gap-2">
          <button onClick={() => onToggle(bidders.map((b: any) => b.bidder_id))} className="text-saffron-700 hover:text-saffron-900">Select all</button>
          <button onClick={() => onToggle([])} className="text-ink-500 hover:text-ink-900">Clear</button>
        </div>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-2 mb-6">
        {bidders.map((b: any) => {
          const checked = selected.includes(b.bidder_id);
          const verdictColor = b.baseline_verdict === "QUALIFIED" ? "text-leaf-700"
                            : b.baseline_verdict === "DISQUALIFIED" ? "text-red-700"
                            : b.baseline_verdict?.includes("FLAGGED") ? "text-amber-700" : "text-ink-500";
          return (
            <label key={b.bidder_id}
              className={cn(
                "flex items-start gap-2 rounded-md border bg-white px-3 py-2 cursor-pointer transition-all",
                checked ? "border-ink-900 bg-ink-50" : "border-mist-200 hover:bg-mist-50",
              )}>
              <input type="checkbox" checked={checked} onChange={() => toggle(b.bidder_id)} className="mt-1" />
              <div className="flex-1 min-w-0">
                <div className="text-xs font-bold text-ink-900 truncate">{b.company_name}</div>
                <code className="text-[10px] text-ink-500">{b.bidder_id.toUpperCase()}</code>
                {b.is_jv && <span className="ml-1 rounded bg-leaf-100 text-leaf-800 px-1 text-[9px]">JV</span>}
                <div className={`text-[10px] mt-1 ${verdictColor}`}>
                  baseline: {b.baseline_verdict || "—"}
                </div>
              </div>
            </label>
          );
        })}
      </div>
      <FooterNav back={onBack} next={onNext} nextLabel={`Continue with ${selected.length} bidder${selected.length === 1 ? "" : "s"}`} nextDisabled={selected.length === 0} />
    </div>
  );
}


// ─── Step 3: View bid documents ───────────────────────────────────────


function Step3({ tenderId, bidderIds, onStart, onBack }: any) {
  const [activeBid, setActiveBid] = useState(bidderIds[0]);
  const [bid, setBid] = useState<any | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    setLoading(true);
    fetch(`/api/m3/bidders/${activeBid}/bid/${tenderId}`).then(r => r.json()).then(setBid).finally(() => setLoading(false));
  }, [activeBid, tenderId]);

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <div className="text-xs text-ink-500">Review bid documents before starting the validators ({bidderIds.length} bidders)</div>
        {bidderIds.length > 1 && (
          <div className="flex gap-1">
            {bidderIds.map((b: string) => (
              <button key={b} onClick={() => setActiveBid(b)}
                className={cn("px-2.5 py-1 text-xs rounded-md border",
                  activeBid === b ? "bg-ink-900 text-white border-ink-900" : "bg-white border-mist-200 hover:bg-mist-50")}>
                {b.toUpperCase()}
              </button>
            ))}
          </div>
        )}
      </div>
      {loading || !bid ? <Loader /> : (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-6">
          <DocCard title="Letter of Bid" icon={<FileText className="h-4 w-4" />}
            doc={bid.letter_of_bid} />
          <DocCard title="EMD / Bank Guarantee" icon={<ShieldCheck className="h-4 w-4" />}
            doc={bid.emd_bg} />
          <DocCard title="Priced BoQ" icon={<Receipt className="h-4 w-4" />}
            doc={bid.priced_boq} />
        </div>
      )}
      <FooterNav back={onBack} next={onStart} nextLabel="Start evaluation" nextIcon={<Play className="h-3.5 w-3.5" />} />
    </div>
  );
}

function DocCard({ title, icon, doc }: any) {
  const p = doc?.properties || {};
  return (
    <div className="rounded-md border border-mist-200 bg-white p-4">
      <div className="flex items-center gap-2 text-[10px] uppercase tracking-wider text-ink-500 font-semibold mb-2">
        {icon} {title}
      </div>
      {!doc ? <div className="text-xs text-ink-400 italic">Not available</div> : (
        <>
          <div className="text-xs text-ink-900 font-semibold mb-1 line-clamp-2">{doc.label}</div>
          <dl className="text-[11px] space-y-0.5">
            {Object.entries(p).slice(0, 6).map(([k, v]) => (
              <div key={k} className="grid grid-cols-[100px_1fr] gap-1">
                <dt className="text-ink-500 font-mono">{k}</dt>
                <dd className="text-ink-900 truncate">{String(v).slice(0, 60)}</dd>
              </div>
            ))}
          </dl>
        </>
      )}
    </div>
  );
}


// ─── Step 4: Live evaluation ──────────────────────────────────────────


function Step4({ runId, bidderIds, onComplete }: any) {
  const [events, setEvents] = useState<any[]>([]);
  const [completed, setCompleted] = useState(false);
  const seenComplete = useRef(false);

  useEffect(() => {
    const es = new EventSource(`/api/m3/evaluate/${runId}/stream`);
    es.onmessage = (e) => {
      try {
        const ev = JSON.parse(e.data);
        setEvents(prev => [...prev, ev]);
        if (ev.type === "evaluation_complete" && !seenComplete.current) {
          seenComplete.current = true;
          setCompleted(true);
          setTimeout(() => onComplete(), 800);    // brief celebration before advancing
          es.close();
        }
      } catch { /* ignore */ }
    };
    es.onerror = () => { es.close(); };
    return () => es.close();
  }, [runId, onComplete]);

  // Aggregate per (bidder, validator)
  const grid: Record<string, Record<string, any>> = {};
  for (const ev of events) {
    const b = ev.bidder_id;
    if (!b) continue;
    grid[b] ||= {};
    const v = ev.validator_id;
    if (!v) continue;
    grid[b][v] = grid[b][v] || { status: "queued", findings: 0 };
    if (ev.type === "validator_started")   grid[b][v].status = "running";
    if (ev.type === "validator_complete") {
      grid[b][v].status = "complete";
      grid[b][v].verdict = ev.verdict;
      grid[b][v].findings = ev.findings_count;
    }
    if (ev.type === "validator_finding") grid[b][v].findings += 1;
  }

  const validators = events.find(e => e.type === "evaluation_started")?.validators || [];

  return (
    <div>
      <div className="rounded-md bg-saffron-50 border border-saffron-200 px-4 py-2 mb-4 text-xs">
        <strong className="text-saffron-900">{completed ? "Evaluation complete." : "Evaluation running…"}</strong>{" "}
        <span className="text-ink-700">Streaming live from m3-evaluator. {bidderIds.length} bidder(s) × 14 validators.</span>
      </div>
      {bidderIds.map((bid: string) => (
        <div key={bid} className="mb-5">
          <h3 className="text-sm font-bold text-ink-900 mb-2">Bidder {bid.toUpperCase()}</h3>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
            {validators.map((vid: string) => {
              const state = grid[bid]?.[vid] || { status: "queued" };
              const icon = state.status === "complete"
                ? (state.verdict === "PASS" ? <CheckCircle2 className="h-3 w-3 text-leaf-700" />
                   : state.verdict === "FAIL" ? <XCircle className="h-3 w-3 text-red-700" />
                   : <AlertTriangle className="h-3 w-3 text-amber-700" />)
                : state.status === "running" ? <Loader2 className="h-3 w-3 animate-spin text-ink-500" />
                : <div className="h-3 w-3 rounded-full border border-mist-300" />;
              return (
                <div key={vid} className={cn(
                  "rounded-md border bg-white px-2.5 py-1.5 transition-colors",
                  state.status === "complete" ? "border-mist-200" :
                  state.status === "running"  ? "border-saffron-500 bg-saffron-50/30" : "border-mist-100"
                )}>
                  <div className="flex items-center gap-1.5">
                    {icon}
                    <div className="text-[11px] font-medium text-ink-900 truncate flex-1">{vid}</div>
                    {state.findings > 0 && <span className="text-[10px] bg-amber-100 text-amber-800 px-1 rounded">{state.findings}</span>}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}


// ─── Step 5: Results ──────────────────────────────────────────────────


function Step5({ runId, onRestart }: any) {
  const [results, setResults] = useState<any | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    fetch(`/api/m3/evaluate/${runId}/results`).then(r => r.json()).then(setResults).finally(() => setLoading(false));
  }, [runId]);
  if (loading || !results) return <Loader />;

  const r = results.results || {};
  const em = r.eligibility_matrix || {};
  const ranking = r.tender_ranking || {};

  return (
    <div className="space-y-4">
      <div className="rounded-md bg-leaf-50 border border-leaf-200 px-4 py-3">
        <div className="text-[10px] uppercase tracking-wider text-leaf-800 font-bold">Evaluation Complete</div>
        <div className="text-sm text-ink-900 mt-1">
          Total elapsed: {((results.total_elapsed_ms || 0) / 1000).toFixed(1)}s · Run ID: <code className="text-[10px]">{runId.slice(0,8)}</code>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div className="rounded-md border border-mist-200 bg-white p-4">
          <h3 className="text-xs font-bold text-ink-900 mb-2">Eligibility Matrix</h3>
          <dl className="space-y-1.5 text-xs">
            {Object.entries(em.verdict_counts || {}).map(([v, n]) => (
              <div key={v} className="flex justify-between gap-2">
                <dt className="text-ink-700">{v}</dt>
                <dd className="font-mono font-bold text-ink-900">{String(n)}</dd>
              </div>
            ))}
          </dl>
        </div>
        <div className="rounded-md border border-mist-200 bg-white p-4">
          <h3 className="text-xs font-bold text-ink-900 mb-2">Tender Ranking</h3>
          {ranking?.effective_l1_bidder ? (
            <div className="text-xs space-y-1">
              <div><span className="text-ink-500">L1:</span> <strong>{ranking.effective_l1_bidder}</strong></div>
              <div><span className="text-ink-500">L1 amount:</span> ₹{ranking.effective_l1_amount?.toLocaleString?.() || "—"}</div>
              {ranking.ranking?.slice?.(0,5).map?.((r: any, i: number) => (
                <div key={i} className="text-[10px] text-ink-500">{i+1}. {r.bidder_id} · ₹{r.bid_amount}</div>
              ))}
            </div>
          ) : <div className="text-[11px] text-ink-400 italic">No ranking computed</div>}
        </div>
      </div>

      <div className="rounded-md border border-mist-200 bg-white p-4">
        <h3 className="text-xs font-bold text-ink-900 mb-2">Per-bidder results ({Object.keys(r.bidders || {}).length})</h3>
        <ul className="space-y-2">
          {Object.entries(r.bidders || {}).map(([bid, info]: any) => (
            <li key={bid} className="rounded border border-mist-200 px-3 py-2">
              <div className="flex items-center justify-between gap-2">
                <strong className="text-sm">{bid.toUpperCase()}</strong>
                <span className={cn(
                  "text-[10px] rounded-full px-2 py-0.5 font-bold",
                  info.aggregate_verdict === "QUALIFIED" ? "bg-leaf-100 text-leaf-800"
                  : info.aggregate_verdict === "DISQUALIFIED" ? "bg-red-100 text-red-800"
                  : "bg-amber-100 text-amber-900",
                )}>{info.aggregate_verdict}</span>
              </div>
              <div className="text-[10px] text-ink-500 mt-1">
                {info.total_findings} finding(s) across {info.validators?.length || 14} validators
              </div>
            </li>
          ))}
        </ul>
      </div>

      <div className="flex justify-between mt-4">
        <Link href="/module3" className="text-xs text-ink-500 hover:text-ink-900">← Module 3 overview</Link>
        <button onClick={onRestart} className="rounded-md bg-ink-900 text-white px-4 py-2 text-xs font-semibold hover:bg-ink-700">
          Start new evaluation
        </button>
      </div>
    </div>
  );
}


// ─── Helpers ──────────────────────────────────────────────────────────


function FooterNav({ back, next, nextLabel, nextIcon, nextDisabled }: any) {
  return (
    <div className="flex justify-between mt-4">
      <button onClick={back} className="text-xs text-ink-500 hover:text-ink-900 inline-flex items-center gap-1">
        <ChevronLeft className="h-3 w-3" /> Back
      </button>
      <button onClick={next} disabled={nextDisabled}
        className="rounded-md bg-ink-900 text-white px-4 py-2 text-xs font-semibold hover:bg-ink-700 disabled:opacity-50 inline-flex items-center gap-1.5">
        {nextLabel} {nextIcon || <ChevronRight className="h-3.5 w-3.5" />}
      </button>
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
