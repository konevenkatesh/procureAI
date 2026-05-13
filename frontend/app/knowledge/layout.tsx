"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import { BookOpen, FileText, FileCode, Layers, Activity } from "lucide-react";

const TABS = [
  { href: "/knowledge/rules",      label: "Rules",       icon: BookOpen },
  { href: "/knowledge/clauses",    label: "Clauses",     icon: FileText },
  { href: "/knowledge/templates",  label: "Templates",   icon: FileCode },
  { href: "/knowledge/typologies", label: "Typologies",  icon: Layers },
  { href: "/knowledge/executions", label: "Live Execution", icon: Activity },
];

interface Stats {
  rules: number;
  clauses: number;
  sbdSections: number;
  techSpecs: number;
  templates: number;
  validationFindings: number;
  eligibilityMatrix: number;
}

export default function KnowledgeLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [stats, setStats] = useState<Stats | null>(null);

  useEffect(() => {
    fetch("/api/kb/stats")
      .then(r => r.ok ? r.json() : null)
      .then(setStats)
      .catch(() => setStats(null));
  }, []);

  return (
    <div className="min-h-screen">
      <header className="border-b border-mist-200 bg-white sticky top-0 z-20">
        <div className="px-6 md:px-10 pt-6 pb-2">
          <div className="flex items-center gap-2 text-xs font-bold text-saffron-700 tracking-widest mb-1">
            <BookOpen className="h-4 w-4" /> KNOWLEDGE LAYER
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-ink-900">
            Procurement Corpus Browser
          </h1>
          <p className="text-xs text-ink-500 mt-1">
            {stats ? (
              <>
                <span className="font-semibold text-ink-700">{stats.rules.toLocaleString()}</span> rules ·{" "}
                <span className="font-semibold text-ink-700">{stats.clauses.toLocaleString()}</span> clauses ·{" "}
                <span className="font-semibold text-ink-700">{stats.templates}</span> templates ·{" "}
                <span className="font-semibold text-ink-700">{stats.validationFindings}</span> validation findings ·{" "}
                <span className="font-semibold text-ink-700">{stats.eligibilityMatrix}</span> eligibility matrices
              </>
            ) : (
              <span className="text-ink-400">Loading corpus stats…</span>
            )}
          </p>
        </div>
        <nav className="px-6 md:px-10">
          <div className="flex gap-1 overflow-x-auto">
            {TABS.map(t => {
              const Icon = t.icon;
              const active = pathname?.startsWith(t.href);
              return (
                <Link
                  key={t.href}
                  href={t.href}
                  className={cn(
                    "flex items-center gap-2 px-4 py-2 text-sm border-b-2 transition-colors whitespace-nowrap",
                    active
                      ? "border-saffron-500 text-ink-900 font-semibold"
                      : "border-transparent text-ink-500 hover:text-ink-900",
                  )}
                >
                  <Icon className="h-4 w-4" />
                  {t.label}
                </Link>
              );
            })}
          </div>
        </nav>
      </header>
      <div className="px-6 md:px-10 py-6">{children}</div>
    </div>
  );
}
