/**
 * GET /api/kb/recent-executions — top-fired rules across the last 30 days.
 *
 * Aggregates ValidationFinding rows by rule_id; returns top 50 most-fired
 * rules with verdict mix + tender breadth. Powers the "Live Execution" tab
 * of the Knowledge Layer.
 */
import { NextRequest, NextResponse } from "next/server";
import { listNodes, hasServiceRoleKey } from "@/lib/kb-supabase";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(_req: NextRequest) {
  if (!hasServiceRoleKey()) {
    return NextResponse.json({ error: "service-role key unavailable" }, { status: 503 });
  }
  // Pull a large window of recent ValidationFinding rows and aggregate client-side.
  // ValidationFinding count is 154 (sentinel-pinned); fits comfortably in one call.
  const result = await listNodes({
    nodeType: "ValidationFinding",
    page: 0, pageSize: 200,
    orderBy: "created_at.desc",
  });

  const byRule: Record<
    string,
    {
      rule_id: string;
      firing_count: number;
      verdicts: Record<string, number>;
      tenders: Set<string>;
      latest_at: string;
    }
  > = {};

  for (const f of result.rows) {
    const rid = f.properties?.rule_id || "unknown";
    if (!byRule[rid]) {
      byRule[rid] = {
        rule_id:       rid,
        firing_count:  0,
        verdicts:      {},
        tenders:       new Set(),
        latest_at:     f.created_at || "",
      };
    }
    const e = byRule[rid];
    e.firing_count += 1;
    const v = f.properties?.verdict || "unknown";
    e.verdicts[v] = (e.verdicts[v] || 0) + 1;
    if (f.doc_id) e.tenders.add(f.doc_id);
    if ((f.created_at || "") > e.latest_at) e.latest_at = f.created_at || "";
  }

  const top50 = Object.values(byRule)
    .map(e => ({
      rule_id:       e.rule_id,
      firing_count:  e.firing_count,
      verdict_mix:   e.verdicts,
      tender_count:  e.tenders.size,
      latest_at:     e.latest_at,
    }))
    .sort((a, b) => b.firing_count - a.firing_count)
    .slice(0, 50);

  return NextResponse.json({
    top_rules:     top50,
    total_firings: result.rows.length,
    sample_caveat: result.rows.length < result.total
      ? `Sampled ${result.rows.length} of ${result.total} findings.`
      : null,
  });
}
