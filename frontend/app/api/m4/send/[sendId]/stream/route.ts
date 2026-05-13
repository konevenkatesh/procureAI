import { NextRequest } from "next/server";
import { backendUrl, getIdToken } from "@/lib/cloudRun";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(_req: NextRequest, { params }: { params: { sendId: string } }) {
  const path = `/m4/send/${encodeURIComponent(params.sendId)}/stream`;
  const upstream = process.env.M4_COMMUNICATOR_URL
    ? `${backendUrl("m4")}${path}` : `http://localhost:8004${path}`;
  let headers: Record<string, string> = { Accept: "text/event-stream" };
  if (process.env.M4_COMMUNICATOR_URL) {
    const token = await getIdToken(backendUrl("m4"));
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
