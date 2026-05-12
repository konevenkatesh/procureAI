import Link from "next/link";
import { notFound } from "next/navigation";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { fetchAll } from "@/lib/supabase";
import { VERDICT_LABEL, formatCrore, formatPct } from "@/lib/utils";
import { Crown, AlertTriangle, FileWarning, ArrowRight, ArrowLeft, MessageSquare, FileBadge } from "lucide-react";

export const revalidate = 60;

interface PageProps {
  params: { tenderId: string };
}

async function getTenderData(tenderId: string) {
  const [emRows, trRows, afRows, csRows, profileRows] = await Promise.all([
    fetchAll("kg_nodes", {
      select: "node_id,doc_id,properties",
      node_type: "eq.EligibilityMatrix",
      "properties->>tender_id": `eq.${tenderId}`,
    }, 100),
    fetchAll("kg_nodes", {
      select: "node_id,properties",
      node_type: "eq.TenderRanking",
      "properties->>tender_id": `eq.${tenderId}`,
    }, 5),
    fetchAll("kg_nodes", {
      select: "node_id,properties",
      node_type: "eq.BidAnomalyFinding",
      "properties->>tender_id": `eq.${tenderId}`,
    }, 10),
    fetchAll("kg_nodes", {
      select: "node_id,properties",
      node_type: "eq.ComparativeStatement",
      "properties->>tender_id": `eq.${tenderId}`,
    }, 5),
    fetchAll("kg_nodes", {
      select: "node_id,doc_id,properties",
      node_type: "eq.BidderProfile",
    }, 50),
  ]);
  return { emRows, trRows, afRows, csRows, profileRows };
}

const TENDER_NAMES: Record<string, { name: string; nit: string; ecv: number }> = {
  tender_synth_kurnool: { name: "District Hospital, Kurnool", nit: "100/PROC/APIIC/1/2026", ecv: 85.0 },
  tender_synth_ja:      { name: "Andhra Pradesh Judicial Academy", nit: "JA/2026/CW/001", ecv: 125.5 },
  tender_synth_hc:      { name: "Andhra Pradesh High Court Complex", nit: "HC/APCRDA/2026/PROC/001", ecv: 365.16 },
};

function badgeVariant(verdict: string): any {
  if (verdict === "QUALIFIED") return "qualified";
  if (verdict === "FLAGGED_FOR_COMMITTEE_REVIEW") return "flagged";
  if (verdict === "MARK_FOR_DOCUMENTATION_REVIEW") return "markreview";
  if (verdict === "DISQUALIFIED") return "disqualified";
  return "outline";
}

