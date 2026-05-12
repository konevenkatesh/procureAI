/**
 * Shared draft-state hook for the 7-step wizard.
 *
 * Uses React state + localStorage persistence so a Dealing Officer can
 * close the tab and resume mid-wizard. State is keyed by `draft_id`;
 * if absent at mount, a fresh `m1_draft_<uuid>` id is generated.
 */
"use client";

import { useEffect, useState, useCallback } from "react";
import type {
  TenderDraftState,
  EnquiryParticulars,
  Classification,
  Financial,
  Geography,
  Evaluation,
  TenderDocument,
  TenderDates,
  EnquiryForm,
} from "@/types/m1-drafter";
import { inrToWords } from "./inr-words";
import docTemplates from "@/data/document-templates.json";

const STORAGE_KEY = "m1.new-draft.wip";

// ─── Default state — empty form, eGP defaults pre-filled ────────────

function defaultEnquiry(): EnquiryParticulars {
  return {
    department_name: "",
    circle_division: "",
    officer_inviting_bids: "",
    bid_opening_authority: "E E",
    address: "",
    contact_details: "",
    email: "",
    name_of_project: "",
    name_of_work: "",
  };
}

function defaultClassification(): Classification {
  return {
    tender_category: "WORKS",
    type_of_work: "Civil Works",
    tender_type: "OPEN - NCB",
    bidding_type: "OPEN",
    form_of_contract: "L.S",
    consortium_joint_venture: "Not Applicable",
    bid_call_numbers: 1,
  };
}

function defaultFinancial(): Financial {
  return {
    estimated_contract_value_inr: 0,
    estimated_contract_value_words: "",
    period_of_completion_months: 6,
    bid_validity_days: 90,
    bid_security_percent: 1.0,
    bid_security_inr: 0,
    bid_security_in_favour_of: "Online payment",
    mode_of_payment: "Online Payment, Challan Generation, BG",
    currency_type: "INR",
    default_currency: "Indian Rupee - INR",
    transaction_fee_inr: 566,
    transaction_fee_payable_to: "APTS payable at Vijayawada",
    transaction_fee_go_reference: "G.O.Ms No 4, Dtd 17.02.2015 IT&C Dept",
  };
}

function defaultGeography(): Geography {
  return { state: "ANDHRA PRADESH", district: "", mandal: "", assembly: "", parliament: "" };
}

function defaultEvaluation(): Evaluation {
  return { evaluation_type: "Percentage", evaluation_criteria: "Based on Price", display_rank: "Lowest" };
}

function defaultDocuments(): TenderDocument[] {
  return docTemplates.mandatory_default as TenderDocument[];
}

function defaultDates(): TenderDates {
  const now = new Date();
  const sixteenDaysOut = new Date(now.getTime() + 16 * 24 * 3600 * 1000);
  return {
    start_date: now.toISOString(),
    end_date: sixteenDaysOut.toISOString(),
    closing_date: new Date(sixteenDaysOut.getTime() + 30 * 60 * 1000).toISOString(),
  };
}

function defaultEnquiryForms(): EnquiryForm[] {
  return docTemplates.default_enquiry_forms as EnquiryForm[];
}

export type DraftFormState = Omit<
  TenderDraftState,
  | "general_terms"
  | "boq"
  | "citations"
  | "current_gate"
  | "current_assignee_role"
  | "version"
  | "created_at"
  | "last_updated_at"
  | "created_by"
> & { draft_id: string };

export function emptyDraftForm(): DraftFormState {
  const uuid =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : Math.random().toString(36).slice(2);
  return {
    draft_id: `m1_draft_${uuid}`,
    enquiry_particulars: defaultEnquiry(),
    classification: defaultClassification(),
    financial: defaultFinancial(),
    geography: defaultGeography(),
    evaluation: defaultEvaluation(),
    documents: defaultDocuments(),
    dates: defaultDates(),
    enquiry_forms: defaultEnquiryForms(),
  };
}

// ─── Hook ───────────────────────────────────────────────────────────

