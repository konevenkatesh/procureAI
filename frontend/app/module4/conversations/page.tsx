"use client";

/**
 * R12.2 — Module 4 chat-thread conversations UI.
 *
 * Two-pane layout: thread list left, active thread right with composer.
 * URL state: ?thread={thread_id} for shareability.
 */

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  Mail, Send, Sparkles, Languages, Loader2, ChevronLeft, CheckCircle2, AlertTriangle,
  MessageSquare, Inbox, RefreshCw,
} from "lucide-react";
import { cn } from "@/lib/utils";

export default function ConversationsPage() {
  return (
    <Suspense fallback={<div className="p-10 text-sm text-ink-500">Loading conversations…</div>}>
      <Inner />
    </Suspense>
  );
}

interface Thread {
  thread_id: string;
  tender_id: string;
  bidder_id: string;
  bidder_name: string | null;
  recipient_email: string | null;
  subject: string | null;
  status: string;
  last_message_at: string | null;
  last_message_snippet: string | null;
}

interface Message {
  node_id: string;
  created_at: string;
  properties: any;
}

function Inner() {
  const router = useRouter();
  const sp = useSearchParams();
  const activeId = sp.get("thread") || "";
  const [threads, setThreads] = useState<Thread[]>([]);
  const [smtp, setSmtp] = useState(true);
  const [search, setSearch] = useState("");

  useEffect(() => {
    fetch("/api/m4/threads")
      .then(r => r.json())
      .then(d => { setThreads(d.threads || []); setSmtp(d.smtp_available !== false); });
  }, []);

  const setActive = (tid: string) => {
    const next = new URLSearchParams(sp);
    next.set("thread", tid);
    router.push(`/module4/conversations?${next.toString()}`, { scroll: false });
  };

  const filtered = threads.filter(t =>
    !search || (t.bidder_name || "").toLowerCase().includes(search.toLowerCase()) ||
    (t.tender_id || "").toLowerCase().includes(search.toLowerCase()) ||
    (t.last_message_snippet || "").toLowerCase().includes(search.toLowerCase()),
  );

  return (
    <div className="h-screen flex flex-col">
      <header className="px-6 md:px-10 pt-6 pb-2 border-b border-mist-200 bg-white">
        <Link href="/module4" className="text-xs font-semibold text-ink-500 hover:text-ink-900 inline-flex items-center gap-1 mb-2">
          <ChevronLeft className="h-3 w-3" /> Module 4 overview
        </Link>
        <div className="flex items-center justify-between gap-2">
          <div>
            <div className="flex items-center gap-2 text-xs font-bold text-saffron-700 tracking-widest mb-1">
              <MessageSquare className="h-4 w-4" /> MODULE 4 · BIDDER CONVERSATIONS
            </div>
            <h1 className="text-2xl font-bold tracking-tight text-ink-900">Communication Threads</h1>
            <p className="text-xs text-ink-500 mt-1">
              {threads.length} active threads · {smtp
                ? <span className="text-leaf-700 font-semibold">SMTP outbound: live</span>
                : <span className="text-amber-700 font-semibold">SMTP outbound: DEGRADED (sends save as DRAFT)</span>}
            </p>
          </div>
        </div>
      </header>

      <div className="flex-1 flex overflow-hidden">
        {/* Left: thread list */}
        <aside className="w-80 border-r border-mist-200 bg-mist-50/40 overflow-y-auto">
          <div className="p-3 border-b border-mist-200 bg-white">
            <input
              type="text" placeholder="Search threads…"
              value={search} onChange={(e) => setSearch(e.target.value)}
              className="w-full rounded-md border border-mist-200 px-3 py-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-saffron-100 focus:border-saffron-500"
            />
          </div>
          <ul className="divide-y divide-mist-100">
            {filtered.length === 0 && (
              <li className="p-6 text-center text-xs text-ink-400">No threads</li>
            )}
            {filtered.map(t => (
              <li key={t.thread_id}>
                <button
                  onClick={() => setActive(t.thread_id)}
                  className={cn(
                    "w-full text-left px-4 py-3 hover:bg-white transition-colors block",
                    activeId === t.thread_id && "bg-white border-l-2 border-l-saffron-500",
                  )}>
                  <div className="flex items-center justify-between gap-2 mb-1">
                    <div className="text-xs font-bold text-ink-900 truncate">
                      {t.bidder_name || t.bidder_id}
                    </div>
                    <span className="text-[10px] text-ink-500 shrink-0">
                      {t.last_message_at ? new Date(t.last_message_at).toLocaleDateString("en-IN", { day: "2-digit", month: "short" }) : "—"}
                    </span>
                  </div>
                  <div className="text-[10px] text-ink-500 mb-1">{t.tender_id.replace("tender_synth_", "").toUpperCase()}</div>
                  <div className="text-[11px] text-ink-700 line-clamp-2">{t.last_message_snippet || "(no messages)"}</div>
                </button>
              </li>
            ))}
          </ul>
        </aside>

        {/* Right: active thread */}
        <main className="flex-1 overflow-hidden flex flex-col">
          {activeId ? <ThreadView threadId={activeId} smtpAvailable={smtp} /> : <EmptyPane />}
        </main>
      </div>
    </div>
  );
}


