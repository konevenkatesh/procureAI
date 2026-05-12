"use client";

/**
 * R7.7 — BoQ skeleton upload widget for Step 6 of the new-draft wizard.
 *
 * Officer flow:
 *   1. Drag/drop or click to pick a .xlsx / .xls / .csv file
 *   2. We POST it to /api/m1/draft/parse-boq-skeleton which calls the
 *      same `parse_boq_skeleton()` the worker uses
 *   3. On success, parsed rows are stored on `state.boq_skeleton` and
 *      displayed in a compact preview table (first 12 rows, with
 *      collapse/expand)
 *   4. Officer can replace or clear the upload before generating
 *
 * The uploaded skeleton is optional. If absent, the workflow_v2
 * draft_BoQ node short-circuits with a "no_skeleton_supplied" note
 * and the resulting tender ships with an empty BoQ for the officer
 * to fill in later via a re-run path.
 */

import { useCallback, useRef, useState } from "react";
import type { BoQSkeletonRowFE } from "@/lib/draft-form-state";
import { Upload, FileSpreadsheet, X, ChevronDown, ChevronUp, AlertTriangle, CheckCircle2 } from "lucide-react";

interface Props {
  skeleton: BoQSkeletonRowFE[] | undefined;
  filename: string | undefined;
  onChange: (rows: BoQSkeletonRowFE[] | undefined, filename?: string) => void;
}

export default function BoQSkeletonUploader({ skeleton, filename, onChange }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [dragOver, setDragOver] = useState(false);

  const handleFiles = useCallback(async (files: FileList | null) => {
    setError(null);
    if (!files || files.length === 0) return;
    const file = files[0];
    const okExt = /\.(xlsx|xls|csv|txt)$/i.test(file.name);
    if (!okExt) {
      setError("Unsupported format. Use .xlsx, .xls, or .csv.");
      return;
    }
    if (file.size > 10 * 1024 * 1024) {
      setError("File too large (max 10 MB).");
      return;
    }

    setUploading(true);
    const fd = new FormData();
    fd.append("file", file, file.name);
    try {
      const res = await fetch("/api/m1/draft/parse-boq-skeleton", {
        method: "POST",
        body: fd,
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data?.error || `parse failed (${res.status})`);
        return;
      }
      const rows: BoQSkeletonRowFE[] = data.rows || [];
      if (rows.length === 0) {
        setError("Parser returned 0 rows. Check that the file has an item-description column.");
        return;
      }
      onChange(rows, data.filename || file.name);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setUploading(false);
    }
  }, [onChange]);

  const clear = () => {
    onChange(undefined, undefined);
    setError(null);
    if (inputRef.current) inputRef.current.value = "";
  };

  // ─── Render ─────────────────────────────────────────────────────

  if (skeleton && skeleton.length > 0) {
    return (
      <div className="rounded-md border border-leaf-300 bg-leaf-50/40 overflow-hidden">
        <div className="px-4 py-3 flex items-center justify-between border-b border-leaf-200">
          <div className="flex items-center gap-2 min-w-0">
            <CheckCircle2 className="h-4 w-4 text-leaf-700 shrink-0" />
            <div className="min-w-0">
              <div className="text-sm font-bold text-ink-900 truncate">{filename || "BoQ skeleton"}</div>
              <div className="text-xs text-ink-500">
                {skeleton.length} rows parsed — AI will enrich each with full spec_text, citations, work_type
              </div>
            </div>
          </div>
          <div className="flex items-center gap-1.5 shrink-0">
            <button
              onClick={() => setExpanded((v) => !v)}
              className="text-xs text-ink-700 hover:text-ink-900 px-2 py-1 inline-flex items-center gap-1"
            >
              {expanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
              {expanded ? "collapse" : `preview ${Math.min(skeleton.length, 12)} of ${skeleton.length}`}
            </button>
            <button
              onClick={() => inputRef.current?.click()}
              className="text-xs text-ink-700 hover:text-ink-900 px-2 py-1"
              title="Replace skeleton"
            >
              replace
            </button>
            <button
              onClick={clear}
              className="text-xs text-red-700 hover:text-red-900 px-2 py-1 inline-flex items-center gap-1"
              title="Clear skeleton"
            >
              <X className="h-3.5 w-3.5" /> clear
            </button>
          </div>
        </div>
        {expanded && (
          <div className="overflow-x-auto bg-white">
            <table className="w-full text-xs">
              <thead className="bg-mist-50">
                <tr className="text-left text-ink-700">
                  <th className="px-3 py-2 w-10">S.No</th>
                  <th className="px-3 py-2">Item</th>
                  <th className="px-3 py-2 w-20 text-right">Qty</th>
                  <th className="px-3 py-2 w-16">Unit</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-mist-100">
                {skeleton.slice(0, 12).map((r) => (
                  <tr key={r.s_no} className="hover:bg-mist-50/30">
                    <td className="px-3 py-1.5 tabular-nums">{r.s_no}</td>
                    <td className="px-3 py-1.5">{r.item_name}</td>
                    <td className="px-3 py-1.5 tabular-nums text-right">{r.qty}</td>
                    <td className="px-3 py-1.5">{r.unit}</td>
                  </tr>
                ))}
                {skeleton.length > 12 && (
                  <tr>
                    <td colSpan={4} className="px-3 py-1.5 text-center text-ink-500 italic">
                      … and {skeleton.length - 12} more rows
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
        <input
          ref={inputRef}
          type="file"
          accept=".xlsx,.xls,.csv,.txt"
          className="hidden"
          onChange={(e) => handleFiles(e.target.files)}
        />
      </div>
    );
  }

  // Empty state — drop zone
  return (
    <div>
      <div
        className={`rounded-md border-2 border-dashed transition-colors text-center px-6 py-8 cursor-pointer ${
          dragOver
            ? "border-saffron-500 bg-saffron-50"
            : "border-mist-300 bg-mist-50/30 hover:bg-mist-50"
        }`}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          handleFiles(e.dataTransfer.files);
        }}
      >
        {uploading ? (
          <div className="text-sm text-ink-700 flex items-center justify-center gap-2">
            <Upload className="h-4 w-4 animate-pulse" />
            Parsing skeleton…
          </div>
        ) : (
          <>
            <FileSpreadsheet className="h-7 w-7 text-ink-500 mx-auto mb-2" />
            <div className="text-sm font-bold text-ink-900 mb-1">Upload BoQ skeleton (optional)</div>
            <div className="text-xs text-ink-500 mb-2">
              Drop a .xlsx / .xls / .csv file, or click to browse. Each row should have an item
              description column; Qty &amp; Unit columns are detected automatically.
            </div>
            <div className="text-xs text-ink-500">
              AI will write the spec_text, work_type, APSS clause numbers, and IS/EN citations for
              each row. Skipping this upload yields a tender draft with an empty BoQ.
            </div>
          </>
        )}
      </div>
      <input
        ref={inputRef}
        type="file"
        accept=".xlsx,.xls,.csv,.txt"
        className="hidden"
        onChange={(e) => handleFiles(e.target.files)}
      />
      {error && (
        <div className="mt-2 rounded-md bg-red-50 border border-red-200 px-3 py-2 text-xs text-red-700 flex items-start gap-2">
          <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
          {error}
        </div>
      )}
    </div>
  );
}
