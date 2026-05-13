/**
 * R11 — SSE proxy for m3-evaluator evaluation stream.
 * Streams events from Cloud Run to the browser via this Next.js route,
 * preserving the X-Accel-Buffering header so GLB doesn't aggregate chunks.
 */
import { NextRequest } from "next/server";
import { backendUrl, getIdToken } from "@/lib/cloudRun";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(_req: NextRequest, { params }: { params: { runId: string } }) {
  const path = `/m3/evaluate/${encodeURIComponent(params.runId)}/stream`;
  const upstream = process.env.M3_EVALUATOR_URL
    ? `${backendUrl("m3")}${path}`
    : `http://localhost:8003${path}`;

  let headers: Record<string, string> = { Accept: "text/event-stream" };
  if (process.env.M3_EVALUATOR_URL) {
    const token = await getIdToken(backendUrl("m3"));
    if (token) headers.Authorization = `Bearer ${token}`;
  }

  const upstreamResp = await fetch(upstream, { headers, cache: "no-store" });
  if (!upstreamResp.ok || !upstreamResp.body) {
    return new Response(`upstream HTTP ${upstreamResp.status}`, { status: 502 });
  }

  return new Response(upstreamResp.body, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      "Connection": "keep-alive",
      "X-Accel-Buffering": "no",
    },
  });
}
