import { FilePen, Shield, BarChart3, MessageSquare, ArrowRight } from "lucide-react";
import { cn } from "@/lib/utils";

const STEPS = [
  {
    icon: FilePen,
    title: "Draft",
    subtitle: "Tender Document Generation",
    color: "from-saffron-500 to-saffron-700",
    note: "Phase 1 complete",
    detail: "Generates AP-compliant RFPs from rule library; 3 demo tenders shipped (Kurnool / JA / HC)",
  },
  {
    icon: Shield,
    title: "Validate",
    subtitle: "RFP Compliance Check",
    color: "from-blue-500 to-blue-700",
    note: "154 findings",
    detail: "Validates draft tenders against AP-State + Central + CVC rule layers; emits ValidationFindings",
  },
  {
    icon: BarChart3,
    title: "Evaluate",
    subtitle: "Bid Evaluation Pipeline",
    color: "from-leaf-500 to-leaf-700",
    note: "351 findings",
    detail: "13 Tier-2 validators on 9 bidders; 27 EligibilityMatrix + 3 TenderRanking + cartel/ALB detection",
  },
  {
    icon: MessageSquare,
    title: "Communicate",
    subtitle: "Bidder & Officer Communication",
    color: "from-purple-500 to-purple-700",
    note: "75 communications",
    detail: "10 communication types; bilingual EN+TE for bidder-facing via Sarvam-M; internal vigilance referrals",
  },
];

export function WorkflowDiagram() {
  return (
    <div className="grid grid-cols-1 md:grid-cols-4 gap-3 items-stretch">
      {STEPS.map((s, i) => {
        const Icon = s.icon;
        return (
          <div
            key={s.title}
            className="relative bg-white rounded-lg border border-mist-200 shadow-card p-5 flex flex-col"
          >
            <div className="flex items-center gap-3 mb-3">
              <div
                className={cn(
                  "h-10 w-10 rounded-md bg-gradient-to-br text-white flex items-center justify-center shrink-0",
                  s.color,
                )}
              >
                <Icon className="h-5 w-5" />
              </div>
              <div className="flex-1 min-w-0">
                <div className="text-xs text-ink-500 font-medium">Module {i + 1}</div>
                <div className="text-base font-semibold text-ink-900">{s.title}</div>
              </div>
              {i < STEPS.length - 1 && (
                <ArrowRight className="h-4 w-4 text-mist-200 absolute -right-2 top-1/2 -translate-y-1/2 z-10 hidden md:block" />
              )}
            </div>
            <div className="text-sm text-ink-700 font-medium mb-1">{s.subtitle}</div>
            <div className="text-xs text-ink-500 mb-3 flex-1">{s.detail}</div>
            <div className="text-xs font-semibold text-ink-900">{s.note}</div>
          </div>
        );
      })}
    </div>
  );
}