function EmptyPane() {
  return (
    <div className="flex-1 flex items-center justify-center text-ink-400">
      <div className="text-center">
        <Inbox className="h-12 w-12 mx-auto mb-3 text-ink-300" />
        <p className="text-sm">Select a thread to view messages</p>
      </div>
    </div>
  );
}


function ThreadView({ threadId, smtpAvailable }: { threadId: string; smtpAvailable: boolean }) {
  const [data, setData] = useState<{ thread: Thread; messages: Message[] } | null>(null);
  const [loading, setLoading] = useState(true);
  const [reloadKey, setReloadKey] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setLoading(true);
    fetch(`/api/m4/threads/${threadId}`)
      .then(r => r.json())
      .then(setData)
      .finally(() => setLoading(false));
  }, [threadId, reloadKey]);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" });
  }, [data]);

  if (loading) return <div className="flex-1 flex items-center justify-center"><Loader2 className="h-5 w-5 animate-spin text-ink-500" /></div>;
  if (!data?.thread) return <div className="flex-1 flex items-center justify-center text-xs text-ink-500">Thread not found</div>;

  const t = data.thread;
  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <header className="px-6 py-3 border-b border-mist-200 bg-white">
        <div className="flex items-center justify-between">
          <div>
            <div className="text-sm font-bold text-ink-900">{t.bidder_name || t.bidder_id}</div>
            <div className="text-[10px] text-ink-500">
              {t.recipient_email || "no email on file"} · {t.tender_id}
            </div>
          </div>
          <span className={cn(
            "text-[10px] rounded-full px-2 py-0.5 font-bold",
            t.status === "active" ? "bg-leaf-100 text-leaf-800" : "bg-mist-100 text-ink-700",
          )}>{t.status.toUpperCase()}</span>
        </div>
      </header>

      {/* Message list */}
      <div ref={listRef} className="flex-1 overflow-y-auto px-6 py-4 space-y-3 bg-mist-50/30">
        {data.messages.length === 0 && (
          <div className="text-center text-xs text-ink-400 py-10">No messages in this thread yet.</div>
        )}
        {data.messages.map(m => <MessageBubble key={m.node_id} m={m} />)}
      </div>

      <Composer
        threadId={threadId}
        bidderName={t.bidder_name || t.bidder_id}
        recipientEmail={t.recipient_email || ""}
        smtpAvailable={smtpAvailable}
        onSent={() => setReloadKey(k => k + 1)}
      />
    </div>
  );
}


