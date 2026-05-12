"use client";

import type { DraftFormState } from "@/lib/draft-form-state";
import { formatINR } from "@/lib/inr-words";
import { CheckCircle2 } from "lucide-react";

interface Props {
  state: DraftFormState;
}

export default function Step7_Review({ state }: Props) {
  return (
    <div className="space-y-4">
      <h2 className="text-lg font-bold text-ink-900 mb-1">Step 7 — Review & Generate</h2>
      <p className="text-xs text-ink-500 mb-4">
        Review the eGP-format summary below. Click <strong>Generate Tender Draft</strong> to invoke
        the 12-node LangGraph workflow.
      </p>

      <SummaryCard title="Authority">
        <KV k="Department">{state.enquiry_particulars.department_name}</KV>
        <KV k="Circle / Division">{state.enquiry_particulars.circle_division}</KV>
        <KV k="Officer">{state.enquiry_particulars.officer_inviting_bids}</KV>
        <KV k="Project">{state.enquiry_particulars.name_of_project}</KV>
        <KV k="Name of Work" wide>{state.enquiry_particulars.name_of_work}</KV>
        <KV k="Email">{state.enquiry_particulars.email}</KV>
        <KV k="Contact">{state.enquiry_particulars.contact_details}</KV>
        <KV k="Address" wide>{state.enquiry_particulars.address}</KV>
      </SummaryCard>

      <SummaryCard title="Classification">
        <KV k="Category">{state.classification.tender_category}</KV>
        <KV k="Type of Work">{state.classification.type_of_work}</KV>
        <KV k="Tender Type">{state.classification.tender_type}</KV>
        <KV k="Bidding Type">{state.classification.bidding_type}</KV>
        <KV k="Form of Contract">{state.classification.form_of_contract}</KV>
        <KV k="Consortium / JV">{state.classification.consortium_joint_venture}</KV>
        <KV k="Bid Call Numbers">{state.classification.bid_call_numbers}</KV>
      </SummaryCard>

      <SummaryCard title="Financial">
        <KV k="ECV" wide>
          <strong className="text-ink-900">{formatINR(state.financial.estimated_contract_value_inr)}</strong>
          <br />
          <em className="text-xs text-ink-500">{state.financial.estimated_contract_value_words}</em>
        </KV>
        <KV k="Period of Completion">{state.financial.period_of_completion_months} months</KV>
        <KV k="Bid Validity">{state.financial.bid_validity_days} days</KV>
        <KV k="Bid Security">
          {state.financial.bid_security_percent}% = {formatINR(state.financial.bid_security_inr)}
        </KV>
        <KV k="Mode of Payment">{state.financial.mode_of_payment}</KV>
        <KV k="Transaction Fee">{formatINR(state.financial.transaction_fee_inr)}</KV>
      </SummaryCard>

      <SummaryCard title="Geography">
        <KV k="State">{state.geography.state}</KV>
        <KV k="District">{state.geography.district}</KV>
        <KV k="Mandal">{state.geography.mandal}</KV>
        <KV k="Assembly">{state.geography.assembly}</KV>
        <KV k="Parliament">{state.geography.parliament}</KV>
      </SummaryCard>

      <SummaryCard title="Evaluation">
        <KV k="Type">{state.evaluation.evaluation_type}</KV>
        <KV k="Criteria">{state.evaluation.evaluation_criteria}</KV>
        <KV k="Display Rank">{state.evaluation.display_rank}</KV>
      </SummaryCard>

      <SummaryCard title={`Documents & Dates (${state.documents.length} docs)`}>
        <KV k="Start" wide>{new Date(state.dates.start_date).toLocaleString("en-IN")}</KV>
        <KV k="End" wide>{new Date(state.dates.end_date).toLocaleString("en-IN")}</KV>
        <KV k="Closing" wide>
          <strong className="text-ink-900">{new Date(state.dates.closing_date).toLocaleString("en-IN")}</strong>
        </KV>
      </SummaryCard>

      <SummaryCard title="BoQ Skeleton">
        {state.boq_skeleton && state.boq_skeleton.length > 0 ? (
          <>
            <KV k="File">{state.boq_skeleton_filename || "(uploaded)"}</KV>
            <KV k="Rows to enrich">
              <strong className="text-ink-900">{state.boq_skeleton.length}</strong> items
              <span className="text-ink-500"> — AI will write spec + citations</span>
            </KV>
          </>
        ) : (
          <KV k="Skeleton" wide>
            <em className="text-ink-500">
              Not provided. BoQ will be empty in the AI draft; officer can add it later in the
              Technical gate.
            </em>
          </KV>
        )}
      </SummaryCard>

      <div className="rounded-md bg-saffron-50 border border-saffron-500 p-4 text-sm text-ink-700">
        <div className="flex items-start gap-2">
          <CheckCircle2 className="h-5 w-5 text-saffron-700 mt-0.5 shrink-0" />
          <div>
            <strong className="text-ink-900">Ready to generate.</strong> The 12-node LangGraph
            workflow will compose the full Bid Document, ITB, BoQ skeleton, eligibility criteria,
            and legal terms. You'll watch sections fill in live. Total time: typically 60–90s.
          </div>
        </div>
      </div>
    </div>
  );
}

function SummaryCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-md border border-mist-200 bg-white overflow-hidden">
      <div className="bg-mist-50 px-4 py-2 text-xs font-bold text-ink-700 border-b border-mist-200">
        {title}
      </div>
      <div className="p-4 grid grid-cols-1 md:grid-cols-2 gap-2 text-sm">
        {children}
      </div>
    </div>
  );
}

function KV({ k, children, wide }: { k: string; children: React.ReactNode; wide?: boolean }) {
  return (
    <div className={wide ? "md:col-span-2" : undefined}>
      <div className="text-[10px] font-semibold text-ink-500 uppercase tracking-wide">{k}</div>
      <div className="text-ink-900 text-sm">{children || <em className="text-ink-500">—</em>}</div>
    </div>
  );
}
