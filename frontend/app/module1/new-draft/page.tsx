"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import Link from "next/link";
import { Card, CardContent } from "@/components/ui/card";
import { useDraftForm, validateStep } from "@/lib/draft-form-state";
import { ArrowLeft, ArrowRight, RefreshCw, Sparkles, CheckCircle2 } from "lucide-react";
import { cn } from "@/lib/utils";
import Step1_Authority from "./steps/Step1_Authority";
import Step2_Classification from "./steps/Step2_Classification";
import Step3_Financial from "./steps/Step3_Financial";
import Step4_Geography from "./steps/Step4_Geography";
import Step5_Evaluation from "./steps/Step5_Evaluation";
import Step6_DocumentsDates from "./steps/Step6_DocumentsDates";
import Step7_Review from "./steps/Step7_Review";

const STEP_LABELS = [
  "Authority",
  "Classification",
  "Financial",
  "Geography",
  "Evaluation",
  "Documents & Dates",
  "Review & Generate",
];

export default function NewDraftWizard() {
  const router = useRouter();
  const { state, setState, step, setStep, update, updateFinancialWithDerivations, reset } =
    useDraftForm();
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);

  if (!state) {
    return (
      <div className="p-10 text-ink-500 text-sm">Loading wizard…</div>
    );
  }

  const goNext = () => {
    const { ok, errors } = validateStep(step, state);
    setErrors(errors);
    if (!ok) return;
    if (step < 7) setStep(step + 1);
  };

  const goBack = () => {
    if (step > 1) setStep(step - 1);
    setErrors({});
  };

  const handleGenerate = async () => {
    const { ok, errors: stepErrors } = validateStep(7, state);
    setErrors(stepErrors);
    if (!ok) return;
    setSubmitting(true);
    try {
      const res = await fetch("/api/m1/draft/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          draft_id: state.draft_id,
          initiator_role: "DEALING_OFFICER",
          initiator_id: "demo_dealing_officer",
          initial_payload: {
            enquiry_particulars: state.enquiry_particulars,
            classification: state.classification,
            financial: state.financial,
            geography: state.geography,
            evaluation: state.evaluation,
            documents: state.documents,
            dates: state.dates,
            enquiry_forms: state.enquiry_forms,
          },
          // R7.7 — optional BoQ skeleton uploaded in Step 6
          boq_skeleton: state.boq_skeleton,
          boq_skeleton_filename: state.boq_skeleton_filename,
        }),
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(`POST /api/m1/draft/start failed: ${res.status} ${text.slice(0, 200)}`);
      }
      const data = await res.json();
      // Clear localStorage WIP after successful submission
      reset();
      router.push(`/module1/draft/${data.draft_id || state.draft_id}/generate`);
    } catch (e: any) {
      setErrors({ "_submit": e.message || String(e) });
      setSubmitting(false);
    }
  };

  const currentStepComponent = () => {
    const stepProps = { state, update, updateFinancialWithDerivations, errors };
    switch (step) {
      case 1: return <Step1_Authority {...stepProps} />;
      case 2: return <Step2_Classification {...stepProps} />;
      case 3: return <Step3_Financial {...stepProps} />;
      case 4: return <Step4_Geography {...stepProps} />;
      case 5: return <Step5_Evaluation {...stepProps} />;
      case 6: return <Step6_DocumentsDates {...stepProps} />;
      case 7: return <Step7_Review state={state} />;
      default: return null;
    }
  };

  return (
    <div className="p-8 md:p-10 max-w-5xl">
      <Link
        href="/module1"
        className="inline-flex items-center gap-1.5 text-xs font-semibold text-ink-500 hover:text-ink-900 mb-4"
      >
        <ArrowLeft className="h-3.5 w-3.5" /> Back to Module 1
      </Link>

      <header className="mb-6">
        <div className="flex items-center gap-2 text-xs font-bold text-saffron-700 tracking-widest mb-2">
          <Sparkles className="h-4 w-4" /> MODULE 1 · NEW TENDER DRAFT
        </div>
        <h1 className="text-3xl font-bold tracking-tight text-ink-900 mb-2">
          7-Step Tender Initiation
        </h1>
        <p className="text-ink-500 max-w-3xl">
          Dealing Officer wizard. Fill the 7 steps below; on Generate the 12-node LangGraph drafter
          composes the full eGP-format Bid Document while you watch sections fill in live. Form
          state is preserved in localStorage if you close this tab.
        </p>
      </header>

      {/* Step indicator */}
      <ol className="grid grid-cols-7 gap-1 mb-6">
        {STEP_LABELS.map((label, i) => {
          const n = i + 1;
          const active = n === step;
          const done = n < step;
          return (
            <li
              key={label}
              className={cn(
                "flex flex-col items-center gap-1 cursor-pointer select-none rounded-md p-2 text-center text-[10px] transition-colors",
                active ? "bg-ink-900 text-white" :
                done ? "bg-leaf-100 text-leaf-700 hover:bg-leaf-200" :
                "bg-mist-100 text-ink-500 hover:bg-mist-200",
              )}
              onClick={() => done && setStep(n)}
            >
              <div className="font-bold">
                {done ? <CheckCircle2 className="h-3.5 w-3.5 inline" /> : `Step ${n}`}
              </div>
              <div className="font-medium leading-tight">{label}</div>
            </li>
          );
        })}
      </ol>

      <Card>
        <CardContent className="p-6">
          {currentStepComponent()}
        </CardContent>
      </Card>

      {errors._submit && (
        <div className="mt-3 rounded-md bg-red-50 border border-red-300 p-3 text-sm text-red-700">
          {errors._submit}
        </div>
      )}

      {/* Footer navigation */}
      <div className="mt-6 flex items-center justify-between">
        <button
          onClick={goBack}
          disabled={step === 1}
          className={cn(
            "rounded-md px-4 py-2 text-sm font-semibold border transition-colors",
            "bg-white text-ink-700 border-mist-200 hover:bg-mist-50",
            step === 1 && "opacity-40 cursor-not-allowed",
          )}
        >
          <ArrowLeft className="inline h-3.5 w-3.5 mr-1.5" /> Back
        </button>

        <button
          type="button"
          onClick={() => {
            if (confirm("Discard wizard data and start fresh?")) reset();
          }}
          className="text-xs text-ink-500 hover:text-red-700 inline-flex items-center gap-1"
        >
          <RefreshCw className="h-3 w-3" /> Reset
        </button>

        {step < 7 ? (
          <button
            onClick={goNext}
            className="rounded-md bg-ink-900 hover:bg-ink-700 px-5 py-2 text-sm font-semibold text-white transition-colors"
          >
            Next <ArrowRight className="inline h-3.5 w-3.5 ml-1.5" />
          </button>
        ) : (
          <button
            onClick={handleGenerate}
            disabled={submitting}
            className={cn(
              "rounded-md px-5 py-2 text-sm font-semibold text-white transition-colors inline-flex items-center gap-2",
              submitting ? "bg-mist-200 cursor-not-allowed" : "bg-saffron-500 hover:bg-saffron-700",
            )}
          >
            <Sparkles className="h-4 w-4" />
            {submitting ? "Submitting…" : "Generate Tender Draft"}
          </button>
        )}
      </div>
    </div>
  );
}
