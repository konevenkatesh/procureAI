"use client";

/**
 * JobRunner — single-button POST-and-poll widget.
 *
 * Props:
 *   endpoint   — Next.js API route to POST (e.g. "/api/m1/draft")
 *   payload    — request body. Either a static object or a function
 *                that returns one (so module pages with forms can
 *                supply form state lazily).
 *   label      — button label when idle ("Generate Draft")
 *   runningLabel — label while QUEUED/RUNNING ("Drafting…")
 *   onDone     — optional callback when status reaches DONE/ERROR;
 *                receives the full job row.
 *
 * Polls /api/jobs/{module}/{job_id} every 2 seconds. Stops on done.
 */
import { useCallback, useEffect, useRef, useState } from "react";

interface JobPayload {
  tender_id?: string;
  params?: Record<string, any>;
}

interface JobState {
  job_id?: string;
  module?: string;
  status?: string;       // QUEUED | RUNNING | DONE | ERROR | COMPLETED_INLINE
  started_at?: string;
  finished_at?: string;
  result?: any;
  error?: string;
  done?: boolean;
}

interface Props {
  endpoint: string;
  payload: JobPayload | (() => JobPayload);
  label: string;
  runningLabel?: string;
  className?: string;
  onDone?: (job: JobState) => void;
}

export default function JobRunner({
  endpoint, payload, label,
  runningLabel = "Running…",
  className = "",
  onDone,
}: Props) {
  const [state, setState] = useState<JobState | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPoll = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  useEffect(() => () => stopPoll(), [stopPoll]);

  const startPoll = useCallback(
    (pollUrl: string) => {
      stopPoll();
      pollRef.current = setInterval(async () => {
        try {
          const r = await fetch(pollUrl, { cache: "no-store" });
          if (!r.ok) {
            setError(`Poll failed: HTTP ${r.status}`);
            stopPoll();
            return;
          }
          const j: JobState = await r.json();
          setState(j);
          if (j.done) {
            stopPoll();
            onDone?.(j);
          }
        } catch (e: any) {
          setError(`Poll error: ${e?.message || e}`);
          stopPoll();
        }
      }, 2000);
    },
    [onDone, stopPoll],
  );

  const handleClick = async () => {
    setError(null);
    setState(null);
    setSubmitting(true);
    try {
      const body = typeof payload === "function" ? payload() : payload;
      const r = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body || {}),
      });
      const j = await r.json();
      if (!r.ok) {
        setError(j?.error || `HTTP ${r.status}`);
        setSubmitting(false);
        return;
      }
      setState({ job_id: j.job_id, module: j.module, status: j.status });
      if (j.poll_url) startPoll(j.poll_url);
    } catch (e: any) {
      setError(e?.message || "submit failed");
    } finally {
      setSubmitting(false);
    }
  };

  const isRunning =
    !!state &&
    !state.done &&
    state.status !== "ERROR" &&
    !!state.job_id;

  return (
    <div className={className}>
      <button
        onClick={handleClick}
        disabled={submitting || isRunning}
        className={
          "inline-flex items-center gap-2 rounded-md px-4 py-2 text-sm font-semibold transition-colors " +
          (submitting || isRunning
            ? "bg-mist-100 text-ink-500 cursor-not-allowed"
            : "bg-saffron-700 text-white hover:bg-saffron-800")
        }
      >
        {submitting ? "Submitting…" : isRunning ? runningLabel : label}
      </button>
      {error && (
        <div className="mt-3 text-sm text-red-700">⚠ {error}</div>
      )}
      {state && <JobStatusCard state={state} />}
    </div>
  );
}

function JobStatusCard({ state }: { state: JobState }) {
  const status = state.status || "?";
  const tone =
    status === "DONE" || status === "COMPLETED_INLINE"
      ? "bg-green-50 border-green-200 text-green-900"
      : status === "ERROR"
        ? "bg-red-50 border-red-200 text-red-900"
        : "bg-mist-50 border-mist-200 text-ink-900";
  return (
    <div className={`mt-3 rounded-md border px-3 py-2 text-sm ${tone}`}>
      <div className="flex items-center justify-between">
        <div className="font-mono text-xs opacity-70">
          job_id: {state.job_id?.slice(0, 8)}…
        </div>
        <div className="font-bold tracking-wider">{status}</div>
      </div>
      {state.error && (
        <div className="mt-2 text-xs">error: {state.error}</div>
      )}
      {state.result && (
        <details className="mt-2">
          <summary className="cursor-pointer text-xs font-semibold opacity-80">
            result ({Object.keys(state.result).length} fields)
          </summary>
          <pre className="mt-2 overflow-x-auto text-[11px] leading-tight">
            {JSON.stringify(state.result, null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
}