export default async function TenderPage({ params }: PageProps) {
  const t = TENDER_NAMES[params.tenderId];
  if (!t) notFound();

  const { emRows, trRows, afRows, csRows, profileRows } = await getTenderData(params.tenderId);
  const cs = csRows[0]?.properties || {};
  const tr = trRows[0]?.properties || {};
  const ranking = tr.ranking || [];

  // Build bidder lookup
  const profileById: Record<string, any> = {};
  for (const p of profileRows) {
    const props = p.properties || {};
    if (props.profile_id) profileById[props.profile_id] = props;
  }

  return (
    <div className="p-8 md:p-10">
      <Link
        href="/module3"
        className="inline-flex items-center gap-1.5 text-xs font-semibold text-ink-500 hover:text-ink-900 mb-4"
      >
        <ArrowLeft className="h-3.5 w-3.5" /> Back to all tenders
      </Link>

      <header className="mb-6">
        <div className="text-xs font-bold text-saffron-700 tracking-widest mb-2">
          MODULE 3 · EVALUATION REPORT
        </div>
        <h1 className="text-3xl font-bold tracking-tight text-ink-900 mb-2">
          {t.name}
        </h1>
        <div className="text-sm text-ink-500 flex flex-wrap gap-x-6 gap-y-1">
          <span><strong className="text-ink-700">NIT:</strong> <code className="text-xs">{t.nit}</code></span>
          <span><strong className="text-ink-700">ECV:</strong> {formatCrore(t.ecv)}</span>
          <span><strong className="text-ink-700">Bidders:</strong> {emRows.length}</span>
        </div>
      </header>

      {/* Effective L1 callout */}
      {cs.effective_l1_bidder_id && (
        <Card className="mb-6 border-leaf-500 bg-leaf-50">
          <CardContent className="p-5">
            <div className="flex items-start gap-3">
              <Crown className="h-6 w-6 text-leaf-700 mt-0.5 shrink-0" />
              <div className="flex-1 min-w-0">
                <div className="text-xs font-semibold text-leaf-700 uppercase tracking-wide">Effective L1 (post-anomaly adjustment)</div>
                <div className="text-lg font-bold text-ink-900 mt-1">{cs.effective_l1_bidder_name}</div>
                <div className="text-sm text-ink-700">
                  Award amount: <strong>{formatCrore(cs.effective_l1_amount_cr)}</strong>
                  {tr.l1_alb_flag && (
                    <span className="ml-3 text-ink-500">
                      (raw L1 {cs.l1_winner_bidder_name} flagged ALB → skipped)
                    </span>
                  )}
                </div>
                <div className="text-xs text-ink-500 mt-2 italic">{cs.effective_l1_rationale}</div>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Bidder participation table */}
      <section className="mb-8">
        <h2 className="text-lg font-bold text-ink-900 mb-3">Bidder Participation ({emRows.length})</h2>
        <Table>
          <THead>
            <TR>
              <TH>Bidder</TH>
              <TH>Verdict</TH>
              <TH className="text-right">Qualified</TH>
              <TH className="text-right">Hard-Block</TH>
              <TH className="text-right">Warning</TH>
              <TH className="text-right">Gap</TH>
              <TH></TH>
            </TR>
          </THead>
          <TBody>
            {emRows
              .sort((a: any, b: any) => (a.properties?.bidder_profile_id || "").localeCompare(b.properties?.bidder_profile_id || ""))
              .map((em: any) => {
                const p = em.properties || {};
                const profile = profileById[p.bidder_profile_id] || {};
                return (
                  <TR key={em.node_id}>
                    <TD>
                      <div className="font-semibold text-ink-900">{profile.company_name || p.bidder_profile_id}</div>
                      <div className="text-xs text-ink-500">{profile.contractor_class || "—"}</div>
                    </TD>
                    <TD>
                      <Badge variant={badgeVariant(p.aggregate_verdict)}>
                        {VERDICT_LABEL[p.aggregate_verdict] || p.aggregate_verdict}
                      </Badge>
                    </TD>
                    <TD className="text-right tabular-nums">{p.count_qualified ?? "—"}</TD>
                    <TD className="text-right tabular-nums text-red-700 font-semibold">{p.count_ineligible_hard_block ?? "—"}</TD>
                    <TD className="text-right tabular-nums text-amber-700">{p.count_ineligible_warning ?? "—"}</TD>
                    <TD className="text-right tabular-nums text-blue-700">{p.count_gap ?? "—"}</TD>
                    <TD>
                      <Link
                        href={`/module3/${params.tenderId}/bidder/${p.bidder_profile_id}`}
                        className="text-xs font-semibold text-ink-900 hover:text-saffron-700 inline-flex items-center gap-1"
                      >
                        Details <ArrowRight className="h-3 w-3" />
                      </Link>
                    </TD>
                  </TR>
                );
              })}
          </TBody>
        </Table>
      </section>

      {/* Tender Ranking */}
      {ranking.length > 0 && (
        <section className="mb-8">
          <h2 className="text-lg font-bold text-ink-900 mb-3">Ranking of QUALIFIED Bidders ({ranking.length})</h2>
          <Table>
            <THead>
              <TR>
                <TH>Rank</TH>
                <TH>Bidder</TH>
                <TH className="text-right">Bid Amount</TH>
                <TH className="text-right">Premium vs ECV</TH>
                <TH>ALB?</TH>
                <TH className="text-right">Distance from L1</TH>
              </TR>
            </THead>
            <TBody>
              {ranking.map((r: any) => {
                const isEffL1 = r.bidder_profile_id === cs.effective_l1_bidder_id;
                return (
                  <TR key={r.rank_position} className={isEffL1 ? "bg-leaf-50/60" : undefined}>
                    <TD className="font-semibold">
                      {r.rank_position}
                      {isEffL1 && <Crown className="inline h-3.5 w-3.5 ml-1 text-leaf-700" />}
                    </TD>
                    <TD>
                      <div className={"font-semibold " + (isEffL1 ? "text-leaf-700" : "text-ink-900")}>
                        {r.bidder_name}
                      </div>
                    </TD>
                    <TD className="text-right tabular-nums">{formatCrore(r.bid_amount_cr)}</TD>
                    <TD className="text-right tabular-nums">{formatPct(r.premium_pct)}</TD>
                    <TD>{r.alb_flag ? <Badge variant="disqualified" className="text-[10px]">ALB</Badge> : "—"}</TD>
                    <TD className="text-right tabular-nums text-ink-500">
                      {r.rank_position === "L1" ? "—" : `+${formatCrore(r.distance_from_l1_cr)} (${formatPct(r.distance_from_l1_pct)})`}
                    </TD>
                  </TR>
                );
              })}
            </TBody>
          </Table>
          {tr.alb_action_required && (
            <p className="mt-2 text-xs text-red-700 font-semibold flex items-center gap-1">
              <AlertTriangle className="h-3.5 w-3.5" />
              ALB action required: raw L1 ({cs.l1_winner_bidder_name}) is an ALB candidate. Effective L1 falls to the next non-skipped bidder.
            </p>
          )}
        </section>
      )}

      {/* Anomaly findings */}
      {afRows.length > 0 && (
        <section className="mb-8">
          <h2 className="text-lg font-bold text-ink-900 mb-3 flex items-center gap-2">
            <FileWarning className="h-5 w-5 text-amber-700" />
            Anomaly Findings ({afRows.length})
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {afRows.map((af: any) => {
              const ap = af.properties || {};
              return (
                <Card key={af.node_id} className="border-amber-300 bg-amber-50/30">
                  <CardHeader>
                    <div className="flex items-start justify-between gap-2">
                      <CardTitle className="text-base">{ap.anomaly_class}</CardTitle>
                      <Badge variant={ap.aggregate_severity === "HIGH" ? "hardblock" : "warning"}>
                        {ap.aggregate_severity}
                      </Badge>
                    </div>
                    <CardDescription>
                      Primary bidders: <strong>{(ap.primary_bidder_names || []).join(", ")}</strong>
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="text-sm space-y-2">
                    <div>
                      <span className="text-ink-500">Confidence:</span>{" "}
                      <strong>{ap.detection_confidence}</strong>{" "}
                      <span className="text-ink-500">· Cross-tender:</span>{" "}
                      <strong>{ap.cross_tender_appearances}/3</strong>{" "}
                      {ap.cross_tender_consistency && <span className="text-red-700 font-semibold">consistent</span>}
                    </div>
                    <div className="text-xs italic text-ink-500 mt-2">{ap.decision_reason}</div>
                    <details className="mt-2">
                      <summary className="text-xs font-semibold text-ink-700 cursor-pointer hover:text-saffron-700">
                        Signals ({(ap.signals || []).length})
                      </summary>
                      <div className="mt-2 space-y-1.5 text-xs">
                        {(ap.signals || []).map((s: any, i: number) => (
                          <div key={i} className="border-l-2 border-amber-300 pl-2">
                            <div className="font-semibold text-ink-900">{s.signal_type}</div>
                            <div className="text-ink-500">{s.evidence}</div>
                          </div>
                        ))}
                      </div>
                    </details>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        </section>
      )}

      {/* Artifact links */}
      {cs.audit_id && (
        <section className="mb-8">
          <h2 className="text-lg font-bold text-ink-900 mb-3 flex items-center gap-2">
            <FileBadge className="h-5 w-5" /> ComparativeStatement Artifacts
          </h2>
          <Card>
            <CardContent className="p-5">
              <div className="text-xs text-ink-500 mb-2">
                Audit ID: <code className="text-ink-700">{cs.audit_id}</code>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-sm">
                <div className="rounded border border-mist-200 p-3">
                  <div className="text-xs font-semibold text-ink-500 mb-1">Markdown</div>
                  <code className="text-xs break-all">{cs.md_artifact_path}</code>
                </div>
                <div className="rounded border border-mist-200 p-3">
                  <div className="text-xs font-semibold text-ink-500 mb-1">DOCX</div>
                  <code className="text-xs break-all">{cs.docx_artifact_path}</code>
                </div>
                <div className="rounded border border-mist-200 p-3">
                  <div className="text-xs font-semibold text-ink-500 mb-1">PDF (L75 reportlab)</div>
                  <code className="text-xs break-all">{cs.pdf_artifact_path || "—"}</code>
                </div>
              </div>
              <div className="mt-3">
                <Link
                  href={`/module4?tender=${params.tenderId}`}
                  className="inline-flex items-center gap-1.5 text-xs font-semibold text-ink-900 hover:text-saffron-700"
                >
                  <MessageSquare className="h-3.5 w-3.5" />
                  View communications generated for this tender
                  <ArrowRight className="h-3 w-3" />
                </Link>
              </div>
            </CardContent>
          </Card>
        </section>
      )}
    </div>
  );
}
