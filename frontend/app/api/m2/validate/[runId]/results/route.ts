import { NextRequest, NextResponse } from "next/server";
import { m2Get } from "@/lib/m2-proxy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(_req: NextRequest, { params }: { params: { runId: string } }) {
  const r = await m2Get(`/m2/validate/${encodeURIComponent(params.runId)}/results`);
  return NextResponse.json(r.data, { status: r.status });
}
