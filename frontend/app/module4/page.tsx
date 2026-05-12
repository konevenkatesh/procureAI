import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { fetchAll } from "@/lib/supabase";
import { MessageSquare, ArrowRight, Sparkles } from "lucide-react";
import PipelineLauncher from "@/components/PipelineLauncher";
import ClarificationLauncher from "@/components/ClarificationLauncher";
import RegionBadge from "@/components/RegionBadge";

export const revalidate = 60;

interface SearchParams { type?: string; tender?: string; }

const BIDDER_FACING = new Set([
  "DISQUALIFICATION", "AWARD", "ALB_JUSTIFICATION", "BID_ACK",
  "FLAGGED", "DOC_REVIEW", "REGRET", "BIDDER_CLARIFICATION_QA",
]);

const TYPE_ORDER = [
  "AWARD", "DISQUALIFICATION", "ALB_JUSTIFICATION", "FLAGGED", "DOC_REVIEW",
  "BIDDER_CLARIFICATION_QA", "REGRET", "BID_ACK",
  "CARTEL_REVIEW", "INTERNAL_ROUTING",
];

const TENDER_NAMES: Record<string, string> = {
  tender_synth_kurnool: "Kurnool",
  tender_synth_ja: "JA",
  tender_synth_hc: "HC",
};

async function getCommunications() {
  const rows = await fetchAll("kg_nodes", {
    select: "node_id,doc_id,properties",
    node_type: "eq.Communication",
  }, 200);
  return rows;
}

