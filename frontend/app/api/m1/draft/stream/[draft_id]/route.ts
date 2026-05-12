/**
 * Server-side SSE pass-through with Cloud Run ID-token injection.
 *
 * Browser opens EventSource(/api/m1/draft/stream/{draft_id}); we proxy
 * to the m1-drafter backend's /m1/draft/{draft_id}/stream and pipe the
 * upstream Response.body to the client.
 *
 * When M1_DRAFTER_URL is set (production), we mint an ID token via
 * the metadata server and pass it as a Bearer header. Local dev hits
 * localhost:8001 unauthenticated.
 */
import { NextRequest } from "next/server";
import { backendUrl, getIdToken } from "@/lib/cloudRun";

export const dynamic = "force-dynamic";

export async function GET(
  req: NextRequest,
  { params }: { params: { draft_id: string } },
) {
  const draftId = encodeURIComponent(params.draft_id);
  const isProd = !!process.env.M1_DRAFTER_URL;
  const base = isProd
    ? backendUrl("m1")
    : "http://localhost:8001";
  const upstreamUrl = `${base}/m1/draft/${draftId}/stream`;

  const headers: Record<string, string> = { Accept: "text/event-stream" };
  if (isProd) {
    const token = await getIdToken(base);
    if (!token) {
      return new Response(
        `data: {"type":"error","node":"system","message":"backend_auth_unavailable"}\n\n`,
        { status: 503, headers: { "Content-Type": "text/event-stream" } },
      );
    }
    headers["Authorization"] = `Bearer ${token}`;
  }

  try {
    const upstreamRes = await fetch(upstreamUrl, {
      headers,
      cache: "no-store",
      signal: req.signal,
    });
    if (!upstreamRes.ok || !upstreamRes.body) {
      return new Response(
        `data: {"type":"error","node":"system","message":"upstream ${upstreamRes.status}"}\n\n`,
        {
          status: upstreamRes.ok ? 502 : upstreamRes.status,
          headers: { "Content-Type": "text/event-stream" },
        },
      );
    }
    return new Response(upstreamRes.body, {
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        Connection: "keep-alive",
        "X-Accel-Buffering": "no",
      },
    });
  } catch (e: any) {
    return new Response(
      `data: {"type":"error","node":"system","message":"${String(e?.message || e).replace(/"/g, "")}"}\n\n`,
      {
        status: 503,
        headers: { "Content-Type": "text/event-stream" },
      },
    );
  }
}
