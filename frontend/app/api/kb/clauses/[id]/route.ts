/**
 * GET /api/kb/clauses/[id] — single Section detail + linked rules.
 */
import { NextRequest, NextResponse } from "next/server";
import { getNode, listEdges, hasServiceRoleKey } from "@/lib/kb-supabase";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(_req: NextRequest, { params }: { params: { id: string } }) {
  if (!hasServiceRoleKey()) {
    return NextResponse.json({ error: "service-role key unavailable" }, { status: 503 });
  }
  const clause = await getNode(params.id);
  if (!clause) {
    return NextResponse.json({ error: "clause not found" }, { status: 404 });
  }
  const ruleEdges = await listEdges({ edgeType: "RULE_CITES_CLAUSE", toId: params.id, limit: 50 });
  return NextResponse.json({
    clause,
    linked_rules: ruleEdges.map(e => ({ from_id: e.from_id, properties: e.properties })),
  });
}
