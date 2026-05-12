import Link from "next/link";
import { notFound } from "next/navigation";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { fetchAll } from "@/lib/supabase";
import { LangToggle } from "@/components/lang-toggle";
import { ArrowLeft, MessageSquare, FileText, ExternalLink } from "lucide-react";

export const dynamic = "force-dynamic";
export const revalidate = 0;

interface PageProps {
  params: { communicationId: string };
}

async function getCommunication(id: string) {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !key) return null;
  try {
    const r = await fetch(
      `${url}/rest/v1/kg_nodes?select=node_id,doc_id,properties,label&node_id=eq.${id}`,
      {
        headers: { apikey: key, Authorization: `Bearer ${key}` },
        cache: "no-store",
      },
    );
    if (!r.ok) return null;
    const arr = await r.json();
    return arr[0] || null;
  } catch {
    return null;
  }
}

async function getSourceFindings(ids: string[]) {
  if (!ids || ids.length === 0) return [];
  const or = ids.map((x) => `node_id.eq.${x}`).join(",");
  return fetchAll("kg_nodes", {
    select: "node_id,node_type,doc_id,label,properties",
    or: `(${or})`,
  }, 50);
}

const TENDER_NAMES: Record<string, string> = {
  tender_synth_kurnool: "District Hospital, Kurnool",
  tender_synth_ja:      "Andhra Pradesh Judicial Academy",
  tender_synth_hc:      "Andhra Pradesh High Court Complex",
};

const BIDDER_FACING = new Set([
  "DISQUALIFICATION", "AWARD", "ALB_JUSTIFICATION", "BID_ACK",
  "FLAGGED", "DOC_REVIEW", "REGRET", "BIDDER_CLARIFICATION_QA",
]);

