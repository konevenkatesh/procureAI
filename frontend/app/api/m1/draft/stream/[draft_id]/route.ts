/**
 * Server-side SSE pass-through.
 *
 * Browser opens EventSource(/api/m1/draft/stream/{draft_id}); we proxy
 * to the m1-drafter backend's /m1/draft/{draft_id}/stream and pipe the
 * upstream Response.body to the client. Required because the backend
 * lives at localhost:8001 (local) or a different domain (Cloud Run);
 * EventSource is same-origin only.
 */
import { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

export async function GET(
  req: NextRequest,
  { params }: { params: { draft_id: string } },
) {
  const drafterUrl = process.env.M1_DRAFTER_URL || "http://localhost:8001";
  const upstream = `${drafterUrl}/m1/draft/${encodeURIComponent(params.draft_id)}/stream`;

  try {
    const upstreamRes = await fetch(upstream, {
      headers: { Accept: "text/event-stream" },
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
