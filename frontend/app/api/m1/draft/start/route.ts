/**
 * POST /api/m1/draft/start
 *
 * Server-side proxy to the m1-drafter Cloud Run service. Adapts the
 * frontend wizard payload to the worker contract, attaches a Cloud Run
 * ID token if needed (deferred to M1.10), and returns the draft_id +
 * stream_url so the frontend can redirect to the live generation view.
 *
 * Local dev: forwards to http://localhost:8001/m1/run (uvicorn).
 * Production: forwards to M1_DRAFTER_URL (Cloud Run) with ID token.
 */
import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  let body: any;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "invalid json body" }, { status: 400 });
  }

  // Required: draft_id, initiator_role, initiator_id, initial_payload
  if (!body.initial_payload) {
    return NextResponse.json(
      { error: "missing initial_payload" },
      { status: 400 },
    );
  }

  const drafterUrl =
    process.env.M1_DRAFTER_URL || "http://localhost:8001";

  try {
    const upstream = await fetch(`${drafterUrl}/m1/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tender_id: null,                              // assigned at PUBLISHED
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
      job_id: data.job_id,
      status: data.status,
      poll_url: data.poll_url,
      draft_id: body.draft_id,
      stream_url: `/api/m1/draft/stream/${body.draft_id}`,
    });
  } catch (e: any) {
    return NextResponse.json(
      {
        error: "m1-drafter unreachable",
        detail: String(e?.message || e),
        hint: "Is the local m1-drafter running at port 8001? (cd services/m1-drafter && uvicorn app.main:app --reload --port 8001)",
      },
      { status: 503 },
    );
  }
}
