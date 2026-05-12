/**
 * POST /api/m4/communicate
 *
 * Server-side proxy from the frontend to the m4-communicator Cloud Run
 * service (--no-allow-unauthenticated). Mints an ID token via the
 * metadata server, forwards the body to `<m4-communicator>/m4/run`.
 *
 * Request:  { tender_id?: string, params: {...} }
 * Response: { job_id, status, poll_url, module }
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
  const r = await forwardJson("m4", "/m4/run", { method: "POST", body });
  if (!r.ok) {
    return NextResponse.json(
      { error: r.body?.error || r.message || "backend_error", status: r.status },
      { status: r.status },
    );
  }
  return NextResponse.json({
    job_id: r.body.job_id,
    status: r.body.status,
    module: "m4",
    poll_url: `/api/jobs/m4/${r.body.job_id}`,
  });
}
