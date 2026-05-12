/**
 * POST /api/m1/draft/start
 *
 * Server-side proxy to the m1-drafter Cloud Run service. Adapts the
 * frontend wizard payload to the worker contract, attaches a Cloud Run
 * ID token via the metadata server (when running on Cloud Run), and
 * returns the draft_id + stream_url so the frontend can redirect to
 * the live generation view.
 */
import { NextRequest, NextResponse } from "next/server";
import { forwardJson, backendUrl, getIdToken } from "@/lib/cloudRun";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  let body: any;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "invalid json body" }, { status: 400 });
  }
  if (!body.initial_payload) {
    return NextResponse.json(
      { error: "missing initial_payload" },
      { status: 400 },
    );
  }

  // When M1_DRAFTER_URL is set, forward to Cloud Run with ID token (production).
  // When unset, fall back to localhost:8001 unauthenticated (local dev).
  try {
    if (process.env.M1_DRAFTER_URL) {
      const result = await forwardJson("m1", "/m1/run", {
        method: "POST",
        body: {
          tender_id: null,
          params: {
            draft_id: body.draft_id,
            initiator_role: body.initiator_role,
            initiator_id: body.initiator_id,
            initial_payload: body.initial_payload,
          },
        },
      });
      if (!result.ok) {
        return NextResponse.json(
          { error: result.body?.error || result.body?.detail || result.message || "drafter rejected request", status: result.status },
          { status: 502 },
        );
      }
      return NextResponse.json({
        ...result.body,
        draft_id: body.draft_id,
        stream_url: `/api/m1/draft/stream/${body.draft_id}`,
      });
    }

    // Local dev fallback (no auth)
    const localUrl = "http://localhost:8001";
    const upstream = await fetch(`${localUrl}/m1/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tender_id: null,
        params: {
          draft_id: body.draft_id,
          initiator_role: body.initiator_role,
          initiator_id: body.initiator_id,
          initial_payload: body.initial_payload,
        },
      }),
      cache: "no-store",
    });
    const data = await upstream.json().catch(() => ({}));
    if (!upstream.ok) {
      return NextResponse.json(
        { error: data?.detail || data?.error || "drafter rejected request", status: upstream.status },
        { status: 502 },
      );
    }
    return NextResponse.json({
      ...data,
      draft_id: body.draft_id,
      stream_url: `/api/m1/draft/stream/${body.draft_id}`,
    });
  } catch (e: any) {
    return NextResponse.json(
      {
        error: "m1-drafter unreachable",
        detail: String(e?.message || e),
      },
      { status: 503 },
    );
  }
}
