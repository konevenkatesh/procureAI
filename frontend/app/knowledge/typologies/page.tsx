"use client";

import { useEffect, useState } from "react";
import { Loader2, Layers } from "lucide-react";
import { KbDetailModal } from "@/components/knowledge/KbDetailModal";

interface Typology {
  typology_code: string;
  rule_count: number;
  severities: Record<string, number>;
}

export default function TypologiesPage() {
  const [typologies, setTypologies] = useState<Typology[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/kb/typologies")
      .then(r => r.ok ? r.json() : Promise.reject())
      .then((d) => setTypologies(d.typologies || []))
      .catch(() => setTypologies([]))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div>
      <p className="text-xs text-ink-500 mb-4">
        Grouped rule typologies extracted from the corpus. Click a card to view the full rule chain and recent firings.
      </p>
      {loading && (
        <div className="flex items-center justify-center py-12 text-ink-500">
          <Loader2 className="h-5 w-5 animate-spin mr-2" /> Loading typologies…
        </div>
      )}
      {!loading && typologies.length === 0 && (
        <div className="rounded-md border border-mist-200 bg-white p-8 text-center text-ink-500 italic">
          No typology codes found in the corpus.
        </div>
      )}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
        {typologies.map(t => (
          <button
            key={t.typology_code}
            onClick={() => setSelectedId(t.typology_code)}
            className="text-left rounded-md border border-mist-200 bg-white p-4 hover:shadow-md hover:border-saffron-500 transition-all"
          >
            <div className="flex items-start justify-between gap-2 mb-2">
              <div className="flex items-center gap-2 min-w-0">
                <Layers className="h-3.5 w-3.5 text-ink-500 shrink-0" />
                <div className="text-sm font-semibold text-ink-900 truncate">{t.typology_code}</div>
              </div>
              <span className="shrink-0 rounded bg-ink-900 text-white text-[10px] px-2 py-0.5 font-bold">
                {t.rule_count}
              </span>
            </div>
            <div className="flex flex-wrap gap-1.5 mt-2">
              {Object.entries(t.severities).map(([sev, n]) => {
                const color = sev === "HARD_BLOCK" ? "bg-red-100 text-red-800" :
                              sev === "WARNING"   ? "bg-amber-100 text-amber-800" :
                              "bg-mist-100 text-ink-700";
                return (
                  <span key={sev} className={`rounded px-2 py-0.5 text-[10px] ${color}`}>
                    {sev}: {String(n)}
                  </span>
                );
              })}
            </div>
          </button>
        ))}
      </div>
      {selectedId && (
        <KbDetailModal
          id={selectedId}
          endpoint="/api/kb/typologies"
          onClose={() => setSelectedId(null)}
        />
      )}
    </div>
  );
}
