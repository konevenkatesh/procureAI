"use client";

import { Field, TextInput, Select } from "@/components/m1/form-fields";
import type { DraftFormState } from "@/lib/draft-form-state";
import docTemplates from "@/data/document-templates.json";
import { CheckCircle2, Plus, Trash2 } from "lucide-react";

interface Props {
  state: DraftFormState;
  update: <K extends keyof DraftFormState>(key: K, value: DraftFormState[K]) => void;
  errors: Record<string, string>;
}

function toLocalInput(iso: string): string {
  // ISO with TZ → "YYYY-MM-DDTHH:MM" for datetime-local input
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const off = d.getTimezoneOffset();
  const local = new Date(d.getTime() - off * 60 * 1000);
  return local.toISOString().slice(0, 16);
}

function fromLocalInput(localStr: string): string {
  if (!localStr) return "";
  // Treat input as local time, convert back to ISO with TZ
  return new Date(localStr).toISOString();
}

export default function Step6_DocumentsDates({ state, update, errors }: Props) {
  const docs = state.documents;
  const dates = state.dates;

  const toggleOptional = (docName: string) => {
    const existing = docs.find((d) => d.document_name === docName);
    if (existing) {
      // remove
      const filtered = docs.filter((d) => d.document_name !== docName);
      // re-number
      const renumbered = filtered.map((d, i) => ({ ...d, s_no: i + 1 }));
      update("documents", renumbered);
    } else {
      // add
      const opt = docTemplates.optional_additional.find((o) => o.document_name === docName);
      if (opt) {
        update("documents", [...docs, { ...opt, s_no: docs.length + 1 } as any]);
      }
    }
  };

  const removeDoc = (s_no: number) => {
    const filtered = docs.filter((d) => d.s_no !== s_no);
    const renumbered = filtered.map((d, i) => ({ ...d, s_no: i + 1 }));
    update("documents", renumbered);
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-bold text-ink-900 mb-3">Step 6 — Documents & Dates</h2>
        <p className="text-xs text-ink-500 mb-4">
          7 mandatory documents pre-loaded. Add optional documents as needed. Dates must satisfy
          GFR-norm windows (closing ≥ start + 16 days for ECV ≥ ₹50 lakh).
        </p>
      </div>

      <div>
        <h3 className="text-sm font-bold text-ink-900 mb-2">Required Tender Documents ({docs.length})</h3>
        <div className="rounded-md border border-mist-200 bg-white overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="bg-mist-50">
              <tr className="text-left text-ink-700">
                <th className="px-3 py-2 w-8">#</th>
                <th className="px-3 py-2">Document</th>
                <th className="px-3 py-2 w-24">Stage</th>
                <th className="px-3 py-2 w-24">Type</th>
                <th className="px-3 py-2 w-12"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-mist-100">
              {docs.map((d) => (
                <tr key={d.s_no} className="hover:bg-mist-50/30">
                  <td className="px-3 py-1.5 tabular-nums">{d.s_no}</td>
                  <td className="px-3 py-1.5">{d.document_name}</td>
                  <td className="px-3 py-1.5"><code className="text-[10px]">{d.stage}</code></td>
                  <td className="px-3 py-1.5">
                    <span className={d.document_type === "Mandatory" ? "text-red-700 font-semibold" : "text-ink-500"}>
                      {d.document_type}
                    </span>
                  </td>
                  <td className="px-3 py-1.5">
                    {d.document_type === "Optional" && (
                      <button onClick={() => removeDoc(d.s_no)} className="text-ink-500 hover:text-red-700">
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {errors.documents && <div className="text-xs text-red-700 mt-1">{errors.documents}</div>}
      </div>

      <div>
        <h3 className="text-sm font-bold text-ink-900 mb-2">Add Optional Documents</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
          {docTemplates.optional_additional.map((opt) => {
            const added = !!docs.find((d) => d.document_name === opt.document_name);
            return (
              <button
                key={opt.document_name}
                onClick={() => toggleOptional(opt.document_name)}
                className={`text-left text-xs rounded-md border px-3 py-2 transition-colors flex items-center gap-2 ${
                  added
                    ? "bg-leaf-50 border-leaf-300 text-leaf-700"
                    : "bg-white border-mist-200 hover:bg-mist-50 text-ink-700"
                }`}
              >
                {added ? <CheckCircle2 className="h-4 w-4" /> : <Plus className="h-4 w-4" />}
                <span>{opt.document_name}</span>
              </button>
            );
          })}
        </div>
      </div>

      <div>
        <h3 className="text-sm font-bold text-ink-900 mb-2">Tender Dates</h3>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <Field label="Start Date & Time" required>
            <input
              type="datetime-local"
              value={toLocalInput(dates.start_date)}
              onChange={(ev) =>
                update("dates", { ...dates, start_date: fromLocalInput(ev.target.value) })
              }
              className="w-full rounded-md border border-mist-200 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-saffron-100 focus:border-saffron-500"
            />
          </Field>

          <Field label="End Date & Time" required>
            <input
              type="datetime-local"
              value={toLocalInput(dates.end_date)}
              onChange={(ev) =>
                update("dates", { ...dates, end_date: fromLocalInput(ev.target.value) })
              }
              className="w-full rounded-md border border-mist-200 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-saffron-100 focus:border-saffron-500"
            />
          </Field>

          <Field
            label="Closing Date & Time"
            required
            error={errors["dates.closing_date"]}
            hint="GFR norm: ≥ start + 16 days for ECV ≥ ₹50 lakh"
          >
            <input
              type="datetime-local"
              value={toLocalInput(dates.closing_date)}
              onChange={(ev) =>
                update("dates", { ...dates, closing_date: fromLocalInput(ev.target.value) })
              }
              className="w-full rounded-md border border-mist-200 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-saffron-100 focus:border-saffron-500"
            />
          </Field>
        </div>
      </div>
    </div>
  );
}
