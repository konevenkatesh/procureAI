import { NextRequest, NextResponse } from "next/server";
import { m4Get } from "@/lib/m4-proxy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(_req: NextRequest, { params }: { params: { id: string } }) {
  const r = await m4Get(`/m4/threads/${encodeURIComponent(params.id)}`);
  return NextResponse.json(r.data, { status: r.status });
}
