"use client";

import { useState } from "react";
import JobRunner from "./JobRunner";

/**
 * DrafterForm — Module 1 "Generate New Draft" form.
 *
 * Posts {sector, ecv, location, contractor_class} to /api/m1/draft.
 * The backend is currently a Phase-2 stub that returns a queued
 * acknowledgement — the form is here so the UI flow is in place
 * for when the real LangGraph drafter ships.
 */

const SECTORS = ["Works", "Goods", "Services", "PPP"] as const;
const CLASSES = ["Class-A", "Class-B", "Class-C", "Special-Class"] as const;

export default function DrafterForm() {
  const [sector, setSector] = useState<typeof SECTORS[number]>("Works");
  const [ecv, setEcv] = useState("");
  const [location, setLocation] = useState("");
  const [contractorClass, setContractorClass] =
    useState<typeof CLASSES[number]>("Class-A");

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <label className="text-sm">
          <div className="font-semibold text-ink-900 mb-1">Sector</div>
          <select
            value={sector}
            onChange={(e) => setSector(e.target.value as any)}
            className="w-full rounded-md border border-mist-300 bg-white px-3 py-2 text-sm"
          >
            {SECTORS.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          <div className="font-semibold text-ink-900 mb-1">ECV (₹ crore)</div>
          <input
            type="number"
            min={0}
            step={0.5}
            value={ecv}
            onChange={(e) => setEcv(e.target.value)}
            placeholder="e.g. 125.5"
            className="w-full rounded-md border border-mist-300 bg-white px-3 py-2 text-sm"
          />
        </label>
        <label className="text-sm">
          <div className="font-semibold text-ink-900 mb-1">Location</div>
          <input
            type="text"
            value={location}
            onChange={(e) => setLocation(e.target.value)}
            placeholder="e.g. Amaravati, Vijayawada district"
            className="w-full rounded-md border border-mist-300 bg-white px-3 py-2 text-sm"
          />
        </label>
        <label className="text-sm">
          <div className="font-semibold text-ink-900 mb-1">Contractor class</div>
          <select
            value={contractorClass}
            onChange={(e) => setContractorClass(e.target.value as any)}
            className="w-full rounded-md border border-mist-300 bg-white px-3 py-2 text-sm"
          >
            {CLASSES.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
        </label>
      </div>
      <JobRunner
        endpoint="/api/m1/draft"
        label="Generate Tender Draft"
        runningLabel="Drafting…"
        payload={() => ({
          params: {
            sector,
            ecv: ecv ? parseFloat(ecv) : null,
            location,
            contractor_class: contractorClass,
          },
        })}
      />
    </div>
  );
}
