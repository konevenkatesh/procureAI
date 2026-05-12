import { cn } from "@/lib/utils";
import * as React from "react";

export interface BadgeProps extends React.HTMLAttributes<HTMLDivElement> {
  variant?: "default" | "outline" | "qualified" | "flagged" | "markreview" | "disqualified" | "hardblock" | "warning" | "advisory";
}

const VARIANT_CLASSES: Record<NonNullable<BadgeProps["variant"]>, string> = {
  default:      "bg-ink-500 text-white",
  outline:      "border border-mist-200 text-ink-700 bg-white",
  qualified:    "bg-leaf-100 text-leaf-700 border border-leaf-500",
  flagged:      "bg-amber-100 text-amber-800 border border-amber-500",
  markreview:   "bg-blue-100 text-blue-700 border border-blue-500",
  disqualified: "bg-red-100 text-red-700 border border-red-500",
  hardblock:    "bg-red-100 text-red-800 border border-red-300",
  warning:      "bg-amber-100 text-amber-800 border border-amber-300",
  advisory:     "bg-mist-100 text-ink-700 border border-mist-200",
};

export function Badge({ className, variant = "default", ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-md px-2 py-0.5 text-xs font-semibold transition-colors",
        VARIANT_CLASSES[variant],
        className,
      )}
      {...props}
    />
  );
}
