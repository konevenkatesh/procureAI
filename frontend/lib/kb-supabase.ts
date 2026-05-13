/**
 * R10.1 — Knowledge Layer Supabase helper (service-role REST).
 *
 * Read-only over kg_nodes. Uses SUPABASE_SERVICE_ROLE_KEY (server-side
 * only) for direct PostgREST queries with full SELECT access regardless
 * of RLS. This module is NOT exported to the client bundle — its callers
 * are Next.js API route handlers under app/api/kb/**.
 */

const SUPABASE_REST_URL =
  process.env.SUPABASE_REST_URL || process.env.NEXT_PUBLIC_SUPABASE_URL || "";
const SUPABASE_SERVICE_ROLE_KEY =
  process.env.SUPABASE_SERVICE_ROLE_KEY ||
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ||
  "";

function headers(extras: Record<string, string> = {}): Record<string, string> {
  return {
    apikey: SUPABASE_SERVICE_ROLE_KEY,
    Authorization: `Bearer ${SUPABASE_SERVICE_ROLE_KEY}`,
    "Content-Type": "application/json",
    ...extras,
  };
}

export interface KgNodeRow {
  node_id: string;
  node_type: string;
  doc_id: string | null;
  label: string;
  properties: Record<string, any>;
  source_ref?: string | null;
  created_at?: string;
}

export interface ListResult<T = KgNodeRow> {
  rows: T[];
  total: number;
  page: number;
  pageSize: number;
}

/**
 * Paginated list of kg_nodes by node_type with optional Postgrest filters.
 * Returns total via Content-Range header.
 */
export async function listNodes(opts: {
  nodeType: string | string[];
  page?: number;
  pageSize?: number;
  search?: string;            // ILIKE against label
  filters?: Record<string, string>;  // e.g. { 'properties->>severity': 'eq.WARNING' }
  orderBy?: string;           // e.g. 'created_at.desc'
}): Promise<ListResult> {
  const page = Math.max(0, opts.page ?? 0);
  const pageSize = Math.min(200, Math.max(1, opts.pageSize ?? 25));
  const offset = page * pageSize;
  const offsetEnd = offset + pageSize - 1;

  const params: Record<string, string> = {
    select: "node_id,node_type,doc_id,label,properties,source_ref,created_at",
  };
  if (Array.isArray(opts.nodeType)) {
    params.node_type = `in.(${opts.nodeType.join(",")})`;
  } else {
    params.node_type = `eq.${opts.nodeType}`;
  }
  if (opts.search) {
    params.label = `ilike.*${opts.search.replace(/[%_]/g, "")}*`;
  }
  if (opts.filters) {
    for (const [k, v] of Object.entries(opts.filters)) {
      params[k] = v;
    }
  }
  if (opts.orderBy) {
    params.order = opts.orderBy;
  } else {
    params.order = "created_at.desc";
  }

  const qs = new URLSearchParams(params).toString();
  const url = `${SUPABASE_REST_URL}/rest/v1/kg_nodes?${qs}`;
  const res = await fetch(url, {
    headers: headers({
      Range: `${offset}-${offsetEnd}`,
      Prefer: "count=exact",
    }),
    cache: "no-store",
  });
  if (!res.ok) {
    return { rows: [], total: 0, page, pageSize };
  }
  const rows = (await res.json()) as KgNodeRow[];
  const contentRange = res.headers.get("Content-Range") || "0/0";
  const total = parseInt(contentRange.split("/")[1] || "0", 10);
  return { rows, total, page, pageSize };
}

/** Fetch a single kg_node by node_id. */
export async function getNode(nodeId: string): Promise<KgNodeRow | null> {
  const params = new URLSearchParams({
    node_id: `eq.${nodeId}`,
    select: "node_id,node_type,doc_id,label,properties,source_ref,created_at",
  });
  const url = `${SUPABASE_REST_URL}/rest/v1/kg_nodes?${params}`;
  const res = await fetch(url, { headers: headers(), cache: "no-store" });
  if (!res.ok) return null;
  const rows = (await res.json()) as KgNodeRow[];
  return rows[0] ?? null;
}

/** Count by node_type (single call per type). Used by stats. */
export async function countByType(nodeType: string): Promise<number> {
  const params = new URLSearchParams({
    node_type: `eq.${nodeType}`,
    select: "node_id",
  });
  const url = `${SUPABASE_REST_URL}/rest/v1/kg_nodes?${params}`;
  const res = await fetch(url, {
    headers: headers({ Range: "0-0", Prefer: "count=exact" }),
    cache: "no-store",
  });
  if (!res.ok) return 0;
  const cr = res.headers.get("Content-Range") || "0/0";
  return parseInt(cr.split("/")[1] || "0", 10);
}

/** Generic kg_edges fetch — used for rule→clause linkage. */
export async function listEdges(opts: {
  edgeType?: string;
  fromId?: string;
  toId?: string;
  limit?: number;
}): Promise<any[]> {
  const params: Record<string, string> = {
    select: "edge_id,edge_type,from_id,to_id,properties",
  };
  if (opts.edgeType) params.edge_type = `eq.${opts.edgeType}`;
  if (opts.fromId) params.from_id = `eq.${opts.fromId}`;
  if (opts.toId) params.to_id = `eq.${opts.toId}`;
  const qs = new URLSearchParams(params).toString();
  const url = `${SUPABASE_REST_URL}/rest/v1/kg_edges?${qs}`;
  const res = await fetch(url, {
    headers: headers({ Range: `0-${(opts.limit ?? 50) - 1}` }),
    cache: "no-store",
  });
  if (!res.ok) return [];
  return res.json();
}

export function hasServiceRoleKey(): boolean {
  return !!SUPABASE_SERVICE_ROLE_KEY && !!SUPABASE_REST_URL;
}
