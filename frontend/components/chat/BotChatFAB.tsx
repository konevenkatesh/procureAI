"use client";

/**
 * R10.4 — Floating chat assistant FAB + overlay.
 *
 * Mount once at the root layout; the FAB stays bottom-right on every page.
 * Click opens a slide-in chat overlay backed by /api/kb/chat SSE.
 * Citations [Rule:ID] / [Clause:ID] / [Template:ID] are inline-clickable
 * deep-links to /knowledge/{tab}?detail=ID (caught by the layout via a URL
 * state hook — see KbDetailModal for the read side).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { MessageSquare, X, Send, Trash2, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources?: { id: string; type: string; label: string; snippet?: string }[];
  isStreaming?: boolean;
  error?: string;
}

const STORAGE_KEY = "kb_chat_history_v1";
const SUGGESTED = [
  "What's the bid security for capital projects above ₹500cr?",
  "Show me IS codes for HVAC ducting in MEP tenders",
  "Which rules check turnover thresholds at tender evaluation?",
];


// ─── Inline citation parser ───────────────────────────────────────────


type Token = { kind: "text"; text: string } | { kind: "cite"; type: string; id: string; raw: string };

function tokenizeCitations(text: string): Token[] {
  const re = /\[(Rule|Clause|Template):([^\]\s]+)\]/g;
  const out: Token[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push({ kind: "text", text: text.slice(last, m.index) });
    out.push({ kind: "cite", type: m[1], id: m[2], raw: m[0] });
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push({ kind: "text", text: text.slice(last) });
  return out;
}

function CitationChip({ type, id }: { type: string; id: string }) {
  const tab = type === "Rule" ? "rules" : type === "Clause" ? "clauses" : "templates";
  return (
    <Link
      href={`/knowledge/${tab}?detail=${encodeURIComponent(id)}`}
      className="inline-block rounded bg-saffron-100 text-saffron-900 text-[10px] font-semibold px-1.5 py-0.5 mx-0.5 align-middle hover:bg-saffron-200 transition-colors"
      title={`Open ${type} ${id}`}
    >
      {type}:{id.slice(0, 8)}
    </Link>
  );
}


// ─── Chat overlay ─────────────────────────────────────────────────────


function ChatOverlay({ onClose }: { onClose: () => void }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);
  const sessionId = useRef<string>(
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : Math.random().toString(36).slice(2)
  );

  // Restore history from localStorage
  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) setMessages(JSON.parse(raw));
    } catch { /* ignore */ }
  }, []);

  // Persist on update
  useEffect(() => {
    if (typeof window === "undefined") return;
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(messages)); } catch { /* ignore */ }
  }, [messages]);

  // Auto-scroll
  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  const sendMessage = useCallback(async (text: string) => {
    if (!text.trim() || streaming) return;
    const userMsg: Message = { id: `u_${Date.now()}`, role: "user", content: text };
    const botPlaceholder: Message = {
      id: `b_${Date.now()}`, role: "assistant", content: "", isStreaming: true,
    };
    setMessages(prev => [...prev, userMsg, botPlaceholder]);
    setInput("");
    setStreaming(true);

    try {
      const res = await fetch("/api/kb/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          messages: [...messages, userMsg].map(m => ({ role: m.role, content: m.content })),
          session_id: sessionId.current,
        }),
      });
      if (!res.body) throw new Error(`HTTP ${res.status}`);
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      let acc = "";
      let event: string | null = null;
      let sources: Message["sources"] = undefined;

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let nl: number;
        while ((nl = buf.indexOf("\n\n")) >= 0) {
          const chunk = buf.slice(0, nl);
          buf = buf.slice(nl + 2);
          event = null;
          let dataLine = "";
          for (const line of chunk.split("\n")) {
            if (line.startsWith("event:")) event = line.slice(6).trim();
            else if (line.startsWith("data:")) dataLine = line.slice(5).trim();
          }
          if (!dataLine) continue;
          try {
            const obj = JSON.parse(dataLine);
            if (event === "chunk" && obj.delta) {
              acc += obj.delta;
              setMessages(prev => prev.map(m =>
                m.id === botPlaceholder.id ? { ...m, content: acc } : m
              ));
            } else if (event === "sources") {
              sources = obj.sources || [];
            } else if (event === "error") {
              setMessages(prev => prev.map(m =>
                m.id === botPlaceholder.id ? { ...m, error: obj.message, isStreaming: false } : m
              ));
            }
          } catch { /* malformed */ }
        }
      }
      setMessages(prev => prev.map(m =>
        m.id === botPlaceholder.id
          ? { ...m, isStreaming: false, sources, content: acc || m.content }
          : m
      ));
    } catch (e: any) {
      setMessages(prev => prev.map(m =>
        m.id === botPlaceholder.id
          ? { ...m, error: String(e?.message || e), isStreaming: false }
          : m
      ));
    } finally {
      setStreaming(false);
    }
  }, [messages, streaming]);

  const clearHistory = () => {
    setMessages([]);
    if (typeof window !== "undefined") localStorage.removeItem(STORAGE_KEY);
  };

  return (
    <>
      <div
        className="fixed inset-0 z-40 bg-ink-900/40 md:bg-ink-900/20"
        onClick={onClose}
      />
      <aside
        className="fixed right-0 top-0 bottom-0 z-50 bg-white shadow-2xl flex flex-col w-full md:w-[420px] animate-in slide-in-from-right duration-200"
      >
        <header className="px-4 py-3 border-b border-mist-200 flex items-center justify-between">
          <div>
            <div className="text-xs font-bold text-saffron-700 tracking-widest">PROCURE BOT</div>
            <div className="text-sm font-semibold text-ink-900">Ask the corpus</div>
          </div>
          <div className="flex items-center gap-1">
            <button
              onClick={clearHistory}
              className="text-ink-500 hover:text-red-700 p-1.5 rounded hover:bg-mist-100"
              title="Clear conversation"
            >
              <Trash2 className="h-4 w-4" />
            </button>
            <button
              onClick={onClose}
              className="text-ink-500 hover:text-ink-900 p-1.5 rounded hover:bg-mist-100"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </header>

        <div ref={listRef} className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
          {messages.length === 0 && (
            <div className="text-center py-12 text-xs text-ink-500">
              <MessageSquare className="h-8 w-8 mx-auto mb-3 text-ink-300" />
              <p className="mb-4">Ask about procurement rules, clauses, or templates.</p>
              <div className="space-y-1.5">
                {SUGGESTED.map(s => (
                  <button
                    key={s}
                    onClick={() => sendMessage(s)}
                    className="block w-full text-left rounded-md border border-mist-200 px-3 py-2 hover:bg-mist-50 transition-colors text-ink-700"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.map(m => (
            <div key={m.id} className={cn("flex", m.role === "user" ? "justify-end" : "justify-start")}>
              <div className={cn(
                "max-w-[85%] rounded-lg px-3 py-2 text-sm",
                m.role === "user" ? "bg-saffron-100 text-ink-900" : "bg-mist-50 text-ink-900"
              )}>
                {m.error ? (
                  <span className="text-red-700">{m.error}</span>
                ) : (
                  <div className="whitespace-pre-wrap leading-relaxed">
                    {tokenizeCitations(m.content).map((tok, i) =>
                      tok.kind === "text" ? <span key={i}>{tok.text}</span> :
                      <CitationChip key={i} type={tok.type} id={tok.id} />
                    )}
                    {m.isStreaming && <span className="inline-block ml-1 animate-pulse">▋</span>}
                  </div>
                )}
                {m.sources && m.sources.length > 0 && (
                  <div className="mt-2 pt-2 border-t border-mist-200">
                    <div className="text-[10px] uppercase tracking-wider text-ink-500 font-semibold mb-1">
                      Sources ({m.sources.length})
                    </div>
                    <ul className="space-y-1">
                      {m.sources.slice(0, 5).map((s, i) => {
                        const tab = s.type === "RuleNode" ? "rules" :
                                    s.type === "TechSpecTemplate" || s.type === "SBDSection" ? "templates" :
                                    "clauses";
                        return (
                          <li key={i}>
                            <Link
                              href={`/knowledge/${tab}?detail=${encodeURIComponent(s.id)}`}
                              className="block text-[11px] text-ink-700 hover:text-saffron-700 truncate"
                              title={s.label}
                            >
                              [{i + 1}] {s.label.slice(0, 60)}
                            </Link>
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>

        <footer className="border-t border-mist-200 px-3 py-2">
          <div className="flex items-end gap-2">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  sendMessage(input);
                }
              }}
              rows={1}
              placeholder="Ask about rules, clauses, or templates…"
              disabled={streaming}
              className="flex-1 resize-none rounded-md border border-mist-200 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-saffron-100 focus:border-saffron-500 disabled:opacity-50 max-h-24"
            />
            <button
              onClick={() => sendMessage(input)}
              disabled={streaming || !input.trim()}
              className="rounded-md bg-ink-900 text-white px-3 py-2 disabled:opacity-50 hover:bg-ink-700 transition-colors"
              title="Send"
            >
              {streaming
                ? <Loader2 className="h-4 w-4 animate-spin" />
                : <Send className="h-4 w-4" />}
            </button>
          </div>
          <div className="text-[10px] text-ink-400 mt-1 ml-1">Enter to send · Shift+Enter for newline</div>
        </footer>
      </aside>
    </>
  );
}


// ─── FAB (default export) ─────────────────────────────────────────────


export default function BotChatFAB() {
  const [open, setOpen] = useState(false);
  return (
    <>
      {!open && (
        <button
          onClick={() => setOpen(true)}
          className="fixed bottom-6 right-6 z-40 h-14 w-14 rounded-full bg-ink-900 text-white shadow-lg hover:bg-ink-700 hover:shadow-xl transition-all flex items-center justify-center group"
          title="Ask about rules, clauses, templates"
        >
          <MessageSquare className="h-6 w-6" />
          <span className="absolute -top-1 -right-1 h-3 w-3 rounded-full bg-saffron-500 animate-pulse" />
        </button>
      )}
      {open && <ChatOverlay onClose={() => setOpen(false)} />}
    </>
  );
}
