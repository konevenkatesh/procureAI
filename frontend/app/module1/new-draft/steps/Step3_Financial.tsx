"use client";

import { Field, TextInput, Select, NumberInput } from "@/components/m1/form-fields";
import type { DraftFormState } from "@/lib/draft-form-state";
import { formatINR } from "@/lib/inr-words";

interface Props {
  state: DraftFormState;
  update: <K extends keyof DraftFormState>(key: K, value: DraftFormState[K]) => void;
  updateFinancialWithDerivations: (patch: Partial<DraftFormState["financial"]>) => void;
  errors: Record<string, string>;
}

export default function Step3_Financial({ state, updateFinancialWithDerivations, errors }: Props) {
  const f = state.financial;

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-bold text-ink-900 mb-3">Step 3 — Financial Particulars</h2>
      <p className="text-xs text-ink-500 mb-4">
        ECV auto-derives the words form + bid security amount. Per APTS norm, transaction fee
        defaults to ₹566 (G.O.Ms No 4, Dtd 17.02.2015 IT&C Dept).
      </p>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Field
          label="Estimated Contract Value (INR)"
          required
          error={errors["financial.estimated_contract_value_inr"]}
          className="md:col-span-2"
          hint={
            f.estimated_contract_value_inr > 0
              ? `${formatINR(f.estimated_contract_value_inr)} — ${f.estimated_contract_value_words}`
              : "Auto-derives words + bid security amount when entered"
          }
        >
          <NumberInput
            min={1}
            value={f.estimated_contract_value_inr || ""}
            onChange={(ev) =>
              updateFinancialWithDerivations({
                estimated_contract_value_inr: parseInt(ev.target.value || "0", 10),
              })
            }
            placeholder="e.g. 1597185"
            hasError={!!errors["financial.estimated_contract_value_inr"]}
          />
        </Field>

        <Field
          label="Period of Completion (Months)"
          required
          error={errors["financial.period_of_completion_months"]}
        >
          <NumberInput
            min={1}
            max={120}
            value={f.period_of_completion_months}
            onChange={(ev) =>
              updateFinancialWithDerivations({
                period_of_completion_months: parseInt(ev.target.value || "1", 10),
              })
            }
            hasError={!!errors["financial.period_of_completion_months"]}
          />
        </Field>

        <Field
          label="Bid Validity Period (Days)"
          required
          hint="Typically 90-180 days for AP works tenders"
          error={errors["financial.bid_validity_days"]}
        >
          <NumberInput
            min={30}
            max={365}
            value={f.bid_validity_days}
            onChange={(ev) =>
              updateFinancialWithDerivations({
                bid_validity_days: parseInt(ev.target.value || "90", 10),
              })
            }
            hasError={!!errors["financial.bid_validity_days"]}
          />
        </Field>

        <Field
          label="Bid Security %"
          required
          error={errors["financial.bid_security_percent"]}
          hint="Typically 1% of ECV"
        >
          <NumberInput
            min={0}
            max={10}
            step={0.1}
            value={f.bid_security_percent}
            onChange={(ev) =>
              updateFinancialWithDerivations({
                bid_security_percent: parseFloat(ev.target.value || "1"),
              })
            }
            hasError={!!errors["financial.bid_security_percent"]}
          />
        </Field>

        <Field
          label="Bid Security Amount (auto)"
          hint={`Derived: ECV × ${f.bid_security_percent}% = ${formatINR(f.bid_security_inr)}`}
        >
          <TextInput
            value={formatINR(f.bid_security_inr)}
            disabled
            className="bg-mist-50 font-semibold tabular-nums"
          />
        </Field>

        <Field label="Bid Security In Favour Of">
          <TextInput
            value={f.bid_security_in_favour_of}
            onChange={(ev) =>
              updateFinancialWithDerivations({ bid_security_in_favour_of: ev.target.value })
            }
          />
        </Field>

        <Field label="Mode of Payment" className="md:col-span-2">
          <TextInput
            value={f.mode_of_payment}
            onChange={(ev) =>
              updateFinancialWithDerivations({ mode_of_payment: ev.target.value })
            }
          />
        </Field>

        <Field label="Currency Type">
          <Select
            value={f.currency_type}
            onChange={(ev) =>
              updateFinancialWithDerivations({
                currency_type: ev.target.value as any,
              })
            }
          >
            <option value="INR">INR (Indian Rupee)</option>
            <option value="USD">USD</option>
            <option value="EUR">EUR</option>
          </Select>
        </Field>

        <Field
          label="Transaction Fee (INR)"
          hint="Default ₹566 per APTS norm. Edit only if revised G.O. applies."
        >
          <NumberInput
            min={0}
            value={f.transaction_fee_inr}
            onChange={(ev) =>
              updateFinancialWithDerivations({
                transaction_fee_inr: parseInt(ev.target.value || "566", 10),
              })
            }
          />
        </Field>
      </div>
    </div>
  );
}
