/**
 * R10.3 — Vertex AI REST helpers for the BOT chat backend.
 *
 * Mints OAuth 2.0 access tokens via the GCP metadata server (Cloud Run runtime SA)
 * for Vertex AI's text-embedding-005 + Gemini Flash endpoints. Falls back to
 * a configured GCP_ACCESS_TOKEN env var if metadata server unreachable (local dev).
 *
 * No new packages — pure fetch over Node runtime.
 */

const GCP_PROJECT_ID = process.env.GCP_PROJECT_ID || "procureai-prod";
const VERTEX_LOCATION = process.env.VERTEX_LOCATION || "us-central1";
const FLASH_MODEL_ID = "gemini-2.5-flash";
const EMBED_MODEL_ID = "text-embedding-005";

let _tokenCache: { token: string; expires_at: number } | null = null;

async function getAccessToken(): Promise<string | null> {
  const now = Date.now();
  if (_tokenCache && _tokenCache.expires_at > now + 30_000) {
    return _tokenCache.token;
  }
  // 1) Metadata server (Cloud Run)
  try {
    const r = await fetch(
      "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
      { headers: { "Metadata-Flavor": "Google" }, signal: AbortSignal.timeout(3000) },
    );
    if (r.ok) {
      const data = (await r.json()) as { access_token: string; expires_in: number };
      _tokenCache = {
        token: data.access_token,
        expires_at: now + (data.expires_in * 1000),
      };
      return data.access_token;
    }
  } catch {
    // fall through
  }
  // 2) Manual env override (local dev or test)
  if (process.env.GCP_ACCESS_TOKEN) {
    _tokenCache = { token: process.env.GCP_ACCESS_TOKEN, expires_at: now + (30 * 60 * 1000) };
    return process.env.GCP_ACCESS_TOKEN;
  }
  return null;
}

export async function embedQuery(text: string): Promise<number[] | null> {
  const token = await getAccessToken();
  if (!token) return null;
  const url = `https://${VERTEX_LOCATION}-aiplatform.googleapis.com/v1/projects/${GCP_PROJECT_ID}/locations/${VERTEX_LOCATION}/publishers/google/models/${EMBED_MODEL_ID}:predict`;
  const body = {
    instances: [{ task_type: "RETRIEVAL_QUERY", content: text.slice(0, 20000) }],
  };
  try {
    const r = await fetch(url, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(15000),
    });
    if (!r.ok) return null;
    const data = (await r.json()) as { predictions: Array<{ embeddings: { values: number[] } }> };
    return data.predictions[0]?.embeddings?.values ?? null;
  } catch {
    return null;
  }
}

export async function* streamGemini(
  prompt: string,
  options: { systemInstruction?: string; maxOutputTokens?: number; temperature?: number } = {},
): AsyncGenerator<{ delta?: string; usage?: any; error?: string }> {
  const token = await getAccessToken();
  if (!token) {
    yield { error: "GCP token unavailable" };
    return;
  }
  const url = `https://${VERTEX_LOCATION}-aiplatform.googleapis.com/v1/projects/${GCP_PROJECT_ID}/locations/${VERTEX_LOCATION}/publishers/google/models/${FLASH_MODEL_ID}:streamGenerateContent?alt=sse`;
  const body: any = {
    contents: [{ role: "user", parts: [{ text: prompt }] }],
    generationConfig: {
      maxOutputTokens: options.maxOutputTokens ?? 1024,
      temperature: options.temperature ?? 0.2,
      thinkingConfig: { thinkingBudget: 0 },     // R7.4 lesson
    },
  };
  if (options.systemInstruction) {
    body.systemInstruction = { parts: [{ text: options.systemInstruction }] };
  }

  let resp: Response;
  try {
    resp = await fetch(url, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(60_000),
    });
  } catch (e: any) {
    yield { error: `Vertex stream failed: ${e?.message || e}` };
    return;
  }
  if (!resp.ok || !resp.body) {
    const text = await resp.text().catch(() => "");
    yield { error: `Vertex HTTP ${resp.status}: ${text.slice(0, 200)}` };
    return;
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let usage: any = undefined;
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    // SSE chunks: lines starting with "data: " separated by \n\n
    let nl: number;
    while ((nl = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, nl);
      buf = buf.slice(nl + 2);
      for (const line of chunk.split("\n")) {
        if (!line.startsWith("data:")) continue;
        const json = line.slice(5).trim();
        if (!json) continue;
        try {
          const obj = JSON.parse(json);
          const cand = obj.candidates?.[0];
          const text = cand?.content?.parts?.map((p: any) => p.text ?? "").join("") || "";
          if (text) yield { delta: text };
          if (obj.usageMetadata) usage = obj.usageMetadata;
        } catch { /* malformed, skip */ }
      }
    }
  }
  if (usage) yield { usage };
}
