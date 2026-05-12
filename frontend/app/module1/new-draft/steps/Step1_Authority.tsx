"use client";

import { useMemo } from "react";
import { Field, TextInput, Select } from "@/components/m1/form-fields";
import type { DraftFormState } from "@/lib/draft-form-state";
import deptData from "@/data/ap-departments.json";

interface Props {
  state: DraftFormState;
  update: <K extends keyof DraftFormState>(key: K, value: DraftFormState[K]) => void;
  errors: Record<string, string>;
}

export default function Step1_Authority({ state, update, errors }: Props) {
  const e = state.enquiry_particulars;

  // Cascading: when department changes, reset circle/division
  const selectedDept = useMemo(
    () => deptData.departments.find((d) => d.name === e.department_name),
    [e.department_name],
  );

  const set = (key: keyof typeof e, value: any) => {
    update("enquiry_particulars", { ...e, [key]: value });
  };

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-bold text-ink-900 mb-3">Step 1 — Tender Inviting Authority</h2>
      <p className="text-xs text-ink-500 mb-4">
        Per the eGP "Enquiry Particulars" + "Tender Inviting Authority Particulars" sections.
      </p>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Field label="Department Name" required error={errors["enquiry_particulars.department_name"]}>
          <Select
            value={e.department_name}
            onChange={(ev) => {
              set("department_name", ev.target.value);
              set("circle_division", ""); // reset cascade
            }}
            hasError={!!errors["enquiry_particulars.department_name"]}
          >
            <option value="">— Select Department —</option>
            {deptData.departments.map((d) => (
              <option key={d.code} value={d.name}>
                {d.name} — {d.full_name}
              </option>
            ))}
          </Select>
        </Field>

        <Field
          label="Circle / Division"
          required
          error={errors["enquiry_particulars.circle_division"]}
          hint={!selectedDept ? "Select a department first" : undefined}
        >
          <Select
            value={e.circle_division}
            onChange={(ev) => set("circle_division", ev.target.value)}
            disabled={!selectedDept}
            hasError={!!errors["enquiry_particulars.circle_division"]}
          >
            <option value="">— Select Circle/Division —</option>
            {selectedDept?.circles.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </Select>
        </Field>

        <Field
          label="Officer Inviting Bids"
          required
          error={errors["enquiry_particulars.officer_inviting_bids"]}
        >
          <TextInput
            value={e.officer_inviting_bids}
            onChange={(ev) => set("officer_inviting_bids", ev.target.value)}
            placeholder="e.g. Executive Engineer, PR PIU division, Kurnool"
            hasError={!!errors["enquiry_particulars.officer_inviting_bids"]}
          />
        </Field>

        <Field label="Bid Opening Authority">
          <TextInput
            value={e.bid_opening_authority}
            onChange={(ev) => set("bid_opening_authority", ev.target.value)}
            placeholder="e.g. E E"
          />
        </Field>

        <Field
          label="Address"
          required
          className="md:col-span-2"
          error={errors["enquiry_particulars.address"]}
        >
          <TextInput
            value={e.address}
            onChange={(ev) => set("address", ev.target.value)}
            placeholder="e.g. Nunepalli MPDO Office Compound"
            hasError={!!errors["enquiry_particulars.address"]}
          />
        </Field>

        <Field label="Contact Details" required error={errors["enquiry_particulars.contact_details"]}>
          <TextInput
            value={e.contact_details}
            onChange={(ev) => set("contact_details", ev.target.value)}
            placeholder="e.g. 7780743028"
            hasError={!!errors["enquiry_particulars.contact_details"]}
          />
        </Field>

        <Field label="Email" required error={errors["enquiry_particulars.email"]}>
          <TextInput
            type="email"
            value={e.email}
            onChange={(ev) => set("email", ev.target.value)}
            placeholder="e.g. eepiuknl@yahoo.com"
            hasError={!!errors["enquiry_particulars.email"]}
          />
        </Field>

        <Field
          label="Name of Project"
          required
          error={errors["enquiry_particulars.name_of_project"]}
        >
          <TextInput
            value={e.name_of_project}
            onChange={(ev) => set("name_of_project", ev.target.value)}
            placeholder="e.g. DMF"
            hasError={!!errors["enquiry_particulars.name_of_project"]}
          />
        </Field>

        <Field
          label="Name of Work"
          required
          className="md:col-span-2"
          error={errors["enquiry_particulars.name_of_work"]}
          hint="Minimum 10 characters. Describe the specific work scope."
        >
          <TextInput
            value={e.name_of_work}
            onChange={(ev) => set("name_of_work", ev.target.value)}
            placeholder="e.g. Providing Kitchen Shed and additional facilities to Shadikhana at Banaganapalli"
            hasError={!!errors["enquiry_particulars.name_of_work"]}
          />
        </Field>
      </div>
    </div>
  );
}
