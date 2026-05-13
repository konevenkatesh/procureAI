/**
 * GET /api/kb/templates?page=N&pageSize=M&search=text&discipline=HVAC&kind=tech|sbd
 *
 * Combined view across TechSpecTemplate (R7.3 BoQ templates) +
 * SBDSection (R7.1 9-section templates). Each row carries a synthetic
 * `kind` field so the UI can group correctly.
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
  const discipline = sp.get("discipline") || undefined;
  const kind = sp.get("kind") || undefined;             // 'tech' | 'sbd' | null=both

  const nodeTypes: string[] = [];
  if (!kind || kind === "tech") nodeTypes.push("TechSpecTemplate");
  if (!kind || kind === "sbd") nodeTypes.push("SBDSection");

  const filters: Record<string, string> = {};
  if (discipline) filters["properties->>discipline"] = `eq.${discipline}`;

  const result = await listNodes({
    nodeType: nodeTypes,
    page, pageSize, search, filters,
    orderBy: "label.asc",
  });
  // Add synthetic 'kind' for UI display
  return NextResponse.json({
    ...result,
    rows: result.rows.map(r => ({
      ...r,
      kind: r.node_type === "TechSpecTemplate" ? "tech" : "sbd",
    })),
  });
}
