"use client";

import { Field, TextInput, Select, NumberInput } from "@/components/m1/form-fields";
import type { DraftFormState } from "@/lib/draft-form-state";

interface Props {
  state: DraftFormState;
  update: <K extends keyof DraftFormState>(key: K, value: DraftFormState[K]) => void;
  errors: Record<string, string>;
}

export default function Step2_Classification({ state, update, errors }: Props) {
  const c = state.classification;
  const set = (key: keyof typeof c, value: any) => {
    update("classification", { ...c, [key]: value });
  };

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-bold text-ink-900 mb-3">Step 2 — Tender Classification</h2>
      <p className="text-xs text-ink-500 mb-4">
        Drives the rule library + the LangGraph prompt set used during AI generation.
      </p>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Field label="Tender Category" required>
          <Select
            value={c.tender_category}
            onChange={(ev) => set("tender_category", ev.target.value)}
          >
            <option value="WORKS">WORKS</option>
            <option value="GOODS">GOODS</option>
            <option value="SERVICES">SERVICES</option>
          </Select>
        </Field>

        <Field
          label="Type of Work"
          required
          error={errors["classification.type_of_work"]}
          hint="e.g. Civil Works, Mechanical, Electrical, Roads, Buildings"
        >
          <TextInput
            value={c.type_of_work}
            onChange={(ev) => set("type_of_work", ev.target.value)}
            placeholder="e.g. Civil Works"
            hasError={!!errors["classification.type_of_work"]}
          />
        </Field>

        <Field label="Tender Type" required>
          <Select
            value={c.tender_type}
            onChange={(ev) => set("tender_type", ev.target.value)}
          >
            <option value="OPEN - NCB">OPEN - NCB (National Competitive Bidding)</option>
            <option value="OPEN - ICB">OPEN - ICB (International Competitive Bidding)</option>
            <option value="LIMITED">LIMITED</option>
            <option value="SINGLE_TENDER">SINGLE TENDER</option>
            <option value="EOI">EOI (Expression of Interest)</option>
          </Select>
        </Field>

        <Field label="Bidding Type" required>
          <Select
            value={c.bidding_type}
            onChange={(ev) => set("bidding_type", ev.target.value)}
          >
            <option value="OPEN">OPEN</option>
            <option value="LIMITED">LIMITED</option>
            <option value="EOI">EOI</option>
            <option value="SINGLE_SOURCE">SINGLE_SOURCE</option>
          </Select>
        </Field>

        <Field label="Form of Contract" required>
          <Select
            value={c.form_of_contract}
            onChange={(ev) => set("form_of_contract", ev.target.value)}
          >
            <option value="L.S">L.S (Lump Sum)</option>
            <option value="Item Rate">Item Rate</option>
            <option value="Percentage">Percentage</option>
            <option value="EPC">EPC</option>
            <option value="Cost Plus">Cost Plus</option>
          </Select>
        </Field>

        <Field label="Consortium / Joint Venture" required>
          <Select
            value={c.consortium_joint_venture}
            onChange={(ev) => set("consortium_joint_venture", ev.target.value)}
          >
            <option value="Not Applicable">Not Applicable</option>
            <option value="Applicable">Applicable (JV permitted)</option>
          </Select>
        </Field>

        <Field
          label="Bid Call Numbers"
          required
          hint="Number of times this tender has been called. Default 1 for fresh tenders; 2+ for re-tenders."
          error={errors["classification.bid_call_numbers"]}
        >
          <NumberInput
            min={1}
            max={5}
            value={c.bid_call_numbers}
            onChange={(ev) => set("bid_call_numbers", parseInt(ev.target.value || "1", 10))}
            hasError={!!errors["classification.bid_call_numbers"]}
          />
        </Field>
      </div>
    </div>
  );
}
