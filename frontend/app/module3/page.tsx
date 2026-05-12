// Placeholder — Sub-block 5 builds full Module 3 view
import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { BarChart3, ArrowRight } from "lucide-react";

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
    </div>
  );
}
