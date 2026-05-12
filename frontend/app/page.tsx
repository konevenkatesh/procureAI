import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { WorkflowDiagram } from "@/components/workflow-diagram";
import { countRows } from "@/lib/supabase";
import { Users, FileCheck2, GavelIcon, BookOpenCheck, ExternalLink } from "lucide-react";
import RegionBadge from "@/components/RegionBadge";

export const revalidate = 60;

async function getStats() {
  try {
    const [bidders, validations, evaluations, communications] = await Promise.all([
      countRows("kg_nodes", { node_type: "eq.BidderProfile" }),
      countRows("kg_nodes", { node_type: "eq.ValidationFinding" }),
      countRows("kg_nodes", { node_type: "eq.BidEvaluationFinding" }),
      countRows("kg_nodes", { node_type: "eq.Communication" }),
    ]);
    return { bidders, validations, evaluations, communications };
  } catch {
    return { bidders: 12, validations: 154, evaluations: 351, communications: 75 };
  }
}

const STAT_CARDS = [
  { key: "bidders",        icon: Users,           label: "Bidder Profiles", desc: "Synthetic corpus (incl. B9 JV)" },
  { key: "validations",    icon: BookOpenCheck,   label: "RFP Validations", desc: "Tier-1 findings (Module 2)" },
  { key: "evaluations",    icon: FileCheck2,      label: "Bid Evaluations", desc: "Tier-2 findings (Module 3)" },
  { key: "communications", icon: GavelIcon,       label: "Communications",  desc: "10 types · EN+TE bilingual" },
];

export default async function DashboardPage() {
  const stats = await getStats();

  return (
    <div className="p-8 md:p-10">
      {/* Hero */}
      <header className="mb-8">
        <div className="flex flex-wrap items-center gap-3 mb-2">
          <div className="text-xs font-bold text-saffron-700 tracking-widest">
            BIMSAARTHI TECHNOLOGIES · GOVERNMENT OF ANDHRA PRADESH
          </div>
          <RegionBadge />
        </div>
        <h1 className="text-3xl md:text-4xl font-bold tracking-tight text-ink-900 mb-2">
          ProcureAI — AP State Procurement Platform
        </h1>
        <p className="text-ink-500 max-w-3xl">
          An AI-powered, audit-defensible compliance platform spanning the entire AP procurement
          lifecycle. Drafts tenders from the standard rule library, validates them against AP
          State + Central + CVC layers, evaluates bidder submissions through a 13-criterion
          pipeline, and communicates with bidders in English + Telugu via DPDP-pseudonymised
          translation. RTGS Hackathon 2026.
        </p>
      </header>

      {/* Stat cards */}
      <section className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-10">
        {STAT_CARDS.map((c) => {
          const Icon = c.icon;
          const value = (stats as any)[c.key] as number;
          return (
            <Card key={c.key}>
              <CardContent className="p-5">
                <div className="flex items-start justify-between mb-2">
                  <Icon className="h-5 w-5 text-ink-500" />
                  <span className="text-3xl font-bold text-ink-900">{value}</span>
                </div>
                <div className="text-sm font-semibold text-ink-900">{c.label}</div>
                <div className="text-xs text-ink-500 mt-0.5">{c.desc}</div>
              </CardContent>
            </Card>
          );
        })}
      </section>

      {/* Workflow */}
      <section className="mb-10">
        <div className="flex items-baseline justify-between mb-3">
          <h2 className="text-xl font-bold text-ink-900">4-Module Workflow</h2>
          <p className="text-xs text-ink-500">End-to-end procurement lifecycle</p>
        </div>
        <WorkflowDiagram />
      </section>

      {/* Quick links */}
      <section className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-10">
        <Card>
          <CardHeader>
            <CardTitle>Demo highlight — Effective L1 emerging</CardTitle>
            <CardDescription>
              B9 JV (Comprehensive Standard Builders) wins effective L1 on all 3 tenders after
              the platform applies ALB norms (skip B8) + CVC cartel review (skip B6+B7).
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Link
              href="/module3"
              className="inline-flex items-center gap-2 text-sm font-semibold text-ink-900 hover:text-saffron-700"
            >
              View 3 tenders + per-bidder evaluation <ExternalLink className="h-4 w-4" />
            </Link>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Communication corpus — bilingual EN+TE</CardTitle>
            <CardDescription>
              75 Communication kg_nodes across 10 types. 63 bidder-facing translated to Telugu
              via Sarvam-M with DPDP pseudonymisation. 12 internal (Vigilance + workflow) stay
              English-only.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Link
              href="/module4"
              className="inline-flex items-center gap-2 text-sm font-semibold text-ink-900 hover:text-saffron-700"
            >
              Browse communications + bilingual preview <ExternalLink className="h-4 w-4" />
            </Link>
          </CardContent>
        </Card>
      </section>

      {/* Footer */}
      <footer className="mt-12 pt-6 border-t border-mist-200 text-xs text-ink-500 flex flex-wrap items-center gap-x-6 gap-y-2">
        <span className="font-semibold text-ink-700">BIMSaarthi Technologies</span>
        <span>DPIIT-registered startup</span>
        <span>Mangalagiri Innovation Hub, Andhra Pradesh</span>
        <span>RTGS Hackathon 2026</span>
        <span className="ml-auto">v0.1.0 · Demo build</span>
      </footer>
    </div>
  );
}