export default async function Module4Page({ searchParams }: { searchParams: SearchParams }) {
  const all = await getCommunications();
  const filtered = all.filter((c: any) => {
    const p = c.properties || {};
    if (searchParams.type && p.communication_type !== searchParams.type) return false;
    if (searchParams.tender && p.tender_id !== searchParams.tender) return false;
    return true;
  });

  const countsByType: Record<string, number> = {};
  for (const c of all) {
    const t = c.properties?.communication_type;
    if (t) countsByType[t] = (countsByType[t] || 0) + 1;
  }

  return (
    <div className="p-8 md:p-10">
      <header className="mb-6">
        <div className="flex items-center gap-2 text-xs font-bold text-saffron-700 tracking-widest mb-2">
          <MessageSquare className="h-4 w-4" /> MODULE 4 · COMMUNICATIONS
        </div>
        <h1 className="text-3xl font-bold tracking-tight text-ink-900 mb-2">
          Bidder & Officer Communications
        </h1>
        <p className="text-ink-500 max-w-3xl">
          {all.length} Communication kg_nodes across 10 types. Bidder-facing communications are
          bilingual EN+TE via Sarvam-M with DPDP pseudonymisation. Internal communications stay
          English-only.
        </p>
        <div className="mt-4 flex items-center gap-3">
          <ClarificationLauncher />
          <RegionBadge />
        </div>
      </header>

      <section className="mb-6">
        <Card className="bg-mist-50/40">
          <CardHeader>
            <div className="flex items-start gap-3">
              <Sparkles className="h-5 w-5 text-saffron-700 mt-1" />
              <div>
                <CardTitle className="text-base">Generate Communications</CardTitle>
                <CardDescription>
                  Submit a tender to the <span className="font-semibold text-ink-700">m4-communicator</span> Cloud
                  Run service (asia-south1). The pipeline runs the 11 drafters (award / regret /
                  clarification-QA / etc.) with Sarvam-M bilingual rendering. Today's deploy
                  returns the drafter inventory; full execution lands in a follow-up commit
                  per the GCP-2 NOT-IN-SCOPE rule.
                </CardDescription>
              </div>
            </div>
          </CardHeader>
          <CardContent>
            <PipelineLauncher
              endpoint="/api/m4/communicate"
              actionLabel="Generate Communications"
              runningLabel="Drafting…"
              tenders={[
                { id: "tender_synth_kurnool", name: "Kurnool — District Hospital" },
                { id: "tender_synth_ja",      name: "AP Judicial Academy" },
                { id: "tender_synth_hc",      name: "AP High Court Complex" },
              ]}
              extraField={{
                key: "language",
                label: "Language",
                options: [
                  { value: "EN",   label: "English only" },
                  { value: "TE",   label: "Telugu only (Sarvam-M)" },
                  { value: "BOTH", label: "EN + TE bilingual" },
                ],
                defaultValue: "BOTH",
              }}
            />
          </CardContent>
        </Card>
      </section>

      {/* Type filter chips */}
      <section className="mb-6">
        <div className="flex flex-wrap gap-2">
          <Link
            href="/module4"
            className={
              "rounded-md px-3 py-1.5 text-xs font-semibold border transition-colors " +
              (!searchParams.type
                ? "bg-ink-900 text-white border-ink-900"
                : "bg-white text-ink-700 border-mist-200 hover:bg-mist-50")
            }
          >
            All ({all.length})
          </Link>
          {TYPE_ORDER.map((t) => {
            const active = searchParams.type === t;
            const scope = BIDDER_FACING.has(t) ? "bidder" : "internal";
            return (
              <Link
                key={t}
                href={`/module4?type=${t}`}
                className={
                  "rounded-md px-3 py-1.5 text-xs font-semibold border transition-colors " +
                  (active
                    ? "bg-ink-900 text-white border-ink-900"
                    : "bg-white text-ink-700 border-mist-200 hover:bg-mist-50")
                }
              >
                {t}
                <span className="ml-1.5 opacity-70">({countsByType[t] || 0})</span>
                {scope === "internal" && !active && (
                  <span className="ml-1 text-[9px] text-ink-500">internal</span>
                )}
              </Link>
            );
          })}
        </div>
      </section>

      {/* Table */}
      <section>
        <h2 className="text-sm font-semibold text-ink-700 mb-3">
          {filtered.length} of {all.length} communications shown
          {searchParams.type && ` · type: ${searchParams.type}`}
          {searchParams.tender && ` · tender: ${TENDER_NAMES[searchParams.tender]}`}
        </h2>
        <Table>
          <THead>
            <TR>
              <TH>Type</TH>
              <TH>Recipient</TH>
              <TH>Tender</TH>
              <TH>Language</TH>
              <TH>Status</TH>
              <TH>Audit ID</TH>
              <TH></TH>
            </TR>
          </THead>
          <TBody>
            {filtered
              .sort((a: any, b: any) => {
                const ta = a.properties?.communication_type || "";
                const tb = b.properties?.communication_type || "";
                if (ta !== tb) return ta.localeCompare(tb);
                return (a.properties?.tender_id || "").localeCompare(b.properties?.tender_id || "");
              })
              .map((c: any) => {
                const p = c.properties || {};
                const isBidder = BIDDER_FACING.has(p.communication_type);
                return (
                  <TR key={c.node_id}>
                    <TD>
                      <Badge variant={isBidder ? "outline" : "advisory"} className="text-[10px]">
                        {p.communication_type}
                      </Badge>
                    </TD>
                    <TD>
                      <div className="font-semibold text-ink-900">
                        {p.bidder_name || p.recipient_role || "—"}
                      </div>
                      <div className="text-xs text-ink-500">
                        {isBidder ? `bidder · ${p.recipient_email || "?"}` : `internal · ${p.recipient_role || p.channel || "?"}`}
                      </div>
                    </TD>
                    <TD className="text-xs">
                      <Link
                        href={`/module4?tender=${p.tender_id}`}
                        className="text-ink-700 hover:text-saffron-700"
                      >
                        {TENDER_NAMES[p.tender_id] || p.tender_id}
                      </Link>
                    </TD>
                    <TD>
                      <Badge variant={p.language === "EN+TE" ? "qualified" : "advisory"} className="text-[10px]">
                        {p.language || "EN"}
                      </Badge>
                    </TD>
                    <TD className="text-xs">
                      <Badge variant={p.status === "DRAFT" ? "outline" : "outline"} className="text-[10px]">
                        {p.status || "DRAFT"}
                      </Badge>
                    </TD>
                    <TD>
                      <code className="text-[10px] text-ink-500">{(p.audit_id || "").slice(0, 12)}</code>
                    </TD>
                    <TD>
                      <Link
                        href={`/module4/${c.node_id}`}
                        className="text-xs font-semibold text-ink-900 hover:text-saffron-700 inline-flex items-center gap-1"
                      >
                        Open <ArrowRight className="h-3 w-3" />
                      </Link>
                    </TD>
                  </TR>
                );
              })}
          </TBody>
        </Table>
      </section>
    </div>
  );
}
