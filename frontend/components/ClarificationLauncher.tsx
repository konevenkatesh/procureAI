"use client";

import { useState } from "react";
import { MessageSquarePlus } from "lucide-react";
import ClarificationModal from "./ClarificationModal";

/**
 * Tiny launcher button + modal pair, so a Server Component page can
 * render a single import (`<ClarificationLauncher />`) without
 * needing its own client-state.
 */
export default function ClarificationLauncher() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="inline-flex items-center gap-2 rounded-md bg-saffron-700 px-4 py-2 text-sm font-semibold text-white hover:bg-saffron-800"
      >
        <MessageSquarePlus className="h-4 w-4" />
        Submit New Clarification
      </button>
      <ClarificationModal open={open} onClose={() => setOpen(false)} />
    </>
  );
}
