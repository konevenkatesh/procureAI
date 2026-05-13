/**
 * GET /api/kb/typologies/[id] — single typology detail.
 *
 * Returns all RuleNode rows for the typology + recent ValidationFinding firings.
 */
import { NextRequest, NextResponse } from "next/server";
import { listNodes, hasServiceRoleKey } from "@/lib/kb-supabase";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(_req: NextRequest, { params }: { params: { id: string } }) {
  if (!hasServiceRoleKey()) {
    return NextResponse.json({ error: "service-role key unavailable" }, { status: 503 });
  }
  const typology = params.id;

  const rulesResult = await listNodes({
    nodeType: "RuleNode",
    page: 0, pageSize: 100,
    filters: { "properties->>typology_code": `eq.${typology}` },
    orderBy: "label.asc",
  });

  const firingsResult = await listNodes({
    nodeType: "ValidationFinding",
    page: 0, pageSize: 25,
    filters: { "properties->>typology_code": `eq.${typology}` },
    orderBy: "created_at.desc",
  });

  // Verdict distribution
  const verdictMix: Record<string, number> = {};
  for (const f of firingsResult.rows) {
    const v = f.properties?.verdict || "unknown";
    verdictMix[v] = (verdictMix[v] || 0) + 1;
  }

  return NextResponse.json({
    typology_code: typology,
    rules: rulesResult.rows,
    rule_count: rulesResult.total,
    recent_firings: firingsResult.rows,
    firing_count: firingsResult.total,
    verdict_mix: verdictMix,
  });
}
