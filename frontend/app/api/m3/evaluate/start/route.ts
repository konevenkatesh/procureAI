import { NextRequest, NextResponse } from "next/server";
import { m3Post } from "@/lib/m3-proxy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function POST(req: NextRequest) {
  let body: any;
  try { body = await req.json(); } catch { return NextResponse.json({ error: "invalid json" }, { status: 400 }); }
  const r = await m3Post("/m3/evaluate/start", body);
  return NextResponse.json(r.data, { status: r.status });
}
