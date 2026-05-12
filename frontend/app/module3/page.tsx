// Placeholder — Sub-block 5 builds full Module 3 view
import Link from "next/link";
import { Card, CardContent, CardHeader, CardDescription, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { BarChart3, ArrowRight, Sparkles } from "lucide-react";
import PipelineLauncher from "@/components/PipelineLauncher";

const TENDERS = [
  { id: "tender_synth_kurnool", name: "District Hospital, Kurnool",  ecv: "₹85.00 cr",   bidders: 9, effL1: "B9 JV @ ₹79.90 cr" },
  { id: "tender_synth_ja",      name: "AP Judicial Academy",         ecv: "₹125.50 cr",  bidders: 9, effL1: "B9 JV @ ₹117.97 cr" },
  { id: "tender_synth_hc",      name: "AP High Court Complex",       ecv: "₹365.16 cr",  bidders: 9, effL1: "B9 JV @ ₹343.25 cr" },
];

export default function Module3IndexPage() {
  return (
    <div className="p-8 md:p-10">
      <header className="mb-8">
        <div className="flex items-center gap-2 text-xs font-bold text-saffron-700 tracking-widest mb-2">
          <BarChart3 className="h-4 w-4" /> MODULE 3 · EVALUATOR
        </div>
        <h1 className="text-3xl font-bold tracking-tight text-ink-900 mb-2">
          Bid Evaluation Pipeline
        </h1>
        <p className="text-ink-500 max-w-3xl">
          13 Tier-2 validators on 9 bidders × 3 tenders = 351 BidEvaluationFindings.
          EligibilityMatrix aggregator + TenderRanking + CrossBidAnomalyDetector + ComparativeStatement
          chain produces a full audit-defensible evaluation outcome per tender.
        </p>
      </header>

      <section>
        <h2 className="text-lg font-bold text-ink-900 mb-3">Tenders evaluated</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {TENDERS.map((t) => (
            <Card key={t.id} className="hover:shadow-elev transition-shadow">
              <CardHeader>
                <CardTitle className="text-base">{t.name}</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-sm space-y-2">
                  <div className="flex justify-between">
                    <span className="text-ink-500">ECV</span>
                    <span className="font-semibold text-ink-900">{t.ecv}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-ink-500">Bidders</span>
                    <span className="font-semibold text-ink-900">{t.bidders}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-ink-500">Effective L1</span>
                    <Badge variant="qualified" className="text-xs">{t.effL1}</Badge>
                  </div>
                </div>
                <Link
                  href={`/module3/${t.id}`}
                  className="inline-flex items-center gap-1.5 text-sm font-semibold text-ink-900 hover:text-saffron-700 mt-4"
                >
                  Open evaluation report <ArrowRight className="h-4 w-4" />
                </Link>
              </CardContent>
            </Card>
          ))}
        </div>
      </section>

      <section className="mt-8">
        <Card className="bg-mist-50/40">
          <CardHeader>
            <div className="flex items-start gap-3">
              <Sparkles className="h-5 w-5 text-saffron-700 mt-1" />
              <div>
                <CardTitle className="text-base">Re-run Evaluation Pipeline</CardTitle>
                <CardDescription>
                  Submit a tender to the <span className="font-semibold text-ink-700">m3-evaluator</span> Cloud
                  Run service (asia-south1). The pipeline runs the 14 Tier-2 bid evaluators
                  (Supabase-only, no Qdrant) and updates BidEvaluationFindings. Today's deploy
                  returns the evaluator inventory; full execution lands in a follow-up commit
                  per the GCP-2 NOT-IN-SCOPE rule.
                </CardDescription>
              </div>
            </div>
          </CardHeader>
          <CardContent>
            <PipelineLauncher
              endpoint="/api/m3/evaluate"
              actionLabel="Run Evaluation"
              runningLabel="Evaluating…"
              tenders={TENDERS.map((t) => ({ id: t.id, name: t.name }))}
            />
          </CardContent>
        </Card>
      </section>
    </div>
  );
}
