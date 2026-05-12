"""R8.4 — Token + cost actuals consolidation across R8.1 / R8.2 / R8.3 smokes.

Reads /tmp/r8{1,2,3}_smoke_result.json files and produces:
  - /tmp/r8_4_cost_actuals.md with per-scale cost + token actuals
  - Pre-flight estimate vs actual variance
  - Cost-per-row + cost-per-page extrapolations for production planning
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

USD_INR = 83.0

PRE_FLIGHT_ESTIMATES = {
    "Banaganapalli":      {"cost_inr": 0.40, "n_rows":  30,  "wall_clock_sec":  90},
    "LPS Zone-11 mid":    {"cost_inr": 10.0, "n_rows": 800,  "wall_clock_sec": 300},
    "HOD Towers capital": {"cost_inr": 40.0, "n_rows": 3000, "wall_clock_sec": 900},
}


def main():
    results = {}
    for stem in ("r81", "r82", "r83"):
        path = Path(f"/tmp/{stem}_smoke_result.json")
        if path.exists():
            results[stem] = json.loads(path.read_text())

    if not results:
        print("No smoke result files found in /tmp/ (r81/r82/r83 *_smoke_result.json).")
        return 1

    lines = []
    lines.append("# R8.4 — Run 8 Token + Cost Actuals\n")
    lines.append(f"_Date: 2026-05-13 · Source: /tmp/r8{{1,2,3}}_smoke_result.json_\n")
    lines.append("\n## Per-scale actuals vs pre-flight estimates\n\n")
    lines.append("| Scale | Rows | Wall (s) | Wall vs est | Cost ₹ | Cost vs est | Flash in | Flash out | Pro in | Pro out | Pass |\n")
    lines.append("|-------|-----:|---------:|------------:|------:|------------:|---------:|----------:|-------:|--------:|:----:|\n")

    total_cost = 0.0
    total_flash_in = 0
    total_flash_out = 0
    total_pro_in = 0
    total_pro_out = 0
    for stem, r in results.items():
        label = r.get("label", stem)
        est = PRE_FLIGHT_ESTIMATES.get(label, {"cost_inr": 0, "wall_clock_sec": 0})
        wall_var = (r["wall_clock_sec"] - est["wall_clock_sec"]) / max(est["wall_clock_sec"], 1) * 100
        cost_var = (r["cost_inr"] - est["cost_inr"]) / max(est["cost_inr"], 0.01) * 100
        total_cost += r.get("cost_inr", 0)
        total_flash_in  += r.get("flash_in", 0)
        total_flash_out += r.get("flash_out", 0)
        total_pro_in    += r.get("pro_in", 0)
        total_pro_out   += r.get("pro_out", 0)
        passed = "✅" if r.get("all_pass") else "❌"
        lines.append(
            f"| {label} | {r['n_skeleton']} | {r['wall_clock_sec']:.1f} | "
            f"{wall_var:+.0f}% | ₹{r['cost_inr']:.2f} | {cost_var:+.0f}% | "
            f"{r.get('flash_in', 0):,} | {r.get('flash_out', 0):,} | "
            f"{r.get('pro_in', 0):,} | {r.get('pro_out', 0):,} | {passed} |\n"
        )

    lines.append(f"\n**Run 8 cumulative cost: ₹{total_cost:.2f}** (budget ₹100)\n")
    lines.append(f"Flash tokens: {total_flash_in:,} in / {total_flash_out:,} out\n")
    lines.append(f"Pro tokens:   {total_pro_in:,} in / {total_pro_out:,} out\n\n")

    # Cost-per-row analysis
    lines.append("## Cost-per-row scaling\n\n")
    lines.append("| Scale | Rows | Cost ₹ | ₹/row | ₹/100 rows |\n")
    lines.append("|-------|-----:|------:|------:|----------:|\n")
    for stem, r in results.items():
        rows = r.get("n_boq_rows") or r.get("n_skeleton") or 1
        per_row = r.get("cost_inr", 0) / max(rows, 1)
        lines.append(
            f"| {r.get('label', stem)} | {rows} | ₹{r['cost_inr']:.2f} | "
            f"₹{per_row:.5f} | ₹{per_row*100:.3f} |\n"
        )

    lines.append("\n## Throughput\n\n")
    lines.append("| Scale | Wall (s) | Rows | rows/s | batches | wave count (max_conc=10) |\n")
    lines.append("|-------|---------:|-----:|-------:|--------:|-------------------------:|\n")
    for stem, r in results.items():
        rps = r.get("n_boq_rows", 0) / max(r.get("wall_clock_sec", 1), 1)
        n_batches = r.get("n_batches", 0)
        waves = (n_batches + 9) // 10 if n_batches else 0
        lines.append(
            f"| {r.get('label', stem)} | {r['wall_clock_sec']:.1f} | "
            f"{r.get('n_boq_rows', 0)} | {rps:.2f} | {n_batches} | {waves} |\n"
        )

    # Variance analysis
    lines.append("\n## Variance from pre-flight estimates\n\n")
    for stem, r in results.items():
        label = r.get("label", stem)
        est = PRE_FLIGHT_ESTIMATES.get(label)
        if not est:
            continue
        wall_pct = (r["wall_clock_sec"] / est["wall_clock_sec"] - 1) * 100
        cost_pct = (r["cost_inr"] / max(est["cost_inr"], 0.01) - 1) * 100
        wall_msg = f"+{wall_pct:.0f}% slower" if wall_pct > 0 else f"{wall_pct:.0f}% faster"
        cost_msg = f"+{cost_pct:.0f}% more" if cost_pct > 0 else f"{cost_pct:.0f}% less"
        lines.append(
            f"- **{label}**: wall {wall_msg} ({r['wall_clock_sec']:.0f}s vs {est['wall_clock_sec']}s estimated); "
            f"cost {cost_msg} (₹{r['cost_inr']:.2f} vs ₹{est['cost_inr']:.2f} estimated)\n"
        )

    out = "".join(lines)
    Path("/tmp/r8_4_cost_actuals.md").write_text(out)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
