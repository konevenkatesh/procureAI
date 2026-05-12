import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export async function POST(
  req: NextRequest,
  { params }: { params: { draft_id: string } },
) {
  let body: any;
  try { body = await req.json(); } catch { return NextResponse.json({ error: "invalid json" }, { status: 400 }); }
  const url = (process.env.M1_DRAFTER_URL || "http://localhost:8001") +
    `/m1/draft/${encodeURIComponent(params.draft_id)}/publish`;
  try {
    const r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...body, draft_id: params.draft_id }),
      cache: "no-store",
    });
    const data = await r.json().catch(() => ({}));
    return NextResponse.json(data, { status: r.status });
  } catch (e: any) {
    return NextResponse.json({ error: String(e?.message || e) }, { status: 503 });
  }
}
