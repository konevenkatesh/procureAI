import { cn } from "@/lib/utils";
import * as React from "react";

export function Table({ className, ...props }: React.HTMLAttributes<HTMLTableElement>) {
  return (
    <div className="w-full overflow-x-auto rounded-lg border border-mist-200 bg-white">
      <table className={cn("w-full text-sm", className)} {...props} />
    </div>
  );
}

export function THead({ className, ...props }: React.HTMLAttributes<HTMLTableSectionElement>) {
  return <thead className={cn("bg-mist-50/80 text-ink-700", className)} {...props} />;
}

export function TBody({ className, ...props }: React.HTMLAttributes<HTMLTableSectionElement>) {
  return <tbody className={cn("divide-y divide-mist-100", className)} {...props} />;
}

export function TR({ className, ...props }: React.HTMLAttributes<HTMLTableRowElement>) {
  return <tr className={cn("hover:bg-mist-50/40", className)} {...props} />;
}

export function TH({ className, ...props }: React.ThHTMLAttributes<HTMLTableCellElement>) {
  return (
    <th
      className={cn(
        "text-left text-xs font-semibold uppercase tracking-wide text-ink-700 px-3 py-2 border-b border-mist-200",
        className,
      )}
      {...props}
    />
  );
}

export function TD({ className, ...props }: React.TdHTMLAttributes<HTMLTableCellElement>) {
  return <td className={cn("px-3 py-2 align-top", className)} {...props} />;
}
