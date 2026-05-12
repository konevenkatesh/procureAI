"use client";

import { useEffect, useState } from "react";
import type { RoleName } from "@/types/m1-drafter";
import { cn } from "@/lib/utils";
import { UserCheck } from "lucide-react";

const ROLES: { value: RoleName; label: string }[] = [
  { value: "DEALING_OFFICER", label: "Dealing Officer" },
  { value: "SENIOR_ENGINEER", label: "Senior Engineer (Technical)" },
  { value: "DEPARTMENT_HEAD", label: "Department Head (Financial)" },
  { value: "PROCUREMENT_OFFICER", label: "Procurement Officer" },
  { value: "TENDER_INVITING_AUTHORITY", label: "Tender Inviting Authority" },
];

const STORAGE_KEY = "m1.demo.role";

export function useDemoRole(): [RoleName, (r: RoleName) => void] {
  const [role, setRole] = useState<RoleName>("DEALING_OFFICER");
  useEffect(() => {
    if (typeof window === "undefined") return;
    const stored = localStorage.getItem(STORAGE_KEY) as RoleName | null;
    if (stored && ROLES.find((r) => r.value === stored)) setRole(stored);
  }, []);
  const update = (r: RoleName) => {
    setRole(r);
    if (typeof window !== "undefined") localStorage.setItem(STORAGE_KEY, r);
  };
  return [role, update];
}

export function RoleSwitcher({ className }: { className?: string }) {
  const [role, setRole] = useDemoRole();

  return (
    <div className={cn("inline-flex items-center gap-2 rounded-md border border-mist-200 bg-white px-3 py-1.5 shadow-card", className)}>
      <UserCheck className="h-4 w-4 text-saffron-700" />
      <span className="text-[10px] font-semibold text-ink-500 uppercase tracking-wide">Demo role</span>
      <select
        value={role}
        onChange={(ev) => setRole(ev.target.value as RoleName)}
        className="bg-transparent text-xs font-semibold text-ink-900 focus:outline-none cursor-pointer"
      >
        {ROLES.map((r) => (
          <option key={r.value} value={r.value}>{r.label}</option>
        ))}
      </select>
    </div>
  );
}
