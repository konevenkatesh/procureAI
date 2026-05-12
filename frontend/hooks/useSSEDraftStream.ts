"use client";

import { useEffect, useReducer, useRef, useState } from "react";
import type {
  SSEEvent,
  LangGraphNode,
  TenderDraftState,
} from "@/types/m1-drafter";
import { LANGGRAPH_NODES_IN_ORDER } from "@/types/m1-drafter";

// ─── Reducer state ──────────────────────────────────────────────────

export interface NodeStatus {
  node: LangGraphNode;
  status: "queued" | "running" | "done" | "error";
  index: number;
  total: number;
  elapsed_ms?: number;
  citations?: any;
  error_message?: string;
  started_at?: number;
}

export interface DraftStreamState {
  draft: Partial<TenderDraftState>;
  nodes: NodeStatus[];
  events: SSEEvent[];               // raw log for the demo's expandable panel
  workflow_complete: boolean;
  workflow_started: boolean;
  total_elapsed_ms?: number;
  errors: string[];
}

const initialNodeStatuses = (): NodeStatus[] =>
  LANGGRAPH_NODES_IN_ORDER.map((node, i) => ({
    node,
    status: "queued",
    index: i + 1,
    total: LANGGRAPH_NODES_IN_ORDER.length,
  }));

function emptyState(): DraftStreamState {
  return {
    draft: {
      general_terms: { eligibility: "", technical: "", legal: "", bid_procedure: "" },
      boq: [],
    },
    nodes: initialNodeStatuses(),
    events: [],
    workflow_complete: false,
    workflow_started: false,
    errors: [],
  };
}

// Dot-path setter on plain object (matches backend gates._set_path).
function setPath(obj: any, path: string, value: any): any {
  if (path === "tender_notice_number") {
    obj[path] = value;
    return obj;
  }
  const parts = path.split(".");
  let cur = obj;
  for (let i = 0; i < parts.length - 1; i++) {
    const p = parts[i];
    if (/^\d+$/.test(p) && Array.isArray(cur)) {
      cur = cur[Number(p)];
    } else if (typeof cur === "object" && cur !== null) {
      if (!(p in cur)) cur[p] = {};
      cur = cur[p];
    } else {
      return obj;
    }
  }
  const last = parts[parts.length - 1];
  if (/^\d+$/.test(last) && Array.isArray(cur)) {
    cur[Number(last)] = value;
  } else if (typeof cur === "object" && cur !== null) {
    cur[last] = value;
  }
  return obj;
}

function getPath(obj: any, path: string): any {
  const parts = path.split(".");
  let cur = obj;
  for (const p of parts) {
    if (cur === undefined || cur === null) return undefined;
    if (/^\d+$/.test(p) && Array.isArray(cur)) cur = cur[Number(p)];
    else if (typeof cur === "object") cur = cur[p];
    else return undefined;
  }
  return cur;
}

function applyEvent(state: DraftStreamState, ev: SSEEvent): DraftStreamState {
  const next = { ...state, events: [...state.events, ev] };

  switch (ev.type) {
    case "node_started": {
      const nodes = state.nodes.map((n) =>
        n.node === ev.node ? { ...n, status: "running" as const, started_at: Date.now() } : n,
      );
      return { ...next, nodes, workflow_started: true };
    }
    case "node_complete": {
      const nodes = state.nodes.map((n) =>
        n.node === ev.node
          ? { ...n, status: "done" as const, elapsed_ms: ev.elapsed_ms, citations: ev.citations }
          : n,
      );
      return { ...next, nodes };
    }
    case "field_update": {
      const draft = JSON.parse(JSON.stringify(state.draft));
      setPath(draft, ev.path, ev.value);
      return { ...next, draft };
    }
    case "text_chunk": {
      const draft = JSON.parse(JSON.stringify(state.draft));
      const existing = getPath(draft, ev.path) || "";
      setPath(draft, ev.path, existing + ev.chunk);
      return { ...next, draft };
    }
    case "table_row_added": {
      const draft = JSON.parse(JSON.stringify(state.draft));
      const cur = getPath(draft, ev.table) || [];
      setPath(draft, ev.table, [...cur, ev.row]);
      return { ...next, draft };
    }
    case "workflow_complete": {
      return {
        ...next,
        workflow_complete: true,
        total_elapsed_ms: ev.total_elapsed_ms,
        // Mark any still-running nodes as done (defensive)
        nodes: state.nodes.map((n) =>
          n.status === "running" ? { ...n, status: "done" as const } : n,
        ),
      };
    }
    case "error": {
      const nodes = state.nodes.map((n) =>
        n.node === (ev.node as any)
          ? { ...n, status: "error" as const, error_message: ev.message }
          : n,
      );
      return { ...next, nodes, errors: [...state.errors, ev.message] };
    }
    case "section_started":
    case "section_complete":
      return next;
    default:
      return next;
  }
}

type Action =
  | { type: "event"; event: SSEEvent }
  | { type: "hydrate"; payload: Partial<TenderDraftState> }
  | { type: "reset" };

function reducer(state: DraftStreamState, action: Action): DraftStreamState {
  switch (action.type) {
    case "event":
      return applyEvent(state, action.event);
    case "hydrate":
      return { ...state, draft: { ...state.draft, ...action.payload } };
    case "reset":
      return emptyState();
    default:
      return state;
  }
}

// ─── Hook ───────────────────────────────────────────────────────────

export function useSSEDraftStream(draftId: string | null) {
  const [state, dispatch] = useReducer(reducer, undefined, emptyState);
  const [connected, setConnected] = useState(false);
  const esRef = useRef<EventSource | null>(null);

  // Initial hydrate: fetch existing draft state (in case workflow already ran)
  useEffect(() => {
    if (!draftId) return;
    let aborted = false;
    fetch(`/api/m1/draft/${draftId}/get`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (aborted || !data) return;
        dispatch({ type: "hydrate", payload: data });
      })
      .catch(() => {});
    return () => {
      aborted = true;
    };
  }, [draftId]);

  // SSE connection
  useEffect(() => {
    if (!draftId) return;
    if (typeof EventSource === "undefined") return;

    const url = `/api/m1/draft/stream/${draftId}`;
    const es = new EventSource(url);
    esRef.current = es;
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    es.onmessage = (msg) => {
      try {
        const ev: SSEEvent = JSON.parse(msg.data);
        dispatch({ type: "event", event: ev });
        if (ev.type === "workflow_complete") {
          es.close();
          setConnected(false);
        }
      } catch (e) {
        // ignore malformed
      }
    };

    return () => {
      es.close();
      esRef.current = null;
    };
  }, [draftId]);

  return { state, dispatch, connected };
}
