/**
 * Cloud Run backend forwarder.
 *
 * The 4 backend services (m1/m2/m3/m4) are deployed
 * `--no-allow-unauthenticated`. Calls from this Next.js frontend
 * must carry an ID token signed by the GCP metadata server,
 * audience-bound to the backend URL.
 *
 * When this code runs on Cloud Run, `http://metadata.google.internal`
 * returns a raw JWT for the runtime service account. Outside Cloud
 * Run (local `npm run dev`), the metadata server is unreachable —
 * we return null and the route handler decides whether to 503 or
 * fall back to unauthenticated dev mode.
 *
 * Audience must EXACTLY match the backend URL (no trailing slash).
 * Cloud Run validates audience against its own URL before serving.
 */

const META_BASE =
  "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity";

const BACKENDS: Record<string, string | undefined> = {
  m1: process.env.M1_DRAFTER_URL,
  m2: process.env.M2_VALIDATOR_URL,
  m3: process.env.M3_EVALUATOR_URL,
  m4: process.env.M4_COMMUNICATOR_URL,
};

export type ModuleKey = "m1" | "m2" | "m3" | "m4";

export function backendUrl(module: ModuleKey): string {
  const u = BACKENDS[module];
  if (!u) {
    throw new Error(`Backend URL env not set for ${module}`);
  }
  return u.replace(/\/+$/, "");
}

/** Mint an ID token for a backend audience. Returns null off-Cloud-Run. */
export async function getIdToken(audience: string): Promise<string | null> {
  try {
    const r = await fetch(
      `${META_BASE}?audience=${encodeURIComponent(audience)}`,
      { headers: { "Metadata-Flavor": "Google" }, cache: "no-store" },
    );
    if (!r.ok) return null;
    return (await r.text()).trim();
  } catch {
    // metadata.google.internal unreachable → not on Cloud Run
    return null;
  }
}

export interface ForwardResult<T = any> {
  ok: boolean;
  status: number;
  body: T;
  message?: string;
}

/**
 * Forward a JSON request to a backend Cloud Run service.
 * Adds the Bearer ID token automatically.
 */
export async function forwardJson<T = any>(
  module: ModuleKey,
  path: string,
  init: { method?: string; body?: any } = {},
): Promise<ForwardResult<T>> {
  const base = backendUrl(module);
  const url = base + (path.startsWith("/") ? path : `/${path}`);
  const token = await getIdToken(base);
  if (!token) {
    return {
      ok: false,
      status: 503,
      body: { error: "backend_auth_unavailable" } as unknown as T,
      message:
        "Could not mint ID token via metadata server. " +
        "This usually means the Next.js server isn't on Cloud Run.",
    };
  }
  const headers: Record<string, string> = {
    Authorization: `Bearer ${token}`,
    "Content-Type": "application/json",
  };
  const res = await fetch(url, {
    method: init.method || "POST",
    headers,
    body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
    cache: "no-store",
  });
  let body: any;
  try {
    body = await res.json();
  } catch {
    body = { error: "non_json_backend_response" };
  }
  return { ok: res.ok, status: res.status, body };
}
