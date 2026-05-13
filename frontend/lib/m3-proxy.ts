/**
 * R11 — Shared helpers for proxying Module 3 evaluator requests to Cloud Run.
 * Same auth pattern as M1: runtime SA ID token on Cloud Run, localhost fallback for dev.
 */
import { backendUrl, getIdToken } from "@/lib/cloudRun";

export async function m3Get<T = any>(path: string): Promise<{ ok: boolean; status: number; data: T | null }> {
  try {
    if (process.env.M3_EVALUATOR_URL) {
      const base = backendUrl("m3");
      const token = await getIdToken(base);
      const r = await fetch(`${base}${path}`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        cache: "no-store",
      });
      const data = await r.json().catch(() => null);
      return { ok: r.ok, status: r.status, data };
    }
    const r = await fetch(`http://localhost:8003${path}`, { cache: "no-store" });
    const data = await r.json().catch(() => null);
    return { ok: r.ok, status: r.status, data };
  } catch (e: any) {
    return { ok: false, status: 503, data: { error: String(e?.message || e) } as any };
  }
}

export async function m3Post<T = any>(path: string, body: any): Promise<{ ok: boolean; status: number; data: T | null }> {
  try {
    if (process.env.M3_EVALUATOR_URL) {
      const base = backendUrl("m3");
      const token = await getIdToken(base);
      const r = await fetch(`${base}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        body: JSON.stringify(body),
        cache: "no-store",
      });
      const data = await r.json().catch(() => null);
      return { ok: r.ok, status: r.status, data };
    }
    const r = await fetch(`http://localhost:8003${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await r.json().catch(() => null);
    return { ok: r.ok, status: r.status, data };
  } catch (e: any) {
    return { ok: false, status: 503, data: { error: String(e?.message || e) } as any };
  }
}
