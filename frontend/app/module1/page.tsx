import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { FilePen, FileText, Sparkles } from "lucide-react";
import DrafterForm from "@/components/DrafterForm";

const PHASE_1_DRAFTS = [
  { tender_id: "tender_synth_kurnool", name: "District Hospital, Kurnool",  ecv: "₹85.00 cr",  nit: "100/PROC/APIIC/1/2026",       month: "Phase 1 demo" },
  { tender_id: "tender_synth_ja",      name: "AP Judicial Academy",         ecv: "₹125.50 cr", nit: "JA/2026/CW/001",                month: "Phase 1 demo" },
  { tender_id: "tender_synth_hc",      name: "AP High Court Complex",       ecv: "₹365.16 cr", nit: "HC/APCRDA/2026/PROC/001",       month: "Phase 1 demo" },
];

export default function Module1Page() {
  return (
    <div className="p-8 md:p-10">
      <header className="mb-6">
        <div className="flex items-center gap-2 text-xs font-bold text-saffron-700 tracking-widest mb-2">
          <FilePen className="h-4 w-4" /> MODULE 1 · DRAFTER
        </div>
        <h1 className="text-3xl font-bold tracking-tight text-ink-900 mb-2">
          Tender Document Drafter
        </h1>
        <p className="text-ink-500 max-w-3xl">
          Generates AP-compliant Request for Proposal (RFP) documents from the standard rule
          library. Three demo tenders were drafted in Phase 1; the live generation pipeline
          ships in Phase 2.
        </p>
      </header>

      <section className="mb-6">
        <h2 className="text-lg font-bold text-ink-900 mb-3">Phase 1 — 3 drafted tenders</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {PHASE_1_DRAFTS.map((d) => (
            <Card key={d.tender_id}>
              <CardHeader>
                <CardTitle className="text-base flex items-start gap-2">
                  <FileText className="h-4 w-4 mt-1 shrink-0 text-saffron-700" />
                  {d.name}
                </CardTitle>
                <CardDescription>
                  NIT: <code className="text-xs">{d.nit}</code>
                </CardDescription>
              </CardHeader>
              <CardContent>
                <div className="text-sm space-y-1.5">
                  <div className="flex justify-between">
                    <span className="text-ink-500">ECV</span>
                    <span className="font-semibold text-ink-900">{d.ecv}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-ink-500">Status</span>
                    <Badge variant="outline">{d.month}</Badge>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      </section>

      <section>
        <Card className="bg-mist-50/40">
          <CardHeader>
            <div className="flex items-start gap-3">
              <Sparkles className="h-5 w-5 text-saffron-700 mt-1" />
              <div>
                <CardTitle className="text-base">Generate New Draft</CardTitle>
                <CardDescription>
                  Submit a tender spec to the <span className="font-semibold text-ink-700">m1-drafter</span> Cloud Run
                  service (asia-south1). The job is enqueued via Cloud Tasks and the status panel
                  below polls every 2&thinsp;seconds. The full LangGraph drafter pipeline lands in
                  Phase 2; the current backend returns a queued acknowledgement so the
                  end-to-end wiring is verifiable today.
                </CardDescription>
              </div>
            </div>
          </CardHeader>
          <CardContent>
            <DrafterForm />
          </CardContent>
        </Card>
      </section>
    </div>
  );
}
