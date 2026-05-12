"use client";

import { useMemo } from "react";
import { Field, TextInput, Select } from "@/components/m1/form-fields";
import type { DraftFormState } from "@/lib/draft-form-state";
import geoData from "@/data/ap-geography.json";

interface Props {
  state: DraftFormState;
  update: <K extends keyof DraftFormState>(key: K, value: DraftFormState[K]) => void;
  errors: Record<string, string>;
}

export default function Step4_Geography({ state, update, errors }: Props) {
  const g = state.geography;
  const set = (key: keyof typeof g, value: any) => {
    update("geography", { ...g, [key]: value });
  };

  const selectedDistrict = useMemo(
    () => geoData.districts.find((d) => d.name === g.district),
    [g.district],
  );

  const selectedMandal = useMemo(
    () => selectedDistrict?.mandals.find((m) => m.name === g.mandal),
    [selectedDistrict, g.mandal],
  );

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-bold text-ink-900 mb-3">Step 4 — Geographical Particulars</h2>
      <p className="text-xs text-ink-500 mb-4">
        Cascade: District → Mandal → Assembly. Parliament auto-fills from District.
      </p>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Field label="State" required>
          <TextInput value={g.state} disabled className="bg-mist-50" />
        </Field>

        <Field label="District" required error={errors["geography.district"]}>
          <Select
            value={g.district}
            onChange={(ev) => {
              const districtName = ev.target.value;
              set("district", districtName);
              set("mandal", "");
              set("assembly", "");
              const d = geoData.districts.find((x) => x.name === districtName);
              set("parliament", d?.parliament || "");
            }}
            hasError={!!errors["geography.district"]}
          >
            <option value="">— Select District —</option>
            {geoData.districts.map((d) => (
              <option key={d.name} value={d.name}>{d.name}</option>
            ))}
          </Select>
        </Field>

        <Field
          label="Mandal"
          required
          error={errors["geography.mandal"]}
          hint={!selectedDistrict ? "Select district first" : undefined}
        >
          <Select
            value={g.mandal}
            onChange={(ev) => {
              const mandalName = ev.target.value;
              set("mandal", mandalName);
              const m = selectedDistrict?.mandals.find((x) => x.name === mandalName);
              set("assembly", m?.assembly || "");
            }}
            disabled={!selectedDistrict}
            hasError={!!errors["geography.mandal"]}
          >
            <option value="">— Select Mandal —</option>
            {selectedDistrict?.mandals.map((m) => (
              <option key={m.name} value={m.name}>{m.name}</option>
            ))}
          </Select>
        </Field>

        <Field label="Assembly Constituency" required error={errors["geography.assembly"]}>
          <TextInput
            value={g.assembly}
            onChange={(ev) => set("assembly", ev.target.value)}
            disabled={!selectedMandal}
            className={!selectedMandal ? "bg-mist-50" : undefined}
            placeholder={selectedMandal ? selectedMandal.assembly : "Select mandal first"}
            hasError={!!errors["geography.assembly"]}
          />
        </Field>

        <Field
          label="Parliamentary Constituency"
          required
          error={errors["geography.parliament"]}
          className="md:col-span-2"
        >
          <TextInput
            value={g.parliament}
            onChange={(ev) => set("parliament", ev.target.value)}
            disabled={!selectedDistrict}
            className={!selectedDistrict ? "bg-mist-50" : undefined}
            hasError={!!errors["geography.parliament"]}
          />
        </Field>
      </div>
    </div>
  );
}
