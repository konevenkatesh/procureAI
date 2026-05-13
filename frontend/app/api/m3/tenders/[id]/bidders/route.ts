import { NextRequest, NextResponse } from "next/server";
import { m3Get } from "@/lib/m3-proxy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(_req: NextRequest, { params }: { params: { id: string } }) {
  const r = await m3Get(`/m3/tenders/${encodeURIComponent(params.id)}/bidders`);
  return NextResponse.json(r.data, { status: r.status });
}
