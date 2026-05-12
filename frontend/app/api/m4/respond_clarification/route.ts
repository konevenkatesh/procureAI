/**
 * POST /api/m4/respond_clarification
 *
 * Proxies the new R4-2b endpoint on m4-communicator. Body is the
 * same shape as the backend expects: {tender_id, bidder_id,
 * bidder_name?, question_text, language}.
 *
 * Returns the bilingual Communication that was persisted, including
 * the EN ↔ TE translation produced by Sarvam-M.
 */
import { NextRequest, NextResponse } from "next/server";
import { forwardJson } from "@/lib/cloudRun";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  let body: any = {};
  try {
    body = await req.json();
  } catch {
    body = {};
  }
  const r = await forwardJson("m4", "/respond_clarification", {
    method: "POST",
    body,
  });
  if (!r.ok) {
    return NextResponse.json(
      { error: r.body?.detail || r.body?.error || "backend_error", status: r.status },
      { status: r.status },
    );
  }
  return NextResponse.json(r.body);
}
