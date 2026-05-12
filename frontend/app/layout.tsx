import "./globals.css";
import type { Metadata } from "next";
import { SidebarNav } from "@/components/sidebar-nav";

export const metadata: Metadata = {
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL || "https://procureai.example.com"),
  title: "ProcureAI — AP State Procurement Platform",
  description:
    "AI-powered procurement compliance platform for Andhra Pradesh State Government tenders. Drafts → validates → evaluates → communicates, end-to-end with full audit trail.",
  authors: [{ name: "BIMSaarthi Technologies" }],
  keywords: [
    "procurement",
    "AP State",
    "Andhra Pradesh",
    "BIMSaarthi",
    "RTGS Hackathon",
    "tender",
    "evaluation",
    "compliance",
    "CVC",
    "DPDP",
  ],
  openGraph: {
    title: "ProcureAI — AP State Procurement Platform",
    description:
      "End-to-end AI procurement compliance: drafts → validates → evaluates → communicates with full audit trail.",
    type: "website",
  },
  robots: { index: true, follow: true },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen page-bg">
        <div className="flex">
          <SidebarNav />
          <main className="flex-1 min-w-0">{children}</main>
        </div>
      </body>
    </html>
  );
}
