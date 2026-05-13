"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import { Home, FilePen, Shield, BarChart3, MessageSquare, Info, BookOpen } from "lucide-react";

const NAV = [
  { href: "/",          label: "Dashboard",       icon: Home,           desc: "Overview" },
  { href: "/module1",   label: "1. Drafter",      icon: FilePen,        desc: "Tender drafting" },
  { href: "/module2",   label: "2. Validator",    icon: Shield,         desc: "RFP validation" },
  { href: "/module3",   label: "3. Evaluator",    icon: BarChart3,      desc: "Bid evaluation" },
  { href: "/module4",   label: "4. Communicator", icon: MessageSquare,  desc: "Bidder communication" },
  { href: "/knowledge", label: "Knowledge",       icon: BookOpen,       desc: "Rules · clauses · templates" },
  { href: "/about",     label: "About",           icon: Info,           desc: "Project info" },
];

export function SidebarNav() {
  const pathname = usePathname();
  return (
    <aside className="w-64 shrink-0 border-r border-mist-200 bg-white px-4 py-6 min-h-screen sticky top-0 hidden md:block">
      <Link href="/" className="block px-2 mb-6">
        <div className="flex items-center gap-2">
          <div className="h-8 w-8 rounded-md bg-gradient-to-br from-saffron-500 via-white to-leaf-500 border border-mist-200 shrink-0" />
          <div>
            <div className="text-sm font-bold text-ink-900 tracking-tight">ProcureAI</div>
            <div className="text-xs text-ink-500">BIMSaarthi · AP State</div>
          </div>
        </div>
      </Link>
      <nav className="space-y-1">
        {NAV.map((item) => {
          const Icon = item.icon;
          const active = pathname === item.href || (item.href !== "/" && pathname?.startsWith(item.href));
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
                active
                  ? "bg-ink-900 text-white font-semibold"
                  : "text-ink-700 hover:bg-mist-100",
              )}
            >
              <Icon className="h-4 w-4 shrink-0" />
              <div className="flex-1 min-w-0">
                <div className="truncate">{item.label}</div>
                {!active && (
                  <div className="text-xs text-ink-500 truncate">{item.desc}</div>
                )}
              </div>
            </Link>
          );
        })}
      </nav>
      <div className="mt-8 px-3 text-xs text-ink-500 leading-relaxed">
        <p className="font-semibold text-ink-700 mb-1">RTGS Hackathon 2026</p>
        <p>Andhra Pradesh State Procurement</p>
        <p className="mt-3 text-mist-200">v0.1.0 · {new Date().getFullYear()}</p>
      </div>
    </aside>
  );
}
