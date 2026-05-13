import { NextRequest } from "next/server";
import { backendUrl, getIdToken } from "@/lib/cloudRun";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(_req: NextRequest, { params }: { params: { runId: string } }) {
  const path = `/m2/validate/${encodeURIComponent(params.runId)}/stream`;
  const upstream = process.env.M2_VALIDATOR_URL
    ? `${backendUrl("m2")}${path}` : `http://localhost:8002${path}`;
  let headers: Record<string, string> = { Accept: "text/event-stream" };
  if (process.env.M2_VALIDATOR_URL) {
    const token = await getIdToken(backendUrl("m2"));
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
      "X-Accel-Buffering": "no",
    },
  });
}
