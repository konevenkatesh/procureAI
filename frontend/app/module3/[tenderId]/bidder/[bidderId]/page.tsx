import Link from "next/link";
import { notFound } from "next/navigation";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { fetchAll } from "@/lib/supabase";
import { VERDICT_LABEL, formatCrore } from "@/lib/utils";
import { ArrowLeft, ShieldCheck, ShieldX, AlertTriangle, HelpCircle } from "lucide-react";

export const revalidate = 60;

interface PageProps {
  params: { tenderId: string; bidderId: string };
}

async function getBidderTenderData(tenderId: string, bidderId: string) {
  const [findings, em, profile] = await Promise.all([
    fetchAll("kg_nodes", {
      select: "node_id,doc_id,properties",
      node_type: "eq.BidEvaluationFinding",
      "properties->>bidder_profile_id": `eq.${bidderId}`,
      "properties->>tender_id": `eq.${tenderId}`,
    }, 50),
    fetchAll("kg_nodes", {
      select: "node_id,properties",
      node_type: "eq.EligibilityMatrix",
      "properties->>bidder_profile_id": `eq.${bidderId}`,
      "properties->>tender_id": `eq.${tenderId}`,
    }, 5),
    fetchAll("kg_nodes", {
      select: "node_id,doc_id,properties",
      doc_id: `eq.${bidderId}`,
      node_type: "eq.BidderProfile",
    }, 5),
  ]);
  return { findings, em: em[0]?.properties, profile: profile[0]?.properties };
}

const TENDER_NAMES: Record<string, string> = {
  tender_synth_kurnool: "District Hospital, Kurnool",
  tender_synth_ja:      "Andhra Pradesh Judicial Academy",
  tender_synth_hc:      "Andhra Pradesh High Court Complex",
};

function findingIcon(verdict: string) {
  if (verdict === "QUALIFIED") return <ShieldCheck className="h-4 w-4 text-leaf-700" />;
  if (verdict === "INELIGIBLE") return <ShieldX className="h-4 w-4 text-red-700" />;
  if (verdict === "GAP_INSUFFICIENT_DATA") return <HelpCircle className="h-4 w-4 text-blue-700" />;
  if (verdict === "SKIP_NOT_APPLICABLE") return <span className="text-mist-200">—</span>;
  return <AlertTriangle className="h-4 w-4 text-amber-700" />;
}

function findingBadge(verdict: string, consequence: string): any {
  if (verdict === "QUALIFIED") return "qualified";
  if (consequence === "HARD_BLOCK") return "hardblock";
  if (consequence === "WARNING") return "warning";
  if (verdict === "GAP_INSUFFICIENT_DATA") return "markreview";
  return "outline";
}

