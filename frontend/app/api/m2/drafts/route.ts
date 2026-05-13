import { NextResponse } from "next/server";
import { m2Get } from "@/lib/m2-proxy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET() {
  const r = await m2Get("/m2/drafts");
  return NextResponse.json(r.data, { status: r.status });
}
