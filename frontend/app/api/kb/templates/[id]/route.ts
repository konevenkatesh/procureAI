/**
 * GET /api/kb/templates/[id] — single template detail.
 *
 * Returns the full template content + placeholders + sample BoQItemSpec rows
 * (for TechSpecTemplate) or sample drafted sections (for SBDSection).
 */
import { NextRequest, NextResponse } from "next/server";
import { getNode, listNodes, hasServiceRoleKey } from "@/lib/kb-supabase";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(_req: NextRequest, { params }: { params: { id: string } }) {
  if (!hasServiceRoleKey()) {
    return NextResponse.json({ error: "service-role key unavailable" }, { status: 503 });
  }
  const tpl = await getNode(params.id);
  if (!tpl) return NextResponse.json({ error: "template not found" }, { status: 404 });

  let samples: any[] = [];
  if (tpl.node_type === "TechSpecTemplate") {
    const disc = tpl.properties?.discipline;
    const subdisc = tpl.properties?.sub_discipline;
    if (disc) {
      const filters: Record<string, string> = {
        "properties->>discipline": `eq.${disc}`,
      };
      if (subdisc) filters["properties->>sub_discipline"] = `eq.${subdisc}`;
      const r = await listNodes({
        nodeType: "BoQItemSpec",
        page: 0, pageSize: 5,
        filters, orderBy: "label.asc",
      });
      samples = r.rows.map(x => ({
        node_id: x.node_id,
        label: x.label,
        short_desc: x.properties?.short_desc,
      }));
    }
  }
  return NextResponse.json({ template: tpl, samples });
}
