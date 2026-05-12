"use client";

import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { formatINR } from "@/lib/inr-words";
import type { TenderDraftState } from "@/types/m1-drafter";

interface Props {
  state: Partial<TenderDraftState>;
  workflowComplete: boolean;
  className?: string;
}

/**
 * eGP-format live structured view. Each section is a card that
 * fills in as SSE events arrive. Empty fields show subtle skeleton
 * placeholders during AI generation.
 */
export function EGPLiveView({ state, workflowComplete, className }: Props) {
  const ep = state.enquiry_particulars;
  const cl = state.classification;
  const fin = state.financial;
  const geo = state.geography;
  const ev = state.evaluation;
  const dates = state.dates;
  const docs = state.documents || [];
  const forms = state.enquiry_forms || [];
  const gt = state.general_terms || { eligibility: "", technical: "", legal: "", bid_procedure: "" };
  const boq = state.boq || [];

  return (
    <div className={cn("space-y-6", className)}>
      {/* Section: Current Tender Details */}
      <Section title="Current Tender Details">
        <KVGrid>
          <KV k="Tender ID">{state.tender_id || <Skel />}</KV>
          <KV k="Tender Notice Number">{state.tender_notice_number || <Skel />}</KV>
          <KV k="Name of Work" wide>{ep?.name_of_work || <Skel />}</KV>
          <KV k="Tender Category">{cl?.tender_category || <Skel />}</KV>
          <KV k="Tender Type">{cl?.tender_type || <Skel />}</KV>
          <KV k="Estimated Contract Value">
            {fin?.estimated_contract_value_inr ? formatINR(fin.estimated_contract_value_inr) : <Skel />}
          </KV>
          <KV k="Submission Closing">{dates?.closing_date ? formatDate(dates.closing_date) : <Skel />}</KV>
          <KV k="Evaluation Type">{ev?.evaluation_type || <Skel />}</KV>
        </KVGrid>
      </Section>

      {/* Section: Enquiry Particulars */}
      <Section title="Enquiry Particulars">
        <KVGrid>
          <KV k="Department">{ep?.department_name || <Skel />}</KV>
          <KV k="Circle / Division">{ep?.circle_division || <Skel />}</KV>
          <KV k="Project">{ep?.name_of_project || <Skel />}</KV>
          <KV k="Type of Work">{cl?.type_of_work || <Skel />}</KV>
          <KV k="Bidding Type">{cl?.bidding_type || <Skel />}</KV>
          <KV k="Bid Call Numbers">{cl?.bid_call_numbers ?? <Skel />}</KV>
          <KV k="ECV (Words)" wide>
            {fin?.estimated_contract_value_words ? (
              <em className="text-ink-700">{fin.estimated_contract_value_words}</em>
            ) : <Skel />}
          </KV>
          <KV k="Period of Completion">
            {fin?.period_of_completion_months != null ? `${fin.period_of_completion_months} months` : <Skel />}
          </KV>
          <KV k="Form of Contract">{cl?.form_of_contract || <Skel />}</KV>
          <KV k="Consortium / JV">{cl?.consortium_joint_venture || <Skel />}</KV>
          <KV k="Currency">{fin?.currency_type || <Skel />}</KV>
        </KVGrid>
      </Section>

      {/* Section: Tender Dates */}
      <Section title="Tender Dates">
        <KVGrid>
          <KV k="Start Date">{dates?.start_date ? formatDate(dates.start_date) : <Skel />}</KV>
          <KV k="End Date">{dates?.end_date ? formatDate(dates.end_date) : <Skel />}</KV>
          <KV k="Closing Date">{dates?.closing_date ? formatDate(dates.closing_date) : <Skel />}</KV>
          <KV k="Bid Validity">
            {fin?.bid_validity_days != null ? `${fin.bid_validity_days} days` : <Skel />}
          </KV>
          <KV k="Display Rank">{ev?.display_rank || <Skel />}</KV>
        </KVGrid>
      </Section>

      {/* Section: Tender Inviting Authority */}
      <Section title="Tender Inviting Authority Particulars">
        <KVGrid>
          <KV k="Officer Inviting Bids">{ep?.officer_inviting_bids || <Skel />}</KV>
          <KV k="Bid Opening Authority">{ep?.bid_opening_authority || <Skel />}</KV>
          <KV k="Address" wide>{ep?.address || <Skel />}</KV>
          <KV k="Contact">{ep?.contact_details || <Skel />}</KV>
          <KV k="Email">{ep?.email || <Skel />}</KV>
        </KVGrid>
      </Section>

      {/* Section: Bid Security */}
      <Section title="Bid Security Details">
        <KVGrid>
          <KV k="Bid Security (INR)">
            {fin?.bid_security_inr != null ? `Rs.${fin.bid_security_inr}.00` : <Skel />}
          </KV>
          <KV k="In Favour Of">{fin?.bid_security_in_favour_of || <Skel />}</KV>
          <KV k="Mode of Payment" wide>{fin?.mode_of_payment || <Skel />}</KV>
        </KVGrid>
      </Section>

      {/* Section: Required Documents */}
      <Section title={`Required Tender Documents (${docs.length})`}>
        {docs.length === 0 ? (
          <Skel block />
        ) : (
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-ink-700 border-b border-mist-200">
                <th className="px-2 py-1 w-10">#</th>
                <th className="px-2 py-1">Document</th>
                <th className="px-2 py-1 w-20">Stage</th>
                <th className="px-2 py-1 w-24">Type</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-mist-100">
              {docs.map((d: any) => (
                <tr key={d.s_no}>
                  <td className="px-2 py-1 tabular-nums">{d.s_no}</td>
                  <td className="px-2 py-1">{d.document_name}</td>
                  <td className="px-2 py-1"><code className="text-[10px]">{d.stage}</code></td>
                  <td className="px-2 py-1">{d.document_type}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>

      {/* Section: General Terms — Eligibility */}
      <Section title="General Terms and Conditions / Eligibility" badge={gt.eligibility ? "FILLED" : undefined}>
        <StreamingPre text={gt.eligibility} />
      </Section>

      {/* Section: General Technical */}
      <Section title="General Technical Terms (Procedure)" badge={gt.technical ? "FILLED" : undefined}>
        <StreamingPre text={gt.technical} />
      </Section>

      {/* Section: Legal */}
      <Section title="Legal Terms & Conditions" badge={gt.legal ? "FILLED" : undefined}>
        <StreamingPre text={gt.legal} />
      </Section>

      {/* Section: Bid Procedure */}
      <Section title="Procedure for Bid Submission" badge={gt.bid_procedure ? "FILLED" : undefined}>
        <StreamingPre text={gt.bid_procedure} />
      </Section>

      {/* Section: Geography */}
      <Section title="Geographical Particulars">
        <KVGrid>
          <KV k="State">{geo?.state || <Skel />}</KV>
          <KV k="District">{geo?.district || <Skel />}</KV>
          <KV k="Mandal">{geo?.mandal || <Skel />}</KV>
          <KV k="Assembly">{geo?.assembly || <Skel />}</KV>
          <KV k="Parliament">{geo?.parliament || <Skel />}</KV>
        </KVGrid>
      </Section>

      {/* Section: BoQ */}
      <Section title={`Bill of Quantities — ${boq.length} items`} badge={boq.length > 0 ? `${boq.length} rows` : undefined}>
        {boq.length === 0 ? (
          <Skel block />
        ) : (
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-ink-700 border-b border-mist-200">
                <th className="px-2 py-1 w-10">#</th>
                <th className="px-2 py-1">Item</th>
                <th className="px-2 py-1 w-16 text-right">Qty</th>
                <th className="px-2 py-1 w-20">Unit</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-mist-100">
              {boq.map((r: any) => (
                <tr key={r.s_no} className="hover:bg-mist-50/40">
                  <td className="px-2 py-1 tabular-nums">{r.s_no}</td>
                  <td className="px-2 py-1">{r.item}</td>
                  <td className="px-2 py-1 text-right tabular-nums">{r.qty}</td>
                  <td className="px-2 py-1">{r.unit}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>

      {/* Section: Enquiry Forms */}
      <Section title="Enquiry Forms — Stage Details">
        {forms.length === 0 ? (
          <Skel block />
        ) : (
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-ink-700 border-b border-mist-200">
                <th className="px-2 py-1">Stage</th>
                <th className="px-2 py-1">Form Name</th>
                <th className="px-2 py-1">Type</th>
                <th className="px-2 py-1">Supporting Doc</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-mist-100">
              {forms.map((f: any, i: number) => (
                <tr key={i}>
                  <td className="px-2 py-1">{f.stage}</td>
                  <td className="px-2 py-1">{f.form_name}</td>
                  <td className="px-2 py-1">{f.type_of_form}</td>
                  <td className="px-2 py-1">{f.supporting_document_required}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>
    </div>
  );
}

// ─── Helper components ──────────────────────────────────────────────

function Section({
  title, badge, children,
}: {
  title: string;
  badge?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-mist-200 bg-white shadow-card overflow-hidden">
      <div className="bg-mist-50/80 px-4 py-2 border-b border-mist-200 flex items-center justify-between">
        <div className="text-xs font-bold text-ink-900">{title}</div>
        {badge && <Badge variant="qualified" className="text-[9px]">{badge}</Badge>}
      </div>
      <div className="p-4">{children}</div>
    </div>
  );
}

function KVGrid({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-2 text-sm">
      {children}
    </div>
  );
}

function KV({ k, children, wide }: { k: string; children: React.ReactNode; wide?: boolean }) {
  return (
    <div className={wide ? "md:col-span-2" : undefined}>
      <div className="text-[10px] font-semibold text-ink-500 uppercase tracking-wide">{k}</div>
      <div className="text-ink-900 text-sm">{children}</div>
    </div>
  );
}

function Skel({ block }: { block?: boolean }) {
  return (
    <span
      className={cn(
        "inline-block bg-gradient-to-r from-mist-100 via-mist-200 to-mist-100 bg-[length:200%_100%] animate-[shimmer_1.5s_ease-in-out_infinite] rounded",
        block ? "h-12 w-full" : "h-3 w-32",
      )}
      aria-label="loading"
    />
  );
}

function StreamingPre({ text }: { text: string }) {
  if (!text) {
    return (
      <div className="space-y-1.5">
        <Skel block />
        <Skel block />
      </div>
    );
  }
  return (
    <pre className="whitespace-pre-wrap text-xs text-ink-700 font-sans leading-relaxed">{text}</pre>
  );
}

function formatDate(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString("en-IN", {
      day: "2-digit", month: "2-digit", year: "numeric",
      hour: "2-digit", minute: "2-digit", hour12: true,
    });
  } catch {
    return iso;
  }
}
