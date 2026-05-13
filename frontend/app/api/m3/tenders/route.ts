import { NextResponse } from "next/server";
import { m3Get } from "@/lib/m3-proxy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET() {
  const r = await m3Get("/m3/tenders");
  return NextResponse.json(r.data, { status: r.status });
}