function MessageBubble({ m }: { m: Message }) {
  const p = m.properties || {};
  const senderRole = p.sender_role || "SYSTEM";
  const isOutbound = senderRole === "PROCUREMENT_OFFICER" || senderRole === "SYSTEM";
  const [showTE, setShowTE] = useState(false);
  const en = p.content_en || "";
  const te = p.content_te || "";
  const status = p.status || "—";
  const aiDrafted = !!p.ai_drafted;

  return (
    <div className={cn("flex", isOutbound ? "justify-end" : "justify-start")}>
      <div className={cn(
        "max-w-[78%] rounded-lg px-4 py-3 text-sm",
        isOutbound ? "bg-ink-900 text-white" : "bg-white border border-mist-200 text-ink-900",
      )}>
        <div className="flex items-center gap-2 mb-1">
          <span className={cn("text-[10px] font-bold uppercase tracking-wider",
            isOutbound ? "text-white/70" : "text-ink-500")}>
            {p.communication_type || "MESSAGE"}
          </span>
          {aiDrafted && (
            <span className={cn("inline-flex items-center gap-0.5 text-[10px] rounded px-1.5 py-0.5",
              isOutbound ? "bg-white/15" : "bg-saffron-100 text-saffron-900")}>
              <Sparkles className="h-2.5 w-2.5" /> AI
            </span>
          )}
          <span className={cn("text-[10px] ml-auto",
            isOutbound ? "text-white/60" : "text-ink-400")}>
            {new Date(m.created_at).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}
          </span>
        </div>
        <div className="whitespace-pre-wrap leading-relaxed text-[13px]">
          {showTE && te ? te : en}
        </div>
        <div className={cn("flex items-center gap-3 mt-2 pt-2 border-t",
          isOutbound ? "border-white/15" : "border-mist-200")}>
          {te && (
            <button onClick={() => setShowTE(!showTE)}
              className={cn("text-[10px] inline-flex items-center gap-1 hover:underline",
                isOutbound ? "text-white/80" : "text-ink-500")}>
              <Languages className="h-2.5 w-2.5" /> {showTE ? "Show English" : "Show తెలుగు"}
            </button>
          )}
          <span className={cn("text-[10px] font-bold",
            status === "SENT" ? (isOutbound ? "text-leaf-300" : "text-leaf-700") :
            status === "DRAFT" ? (isOutbound ? "text-amber-300" : "text-amber-700") :
            isOutbound ? "text-white/60" : "text-ink-500")}>
            {status}
          </span>
        </div>
      </div>
    </div>
  );
}


