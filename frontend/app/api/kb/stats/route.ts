/**
 * GET /api/kb/stats — corpus health summary.
 *
 * Returns total counts for each KB-relevant node_type. Sentinel-aligned so
 * the Knowledge Layer header can show "611 rules · 351 findings · 27 lots · 30 sections · 72 templates".
 */
import { NextResponse } from "next/server";
import { countByType, hasServiceRoleKey } from "@/lib/kb-supabase";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET() {
  if (!hasServiceRoleKey()) {
    return NextResponse.json({ error: "service-role key unavailable" }, { status: 503 });
  }
  const [rules, clauses, sbdSections, techSpecs, validationFindings, eligibilityMatrix] =
    await Promise.all([
      countByType("RuleNode"),
      countByType("Section"),
      countByType("SBDSection"),
      countByType("TechSpecTemplate"),
      countByType("ValidationFinding"),
      countByType("EligibilityMatrix"),
    ]);
  return NextResponse.json({
    rules,
    clauses,
    sbdSections,
    techSpecs,
    templates: sbdSections + techSpecs,        // combined for UI display
    validationFindings,
    eligibilityMatrix,
    timestamp: new Date().toISOString(),
  });
}