export default async function CommunicationDetailPage({ params }: PageProps) {
  const comm = await getCommunication(params.communicationId);
  if (!comm) {
    return (
      <div className="p-8 md:p-10">
        <Link href="/module4" className="inline-flex items-center gap-1.5 text-xs font-semibold text-ink-500 hover:text-ink-900 mb-4">
          <ArrowLeft className="h-3.5 w-3.5" /> Back to all communications
        </Link>
        <Card><CardContent className="p-6">
          <p className="text-ink-700">Communication {params.communicationId} not found.</p>
          <p className="text-xs text-ink-500 mt-2">It may have been re-emitted with a different node_id — return to the list and pick another.</p>
        </CardContent></Card>
      </div>
    );
  }
  const p = comm.properties || {};
  const sources = await getSourceFindings(p.source_finding_node_ids || []);
  const isBidder = BIDDER_FACING.has(p.communication_type);

  return (
    <div className="p-8 md:p-10 max-w-6xl">
      <Link
        href="/module4"
        className="inline-flex items-center gap-1.5 text-xs font-semibold text-ink-500 hover:text-ink-900 mb-4"
      >
        <ArrowLeft className="h-3.5 w-3.5" /> Back to all communications
      </Link>

      <header className="mb-6">
        <div className="flex items-center gap-2 text-xs font-bold text-saffron-700 tracking-widest mb-2">
          <MessageSquare className="h-4 w-4" /> MODULE 4 · COMMUNICATION
        </div>
        <div className="flex flex-wrap items-baseline gap-3 mb-3">
          <h1 className="text-2xl font-bold tracking-tight text-ink-900">
            {p.communication_type}
          </h1>
          <Badge variant={isBidder ? "outline" : "advisory"}>
            {isBidder ? "Bidder-facing" : "Internal"}
          </Badge>
          <Badge variant={p.language === "EN+TE" ? "qualified" : "advisory"}>
            {p.language || "EN"}
          </Badge>
          <Badge variant="outline">{p.status || "DRAFT"}</Badge>
        </div>
        <div className="text-sm text-ink-500 flex flex-wrap gap-x-6 gap-y-1">
          <span><strong className="text-ink-700">From:</strong> {p.sender_role || "—"}</span>
          {p.recipient_bidder_profile_id && (
            <span><strong className="text-ink-700">To:</strong> {p.bidder_name || p.recipient_bidder_profile_id}</span>
          )}
          {p.recipient_role && (
            <span><strong className="text-ink-700">To:</strong> {p.recipient_role}</span>
          )}
          <span><strong className="text-ink-700">Tender:</strong>{" "}
            <Link href={`/module3/${p.tender_id}`} className="hover:text-saffron-700 underline">
              {TENDER_NAMES[p.tender_id] || p.tender_id}
            </Link>
          </span>
          <span><strong className="text-ink-700">Channel:</strong> {p.channel || "EMAIL"}</span>
        </div>
        <div className="mt-2 text-xs text-ink-500">
          <strong className="text-ink-700">Audit ID:</strong>{" "}
          <code className="text-ink-700">{p.audit_id}</code>
        </div>
      </header>

      {/* Bilingual content */}
      <Card className="mb-6">
        <CardContent className="p-6">
          <LangToggle contentEn={p.content_en || ""} contentTe={p.content_te} />
        </CardContent>
      </Card>

      {/* Q&A threading */}
      {p.parent_communication_id && (
        <Card className="mb-6 bg-blue-50/40 border-blue-300">
          <CardContent className="p-4 text-sm">
            <span className="font-semibold text-blue-700">In reply to:</span>{" "}
            <Link href={`/module4/${p.parent_communication_id}`} className="text-ink-900 underline hover:text-saffron-700">
              question communication{" "}
              <code className="text-xs">{p.parent_communication_id?.slice(0, 12)}</code>
            </Link>
          </CardContent>
        </Card>
      )}

      {/* Drilldown: source findings */}
      {sources.length > 0 && (
        <section className="mb-6">
          <h2 className="text-lg font-bold text-ink-900 mb-3 flex items-center gap-2">
            <FileText className="h-4 w-4" />
            Source Findings ({sources.length})
          </h2>
          <p className="text-xs text-ink-500 mb-3">
            Every claim in this communication traces to one of these kg_nodes.
            Click to view the underlying evaluation finding or aggregator row.
          </p>
          <div className="space-y-2">
            {sources.map((s: any) => {
              const sp = s.properties || {};
              const drilldownHref =
                s.node_type === "BidEvaluationFinding" && sp.tender_id && sp.bidder_profile_id
                  ? `/module3/${sp.tender_id}/bidder/${sp.bidder_profile_id}`
                  : s.node_type === "EligibilityMatrix" && sp.tender_id && sp.bidder_profile_id
                  ? `/module3/${sp.tender_id}/bidder/${sp.bidder_profile_id}`
                  : sp.tender_id
                  ? `/module3/${sp.tender_id}`
                  : null;
              return (
                <Card key={s.node_id} className="bg-mist-50/40">
                  <CardContent className="p-3 flex items-start justify-between gap-3">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <Badge variant="outline" className="text-[10px]">
                          {s.node_type}
                        </Badge>
                        <code className="text-[10px] text-ink-500">{s.node_id.slice(0, 12)}</code>
                      </div>
                      <div className="text-xs text-ink-700">
                        {s.label || sp.typology_code || "—"}
                      </div>
                      {sp.rule_id && (
                        <div className="text-[10px] text-ink-500 mt-1">
                          Rule: <code className="text-ink-700">{sp.rule_id}</code>
                          {sp.decision_reason && <span> · {sp.decision_reason.slice(0, 120)}</span>}
                        </div>
                      )}
                    </div>
                    {drilldownHref && (
                      <Link
                        href={drilldownHref}
                        className="text-[11px] font-semibold text-ink-900 hover:text-saffron-700 shrink-0 inline-flex items-center gap-1"
                      >
                        Drilldown <ExternalLink className="h-3 w-3" />
                      </Link>
                    )}
                  </CardContent>
                </Card>
              );
            })}
          </div>
        </section>
      )}

      {/* Artifact paths */}
      <Card className="mb-6">
        <CardHeader>
          <CardTitle className="text-base">Artifact files</CardTitle>
          <CardDescription>Filesystem paths (also linked to kg_node properties)</CardDescription>
        </CardHeader>
        <CardContent className="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
          <div className="rounded border border-mist-200 p-3">
            <div className="text-xs font-semibold text-ink-500 mb-1">Markdown</div>
            <code className="text-xs break-all">{p.artifact_path_md || "—"}</code>
          </div>
          <div className="rounded border border-mist-200 p-3">
            <div className="text-xs font-semibold text-ink-500 mb-1">DOCX</div>
            <code className="text-xs break-all">{p.artifact_path_docx || "—"}</code>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
