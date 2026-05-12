"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect } from "react";
import Link from "next/link";
import { useSSEDraftStream } from "@/hooks/useSSEDraftStream";
import { EGPLiveView } from "@/components/m1/EGPLiveView";
import { LangGraphNodeProgress } from "@/components/m1/LangGraphNodeProgress";
import { RoleSwitcher } from "@/components/m1/RoleSwitcher";
import { Sparkles, ArrowLeft, CheckCircle2, ArrowRight } from "lucide-react";

/**
 * Live AI generation view. Opens SSE to the m1-drafter backend; fills
 * the eGP-format template as events arrive; auto-redirects to the
 * review page when workflow_complete.
 */
export default function GenerateDraftPage() {
  const params = useParams() as { draft_id?: string };
  const router = useRouter();
  const draftId = params.draft_id || "";
  const { state, connected } = useSSEDraftStream(draftId);

  // Auto-advance to review page 1.5s after workflow_complete
  useEffect(() => {
    if (state.workflow_complete) {
      const t = setTimeout(() => {
        router.push(`/module1/draft/${draftId}/review`);
      }, 1500);
      return () => clearTimeout(t);
    }
  }, [state.workflow_complete, draftId, router]);

  return (
    <div className="p-6 md:p-8">
      {/* Top bar */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <Link
            href="/module1/new-draft"
            className="inline-flex items-center gap-1.5 text-xs font-semibold text-ink-500 hover:text-ink-900 mb-2"
          >
            <ArrowLeft className="h-3.5 w-3.5" /> Back to wizard
          </Link>
          <div className="flex items-center gap-2 text-xs font-bold text-saffron-700 tracking-widest">
            <Sparkles className="h-4 w-4" /> MODULE 1 · AI GENERATION
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-ink-900 mt-1">
            {state.workflow_complete ? "Draft ready for review" : "Generating tender draft…"}
          </h1>
          <p className="text-xs text-ink-500 mt-1">
            Draft ID: <code>{draftId}</code>
            {state.workflow_complete && (
              <>
                {" · "}<span className="text-leaf-700 font-semibold">Workflow complete</span>
                {state.total_elapsed_ms && (
                  <> in <strong>{(state.total_elapsed_ms / 1000).toFixed(1)}s</strong></>
                )}
                {" · "}redirecting to TECHNICAL gate review…
              </>
            )}
            {!state.workflow_complete && !connected && state.workflow_started && (
              <> · <span className="text-amber-700">stream disconnected; reconnecting…</span></>
            )}
            {!state.workflow_started && (
              <> · <span className="text-ink-500">awaiting first event from backend…</span></>
            )}
          </p>
        </div>
        <RoleSwitcher />
      </div>

      {/* Two-column: live structured view + node progress sidebar */}
      <div className="grid grid-cols-1 lg:grid-cols-[1fr_320px] gap-6">
        <EGPLiveView state={state.draft} workflowComplete={state.workflow_complete} />
        <div className="hidden lg:block">
          <LangGraphNodeProgress
            nodes={state.nodes}
            totalElapsedMs={state.total_elapsed_ms}
          />
        </div>
      </div>

      {/* Workflow complete banner */}
      {state.workflow_complete && (
        <div className="mt-6 rounded-md bg-leaf-50 border border-leaf-500 p-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <CheckCircle2 className="h-6 w-6 text-leaf-700" />
            <div>
              <strong className="text-ink-900">Workflow complete.</strong>
              <span className="text-sm text-ink-700 ml-2">
                The draft is now at the TECHNICAL gate awaiting Senior Engineer review.
              </span>
            </div>
          </div>
          <Link
            href={`/module1/draft/${draftId}/review`}
            className="rounded-md bg-leaf-700 hover:bg-leaf-700/90 px-4 py-2 text-sm font-semibold text-white inline-flex items-center gap-2"
          >
            Open review <ArrowRight className="h-4 w-4" />
          </Link>
        </div>
      )}

      {/* Errors */}
      {state.errors.length > 0 && (
        <div className="mt-4 rounded-md bg-red-50 border border-red-300 p-3 text-sm text-red-700">
          <strong>Workflow errors:</strong>
          <ul className="list-disc ml-5 mt-1">
            {state.errors.map((e, i) => <li key={i}>{e}</li>)}
          </ul>
        </div>
      )}
    </div>
  );
}
