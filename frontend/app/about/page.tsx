import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Info, Award, Globe2, Library, ShieldCheck, BookOpen } from "lucide-react";

const REFERENCE_SYSTEMS = [
  {
    name: "ALICE",
    country: "Czech Republic",
    desc: "AI-based contract analysis at the Office for the Protection of Competition. Reference for our rule-citation chain + violation typology pattern.",
  },
  {
    name: "INACIA",
    country: "Brazil",
    desc: "Audit assistant deployed at the Federal Court of Accounts. Reference for our composite-finding pattern with sub-aspect breakdown.",
  },
  {
    name: "AIPA",
    country: "Singapore",
    desc: "Government Technology Agency procurement AI. Reference for our effective-L1 computation accounting for ALB + cartel-suspect skip chains.",
  },
];

const STACK = [
  { layer: "Knowledge graph",       items: ["Supabase PostgreSQL", "kg_nodes (JSONB additive)", "kg_edges + fact_sheets"] },
  { layer: "Validation pipelines",  items: ["BGE-M3 embeddings", "OpenRouter (qwen-2.5-72b)", "Three-valued condition_when", "L24 evidence guards"] },
  { layer: "Module 4 Communicator", items: ["Sarvam-M /translate API", "DPDP pseudonymisation", "Filesystem cache (SHA256)", "EN+TE bilingual output"] },
  { layer: "Frontend",              items: ["Next.js 14 (App Router)", "Tailwind CSS", "React 18 server components", "Cloud Run asia-south1"] },
  { layer: "Reports",               items: ["python-docx", "reportlab PDF (L75)", "Markdown intermediary", "5-layer drilldown chain"] },
];

