/**
 * GET /api/kb/clauses?page=N&pageSize=M&search=text&type=ELIGIBILITY
 *
 * Paginated list of clause-bearing Section rows (Section node_type carries
 * the actual clause text from the regulatory document corpus).
 */
import { NextRequest, NextResponse } from "next/server";
import { listNodes, hasServiceRoleKey } from "@/lib/kb-supabase";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(req: NextRequest) {
  if (!hasServiceRoleKey()) {
    return NextResponse.json({ error: "service-role key unavailable" }, { status: 503 });
  }
  const sp = req.nextUrl.searchParams;
  const page = parseInt(sp.get("page") || "0", 10);
  const pageSize = parseInt(sp.get("pageSize") || "25", 10);
  const search = sp.get("search") || undefined;
  const clauseType = sp.get("type") || undefined;

  const filters: Record<string, string> = {};
  if (clauseType) filters["properties->>clause_type"] = `eq.${clauseType}`;

  const result = await listNodes({
    nodeType: "Section",
    page, pageSize, search, filters,
    orderBy: "label.asc",
  });
  return NextResponse.json(result);
}
