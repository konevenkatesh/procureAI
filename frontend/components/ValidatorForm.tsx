"use client";

import { useState } from "react";
import JobRunner from "./JobRunner";

/**
 * ValidatorForm — Module 2 RFP-Validator trigger.
 *
 * Posts {tender_id, checks} to /api/m2/validate. The m2 backend
 * returns GAP_INSUFFICIENT_DATA until Qdrant migrates to GCP
 * (Phase 2); the wiring + UI is in place now.
 */

const DEMO_TENDERS = [
  { id: "tender_synth_kurnool", name: "Kurnool — District Hospital" },
  { id: "tender_synth_ja",      name: "AP Judicial Academy" },
  { id: "tender_synth_hc",      name: "AP High Court Complex" },
];

export default function ValidatorForm() {
  const [tenderId, setTenderId] = useState(DEMO_TENDERS[0].id);
  const [checksMode, setChecksMode] = useState<"all" | "subset">("all");

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <label className="text-sm">
          <div className="font-semibold text-ink-900 mb-1">Tender</div>
          <select
            value={tenderId}
            onChange={(e) => setTenderId(e.target.value)}
            className="w-full rounded-md border border-mist-300 bg-white px-3 py-2 text-sm"
          >
            {DEMO_TENDERS.map((t) => (
              <option key={t.id} value={t.id}>{t.name}</option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          <div className="font-semibold text-ink-900 mb-1">Checks</div>
          <select
            value={checksMode}
            onChange={(e) => setChecksMode(e.target.value as any)}
            className="w-full rounded-md border border-mist-300 bg-white px-3 py-2 text-sm"
          >
            <option value="all">All 24 Tier-1 checks</option>
            <option value="subset">Subset (Phase-2 toggle)</option>
          </select>
        </label>
      </div>
      <JobRunner
        endpoint="/api/m2/validate"
        label="Validate RFP"
        runningLabel="Validating…"
        payload={() => ({
          tender_id: tenderId,
          params: { checks: checksMode },
        })}
      />
    </div>
  );
}
