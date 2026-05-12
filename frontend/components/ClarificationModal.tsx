"use client";

/**
 * ClarificationModal — Module 4's "Submit New Clarification" UI.
 *
 * Opens as a centred overlay with a tender selector, bidder selector,
 * a textarea, and an EN/TE language toggle. On submit, posts to
 * /api/m4/submit_clarification and shows the bilingual result inline
 * once Sarvam-M returns. The new Communication appears in the page
 * list on next refresh.
 *
 * Keep this minimal: it's a one-shot modal, no fancy state.
 */
import { useState } from "react";
import { Sparkles, X } from "lucide-react";

const TENDERS = [
  { id: "tender_synth_kurnool", name: "Kurnool — District Hospital" },
  { id: "tender_synth_ja",      name: "AP Judicial Academy" },
  { id: "tender_synth_hc",      name: "AP High Court Complex" },
];

const BIDDERS = [
  { id: "bid_synth_profile_b1", name: "M/s Apex Infra Pvt Ltd" },
  { id: "bid_synth_profile_b3", name: "M/s Premier Coastal Construction" },
  { id: "bid_synth_profile_b9", name: "M/s Comprehensive Standard Builders JV" },
];

interface Props {
  open: boolean;
  onClose: () => void;
  onSubmitted?: () => void;
}

export default function ClarificationModal({ open, onClose, onSubmitted }: Props) {
  const [tenderId, setTenderId] = useState(TENDERS[0].id);
  const [bidder, setBidder] = useState(BIDDERS[1]);
  const [language, setLanguage] = useState<"en" | "te">("te");
  const [question, setQuestion] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<{
    communication_id: string;
    text_en: string;
    text_te: string;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (!open) return null;

  const handleSubmit = async () => {
    setError(null);
    setResult(null);
    setSubmitting(true);
    try {
      const r = await fetch("/api/m4/submit_clarification", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tender_id:     tenderId,
          bidder_id:     bidder.id,
          bidder_name:   bidder.name,
          question_text: question,
          language,
        }),
      });
      const j = await r.json();
      if (!r.ok) {
        setError(j?.error || `HTTP ${r.status}`);
        return;
      }
      setResult(j);
      onSubmitted?.();
    } catch (e: any) {
      setError(e?.message || "submit failed");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 px-4 pt-20 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="w-full max-w-2xl rounded-lg border border-mist-200 bg-white shadow-elev"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-mist-200 px-5 py-3">
          <div className="flex items-center gap-2 text-sm font-bold text-saffron-700">
            <Sparkles className="h-4 w-4" />
            New Bidder Clarification
          </div>
          <button
            className="rounded-md p-1 text-ink-500 hover:bg-mist-100"
            onClick={onClose}
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        {/* Form */}
        <div className="space-y-4 px-5 py-4">
          <div className="grid grid-cols-2 gap-3">
            <label className="text-sm">
              <div className="font-semibold text-ink-900 mb-1">Tender</div>
              <select
                value={tenderId}
                onChange={(e) => setTenderId(e.target.value)}
                className="w-full rounded-md border border-mist-300 px-3 py-2 text-sm"
              >
                {TENDERS.map((t) => (
                  <option key={t.id} value={t.id}>{t.name}</option>
                ))}
              </select>
            </label>
            <label className="text-sm">
              <div className="font-semibold text-ink-900 mb-1">Bidder</div>
              <select
                value={bidder.id}
                onChange={(e) => {
                  const b = BIDDERS.find((x) => x.id === e.target.value);
                  if (b) setBidder(b);
                }}
                className="w-full rounded-md border border-mist-300 px-3 py-2 text-sm"
              >
                {BIDDERS.map((b) => (
                  <option key={b.id} value={b.id}>{b.name}</option>
                ))}
              </select>
            </label>
          </div>
          <div>
            <div className="font-semibold text-ink-900 mb-1 text-sm flex items-center justify-between">
              <span>Question</span>
              <div className="flex gap-1">
                {(["te", "en"] as const).map((l) => (
                  <button
                    key={l}
                    onClick={() => setLanguage(l)}
                    className={
                      "rounded px-2 py-0.5 text-xs font-semibold border " +
                      (language === l
                        ? "bg-ink-900 text-white border-ink-900"
                        : "bg-white text-ink-700 border-mist-200")
                    }
                  >
                    {l.toUpperCase()}
                  </button>
                ))}
              </div>
            </div>
            <textarea
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              rows={4}
              placeholder={
                language === "te"
                  ? "ఉదా: PBG శాతం 5% మాత్రమే అడుగుతున్నారా లేక 2.5% ఆమోదిస్తారా?"
                  : "e.g. Is the PBG required at 5% only or is 2.5% acceptable per AP-GO-175?"
              }
              className="w-full rounded-md border border-mist-300 px-3 py-2 text-sm"
            />
            <div className="text-xs text-ink-500 mt-1">
              Sarvam-M will auto-translate to {language === "te" ? "English" : "Telugu"}.
              Bidder name + PAN + mobile are pseudonymised before the API call.
            </div>
          </div>
          {error && (
            <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-900">
              ⚠ {error}
            </div>
          )}
          {result && (
            <div className="rounded-md border border-green-200 bg-green-50 px-3 py-2 text-sm">
              <div className="font-semibold text-green-900 mb-1">
                ✓ Communication persisted ({result.communication_id.slice(0, 8)}…)
              </div>
              <div className="space-y-1.5 text-ink-900">
                <div>
                  <span className="font-semibold">EN:</span>{" "}
                  <span className="whitespace-pre-wrap">{result.text_en}</span>
                </div>
                <div>
                  <span className="font-semibold">TE:</span>{" "}
                  <span lang="te" className="whitespace-pre-wrap">{result.text_te}</span>
                </div>
              </div>
            </div>
          )}
          <div className="flex justify-end gap-2 pt-1">
            <button
              onClick={onClose}
              className="rounded-md border border-mist-300 bg-white px-4 py-2 text-sm font-semibold text-ink-700"
            >
              {result ? "Close" : "Cancel"}
            </button>
            {!result && (
              <button
                onClick={handleSubmit}
                disabled={submitting || question.trim().length < 3}
                className={
                  "rounded-md px-4 py-2 text-sm font-semibold " +
                  (submitting || question.trim().length < 3
                    ? "bg-mist-100 text-ink-500 cursor-not-allowed"
                    : "bg-saffron-700 text-white hover:bg-saffron-800")
                }
              >
                {submitting ? "Translating + saving…" : "Submit Clarification"}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
