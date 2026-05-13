import { NextRequest, NextResponse } from "next/server";
import { m2Post } from "@/lib/m2-proxy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function POST(req: NextRequest) {
  let body: any;
  try { body = await req.json(); } catch { return NextResponse.json({ error: "invalid json" }, { status: 400 }); }
  const r = await m2Post("/m2/validate/start", body);
  return NextResponse.json(r.data, { status: r.status });
}
