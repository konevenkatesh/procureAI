"use client";

import { Field, Select } from "@/components/m1/form-fields";
import type { DraftFormState } from "@/lib/draft-form-state";

interface Props {
  state: DraftFormState;
  update: <K extends keyof DraftFormState>(key: K, value: DraftFormState[K]) => void;
  errors: Record<string, string>;
}

export default function Step5_Evaluation({ state, update }: Props) {
  const e = state.evaluation;
  const set = (key: keyof typeof e, value: any) => {
    update("evaluation", { ...e, [key]: value });
  };

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-bold text-ink-900 mb-3">Step 5 — Evaluation Parameters</h2>
      <p className="text-xs text-ink-500 mb-4">
        Drives the L1 / effective-L1 computation downstream in Module 3.
      </p>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Field label="Evaluation Type" required>
          <Select
            value={e.evaluation_type}
            onChange={(ev) => set("evaluation_type", ev.target.value)}
          >
            <option value="Percentage">Percentage (above/below ECV)</option>
            <option value="Item Rate">Item Rate (per-line-item BoQ)</option>
            <option value="L.S">L.S (Lump Sum)</option>
            <option value="Composite">Composite</option>
          </Select>
        </Field>

        <Field label="Evaluation Criteria" required>
          <Select
            value={e.evaluation_criteria}
            onChange={(ev) => set("evaluation_criteria", ev.target.value)}
          >
            <option value="Based on Price">Based on Price (lowest wins)</option>
            <option value="Based on QCBS">Based on QCBS (Quality-Cost weighted)</option>
            <option value="Two-Envelope">Two-Envelope (Tech + Financial)</option>
          </Select>
        </Field>

        <Field label="Display Rank" required className="md:col-span-2">
          <Select
            value={e.display_rank}
            onChange={(ev) => set("display_rank", ev.target.value)}
          >
            <option value="Lowest">Lowest (L1 = lowest bid)</option>
            <option value="Highest">Highest (H1 = highest bid; e.g. lease/concession)</option>
          </Select>
        </Field>
      </div>
    </div>
  );
}
