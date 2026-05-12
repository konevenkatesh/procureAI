import { NextRequest, NextResponse } from "next/server";
import { forwardJson } from "@/lib/cloudRun";

export const dynamic = "force-dynamic";

export async function POST(
  req: NextRequest,
  { params }: { params: { draft_id: string } },
) {
  let body: any;
  try { body = await req.json(); } catch { return NextResponse.json({ error: "invalid json" }, { status: 400 }); }
  const fullBody = { ...body, draft_id: params.draft_id };

  if (process.env.M1_DRAFTER_URL) {
    const result = await forwardJson(
      "m1",
      `/m1/draft/${encodeURIComponent(params.draft_id)}/approve`,
      { method: "POST", body: fullBody },
    );
    return NextResponse.json(result.body, { status: result.status });
  }
  // Local dev
  try {
    const r = await fetch(
      `http://localhost:8001/m1/draft/${encodeURIComponent(params.draft_id)}/approve`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(fullBody),
        cache: "no-store",
      },
    );
    const data = await r.json().catch(() => ({}));
    return NextResponse.json(data, { status: r.status });
  } catch (e: any) {
    return NextResponse.json({ error: String(e?.message || e) }, { status: 503 });
  }
}
