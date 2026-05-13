/**
 * GET /api/kb/rules?page=N&pageSize=M&search=text&source=GFR&severity=WARNING
 *
 * Paginated list of RuleNode rows from kg_nodes.
 * Returns: { rows: [...], total, page, pageSize }.
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
  const severity = sp.get("severity") || undefined;
  const source = sp.get("source") || undefined;
  const typology = sp.get("typology") || undefined;

  const filters: Record<string, string> = {};
  if (severity) filters["properties->>severity"] = `eq.${severity}`;
  if (source) filters["properties->>source"] = `ilike.*${source}*`;
  if (typology) filters["properties->>typology_code"] = `eq.${typology}`;

  const result = await listNodes({
    nodeType: "RuleNode",
    page, pageSize, search, filters,
    orderBy: "label.asc",
  });
  return NextResponse.json(result);
}
