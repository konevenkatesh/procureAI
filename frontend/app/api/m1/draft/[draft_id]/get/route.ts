import { NextRequest, NextResponse } from "next/server";
import { forwardJson } from "@/lib/cloudRun";

export const dynamic = "force-dynamic";

export async function GET(
  _req: NextRequest,
  { params }: { params: { draft_id: string } },
) {
  if (process.env.M1_DRAFTER_URL) {
    const result = await forwardJson(
      "m1",
      `/m1/draft/${encodeURIComponent(params.draft_id)}`,
      { method: "GET" },
    );
    return NextResponse.json(result.body, { status: result.status });
  }
  // Local dev fallback
  try {
    const r = await fetch(
      `http://localhost:8001/m1/draft/${encodeURIComponent(params.draft_id)}`,
      { cache: "no-store" },
    );
    const data = await r.json().catch(() => ({}));
    return NextResponse.json(data, { status: r.status });
  } catch (e: any) {
    return NextResponse.json(
      { error: String(e?.message || e) },
      { status: 503 },
    );
  }
}
