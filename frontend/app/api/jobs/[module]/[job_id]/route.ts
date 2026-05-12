/**
 * GET /api/jobs/{module}/{job_id}
 *
 * Polls the named backend's /jobs/{job_id} endpoint with a fresh ID
 * token. Frontend polls every 2s while a job is QUEUED or RUNNING.
 *
 * Returns the shape the backend gave us, plus a normalized `done`
 * boolean so the client can stop polling without inspecting status
 * strings.
 *
 * SSE upgrade path (future):
 *   Switch to `Response` with a `ReadableStream` that pushes
 *   `data: {json}\n\n` lines every 1–2s until status terminal,
 *   then closes. Cloud Run supports streaming responses; the
 *   client just listens with EventSource.
 */
import { NextRequest, NextResponse } from "next/server";
import { forwardJson, ModuleKey } from "@/lib/cloudRun";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const MODULES = new Set(["m1", "m2", "m3", "m4"]);

export async function GET(
  _req: NextRequest,
  { params }: { params: { module: string; job_id: string } },
) {
  const { module, job_id } = params;
  if (!MODULES.has(module)) {
    return NextResponse.json(
      { error: "unknown_module", module },
      { status: 400 },
    );
  }
  const r = await forwardJson(module as ModuleKey, `/jobs/${job_id}`, {
    method: "GET",
  });
  if (!r.ok) {
    return NextResponse.json(
      { error: r.body?.error || "backend_error", status: r.status },
      { status: r.status },
    );
  }
  const status = r.body?.status;
  return NextResponse.json({
    ...r.body,
    done: status === "DONE" || status === "ERROR",
  });
}
