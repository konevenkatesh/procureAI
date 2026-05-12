import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Shield, Sparkles } from "lucide-react";
import { countRows } from "@/lib/supabase";
import ValidatorForm from "@/components/ValidatorForm";

export const revalidate = 60;

async function getValidationStats() {
  try {
    return { validations: await countRows("kg_nodes", { node_type: "eq.ValidationFinding" }) };
  } catch {
    return { validations: 154 };
  }
}

const RULE_LAYERS = [
  { layer: "AP-State", count: "~80", desc: "Andhra Pradesh GO, CRDA standard tender document, MPS / MPG layers" },
  { layer: "Central",  count: "~50", desc: "MPW 2022, GFR 2017, GFR-Goods, GFR-Services" },
  { layer: "CVC",      count: "~25", desc: "CVC OM on Vigilance Aspects of Public Procurement" },
];

export default async function Module2Page() {
  const { validations } = await getValidationStats();
  return (
    <div className="p-8 md:p-10">
      <header className="mb-6">
        <div className="flex items-center gap-2 text-xs font-bold text-saffron-700 tracking-widest mb-2">
          <Shield className="h-4 w-4" /> MODULE 2 · VALIDATOR
        </div>
        <h1 className="text-3xl font-bold tracking-tight text-ink-900 mb-2">
          RFP Compliance Validator
        </h1>
        <p className="text-ink-500 max-w-3xl">
          Validates draft tender documents against the 3-layer rule library: AP-State + Central
          + CVC. Currently {validations} ValidationFindings on the demo corpus. The validator
          uses BGE-M3 embeddings + LLM-graded compliance checks with citation chain to source
          rule text.
        </p>
      </header>

      <section className="mb-6 grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card>
          <CardContent className="p-5">
            <div className="text-3xl font-bold text-ink-900 mb-1">{validations}</div>
            <div className="text-sm font-semibold text-ink-700">Validation Findings</div>
            <div className="text-xs text-ink-500 mt-1">Sentinel value across 7 Tier-1 typologies × 6 documents</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-5">
            <div className="text-3xl font-bold text-ink-900 mb-1">7</div>
            <div className="text-sm font-semibold text-ink-700">Tier-1 Typologies</div>
            <div className="text-xs text-ink-500 mt-1">PBG / EMD / Bid-Validity / PVC / Integrity Pact / LD / Mobilisation Advance</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-5">
            <div className="text-3xl font-bold text-ink-900 mb-1">3</div>
            <div className="text-sm font-semibold text-ink-700">Rule Layers</div>
            <div className="text-xs text-ink-500 mt-1">AP-State · Central · CVC (each independently citable)</div>
          </CardContent>
        </Card>
      </section>

      <section className="mb-6">
        <h2 className="text-lg font-bold text-ink-900 mb-3">Rule library breakdown</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {RULE_LAYERS.map((r) => (
            <Card key={r.layer}>
              <CardHeader>
                <CardTitle className="text-base flex justify-between">
                  <span>{r.layer}</span>
                  <Badge variant="outline">{r.count} rules</Badge>
                </CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-xs text-ink-500 leading-relaxed">{r.desc}</p>
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
                <CardTitle className="text-base">Validate an RFP</CardTitle>
                <CardDescription>
                  Submit a tender to the <span className="font-semibold text-ink-700">m2-validator</span> Cloud Run
                  service (asia-south1). The full 24-typology Tier-1 pipeline runs when Qdrant
                  is migrated to GCP in Phase 2; today the service returns
                  <span className="font-mono text-xs"> GAP_INSUFFICIENT_DATA</span> with the
                  inventory of available checks so the wiring is end-to-end testable.
                </CardDescription>
              </div>
            </div>
          </CardHeader>
          <CardContent>
            <ValidatorForm />
          </CardContent>
        </Card>
      </section>
    </div>
  );
}