export function useDraftForm() {
  const [state, setState] = useState<DraftFormState | null>(null);
  const [step, setStep] = useState<number>(1);

  // Hydrate from localStorage on first mount (client-only).
  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        setState(parsed.state || emptyDraftForm());
        setStep(parsed.step || 1);
        return;
      }
    } catch {
      // fall through to fresh
    }
    setState(emptyDraftForm());
  }, []);

  // Persist on every change.
  useEffect(() => {
    if (typeof window === "undefined" || state === null) return;
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ state, step }));
    } catch {
      /* localStorage quota etc; non-fatal */
    }
  }, [state, step]);

  const update = useCallback(
    <K extends keyof DraftFormState>(key: K, value: DraftFormState[K]) => {
      setState((s) => (s ? { ...s, [key]: value } : s));
    },
    [],
  );

  // Cross-field derivations (run after ECV or bid_security_percent change)
  const updateFinancialWithDerivations = useCallback((patch: Partial<Financial>) => {
    setState((s) => {
      if (!s) return s;
      const f = { ...s.financial, ...patch };
      // Auto-words for ECV
      if ("estimated_contract_value_inr" in patch) {
        f.estimated_contract_value_words = inrToWords(f.estimated_contract_value_inr);
      }
      // Auto bid_security_inr from ECV × percent / 100
      if (
        "estimated_contract_value_inr" in patch ||
        "bid_security_percent" in patch
      ) {
        f.bid_security_inr = Math.round(
          (f.estimated_contract_value_inr * f.bid_security_percent) / 100,
        );
      }
      return { ...s, financial: f };
    });
  }, []);

  const reset = useCallback(() => {
    if (typeof window !== "undefined") localStorage.removeItem(STORAGE_KEY);
    setState(emptyDraftForm());
    setStep(1);
  }, []);

  return { state, setState, step, setStep, update, updateFinancialWithDerivations, reset };
}

// ─── Step-level validators ──────────────────────────────────────────

export interface ValidationResult {
  ok: boolean;
  errors: Record<string, string>;
}

export function validateStep(stepNum: number, s: DraftFormState): ValidationResult {
  const errors: Record<string, string> = {};

  const need = (path: string, value: any, msg: string) => {
    if (value === null || value === undefined || value === "" ||
        (Array.isArray(value) && value.length === 0)) {
      errors[path] = msg;
    }
  };

  switch (stepNum) {
    case 1: {
      const e = s.enquiry_particulars;
      need("enquiry_particulars.department_name", e.department_name, "Department required");
      need("enquiry_particulars.circle_division", e.circle_division, "Circle/Division required");
      need("enquiry_particulars.officer_inviting_bids", e.officer_inviting_bids, "Officer required");
      need("enquiry_particulars.address", e.address, "Address required");
      need("enquiry_particulars.contact_details", e.contact_details, "Contact required");
      need("enquiry_particulars.email", e.email, "Email required");
      if (e.email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(e.email)) {
        errors["enquiry_particulars.email"] = "Invalid email format";
      }
      need("enquiry_particulars.name_of_project", e.name_of_project, "Project required");
      need("enquiry_particulars.name_of_work", e.name_of_work, "Name of work required");
      if (e.name_of_work && e.name_of_work.length < 10) {
        errors["enquiry_particulars.name_of_work"] = "Name of work too short (≥10 chars)";
      }
      break;
    }
    case 2: {
      const c = s.classification;
      need("classification.type_of_work", c.type_of_work, "Type of work required");
      if (c.bid_call_numbers < 1) errors["classification.bid_call_numbers"] = "Bid calls must be ≥ 1";
      break;
    }
    case 3: {
      const f = s.financial;
      if (f.estimated_contract_value_inr < 1) {
        errors["financial.estimated_contract_value_inr"] = "ECV must be > 0";
      }
      if (f.period_of_completion_months < 1) {
        errors["financial.period_of_completion_months"] = "Period must be ≥ 1 month";
      }
      if (f.bid_validity_days < 30 || f.bid_validity_days > 365) {
        errors["financial.bid_validity_days"] = "Bid validity 30-365 days";
      }
      if (f.bid_security_percent < 0 || f.bid_security_percent > 10) {
        errors["financial.bid_security_percent"] = "Bid security 0-10%";
      }
      break;
    }
    case 4: {
      const g = s.geography;
      need("geography.district", g.district, "District required");
      need("geography.mandal", g.mandal, "Mandal required");
      need("geography.assembly", g.assembly, "Assembly required");
      need("geography.parliament", g.parliament, "Parliament required");
      break;
    }
    case 5: {
      // Evaluation defaults are always set; nothing to validate
      break;
    }
    case 6: {
      need("documents", s.documents, "At least one mandatory document required");
      // GFR-norm date math: closing >= start + 16 days for ECV >= ₹50L
      const ecv = s.financial.estimated_contract_value_inr;
      if (ecv >= 5_000_000) {
        const start = new Date(s.dates.start_date);
        const closing = new Date(s.dates.closing_date);
        const days = (closing.getTime() - start.getTime()) / (24 * 3600 * 1000);
        if (days < 16) {
          errors["dates.closing_date"] = `GFR norm: closing date must be ≥ 16 days after start (currently ${days.toFixed(1)} days)`;
        }
      }
      break;
    }
    case 7: {
      // Final review — re-run all prior steps
      for (let i = 1; i <= 6; i++) {
        const res = validateStep(i, s);
        Object.assign(errors, res.errors);
      }
      break;
    }
  }

  return { ok: Object.keys(errors).length === 0, errors };
}
