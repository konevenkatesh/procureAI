import { NextRequest, NextResponse } from "next/server";
import { m4Post } from "@/lib/m4-proxy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function POST(req: NextRequest, { params }: { params: { id: string } }) {
  let body: any;
  try { body = await req.json(); } catch { return NextResponse.json({ error: "invalid json" }, { status: 400 }); }
  const r = await m4Post(`/m4/threads/${encodeURIComponent(params.id)}/translate`, body);
  return NextResponse.json(r.data, { status: r.status });
}
