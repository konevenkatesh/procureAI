/**
 * Lazy Supabase REST helpers. URL and anon key are read at call time
 * (not at module-import time) so that the build doesn't crash when
 * env vars aren't yet loaded during page-data collection.
 *
 * Read-only via PostgREST + anon key. RLS enforces SELECT-only access.
 */

function getEnv() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  return { url, anonKey };
}

export async function fetchAll(
  table: string,
  params: Record<string, string> = {},
  rangeEnd: number = 500,
) {
  const { url, anonKey } = getEnv();
  if (!url || !anonKey) return [];
  const qs = new URLSearchParams(params).toString();
  try {
    const res = await fetch(`${url}/rest/v1/${table}?${qs}`, {
      headers: {
        apikey: anonKey,
        Authorization: `Bearer ${anonKey}`,
        Range: `0-${rangeEnd}`,
      },
      cache: "no-store",
    });
    if (!res.ok) return [];
    return res.json();
  } catch {
    return [];
  }
}

export async function countRows(
  table: string,
  params: Record<string, string> = {},
) {
  const { url, anonKey } = getEnv();
  if (!url || !anonKey) return 0;
  const qs = new URLSearchParams({ ...params, select: "node_id" }).toString();
  try {
    const res = await fetch(`${url}/rest/v1/${table}?${qs}`, {
      headers: {
        apikey: anonKey,
        Authorization: `Bearer ${anonKey}`,
        Prefer: "count=exact",
        Range: "0-0",
      },
      cache: "no-store",
    });
    const cr = res.headers.get("Content-Range");
    return cr ? parseInt(cr.split("/")[1] || "0", 10) : 0;
  } catch {
    return 0;
  }
}
