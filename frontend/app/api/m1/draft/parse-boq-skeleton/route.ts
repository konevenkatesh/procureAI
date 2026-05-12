/**
 * POST /api/m1/draft/parse-boq-skeleton
 *
 * Multipart proxy to the m1-drafter `/m1/parse-boq-skeleton` endpoint.
 * Used by the Step 6 BoQ uploader to preview parsed rows before the
 * officer hits "Generate Tender Draft" on Step 7.
 *
 * Production path forwards to Cloud Run with a runtime SA ID token.
 * Local dev path posts to http://localhost:8001 unauthenticated.
 */
import { NextRequest, NextResponse } from "next/server";
import { backendUrl, getIdToken } from "@/lib/cloudRun";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";  // needs Buffer/FormData re-stream

export async function POST(req: NextRequest) {
  // Pass through the multipart body as-is. We re-attach an Authorization
  // header for the upstream Cloud Run call if we're running on Cloud Run.
  const incoming = await req.formData();
  const file = incoming.get("file");
  if (!file || !(file instanceof File)) {
    return NextResponse.json({ error: "missing 'file' part" }, { status: 400 });
  }

  const forward = new FormData();
  forward.append("file", file, file.name);

  try {
    if (process.env.M1_DRAFTER_URL) {
      const baseUrl = backendUrl("m1");
      const token = await getIdToken(baseUrl);
      const upstream = await fetch(`${baseUrl}/m1/parse-boq-skeleton`, {
        method: "POST",
        body: forward,
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        cache: "no-store",
      });
      const data = await upstream.json().catch(() => ({}));
      if (!upstream.ok) {
        return NextResponse.json(
          { error: data?.detail || data?.error || "parse rejected", status: upstream.status },
          { status: 502 },
        );
      }
      return NextResponse.json(data);
    }

    // Local dev fallback
    const upstream = await fetch(`http://localhost:8001/m1/parse-boq-skeleton`, {
      method: "POST",
      body: forward,
      cache: "no-store",
    });
    const data = await upstream.json().catch(() => ({}));
    if (!upstream.ok) {
      return NextResponse.json(
        { error: data?.detail || data?.error || "parse rejected", status: upstream.status },
        { status: 502 },
      );
    }
    return NextResponse.json(data);
  } catch (e: any) {
    return NextResponse.json(
      { error: "m1-drafter unreachable", detail: String(e?.message || e) },
      { status: 503 },
    );
  }
}
