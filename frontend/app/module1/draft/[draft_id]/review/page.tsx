"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import Link from "next/link";
import { EGPLiveView } from "@/components/m1/EGPLiveView";
import { RoleSwitcher, useDemoRole } from "@/components/m1/RoleSwitcher";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import {
  ArrowLeft, CheckCircle2, RotateCcw, Send, MessageSquare, AlertCircle,
  Download, Crown,
} from "lucide-react";
import {
  GATE_EDIT_SCOPE, GATE_REVIEWER_ROLE,
} from "@/types/m1-drafter";
import type { TenderDraftState, GateName, RoleName } from "@/types/m1-drafter";
import { cn } from "@/lib/utils";

interface AuditEntry {
  node_id: string;
  properties: any;
  created_at: string;
}

const GATE_BADGE_VARIANT: Record<string, any> = {
  INITIATION:    "outline",
  AI_GENERATION: "warning",
  TECHNICAL:     "outline",
  FINANCIAL:     "outline",
  PROCUREMENT:   "outline",
  AUTHORITY:     "flagged",
  PUBLISHED:     "qualified",
};

export default function ReviewDraftPage() {
  const params = useParams() as { draft_id?: string };
  const router = useRouter();
  const draftId = params.draft_id || "";
  const [demoRole] = useDemoRole();

  const [draft, setDraft] = useState<TenderDraftState | null>(null);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [comments, setComments] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionResult, setActionResult] = useState<any>(null);

  // Load draft + audit
  const refresh = async () => {
    setLoading(true);
    setError(null);
    try {
      const [draftRes, auditRes] = await Promise.all([
        fetch(`/api/m1/draft/${draftId}/get`, { cache: "no-store" }),
        fetch(`/api/m1/draft/${draftId}/audit`, { cache: "no-store" }).catch(() => null),
      ]);
      if (!draftRes.ok) {
        const t = await draftRes.text();
        throw new Error(`get draft failed: ${draftRes.status} ${t.slice(0, 200)}`);
      }
      const draftData = await draftRes.json();
      setDraft(draftData);
      if (auditRes?.ok) {
        const auditData = await auditRes.json();
        setAudit(auditData.transitions || []);
      }
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (draftId) refresh();
    // poll every 8s for collaborative-view freshness
    const t = setInterval(refresh, 8000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draftId]);

  if (loading && !draft) {
    return <div className="p-10 text-sm text-ink-500">Loading draft…</div>;
  }
  if (error) {
    return (
      <div className="p-10">
        <p className="text-sm text-red-700 mb-3">Error: {error}</p>
        <Link href="/module1" className="text-xs text-ink-500 hover:text-ink-900">← Back to Module 1</Link>
      </div>
    );
  }
  if (!draft) {
    return <div className="p-10 text-sm text-ink-500">Draft not found.</div>;
  }

  const currentGate = draft.current_gate as GateName;
  const expectedRole = GATE_REVIEWER_ROLE[currentGate];
  const canAct = expectedRole === demoRole;
  const isPublished = currentGate === "PUBLISHED";
  const isAuthority = currentGate === "AUTHORITY";
  const editScope = GATE_EDIT_SCOPE[currentGate] || [];

  // Action handlers
  const callAction = async (
    action: "approve" | "revise" | "publish" | "sendback",
    extra?: any,
  ) => {
    if ((action === "revise" || action === "sendback") && !comments.trim()) {
      setActionError("Comments are required for this action.");
      return;
    }
    setSubmitting(true);
    setActionError(null);
    setActionResult(null);
    try {
      const body = {
        actor_role: demoRole,
        actor_id: `demo_${demoRole.toLowerCase()}`,
        comments,
        edits: [],
        ...(extra || {}),
      };
      const res = await fetch(`/api/m1/draft/${draftId}/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) {
        setActionError(data?.detail || data?.error || `${action} failed: ${res.status}`);
        return;
      }
      setActionResult(data);
      setComments("");
      await refresh();
    } catch (e: any) {
      setActionError(String(e?.message || e));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="p-6 md:p-8">
      {/* Top bar */}
      <div className="flex items-center justify-between mb-6 flex-wrap gap-3">
        <div>
          <Link
            href="/module1"
            className="inline-flex items-center gap-1.5 text-xs font-semibold text-ink-500 hover:text-ink-900 mb-2"
          >
            <ArrowLeft className="h-3.5 w-3.5" /> Back to Module 1
          </Link>
          <h1 className="text-2xl font-bold tracking-tight text-ink-900">
            {draft.enquiry_particulars.name_of_work}
          </h1>
          <div className="text-xs text-ink-500 mt-1 flex items-center gap-3 flex-wrap">
            <span>Draft ID: <code>{draftId}</code></span>
            {draft.tender_id && (
              <span>Tender ID: <code className="text-leaf-700 font-semibold">{draft.tender_id}</code></span>
            )}
            <span>v{draft.version}</span>
            <span>Last update: {new Date(draft.last_updated_at).toLocaleString("en-IN")}</span>
          </div>
          <div className="mt-2 flex items-center gap-2">
            <Badge variant={GATE_BADGE_VARIANT[currentGate]}>
              Gate: {currentGate}
            </Badge>
            {expectedRole && (
              <Badge variant="outline">
                Pending: {expectedRole}
              </Badge>
            )}
            {isPublished && (
              <Badge variant="qualified" className="inline-flex items-center gap-1">
                <Crown className="h-3 w-3" /> PUBLISHED
              </Badge>
            )}
          </div>
        </div>
        <RoleSwitcher />
      </div>

      {/* Permissions banner */}
      {!isPublished && (
        <div className={cn(
          "mb-4 rounded-md p-3 text-sm border",
          canAct
            ? "bg-leaf-50 border-leaf-300 text-leaf-700"
            : "bg-amber-50 border-amber-300 text-amber-700",
        )}>
          {canAct ? (
            <>
              <strong>{demoRole}</strong> can act on this gate.
              Editable scope: <code className="text-xs">{editScope.length === 0 ? "(read-only)" : editScope.join(", ")}</code>
            </>
          ) : (
            <>
              <strong>{demoRole}</strong> cannot act on this gate (pending {expectedRole}). Switch role in the top-right to demo the gate workflow.
            </>
          )}
        </div>
      )}

      {/* Action result */}
      {actionResult && (
        <div className="mb-4 rounded-md bg-blue-50 border border-blue-300 p-3 text-sm text-blue-700">
          ✓ Action completed. New state: gate=<strong>{actionResult.current_gate}</strong>,
          v{actionResult.version}
          {actionResult.tender_id && (
            <>, tender_id=<strong>{actionResult.tender_id}</strong></>
          )}
          {actionResult.artifacts?.bid_document_docx && (
            <> · artifacts rendered at <code className="text-xs">{actionResult.artifacts.artifact_dir}</code></>
          )}
        </div>
      )}
      {actionError && (
        <div className="mb-4 rounded-md bg-red-50 border border-red-300 p-3 text-sm text-red-700">
          <AlertCircle className="inline h-4 w-4 mr-1" /> {actionError}
        </div>
      )}

      {/* Two-column: draft view + audit trail */}
      <div className="grid grid-cols-1 lg:grid-cols-[1fr_320px] gap-6">
        <EGPLiveView state={draft as any} workflowComplete={true} />

        <div className="space-y-4">
          {/* Audit trail */}
          <Card>
            <CardContent className="p-4">
              <div className="text-xs font-bold text-ink-900 mb-3 flex items-center gap-2">
                <MessageSquare className="h-4 w-4" />
                Audit Trail ({audit.length})
              </div>
              {audit.length === 0 ? (
                <p className="text-xs text-ink-500 italic">No transitions yet.</p>
              ) : (
                <ol className="space-y-2">
                  {audit.map((entry) => {
                    const p = entry.properties || {};
                    return (
                      <li key={entry.node_id} className="text-xs">
                        <div className="flex items-baseline justify-between gap-2">
                          <span className="font-semibold text-ink-900">
                            {p.from_gate} → {p.to_gate}
                          </span>
                          <span className="text-[10px] text-ink-500 tabular-nums">
                            {p.timestamp?.slice(0, 16)?.replace("T", " ")}
                          </span>
                        </div>
                        <div className="text-ink-500 mt-0.5">
                          <code className="text-[10px]">{p.reviewer_role}</code>
                          <span className="ml-1">{p.action}</span>
                        </div>
                        {p.comments && (
                          <div className="italic text-ink-700 mt-1 border-l-2 border-mist-200 pl-2">
                            {p.comments}
                          </div>
                        )}
                      </li>
                    );
                  })}
                </ol>
              )}
            </CardContent>
          </Card>

          {/* Artifacts (if published) */}
          {isPublished && (
            <Card>
              <CardContent className="p-4">
                <div className="text-xs font-bold text-ink-900 mb-3 flex items-center gap-2">
                  <Download className="h-4 w-4" />
                  Published Artifacts
                </div>
                <ArtifactList draftId={draftId} />
              </CardContent>
            </Card>
          )}
        </div>
      </div>

      {/* Action bar — bottom-fixed */}
      {!isPublished && (
        <div className="mt-6 sticky bottom-0 rounded-lg border border-mist-200 bg-white shadow-elev p-4 flex items-center gap-3 flex-wrap">
          <textarea
            value={comments}
            onChange={(e) => setComments(e.target.value)}
            placeholder={
              canAct
                ? `Comments (required for Revise/Sendback)…`
                : `Switch role to ${expectedRole} to act on this gate.`
            }
            disabled={!canAct || submitting}
            className="flex-1 min-w-[250px] rounded-md border border-mist-200 bg-white px-3 py-2 text-sm text-ink-900 focus:outline-none focus:ring-2 focus:ring-saffron-100 focus:border-saffron-500 min-h-[48px]"
          />
          <button
            onClick={() => callAction("approve")}
            disabled={!canAct || submitting || isAuthority}
            className={cn(
              "rounded-md px-4 py-2 text-sm font-semibold transition-colors inline-flex items-center gap-2",
              canAct && !submitting && !isAuthority
                ? "bg-leaf-700 hover:bg-leaf-700/90 text-white"
                : "bg-mist-100 text-ink-500 cursor-not-allowed",
            )}
            title={isAuthority ? "AUTHORITY uses Publish or Send-back" : undefined}
          >
            <CheckCircle2 className="h-4 w-4" /> Approve
          </button>
          {!isAuthority && (
            <button
              onClick={() => callAction("revise")}
              disabled={!canAct || submitting}
              className={cn(
                "rounded-md px-4 py-2 text-sm font-semibold transition-colors inline-flex items-center gap-2",
                canAct && !submitting
                  ? "bg-amber-100 hover:bg-amber-200 text-amber-800 border border-amber-300"
                  : "bg-mist-100 text-ink-500 cursor-not-allowed",
              )}
            >
              <RotateCcw className="h-4 w-4" /> Revise
            </button>
          )}
          {isAuthority && (
            <>
              <button
                onClick={() => callAction("publish")}
                disabled={!canAct || submitting}
                className={cn(
                  "rounded-md px-5 py-2 text-sm font-semibold transition-colors inline-flex items-center gap-2",
                  canAct && !submitting
                    ? "bg-saffron-500 hover:bg-saffron-700 text-white"
                    : "bg-mist-100 text-ink-500 cursor-not-allowed",
                )}
              >
                <Send className="h-4 w-4" /> Publish
              </button>
              <SendBackMenu disabled={!canAct || submitting} onSelect={(g) => callAction("sendback", { send_back_to: g })} />
            </>
          )}
        </div>
      )}
    </div>
  );
}

function ArtifactList({ draftId }: { draftId: string }) {
  const [items, setItems] = useState<Array<{ filename: string; path: string; size_bytes: number }> | null>(null);

  useEffect(() => {
    fetch(`/api/m1/draft/${draftId}/get`)
      .then((r) => r.ok ? r.json() : null)
      .then(async () => {
        const r = await fetch(`${process.env.NEXT_PUBLIC_DRAFTER_URL || ""}/m1/draft/${draftId}/artifacts`)
          .catch(() => null);
        if (!r || !r.ok) {
          // try Cloud Run-style URL or fall back to a static listing message
          setItems([]);
          return;
        }
        const data = await r.json();
        setItems(data.artifacts || []);
      })
      .catch(() => setItems([]));
  }, [draftId]);

  if (items === null) {
    return <p className="text-xs text-ink-500 italic">Loading artifacts…</p>;
  }
  if (items.length === 0) {
    return <p className="text-xs text-ink-500 italic">No artifacts found (paths visible via <code>/m1/draft/{draftId}/artifacts</code> on backend).</p>;
  }
  return (
    <ul className="space-y-1.5">
      {items.map((a) => (
        <li key={a.filename} className="text-xs">
          <div className="font-semibold text-ink-900">{a.filename}</div>
          <div className="text-ink-500 truncate"><code className="text-[10px]">{a.path}</code></div>
          <div className="text-[10px] text-ink-500 tabular-nums">{(a.size_bytes / 1024).toFixed(1)} KB</div>
        </li>
      ))}
    </ul>
  );
}

function SendBackMenu({ disabled, onSelect }: { disabled: boolean; onSelect: (g: GateName) => void }) {
  const [open, setOpen] = useState(false);
  const TARGETS: GateName[] = ["INITIATION", "TECHNICAL", "FINANCIAL", "PROCUREMENT"];
  return (
    <div className="relative">
      <button
        onClick={() => setOpen(!open)}
        disabled={disabled}
        className={cn(
          "rounded-md px-4 py-2 text-sm font-semibold transition-colors inline-flex items-center gap-2 border",
          disabled
            ? "bg-mist-100 text-ink-500 cursor-not-allowed border-mist-200"
            : "bg-white text-ink-700 hover:bg-mist-50 border-mist-200",
        )}
      >
        <RotateCcw className="h-4 w-4" /> Send back ▾
      </button>
      {open && !disabled && (
        <div className="absolute bottom-full mb-2 right-0 rounded-md border border-mist-200 bg-white shadow-elev py-1 min-w-[180px] z-10">
          {TARGETS.map((g) => (
            <button
              key={g}
              onClick={() => { setOpen(false); onSelect(g); }}
              className="w-full text-left px-3 py-1.5 text-xs hover:bg-mist-50"
            >
              Send to <strong>{g}</strong>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