function Composer({ threadId, bidderName, recipientEmail, smtpAvailable, onSent }: any) {
  const [intent, setIntent] = useState("");
  const [body, setBody] = useState("");
  const [bodyTe, setBodyTe] = useState("");
  const [subject, setSubject] = useState("Procurement correspondence");
  const [drafting, setDrafting] = useState(false);
  const [translating, setTranslating] = useState(false);
  const [sending, setSending] = useState(false);
  const [sendStatus, setSendStatus] = useState<string | null>(null);

  const aiDraft = async () => {
    if (!intent.trim()) return;
    setDrafting(true);
    try {
      const r = await fetch(`/api/m4/threads/${threadId}/draft`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ officer_intent: intent }),
      });
      const d = await r.json();
      setBody(d.suggested_reply || "");
    } finally { setDrafting(false); }
  };

  const translate = async () => {
    if (!body.trim()) return;
    setTranslating(true);
    try {
      const r = await fetch(`/api/m4/threads/${threadId}/translate`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: body, direction: "en_to_te" }),
      });
      const d = await r.json();
      setBodyTe(d.translated || "");
    } finally { setTranslating(false); }
  };

  const send = async () => {
    if (!body.trim() || !recipientEmail) return;
    setSending(true);
    setSendStatus(null);
    try {
      const r = await fetch(`/api/m4/threads/${threadId}/send`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          to: recipientEmail, subject,
          body_en: body, body_te: bodyTe || undefined,
          ai_drafted: !!intent.trim(),
        }),
      });
      const { send_id } = await r.json();
      if (!send_id) { setSendStatus("Failed to queue"); setSending(false); return; }

      // Consume SSE stream for status updates
      const es = new EventSource(`/api/m4/send/${send_id}/stream`);
      es.onmessage = (e) => {
        try {
          const ev = JSON.parse(e.data);
          if (ev.type === "send_complete") {
            setSendStatus("Sent ✓"); es.close(); setSending(false);
            setBody(""); setBodyTe(""); setIntent(""); onSent?.();
          } else if (ev.type === "send_degraded") {
            setSendStatus("Saved as DRAFT (SMTP credentials pending)"); es.close(); setSending(false);
            setBody(""); setBodyTe(""); setIntent(""); onSent?.();
          } else if (ev.type === "send_failed") {
            setSendStatus(`Failed: ${ev.error || "unknown"}`); es.close(); setSending(false);
          } else {
            setSendStatus(ev.type);
          }
        } catch { /* ignore */ }
      };
      es.onerror = () => { es.close(); setSending(false); };
    } catch (e: any) {
      setSendStatus(`Error: ${e?.message || e}`); setSending(false);
    }
  };

  return (
    <div className="border-t border-mist-200 bg-white p-4 space-y-3">
      {!smtpAvailable && (
        <div className="rounded-md bg-amber-50 border border-amber-200 px-3 py-1.5 text-[11px] text-amber-900 inline-flex items-center gap-1.5">
          <AlertTriangle className="h-3 w-3" /> SMTP credentials not configured — messages will save as DRAFT
        </div>
      )}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
        <div className="lg:col-span-2 space-y-2">
          <div className="flex items-center gap-2">
            <Sparkles className="h-3.5 w-3.5 text-saffron-700" />
            <input
              type="text" value={intent} onChange={e => setIntent(e.target.value)}
              placeholder="What do you want to say? (e.g. 'request EMD revalidation')"
              className="flex-1 rounded-md border border-mist-200 px-3 py-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-saffron-100 focus:border-saffron-500"
            />
            <button onClick={aiDraft} disabled={drafting || !intent.trim()}
              className="rounded-md bg-saffron-500 text-white px-3 py-1.5 text-xs font-semibold hover:bg-saffron-600 disabled:opacity-50 inline-flex items-center gap-1">
              {drafting ? <Loader2 className="h-3 w-3 animate-spin" /> : <Sparkles className="h-3 w-3" />}
              AI draft
            </button>
          </div>
          <textarea
            value={body} onChange={e => setBody(e.target.value)}
            placeholder="Compose reply in English…"
            rows={6}
            className="w-full rounded-md border border-mist-200 px-3 py-2 text-xs leading-relaxed focus:outline-none focus:ring-2 focus:ring-saffron-100 focus:border-saffron-500"
          />
        </div>
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <label className="text-[10px] uppercase tracking-wider font-bold text-ink-500">Telugu (optional)</label>
            <button onClick={translate} disabled={translating || !body.trim()}
              className="text-[10px] text-saffron-700 hover:text-saffron-900 inline-flex items-center gap-1 disabled:opacity-50">
              {translating ? <Loader2 className="h-2.5 w-2.5 animate-spin" /> : <Languages className="h-2.5 w-2.5" />}
              Translate via Sarvam
            </button>
          </div>
          <textarea
            value={bodyTe} onChange={e => setBodyTe(e.target.value)}
            placeholder="తెలుగు అనువాదం (ఎంచుకోవచ్చు)"
            rows={6}
            className="w-full rounded-md border border-mist-200 px-3 py-2 text-xs leading-relaxed focus:outline-none focus:ring-2 focus:ring-saffron-100 focus:border-saffron-500"
          />
        </div>
      </div>
      <div className="flex items-center justify-between gap-2">
        <input
          type="text" value={subject} onChange={e => setSubject(e.target.value)}
          placeholder="Subject"
          className="flex-1 max-w-md rounded-md border border-mist-200 px-3 py-1.5 text-xs"
        />
        <div className="text-[10px] text-ink-500">
          To: <code>{recipientEmail || "(no email)"}</code>
        </div>
        <button onClick={send} disabled={sending || !body.trim() || !recipientEmail}
          className="rounded-md bg-ink-900 text-white px-4 py-1.5 text-xs font-semibold hover:bg-ink-700 disabled:opacity-50 inline-flex items-center gap-1.5">
          {sending ? <Loader2 className="h-3 w-3 animate-spin" /> : <Send className="h-3 w-3" />}
          {smtpAvailable ? "Send" : "Save as DRAFT"}
        </button>
      </div>
      {sendStatus && (
        <div className="text-[11px] text-ink-700 inline-flex items-center gap-1">
          {sendStatus.startsWith("Sent") || sendStatus.startsWith("Saved")
            ? <CheckCircle2 className="h-3 w-3 text-leaf-700" />
            : <Loader2 className="h-3 w-3 animate-spin" />}
          {sendStatus}
        </div>
      )}
    </div>
  );
}
