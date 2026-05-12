"use client";

import { useState } from "react";
import JobRunner from "./JobRunner";

/**
 * PipelineLauncher — small inline section for Modules 3 / 4 to run
 * the evaluator or communicator on a chosen tender.
 *
 * Props:
 *   endpoint    — /api/m3/evaluate or /api/m4/communicate
 *   actionLabel — "Run Evaluation" or "Generate Communications"
 *   tenders     — list of {id, name} to choose from
 *   extraField  — optional: a {key, label, options} dropdown
 *                 (used by m4 for the language toggle EN/TE/BOTH)
 */

export interface PipelineTender {
  id: string;
  name: string;
}

interface DropdownField {
  key: string;
  label: string;
  options: { value: string; label: string }[];
  defaultValue: string;
}

interface Props {
  endpoint: string;
  actionLabel: string;
  runningLabel?: string;
  tenders: PipelineTender[];
  extraField?: DropdownField;
}

export default function PipelineLauncher({
  endpoint, actionLabel,
  runningLabel,
  tenders, extraField,
}: Props) {
  const [tenderId, setTenderId] = useState(tenders[0]?.id || "");
  const [extra, setExtra] = useState(extraField?.defaultValue || "");

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
            {tenders.map((t) => (
              <option key={t.id} value={t.id}>{t.name}</option>
            ))}
          </select>
        </label>
        {extraField && (
          <label className="text-sm">
            <div className="font-semibold text-ink-900 mb-1">{extraField.label}</div>
            <select
              value={extra}
              onChange={(e) => setExtra(e.target.value)}
              className="w-full rounded-md border border-mist-300 bg-white px-3 py-2 text-sm"
            >
              {extraField.options.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </label>
        )}
      </div>
      <JobRunner
        endpoint={endpoint}
        label={actionLabel}
        runningLabel={runningLabel}
        payload={() => ({
          tender_id: tenderId,
          params: extraField ? { [extraField.key]: extra } : {},
        })}
      />
    </div>
  );
}
