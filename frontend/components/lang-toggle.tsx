"use client";
import { useState } from "react";
import { MarkdownView } from "./markdown-view";
import { cn } from "@/lib/utils";

interface Props {
  contentEn: string;
  contentTe?: string | null;
}

export function LangToggle({ contentEn, contentTe }: Props) {
  const [lang, setLang] = useState<"en" | "te">("en");
  const hasTelugu = !!contentTe;

  return (
    <div>
      {hasTelugu && (
        <div className="inline-flex rounded-lg border border-mist-200 bg-white p-1 mb-4 shadow-card">
          <button
            onClick={() => setLang("en")}
            className={cn(
              "px-4 py-1.5 text-sm font-semibold rounded-md transition-colors",
              lang === "en" ? "bg-ink-900 text-white" : "text-ink-700 hover:bg-mist-50",
            )}
          >
            English
          </button>
          <button
            onClick={() => setLang("te")}
            className={cn(
              "px-4 py-1.5 text-sm font-semibold rounded-md transition-colors",
              lang === "te" ? "bg-ink-900 text-white" : "text-ink-700 hover:bg-mist-50",
            )}
          >
            తెలుగు · Telugu
          </button>
        </div>
      )}
      <MarkdownView source={lang === "te" && contentTe ? contentTe : contentEn} />
    </div>
  );
}
