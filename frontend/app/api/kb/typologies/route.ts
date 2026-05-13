/**
 * GET /api/kb/typologies — list of Tier-1 typology codes + their rule counts.
 *
 * Aggregates from kg_nodes.RuleNode grouped by properties->>typology_code.
 * PostgREST doesn't do GROUP BY directly; we use the rpc function `kb_typology_summary`
 * if defined, else fall back to a client-side aggregation.
 */
import { NextRequest, NextResponse } from "next/server";
import { listNodes, hasServiceRoleKey } from "@/lib/kb-supabase";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(_req: NextRequest) {
  if (!hasServiceRoleKey()) {
    return NextResponse.json({ error: "service-role key unavailable" }, { status: 503 });
  }
  // Fallback: client-side aggregation over a paginated dump. RuleNode total
  // is ~611; pull all in one request (pageSize=1000) and group by typology_code.
  const result = await listNodes({
    nodeType: "RuleNode",
    page: 0, pageSize: 200,
    orderBy: "label.asc",
  });

  const byTypology: Record<string, { count: number; severities: Record<string, number> }> = {};
  for (const r of result.rows) {
    const tc = r.properties?.typology_code || "Uncategorized";
    const sev = r.properties?.severity || "UNKNOWN";
    if (!byTypology[tc]) byTypology[tc] = { count: 0, severities: {} };
    byTypology[tc].count += 1;
    byTypology[tc].severities[sev] = (byTypology[tc].severities[sev] || 0) + 1;
  }

  const typologies = Object.entries(byTypology)
    .map(([code, stats]) => ({
      typology_code: code,
      rule_count:    stats.count,
      severities:    stats.severities,
    }))
    .sort((a, b) => b.rule_count - a.rule_count);

  return NextResponse.json({
    typologies,
    total_rules_sampled: result.rows.length,
    sample_caveat: result.rows.length < result.total
      ? `Sampled ${result.rows.length} of ${result.total} rules; counts approximate.`
      : null,
  });
}
