"use client";

import { cn } from "@/lib/utils";
import * as React from "react";

interface FieldProps {
  label: string;
  required?: boolean;
  error?: string;
  hint?: string;
  className?: string;
  children: React.ReactNode;
}

export function Field({ label, required, error, hint, className, children }: FieldProps) {
  return (
    <div className={cn("space-y-1", className)}>
      <label className="block text-xs font-semibold text-ink-700">
        {label}
        {required && <span className="text-red-600 ml-0.5">*</span>}
      </label>
      {children}
      {hint && !error && <div className="text-xs text-ink-500">{hint}</div>}
      {error && <div className="text-xs text-red-700 font-medium">{error}</div>}
    </div>
  );
}

interface TextInputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  hasError?: boolean;
}

export function TextInput({ hasError, className, ...props }: TextInputProps) {
  return (
    <input
      type="text"
      className={cn(
        "w-full rounded-md border bg-white px-3 py-2 text-sm text-ink-900 transition-colors",
        "focus:outline-none focus:ring-2",
        hasError
          ? "border-red-400 focus:ring-red-300 focus:border-red-500"
          : "border-mist-200 focus:ring-saffron-100 focus:border-saffron-500",
        className,
      )}
      {...props}
    />
  );
}

interface NumberInputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  hasError?: boolean;
}

export function NumberInput({ hasError, className, ...props }: NumberInputProps) {
  return (
    <input
      type="number"
      inputMode="numeric"
      className={cn(
        "w-full rounded-md border bg-white px-3 py-2 text-sm text-ink-900 transition-colors tabular-nums",
        "focus:outline-none focus:ring-2",
        hasError
          ? "border-red-400 focus:ring-red-300 focus:border-red-500"
          : "border-mist-200 focus:ring-saffron-100 focus:border-saffron-500",
        className,
      )}
      {...props}
    />
  );
}

interface SelectProps extends React.SelectHTMLAttributes<HTMLSelectElement> {
  hasError?: boolean;
  children: React.ReactNode;
}

export function Select({ hasError, className, children, ...props }: SelectProps) {
  return (
    <select
      className={cn(
        "w-full rounded-md border bg-white px-3 py-2 text-sm text-ink-900 transition-colors",
        "focus:outline-none focus:ring-2",
        hasError
          ? "border-red-400 focus:ring-red-300 focus:border-red-500"
          : "border-mist-200 focus:ring-saffron-100 focus:border-saffron-500",
        className,
      )}
      {...props}
    >
      {children}
    </select>
  );
}

interface TextAreaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  hasError?: boolean;
}

export function TextArea({ hasError, className, ...props }: TextAreaProps) {
  return (
    <textarea
      className={cn(
        "w-full rounded-md border bg-white px-3 py-2 text-sm text-ink-900 transition-colors min-h-[80px]",
        "focus:outline-none focus:ring-2",
        hasError
          ? "border-red-400 focus:ring-red-300 focus:border-red-500"
          : "border-mist-200 focus:ring-saffron-100 focus:border-saffron-500",
        className,
      )}
      {...props}
    />
  );
}
