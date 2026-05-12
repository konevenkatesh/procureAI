import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export const VERDICT_COLORS: Record<string, string> = {
  QUALIFIED: "bg-leaf-100 text-leaf-700 border-leaf-500",
  FLAGGED_FOR_COMMITTEE_REVIEW: "bg-amber-100 text-amber-700 border-amber-500",
  MARK_FOR_DOCUMENTATION_REVIEW: "bg-blue-100 text-blue-700 border-blue-500",
  DISQUALIFIED: "bg-red-100 text-red-700 border-red-500",
  INELIGIBLE: "bg-red-100 text-red-700 border-red-500",
  GAP_INSUFFICIENT_DATA: "bg-blue-100 text-blue-700 border-blue-500",
  SKIP_NOT_APPLICABLE: "bg-mist-100 text-ink-500 border-mist-200",
};

export const SEVERITY_COLORS: Record<string, string> = {
  HARD_BLOCK: "bg-red-100 text-red-800",
  WARNING:    "bg-amber-100 text-amber-800",
  ADVISORY:   "bg-mist-100 text-ink-700",
};

export const VERDICT_LABEL: Record<string, string> = {
  QUALIFIED: "Qualified",
  FLAGGED_FOR_COMMITTEE_REVIEW: "Flagged for Review",
  MARK_FOR_DOCUMENTATION_REVIEW: "Documentation Review",
  DISQUALIFIED: "Disqualified",
  INELIGIBLE: "Ineligible",
  GAP_INSUFFICIENT_DATA: "Gap — Insufficient Data",
  SKIP_NOT_APPLICABLE: "Not Applicable",
};

export function formatCrore(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `₹${value.toFixed(2)} cr`;
}

export function formatPct(value: number | null | undefined, sign: boolean = true): string {
  if (value === null || value === undefined) return "—";
  return `${sign && value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}