export default function AboutPage() {
  return (
    <div className="p-8 md:p-10 max-w-5xl">
      <header className="mb-6">
        <div className="flex items-center gap-2 text-xs font-bold text-saffron-700 tracking-widest mb-2">
          <Info className="h-4 w-4" /> ABOUT THE PROJECT
        </div>
        <h1 className="text-3xl font-bold tracking-tight text-ink-900 mb-2">
          ProcureAI — Project Background
        </h1>
        <p className="text-ink-500 max-w-3xl">
          ProcureAI is an AI-powered procurement compliance platform built by BIMSaarthi
          Technologies for the Government of Andhra Pradesh. The platform spans the full
          procurement lifecycle — drafting → validating → evaluating → communicating —
          with end-to-end audit trail, DPDP-compliant Telugu translation, and rule citation
          chain from every claim down to the underlying ValidationFinding / BidEvaluationFinding
          kg_node.
        </p>
      </header>

      {/* Team */}
      <Card className="mb-6">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Award className="h-5 w-5 text-saffron-700" />
            BIMSaarthi Technologies
          </CardTitle>
          <CardDescription>DPIIT-registered startup · Mangalagiri Innovation Hub, Andhra Pradesh</CardDescription>
        </CardHeader>
        <CardContent className="text-sm text-ink-700 leading-relaxed">
          <p>
            BIMSaarthi Technologies operates from the Mangalagiri Innovation Hub in
            Andhra Pradesh, focused on building AI tooling for government workflows
            that respect Indian regulatory regimes (DPDP, CVC vigilance norms, AP-State
            procurement standards). This platform is our submission to the RTGS Hackathon
            2026 — Real-Time Governance through Software, hosted by the Government of
            Andhra Pradesh.
          </p>
        </CardContent>
      </Card>

      {/* Reference systems */}
      <Card className="mb-6">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Globe2 className="h-5 w-5 text-blue-700" />
            International Reference Systems
          </CardTitle>
          <CardDescription>
            Systems we studied while designing the procurement-AI stack
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {REFERENCE_SYSTEMS.map((sys) => (
              <div key={sys.name} className="border border-mist-200 rounded p-3">
                <div className="flex items-center gap-2 mb-2">
                  <Badge variant="outline" className="text-[10px]">{sys.country}</Badge>
                </div>
                <div className="font-bold text-ink-900 mb-1">{sys.name}</div>
                <p className="text-xs text-ink-500 leading-relaxed">{sys.desc}</p>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Stack */}
      <Card className="mb-6">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Library className="h-5 w-5 text-leaf-700" />
            Technology Stack
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
            {STACK.map((s) => (
              <div key={s.layer}>
                <div className="font-semibold text-ink-900 mb-1.5">{s.layer}</div>
                <ul className="text-xs text-ink-700 space-y-1">
                  {s.items.map((i) => (
                    <li key={i} className="flex items-start gap-1.5">
                      <span className="text-mist-200 mt-0.5">→</span>
                      <span>{i}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Compliance posture */}
      <Card className="mb-6">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <ShieldCheck className="h-5 w-5 text-leaf-700" />
            Compliance Posture
          </CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-ink-700 leading-relaxed">
          <p className="mb-3">
            The platform is designed for production deployment in AP State government
            infrastructure. Compliance is built in at every layer:
          </p>
          <ul className="space-y-2 ml-4">
            <li className="flex gap-2"><span className="text-leaf-700">✓</span><span><strong>DPDP Act 2023:</strong> bidder PII (PAN, GSTIN, mobile, email, address, signatory) pseudonymised before crossing any external API boundary; pairs sorted longest-first to avoid substring collisions; restored after translation</span></li>
            <li className="flex gap-2"><span className="text-leaf-700">✓</span><span><strong>CVC vigilance:</strong> cartel-suspect detection, ALB corroboration, separation of bidder-facing communications from internal vigilance reasoning</span></li>
            <li className="flex gap-2"><span className="text-leaf-700">✓</span><span><strong>AP-State norms:</strong> AP-GO-094/2003, AP-GO-062 (ABC M=2), AP-GO-089 (12-month solvency), MPG-255, AP-PROC-* seeded rules</span></li>
            <li className="flex gap-2"><span className="text-leaf-700">✓</span><span><strong>Audit defensibility:</strong> deterministic audit_id (SHA256 of source kg_nodes) on every communication; 5-layer drilldown chain from ComparativeStatement → BidAnomalyFinding/EligibilityMatrix/TenderRanking → BidEvaluationFinding → BidSubmission/BidderProfile → fact_sheets</span></li>
            <li className="flex gap-2"><span className="text-leaf-700">✓</span><span><strong>Bilingual operations:</strong> Telugu support for bidder-facing communications via Sarvam-M (India-hosted, in-country data residency); internal communications English-only</span></li>
          </ul>
        </CardContent>
      </Card>

      {/* Lessons archive */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <BookOpen className="h-5 w-5 text-ink-700" />
            Architecture & Lessons Archive
          </CardTitle>
          <CardDescription>
            All architectural decisions documented in <code>LESSONS_LEARNED.md</code> (89 entries, L01–L89).
            Notable patterns:
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ul className="text-xs text-ink-700 space-y-1.5">
            <li><strong>L80:</strong> Composite-finding pattern (N sub-aspects evaluated together)</li>
            <li><strong>L81:</strong> JV-aware validator with cross-profile lookup</li>
            <li><strong>L82:</strong> Module 3 Extensions arc completion + DemoBidder pattern</li>
            <li><strong>L83:</strong> PDF renderer integration via reportlab</li>
            <li><strong>L84:</strong> Module 4 Communication architecture design spec</li>
            <li><strong>L85:</strong> M4.2 drafter pilot pattern</li>
            <li><strong>L86:</strong> JSONB merge via fetch-modify-patch (PostgREST limitation)</li>
            <li><strong>L87:</strong> Sarvam-M Telugu integration with DPDP pseudonymisation</li>
            <li><strong>L88:</strong> 6 remaining communication types (pattern stability)</li>
            <li><strong>L89:</strong> Q&A 2-direction workflow with parent_communication_id threading</li>
          </ul>
        </CardContent>
      </Card>
    </div>
  );
}
