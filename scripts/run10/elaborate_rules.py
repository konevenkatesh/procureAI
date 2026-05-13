"""R10 follow-up — elaborate the 611 RuleNode rows into descriptive prose.

Why: the corpus stores rule statements as short labels (~1 line) with optional
verification_method procedurals. The Knowledge Layer detail modal renders this
as "professor view" but had no actual descriptive text to work with — a problem
the user caught in production.

Fix: for each rule, ask Gemini Flash to expand the label + verification_method
+ classification context into a 200-350 word descriptive explanation that:
  - Restates the rule in plain English
  - Explains the regulatory intent (why this rule exists)
  - Describes the trigger conditions (when this rule fires)
  - Lists practical failure modes and how to spot them
  - Notes related rules in the same typology where useful

The output is stored in properties.rule_explanation via a single UPDATE per row.

Cost: ~611 calls × ~₹0.005 (Flash, 800 prompt + 350 output tokens) ≈ ₹3.
Wall-clock: ~5 min with 10 concurrent batches.

Resumable: skips rows that already have a non-empty rule_explanation.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import psycopg

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "services" / "m1-drafter"))

from builder.config import settings                # noqa: E402
from app.vertex_client import gemini_flash_async    # noqa: E402


SYSTEM_INSTRUCTION = """You are a procurement-policy editor preparing detailed explanations of
Andhra Pradesh State Government and Central procurement rules for a Knowledge Layer.

For each rule given, produce a single descriptive paragraph (240-360 words) covering:
  1. PLAIN-ENGLISH RESTATEMENT — what the rule actually requires (1-2 sentences).
  2. REGULATORY INTENT — why this rule exists, what risk it mitigates, the policy
     anchor (CVC / GFR / MPW / AP-GO / MPG).
  3. TRIGGER CONDITIONS — when this rule fires (which procurement stages, which
     tender categories, which value thresholds).
  4. FAILURE MODES — concrete examples of how a tender might violate this rule,
     written so a Dealing Officer can spot the violation in a draft.
  5. RELATED CONTEXT — how it relates to the typology + linked rules (only if
     relevant; otherwise skip).

OUTPUT FORMAT:
  - One paragraph of plain prose, no headings, no bullet points, no markdown.
  - Professional tone, neutral voice. Suitable for a procurement-officer audience.
  - Do NOT invent facts not implied by the rule label + verification method.
  - Do NOT include the rule_id or severity in the output text — those are shown
    separately in the UI.
  - If the verification_method is procedural ("Compute X ÷ Y"), translate it into
    plain English ("This is checked by computing the ratio of X to Y...").
"""


# ─── Concurrency primitives ──────────────────────────────────────────


async def _elaborate_one(rule: dict, sem: asyncio.Semaphore) -> tuple[str, str | None, str | None]:
    """Return (node_id, explanation_text_or_None, error_or_None)."""
    async with sem:
        rule_id = rule["rule_id"] or rule["node_id"][:8]
        label = rule["label"] or ""
        p = rule["properties"] or {}
        verification = p.get("verification_method") or "(no explicit verification method on file)"
        prompt = (
            f"RULE: {label}\n"
            f"RULE_ID: {rule_id}\n"
            f"SEVERITY: {p.get('severity', '—')}\n"
            f"LAYER: {p.get('layer', '—')} (Central=GFR/CVC/MPW, AP-State=AP-GO, etc.)\n"
            f"TYPOLOGY: {p.get('typology_code', '—')}\n"
            f"PATTERN_TYPE: {p.get('pattern_type', '—')}\n"
            f"VERIFICATION_METHOD: {verification}\n\n"
            f"Produce the descriptive explanation per the system instructions."
        )
        for attempt in range(3):
            try:
                resp = await gemini_flash_async(
                    prompt, system_instruction=SYSTEM_INSTRUCTION,
                    max_output_tokens=600, temperature=0.2, thinking_budget=0,
                )
                text = (resp.get("text") or "").strip()
                if len(text) < 80:
                    return rule["node_id"], None, f"too short ({len(text)} chars)"
                return rule["node_id"], text, None
            except Exception as e:
                if attempt == 2:
                    return rule["node_id"], None, str(e)
                await asyncio.sleep(2 * (attempt + 1))
        return rule["node_id"], None, "exhausted retries"


async def _runner(rules: list[dict], max_concurrent: int = 10):
    sem = asyncio.Semaphore(max_concurrent)
    tasks = [_elaborate_one(r, sem) for r in rules]
    return await asyncio.gather(*tasks)


# ─── Main ────────────────────────────────────────────────────────────


def main() -> int:
    print("R10 follow-up — Gemini Flash elaboration for 611 RuleNode rows")
    print(f"  pooler: {settings.supabase_url[:50]}...")

    with psycopg.connect(settings.supabase_url, sslmode="require", connect_timeout=30) as conn:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = '600000'")     # 10 min hard cap
        conn.commit()

        with conn.cursor() as cur:
            cur.execute("""
                SELECT node_id::text, label, properties
                FROM kg_nodes
                WHERE node_type='RuleNode'
                  AND (properties->>'rule_explanation' IS NULL
                       OR length(properties->>'rule_explanation') < 80)
                ORDER BY label
            """)
            rows = cur.fetchall()
        print(f"  rows needing elaboration: {len(rows)}")
        if not rows:
            print("  nothing to do")
            return 0

        # Build the work list
        rules = [{
            "node_id":    r[0],
            "label":      r[1],
            "rule_id":    (r[2] or {}).get("rule_id"),
            "properties": r[2] or {},
        } for r in rows]

        # Run in waves of 80 rules at a time so we can commit progress + bail safely
        WAVE = 80
        total_done = 0
        total_failed = 0
        cumulative_in = 0
        cumulative_out = 0
        t0 = time.time()
        for start in range(0, len(rules), WAVE):
            wave = rules[start:start + WAVE]
            print(f"\n── wave {start // WAVE + 1}: rules {start + 1}-{start + len(wave)} ──", flush=True)
            wave_t0 = time.time()
            results = asyncio.run(_runner(wave, max_concurrent=10))
            wave_ms = int((time.time() - wave_t0) * 1000)

            updates = []
            wave_ok = 0
            wave_err = 0
            for node_id, text, err in results:
                if err or not text:
                    wave_err += 1
                    continue
                wave_ok += 1
                # Build the JSONB merge
                updates.append((text, node_id))

            if updates:
                with conn.cursor() as cur:
                    cur.executemany(
                        "UPDATE kg_nodes "
                        "SET properties = properties || jsonb_build_object('rule_explanation', %s::text) "
                        "WHERE node_id = %s::uuid",
                        updates,
                    )
                conn.commit()

            total_done += wave_ok
            total_failed += wave_err
            print(f"  wave ok={wave_ok} err={wave_err} elapsed_ms={wave_ms} "
                  f"cumulative {total_done}/{len(rules)} done, {total_failed} failed", flush=True)

    elapsed = time.time() - t0
    print(f"\n=== Done: {total_done} rules elaborated, {total_failed} failed, "
          f"wall-clock {elapsed:.0f}s ===")
    return 0 if total_failed < len(rules) // 4 else 1     # tolerate up to 25% failure


if __name__ == "__main__":
    sys.exit(main())
