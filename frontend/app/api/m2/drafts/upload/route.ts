/**
 * R13 — Multipart upload proxy for draft RFPs.
 * Forwards FormData to m2-validator /m2/drafts/upload.
 */
import { NextRequest, NextResponse } from "next/server";
import { backendUrl, getIdToken } from "@/lib/cloudRun";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function POST(req: NextRequest) {
  const incoming = await req.formData();
  const file = incoming.get("file");
  if (!file || !(file instanceof File)) {
    return NextResponse.json({ error: "missing 'file' part" }, { status: 400 });
  }
  const forward = new FormData();
  forward.append("file", file, file.name);

  try {
    if (process.env.M2_VALIDATOR_URL) {
      const base = backendUrl("m2");
      const token = await getIdToken(base);
      const r = await fetch(`${base}/m2/drafts/upload`, {
        method: "POST",
        body: forward,
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        cache: "no-store",
      });
      const data = await r.json().catch(() => ({}));
      return NextResponse.json(data, { status: r.status });
    }
    const r = await fetch("http://localhost:8002/m2/drafts/upload", { method: "POST", body: forward });
    const data = await r.json().catch(() => ({}));
    return NextResponse.json(data, { status: r.status });
  } catch (e: any) {
    return NextResponse.json({ error: String(e?.message || e) }, { status: 503 });
  }
}
