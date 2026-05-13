/**
 * GET /api/kb/rules/[id] — single RuleNode detail + linked clauses + recent firings.
 */
import { NextRequest, NextResponse } from "next/server";
import { getNode, listNodes, listEdges, hasServiceRoleKey } from "@/lib/kb-supabase";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(_req: NextRequest, { params }: { params: { id: string } }) {
  if (!hasServiceRoleKey()) {
    return NextResponse.json({ error: "service-role key unavailable" }, { status: 503 });
  }
  const rule = await getNode(params.id);
  if (!rule) {
    return NextResponse.json({ error: "rule not found" }, { status: 404 });
  }
  if (rule.node_type !== "RuleNode") {
    return NextResponse.json({ error: "node is not a rule" }, { status: 400 });
  }

  // Linked clauses (edges where this rule cites a clause)
  const clauseEdges = await listEdges({ edgeType: "RULE_CITES_CLAUSE", fromId: params.id, limit: 50 });

  // Recent firings: ValidationFinding rows that reference this rule_id
  const recent = await listNodes({
    nodeType: "ValidationFinding",
    page: 0, pageSize: 10,
    filters: { "properties->>rule_id": `eq.${rule.properties?.rule_id || params.id}` },
    orderBy: "created_at.desc",
  });

  return NextResponse.json({
    rule,
    linked_clauses: clauseEdges.map(e => ({ to_id: e.to_id, properties: e.properties })),
    recent_firings: recent.rows,
  });
}