export default async function BidderTenderPage({ params }: PageProps) {
  const tenderName = TENDER_NAMES[params.tenderId];
  if (!tenderName) notFound();
  const { findings, em: rawEm, profile: rawProfile } = await getBidderTenderData(params.tenderId, params.bidderId);
  const em = rawEm || {};
  const profile = rawProfile || { company_name: params.bidderId, contractor_class: "—", pan: "—", gstin: "—", bidder_type: "—" };

  return (
    <div className="p-8 md:p-10">
      <Link
        href={`/module3/${params.tenderId}`}
        className="inline-flex items-center gap-1.5 text-xs font-semibold text-ink-500 hover:text-ink-900 mb-4"
      >
        <ArrowLeft className="h-3.5 w-3.5" /> Back to {tenderName}
      </Link>

      <header className="mb-6">
        <div className="text-xs font-bold text-saffron-700 tracking-widest mb-2">
          MODULE 3 · BIDDER DETAIL
        </div>
        <h1 className="text-3xl font-bold tracking-tight text-ink-900 mb-2">
          {profile.company_name}
        </h1>
        <div className="text-sm text-ink-500 flex flex-wrap gap-x-6 gap-y-1 mb-4">
          <span><strong className="text-ink-700">Class:</strong> {profile.contractor_class}</span>
          <span><strong className="text-ink-700">Bidder type:</strong> {profile.bidder_type}</span>
          <span><strong className="text-ink-700">PAN:</strong> <code className="text-xs">{profile.pan}</code></span>
          <span><strong className="text-ink-700">GSTIN:</strong> <code className="text-xs">{profile.gstin}</code></span>
        </div>
        <div className="flex items-center gap-3">
          <Badge variant={
            em.aggregate_verdict === "QUALIFIED" ? "qualified" :
            em.aggregate_verdict === "DISQUALIFIED" ? "disqualified" :
            em.aggregate_verdict === "FLAGGED_FOR_COMMITTEE_REVIEW" ? "flagged" :
            "markreview"
          }>
            {VERDICT_LABEL[em.aggregate_verdict]}
          </Badge>
          <span className="text-sm text-ink-700">
            on tender <strong>{tenderName}</strong>
          </span>
        </div>
      </header>

      {/* Aggregate counts */}
      <section className="mb-6 grid grid-cols-2 md:grid-cols-5 gap-3">
        <Card><CardContent className="p-4">
          <div className="text-xs text-ink-500 font-semibold">Total criteria</div>
          <div className="text-2xl font-bold text-ink-900">{em.criteria_total ?? findings.length}</div>
        </CardContent></Card>
        <Card><CardContent className="p-4">
          <div className="text-xs text-leaf-700 font-semibold">QUALIFIED</div>
          <div className="text-2xl font-bold text-leaf-700">{em.count_qualified ?? "—"}</div>
        </CardContent></Card>
        <Card><CardContent className="p-4">
          <div className="text-xs text-red-700 font-semibold">HARD_BLOCK</div>
          <div className="text-2xl font-bold text-red-700">{em.count_ineligible_hard_block ?? "—"}</div>
        </CardContent></Card>
        <Card><CardContent className="p-4">
          <div className="text-xs text-amber-700 font-semibold">WARNING</div>
          <div className="text-2xl font-bold text-amber-700">{em.count_ineligible_warning ?? "—"}</div>
        </CardContent></Card>
        <Card><CardContent className="p-4">
          <div className="text-xs text-blue-700 font-semibold">GAP</div>
          <div className="text-2xl font-bold text-blue-700">{em.count_gap ?? "—"}</div>
        </CardContent></Card>
      </section>

      {em.aggregate_reasoning && (
        <Card className="mb-6 bg-mist-50/60">
          <CardContent className="p-4">
            <p className="text-sm italic text-ink-700">{em.aggregate_reasoning}</p>
          </CardContent>
        </Card>
      )}

      {/* Per-criterion table */}
      <section className="mb-8">
        <h2 className="text-lg font-bold text-ink-900 mb-3">Per-Criterion Evaluation ({findings.length})</h2>
        <Table>
          <THead>
            <TR>
              <TH className="w-8">#</TH>
              <TH>Criterion</TH>
              <TH>Verdict</TH>
              <TH>Severity</TH>
              <TH>Rule</TH>
              <TH>Decision basis</TH>
            </TR>
          </THead>
          <TBody>
            {findings
              .sort((a: any, b: any) => (a.properties?.typology_code || "").localeCompare(b.properties?.typology_code || ""))
              .map((f: any, i: number) => {
                const fp = f.properties || {};
                const v = fp.verdict || "?";
                const cons = fp.evaluation_consequence || "";
                return (
                  <TR key={f.node_id}>
                    <TD className="text-xs text-ink-500">{i + 1}</TD>
                    <TD>
                      <div className="font-semibold text-ink-900 flex items-center gap-2">
                        {findingIcon(v)}
                        {fp.typology_code}
                      </div>
                    </TD>
                    <TD>
                      <Badge variant={findingBadge(v, cons)} className="text-[10px]">
                        {VERDICT_LABEL[v] || v}
                      </Badge>
                    </TD>
                    <TD className="text-xs">
                      {cons && (
                        <Badge variant={cons === "HARD_BLOCK" ? "hardblock" : cons === "WARNING" ? "warning" : "advisory"} className="text-[10px]">
                          {cons}
                        </Badge>
                      )}
                    </TD>
                    <TD>
                      <code className="text-xs text-ink-700">{fp.rule_id}</code>
                    </TD>
                    <TD className="text-xs text-ink-500 max-w-xl">
                      {(fp.decision_reason || "").slice(0, 240)}
                    </TD>
                  </TR>
                );
              })}
          </TBody>
        </Table>
      </section>

      {/* Bidder identity card */}
      <section className="mb-8">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Bidder Identity & Resources</CardTitle>
            <CardDescription>From BidderProfile kg_node</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-3 text-sm">
              <div>
                <div className="text-xs text-ink-500 font-semibold">Years in business</div>
                <div className="text-ink-900">{profile.years_in_business || "—"}</div>
              </div>
              <div>
                <div className="text-xs text-ink-500 font-semibold">Registration state</div>
                <div className="text-ink-900">{profile.registration_state || "—"}</div>
              </div>
              <div>
                <div className="text-xs text-ink-500 font-semibold">Communication address</div>
                <div className="text-ink-900 text-xs">{profile.communication_address || "—"}</div>
              </div>
              <div>
                <div className="text-xs text-ink-500 font-semibold">Construction turnover (5yr avg)</div>
                <div className="text-ink-900">{formatCrore(profile.construction_turnover_5yr_avg_cr)}</div>
              </div>
              <div>
                <div className="text-xs text-ink-500 font-semibold">Financial turnover (3yr avg)</div>
                <div className="text-ink-900">{formatCrore(profile.financial_turnover_3yr_avg_cr)}</div>
              </div>
              <div>
                <div className="text-xs text-ink-500 font-semibold">Max completed work</div>
                <div className="text-ink-900">{formatCrore(profile.max_completed_works_value_cr)}</div>
              </div>
              <div>
                <div className="text-xs text-ink-500 font-semibold">Key personnel count</div>
                <div className="text-ink-900">{profile.key_personnel_count ?? "—"}</div>
              </div>
              <div>
                <div className="text-xs text-ink-500 font-semibold">Equipment register</div>
                <div className="text-ink-900">{profile.equipment_register_completeness || "—"}</div>
              </div>
              <div>
                <div className="text-xs text-ink-500 font-semibold">Blacklist status</div>
                <div className="text-ink-900">{profile.blacklist_status || "—"}</div>
              </div>
              {profile.bidder_type === "JV" && (
                <>
                  <div>
                    <div className="text-xs text-ink-500 font-semibold">JV Lead Partner</div>
                    <div className="text-ink-900 text-xs"><code>{profile.lead_partner_id}</code></div>
                  </div>
                  <div>
                    <div className="text-xs text-ink-500 font-semibold">Partner count</div>
                    <div className="text-ink-900">{(profile.partner_ids || []).length}</div>
                  </div>
                  <div>
                    <div className="text-xs text-ink-500 font-semibold">Liability</div>
                    <div className="text-ink-900">{profile.liability_terms || "—"}</div>
                  </div>
                </>
              )}
            </div>
          </CardContent>
        </Card>
      </section>
    </div>
  );
}
