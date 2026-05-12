"use client";
// Placeholder — M1.3 will replace this with the live SSE structured view.

import { useParams } from "next/navigation";
import { Card, CardContent } from "@/components/ui/card";
import { Sparkles, Loader2 } from "lucide-react";
import Link from "next/link";

export default function GenerateDraftPage() {
  const params = useParams() as { draft_id?: string };
  const draftId = params.draft_id || "(no-id)";

  return (
    <div className="p-8 md:p-10 max-w-5xl">
      <header className="mb-6">
        <div className="flex items-center gap-2 text-xs font-bold text-saffron-700 tracking-widest mb-2">
          <Sparkles className="h-4 w-4" /> MODULE 1 · AI GENERATION
        </div>
        <h1 className="text-3xl font-bold tracking-tight text-ink-900 mb-2">
          Generating tender draft…
        </h1>
        <p className="text-ink-500">
          Draft ID: <code className="text-xs">{draftId}</code>
        </p>
      </header>

      <Card>
        <CardContent className="p-10 text-center">
          <Loader2 className="h-8 w-8 text-saffron-700 animate-spin mx-auto mb-4" />
          <p className="text-sm text-ink-700 font-semibold">
            Live structured view is being built in M1.3 (next sub-block).
          </p>
          <p className="text-xs text-ink-500 mt-2 max-w-lg mx-auto">
            For now, this page acts as the post-Generate landing target.
            The 12-node LangGraph workflow (M1.5) will stream SSE events into the eGP-format
            template visible here. Once M1.3 + M1.5 ship, you'll see fields populate live.
          </p>
          <Link
            href="/module1"
            className="inline-block mt-6 rounded-md bg-ink-900 hover:bg-ink-700 px-4 py-2 text-xs font-semibold text-white"
          >
            Back to Module 1
          </Link>
        </CardContent>
      </Card>
    </div>
  );
}
