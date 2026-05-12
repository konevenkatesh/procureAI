/**
 * RegionBadge — Server Component pill that surfaces:
 *   "asia-south1 · DPDP-compliant"
 *
 * Render once at the top of the dashboard or a page header. No state,
 * no client JS — fits inline next to any heading.
 */
import { ShieldCheck } from "lucide-react";

export default function RegionBadge() {
  return (
    <span
      title="Cloud Run + Cloud Storage + Secret Manager all in asia-south1 (Mumbai). Cloud Audit Logs DATA_READ+DATA_WRITE enabled with 400-day retention."
      className="inline-flex items-center gap-1.5 rounded-full border border-green-200 bg-green-50 px-2.5 py-0.5 text-xs font-semibold text-green-900"
    >
      <ShieldCheck className="h-3 w-3" />
      asia-south1 · DPDP-compliant
    </span>
  );
}
