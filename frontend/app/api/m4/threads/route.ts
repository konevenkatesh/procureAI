import { NextRequest, NextResponse } from "next/server";
import { m4Get } from "@/lib/m4-proxy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(req: NextRequest) {
  const tid = req.nextUrl.searchParams.get("tender_id");
  const path = tid ? `/m4/threads?tender_id=${encodeURIComponent(tid)}` : "/m4/threads";
  const r = await m4Get(path);
  return NextResponse.json(r.data, { status: r.status });
}
