import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export async function GET(
  _req: NextRequest,
  { params }: { params: { draft_id: string } },
) {
  const url = (process.env.M1_DRAFTER_URL || "http://localhost:8001") +
    `/m1/draft/${encodeURIComponent(params.draft_id)}`;
  try {
    const r = await fetch(url, { cache: "no-store" });
    const data = await r.json().catch(() => ({}));
    return NextResponse.json(data, { status: r.status });
  } catch (e: any) {
    return NextResponse.json(
      { error: String(e?.message || e) },
      { status: 503 },
    );
  }
}
