/**
 * POST /api/kb/chat — RAG chat over procurement corpus (rules + clauses + templates).
 *
 * Flow:
 *   1. Embed last user message via Vertex AI text-embedding-005
 *   2. pgvector cosine search across kg_nodes (RuleNode + Section + TechSpecTemplate + SBDSection)
 *   3. Build context with top-5 results + their snippets
 *   4. Gemini Flash streaming generation with citation discipline
 *   5. SSE stream chunks → final 'sources' event → 'done' event
 *
 * Body: { messages: [{role, content}], session_id }
 * Response: SSE stream with event types: chunk | sources | done | error
 */
import { NextRequest } from "next/server";
import { embedQuery, generateGemini, streamGemini } from "@/lib/vertex-rest";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const SUPABASE_REST_URL =
  process.env.SUPABASE_REST_URL || process.env.NEXT_PUBLIC_SUPABASE_URL || "";
const SUPABASE_SERVICE_ROLE_KEY =
  process.env.SUPABASE_SERVICE_ROLE_KEY ||
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ||
  "";

const SYSTEM_INSTRUCTION = `You are ProcureAI, an assistant for Andhra Pradesh State Government procurement officers.

You answer questions about: procurement rules (GFR / AP-GO / CVC / MPW), eligibility clauses, bidding-document templates (NIT, ITB, BoQ, GCC, PCC, SCC), and validation typologies.

CRITICAL RULES:
1. Answer ONLY using the retrieved context below. Do NOT invent facts.
2. Cite sources inline using the format [Rule:NODE_ID] or [Clause:NODE_ID] or [Template:NODE_ID]
   where NODE_ID is the exact node_id from the retrieved context.
3. If the retrieved context does not contain the answer, say so explicitly:
   "I don't have enough information in the corpus to answer that confidently."
4. Keep answers concise (2-4 paragraphs max) and structured. Use bullet points for lists of rules/standards.
5. English only. No Telugu.
6. Never speculate about legal interpretations beyond what the cited sources state.`;


// ─── In-memory rate limit (30/min per session) ───────────────────────


const _rateBuckets = new Map<string, { count: number; reset_at: number }>();

function checkRateLimit(sessionId: string): boolean {
  const now = Date.now();
  const bucket = _rateBuckets.get(sessionId);
  if (!bucket || bucket.reset_at < now) {
    _rateBuckets.set(sessionId, { count: 1, reset_at: now + 60_000 });
    return true;
  }
  if (bucket.count >= 30) return false;
  bucket.count += 1;
  return true;
}


// ─── pgvector retrieval ──────────────────────────────────────────────


interface RetrievedDoc {
  node_id: string;
  node_type: string;
  label: string;
  snippet: string;
  distance: number;
}

function _vectorLiteral(v: number[]): string {
  return "[" + v.map(x => x.toFixed(7)).join(",") + "]";
}

async function retrieveTopK(embedding: number[], k: number = 5): Promise<RetrievedDoc[]> {
  // PostgREST doesn't expose pgvector operators directly via the auto-generated API.
  // Workaround: use a stored function. If unavailable, fall back to label-based search.
  // For R10 we use Supabase's `rpc` endpoint with a function defined as:
  //   create function kb_chat_retrieve(query_embedding vector(768), top_k int)
  //   returns table(node_id uuid, node_type text, label text, snippet text, distance float) ...
  // If the RPC doesn't exist yet, we degrade gracefully.
  try {
    const r = await fetch(`${SUPABASE_REST_URL}/rest/v1/rpc/kb_chat_retrieve`, {
      method: "POST",
      headers: {
        apikey: SUPABASE_SERVICE_ROLE_KEY,
        Authorization: `Bearer ${SUPABASE_SERVICE_ROLE_KEY}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ query_embedding: _vectorLiteral(embedding), top_k: k }),
    });
    if (r.ok) {
      const rows = (await r.json()) as RetrievedDoc[];
      return rows;
    }
  } catch { /* fall through */ }

  // Fallback: PostgREST select with embedding <=> operator via raw `order` param.
  // PostgREST doesn't support pgvector operators in `order`, so this isn't actually
  // possible without the RPC. Return empty and let the LLM answer "no context".
  return [];
}


// ─── SSE helpers ──────────────────────────────────────────────────────


function sse(event: string, data: any): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}


// ─── Main handler ─────────────────────────────────────────────────────


export async function POST(req: NextRequest) {
  let body: any;
  try {
    body = await req.json();
  } catch {
    return new Response("invalid json", { status: 400 });
  }
  const messages = body.messages || [];
  const sessionId = body.session_id || "default";
  const userMsg = [...messages].reverse().find((m: any) => m.role === "user");
  if (!userMsg?.content) {
    return new Response("missing user message", { status: 400 });
  }

  if (!checkRateLimit(sessionId)) {
    return new Response("rate limit exceeded", { status: 429 });
  }

  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      try {
        // 1) Embed
        const embedding = await embedQuery(userMsg.content);
        if (!embedding) {
          controller.enqueue(encoder.encode(sse("chunk", {
            delta: "I couldn't embed your question against the corpus. Please try again in a moment.",
          })));
          controller.enqueue(encoder.encode(sse("done", {})));
          controller.close();
          return;
        }

        // 2) Retrieve
        const docs = await retrieveTopK(embedding, 5);

        // 3) Build context
        const contextBlock = docs.length === 0
          ? "(no matching corpus rows — pgvector retrieval unavailable)"
          : docs.map((d, i) =>
              `[${i + 1}] node_id=${d.node_id} type=${d.node_type}\n` +
              `    label: ${d.label}\n` +
              `    snippet: ${(d.snippet || "").slice(0, 600)}`
            ).join("\n\n");

        // 4) Gemini Flash generation (non-streaming for reliability through Cloud Run).
        // We simulate streaming by chunking the full response back to the client so
        // the SSE consumer's progressive-render UX is preserved.
        const prompt =
          `RETRIEVED CONTEXT (top-${docs.length} matches):\n${contextBlock}\n\n` +
          `USER QUESTION:\n${userMsg.content}\n\n` +
          `Answer using ONLY the retrieved context. Cite sources inline as [Rule:NODE_ID] / [Clause:NODE_ID] / [Template:NODE_ID].`;

        const { text, usage, error } = await generateGemini(prompt, {
          systemInstruction: SYSTEM_INSTRUCTION,
          maxOutputTokens: 1024,
          temperature: 0.2,
        });
        if (error) {
          controller.enqueue(encoder.encode(sse("error", { message: error })));
        } else if (text) {
          // Chunk by words for a progressive feel
          const words = text.split(/(\s+)/);
          for (const w of words) {
            if (w) controller.enqueue(encoder.encode(sse("chunk", { delta: w })));
          }
        } else {
          controller.enqueue(encoder.encode(sse("chunk", {
            delta: "I don't have enough information in the retrieved corpus to answer that confidently.",
          })));
        }

        // 5) Sources event
        controller.enqueue(encoder.encode(sse("sources", {
          sources: docs.map(d => ({
            id: d.node_id,
            type: d.node_type,
            label: d.label,
            snippet: (d.snippet || "").slice(0, 200),
          })),
          usage,
        })));

        controller.enqueue(encoder.encode(sse("done", {})));
      } catch (e: any) {
        controller.enqueue(encoder.encode(sse("error", {
          message: String(e?.message || e),
        })));
      } finally {
        controller.close();
      }
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      "Connection": "keep-alive",
      "X-Accel-Buffering": "no",
    },
  });
}
