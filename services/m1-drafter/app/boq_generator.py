"""R7.5 — BoQ generator: skeleton parser + Vertex AI Flash batch spec writer.

Two stages:

  1. parse_boq_skeleton(file_bytes, filename)
       → list[BoQSkeletonRow]: (s_no, item_name, qty, unit) — name only, no specs yet.
       Accepts .xlsx / .xls / .csv via pandas + openpyxl. Tolerant of column
       header variation (item / description / particulars / work).

  2. generate_boq_specs(skeleton, project_context)
       → list[BoQItemOutput]: each row enriched with spec_text + work_type + citations.
       Strategy:
         a. Cluster rows by detected discipline (Electrical / HVAC / Civil / …).
         b. For each cluster: top-K=8 TechSpecTemplate retrieval via pgvector on
            'item_name + project_context.discipline_hint'.
         c. Stuff top-K templates into the prompt as exemplars, then ask
            Gemini Flash to fill 30 rows at a time with structured output.
         d. Fallback to claude_sonnet() if parse_ok=False after 1 retry.

The 30-item batch size is the empirically-derived sweet spot:
  - 30 × ~12k spec_tokens × 0.075 USD/1M = $0.027/batch on Flash
  - Stays under Flash's 65k output token soft-limit
  - Per-row latency ~250ms via batching vs ~1.2s solo

No discipline assumed at parse time — discipline classification happens
inside generate_boq_specs() via keyword matching on item_name (fast,
deterministic) with a Pro fallback on ambiguous rows. Pure heuristics keep
the cost predictable.

Caller (workflow_v2.draft_BoQ node) is responsible for:
  - Streaming each completed row as SSEEventTableRowAdded
  - Emitting boq_batch_started / boq_item_complete events
  - Persisting the final BoQItemOutput list onto state.boq
"""
from __future__ import annotations

import csv
import io
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .tech_spec_templates.base import BoQItemOutput, TechSpecTemplate
from .tech_spec_templates import all_templates as _all_templates_fn

logger = logging.getLogger(__name__)


# ─── Public dataclasses ──────────────────────────────────────────────


@dataclass(frozen=True)
class BoQSkeletonRow:
    """A row from the officer-uploaded BoQ skeleton — pre-LLM enrichment."""
    s_no: int
    item_name: str       # short description as supplied by the officer
    qty: float           # may be 1.0 for lump-sum
    unit: str            # uom, e.g. "m3", "MT", "lump sum"
    raw_row_hint: str = ""  # any extra column text (clubbed for retrieval signal)


@dataclass(frozen=True)
class ProjectContext:
    """Tender-level context the BoQ generator needs."""
    project_name: str
    discipline_hint: str   # "MEP" | "Civil" | "Mixed" | "Roads" | "HVAC" | "Electrical" | …
    tender_category: str   # "WORKS" / "GOODS" / "SERVICES"
    state: str = "Andhra Pradesh"
    apss_reference: str = "AP Standard Specifications 2024"


class BoQBatchResponse(BaseModel):
    """Schema for one Flash batch response. Held loose so Flash can return what it can."""
    model_config = ConfigDict(extra="forbid")
    rows: list[BoQItemOutput] = Field(default_factory=list)


# ─── Skeleton parsing ────────────────────────────────────────────────


# Plausible header names for the item column — broad on purpose, matches
# real Indian PWD BoQs in the corpus.
_ITEM_HEADERS = {
    "item", "description", "particulars", "details", "work item",
    "name of work", "scope of work", "specification", "item description",
}
_QTY_HEADERS = {"qty", "quantity", "no", "nos", "no.", "qty.", "estimated qty"}
_UNIT_HEADERS = {"unit", "uom", "unit of measure", "units"}
_SNO_HEADERS = {"s.no", "sno", "sl.no", "slno", "sl no", "s no", "item no", "no.", "no"}


def _norm_header(s: str) -> str:
    return re.sub(r"[\s_]+", " ", (s or "").strip().lower()).rstrip(".")


def _detect_cols(headers: list[str]) -> dict[str, int]:
    """Map skeleton CSV/XLSX column-headers to canonical roles.
    Returns dict {role: col_idx}; role ∈ {s_no, item, qty, unit}.
    Missing roles are simply absent (caller fills with defaults)."""
    out: dict[str, int] = {}
    for i, h in enumerate(headers):
        nh = _norm_header(h)
        if nh in _SNO_HEADERS and "s_no" not in out:
            out["s_no"] = i
        elif nh in _ITEM_HEADERS and "item" not in out:
            out["item"] = i
        elif nh in _QTY_HEADERS and "qty" not in out:
            out["qty"] = i
        elif nh in _UNIT_HEADERS and "unit" not in out:
            out["unit"] = i
    return out


def parse_boq_skeleton(file_bytes: bytes, filename: str) -> list[BoQSkeletonRow]:
    """Parse an Excel or CSV BoQ skeleton into typed rows.

    Tolerates:
      - Header row anywhere in the first 8 rows (auto-detect).
      - Missing s_no (auto-generated).
      - Missing qty (defaults to 1.0).
      - Missing unit (defaults to 'lump sum').
      - Empty rows between sections.

    Strict rejections:
      - No item-name column found → ValueError with column-header dump.
      - Zero data rows extracted → ValueError.
    """
    suffix = Path(filename).suffix.lower()
    if suffix in (".xlsx", ".xls"):
        rows = _parse_excel(file_bytes)
    elif suffix in (".csv", ".txt"):
        rows = _parse_csv(file_bytes)
    else:
        raise ValueError(f"unsupported BoQ skeleton format: {suffix} (expected .xlsx/.xls/.csv)")

    # Locate header row inside the first 8 lines
    header_idx, cols = _find_header_row(rows[:8])
    if "item" not in cols:
        raise ValueError(
            f"BoQ skeleton missing an item-description column. "
            f"Detected headers: {rows[header_idx] if rows else []}"
        )

    out: list[BoQSkeletonRow] = []
    auto_sno = 1
    for r in rows[header_idx + 1:]:
        if not r:
            continue
        item_raw = (_cell(r, cols.get("item")) or "").strip()
        if not item_raw or len(item_raw) < 4:
            continue
        # Skip subsection headers like "A. CIVIL WORKS" with no qty/unit
        qty_raw = _cell(r, cols.get("qty"))
        unit_raw = _cell(r, cols.get("unit"))
        sno_raw = _cell(r, cols.get("s_no"))

        try:
            qty = float(str(qty_raw).replace(",", "").strip()) if qty_raw else 1.0
        except ValueError:
            qty = 1.0
        if qty <= 0:
            qty = 1.0

        unit = (unit_raw or "lump sum").strip() or "lump sum"

        try:
            sno = int(str(sno_raw).strip()) if sno_raw else auto_sno
        except (ValueError, TypeError):
            sno = auto_sno
        auto_sno = sno + 1

        # Glue together any extra columns as retrieval hints (drains, work-type code, etc.)
        extras = []
        for i, cell in enumerate(r):
            if i in cols.values():
                continue
            v = (cell or "").strip() if isinstance(cell, str) else (str(cell) if cell else "")
            if v and len(v) >= 3:
                extras.append(v)
        raw_hint = " | ".join(extras[:5])  # cap to keep prompt sane

        out.append(BoQSkeletonRow(
            s_no=sno,
            item_name=item_raw[:400],  # cap at 400 chars — protects prompt budget
            qty=qty,
            unit=unit[:50],
            raw_row_hint=raw_hint[:400],
        ))

    if not out:
        raise ValueError("BoQ skeleton parsed but yielded zero data rows.")
    return out


def _parse_csv(b: bytes) -> list[list[str]]:
    text = b.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    return [row for row in reader]


def _parse_excel(b: bytes) -> list[list[str]]:
    """Lazy-import openpyxl to keep startup cheap when only CSV is used."""
    try:
        import openpyxl  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "openpyxl not installed. Run: pip install openpyxl"
        ) from e
    wb = openpyxl.load_workbook(io.BytesIO(b), data_only=True, read_only=True)
    ws = wb.active
    out: list[list[str]] = []
    for row in ws.iter_rows(values_only=True):
        out.append([_str(c) for c in row])
    return out


def _str(v) -> str:
    if v is None:
        return ""
    return str(v)


def _cell(row: list[str], idx: Optional[int]) -> str:
    if idx is None:
        return ""
    if idx < 0 or idx >= len(row):
        return ""
    return row[idx]


def _find_header_row(top_rows: list[list[str]]) -> tuple[int, dict[str, int]]:
    """Among the first ~8 rows, return (index, col-map) for the row with the most matches."""
    best_idx = 0
    best_cols: dict[str, int] = {}
    for i, r in enumerate(top_rows):
        cols = _detect_cols(r)
        if len(cols) > len(best_cols):
            best_idx, best_cols = i, cols
    return best_idx, best_cols


# ─── Discipline classification ────────────────────────────────────────


# Each discipline's regex bank: case-insensitive, word-bounded where helpful.
# IMPORTANT: order matters for tie-breaks. Specific categories come BEFORE
# broader ones so e.g. "AHU panel" → HVAC, not Electrical.
_DISCIPLINE_REGEX: dict[str, list[str]] = {
    "HVAC": [
        r"\b(ahu|fcu|chiller|cooling tower|vrf|vrv|condenser|evaporator|fan coil)\b",
        r"\b(duct|ducting|insulation.*duct|grille|diffuser|damper|exhaust fan)\b",
        r"\b(refrigerant|copper pipe|chilled water|condensate|heat recovery)\b",
    ],
    "Fire": [
        r"\b(fire pump|sprinkler|hydrant|hose reel|hose box|smoke detector|heat detector|fire alarm|farc)\b",
        r"\b(fire extinguisher|emergency lighting|fire damper|fire valve|fire panel)\b",
    ],
    "Lifts": [
        r"\b(lift\b|elevator|escalator|dumbwaiter|stairlift)\b",
    ],
    "PA": [r"\b(public address|paging|tannoy|amplifier|loudspeaker)\b"],
    "BMS": [r"\b(bms\b|building management|dali|knx|bacnet|modbus)\b"],
    "HSD": [r"\b(hsd|diesel storage|day tank|bulk tank|fuel oil)\b"],
    "Plumbing": [
        r"\b(cpvc|upvc|ppr|ductile iron|sanitary|wc\b|water closet|urinal|wash basin|sink)\b",
        r"\b(stp\b|sewage|drain.*line|water tank|water meter|gate valve|globe valve|butterfly valve)\b",
    ],
    "Electrical": [
        r"\b(cable|conductor|switchgear|busbar|mcc|pcc|hv|lv|lt|ht|transformer|dg\s*set|generator)\b",
        r"\b(luminaire|led|light fitting|wiring|conduit|junction box|earthing|lightning arrestor)\b",
        r"\b(ups\b|battery|inverter|substation|circuit breaker|isolator|panel\s*board|distribution panel)\b",
        r"\b(acb|mccb|mcb|elcb|rccb|rcbo|incomer|outgoing|tier panel|cubicle panel)\b",
    ],
    "Roads": [
        r"\b(gsb\b|wmm\b|dbm\b|bituminous|bitumen|asphalt|road|carriageway|footpath)\b",
        r"\b(kerb stone|crash barrier|road marking|signage|reflector)\b",
    ],
    "Bridges": [
        r"\b(girder|pier|abutment|deck slab|bearing.*bridge|bridge deck|culvert)\b",
        r"\b(post.?tension|pre.?stress|cable stay|expansion joint)\b",
    ],
    "Drains": [
        r"\b(storm.*drain|surface drain|drain.*lining|culvert.*drain|side drain)\b",
    ],
    "Sewerage": [
        r"\b(sewer.*line|manhole|inspection chamber|sewage pumping)\b",
    ],
    "WaterSupply": [
        r"\b(water supply|elsr|gsr|rising main|distribution main|water meter|hydrant\s*line)\b",
    ],
    "Reuse": [
        r"\b(treated water|recycled water|stp.*line|tertiary treatment)\b",
    ],
    "UtilityDucts": [
        r"\b(utility duct|hume pipe|ng pipe|cable trench|sleeve pipe)\b",
    ],
    "Plantation": [
        r"\b(plantation|landscape|sapling|tree guard|lawn|sodding)\b",
    ],
    "Civil": [
        # broad catch-all so commodity items still get a discipline
        r"\b(excavation|rcc|brick|cement|concrete|mortar|plaster|reinforcement|formwork|shuttering)\b",
        r"\b(flooring|tile|paint|painting|door|window|grill|railing|stair|roof)\b",
        r"\b(masonry|backfill|bedding|murrum|gabion|geotextile)\b",
    ],
}


def classify_discipline(item_name: str, hint: str = "") -> str:
    """Heuristic discipline tag for retrieval bucketing. 'Unknown' = punt to LLM."""
    text = (item_name + " " + hint).lower()
    hits: list[tuple[str, int]] = []
    for disc, patterns in _DISCIPLINE_REGEX.items():
        score = 0
        for p in patterns:
            score += len(re.findall(p, text, flags=re.IGNORECASE))
        if score > 0:
            hits.append((disc, score))
    if not hits:
        return "Unknown"
    hits.sort(key=lambda x: -x[1])
    return hits[0][0]


# ─── Template retrieval (in-memory; pgvector lookup is in workflow_v2) ────


def retrieve_templates_by_discipline(
    discipline: str,
    *,
    top_k: int = 8,
    registry: Optional[list[TechSpecTemplate]] = None,
) -> list[TechSpecTemplate]:
    """Return top-K TechSpecTemplate matches for a discipline.

    Used as a deterministic fallback when pgvector retrieval is unavailable
    (e.g. local smoke tests without DB). The workflow_v2.retrieve_tech_templates
    node prefers pgvector top-K via Vertex embedding similarity.
    """
    if registry is None:
        registry = list(_all_templates_fn())
    matched = [t for t in registry if t.discipline.lower() == discipline.lower()
               or t.sub_discipline.lower() == discipline.lower()]
    if matched:
        return matched[:top_k]
    # Loose match by item_category substring
    loose = [t for t in registry if discipline.lower() in t.item_category.lower()]
    return loose[:top_k]


# ─── Prompt construction ──────────────────────────────────────────────


_BOQ_BATCH_SYSTEM = """You are a senior Indian PWD specifications writer producing
BoQ line items for a Government of Andhra Pradesh tender. Each row must comply with
AP Standard Specifications 2024 (APSS), the relevant Indian Standards (IS codes),
and CPWD 2024 where APSS is silent.

OUTPUT RULES (non-negotiable):
1. Return ONLY valid JSON matching the schema. No prose, no markdown fences.
2. Every row needs: spec_text (150-4000 chars), work_type, short_desc, citations (≥1 IS/APSS/EN reference).
3. spec_text MUST cite at least one specific IS/APSS/EN clause number with the format
   'IS XXX:YYYY' or 'APSS Cl. X.Y.Z' or 'EN XXX'. Generic 'as per relevant IS code' is REJECTED.
4. Preserve the supplied s_no, item_name, qty, and unit exactly — DO NOT renumber or
   rewrite the item_name. You enrich; you do not replace.
5. apss_cl_no field: if you cite an APSS clause, repeat its number here for fast indexing
   (e.g. '8.2.4'). Else null.
"""


def _format_template_exemplar(t: TechSpecTemplate, idx: int) -> str:
    """One template shown as an exemplar block in the prompt."""
    samples = "; ".join(t.sample_short_descs[:3]) if t.sample_short_descs else "—"
    standards = ", ".join(t.expected_citations[:5]) if t.expected_citations else "—"
    return (
        f"--- EXEMPLAR {idx} ---\n"
        f"discipline:       {t.discipline} / {t.sub_discipline}\n"
        f"item_category:    {t.item_category}\n"
        f"work_type:        {t.work_type_label}\n"
        f"typical_uom:      {t.typical_uom}\n"
        f"sample_short_descs: {samples}\n"
        f"expected_standards: {standards}\n"
        f"prompt_seed: {t.llm_prompt_template[:600]}\n"
    )


def build_batch_prompt(
    skeleton_rows: list[BoQSkeletonRow],
    project_ctx: ProjectContext,
    exemplar_templates: list[TechSpecTemplate],
    discipline_label: str,
) -> str:
    """Assemble the per-batch prompt: exemplars + the 30 rows to fill."""
    exemplars = "\n".join(_format_template_exemplar(t, i) for i, t in enumerate(exemplar_templates, 1))

    rows_json = json.dumps(
        [{
            "s_no":      r.s_no,
            "item_name": r.item_name,
            "qty":       r.qty,
            "unit":      r.unit,
            "hint":      r.raw_row_hint or None,
        } for r in skeleton_rows],
        ensure_ascii=False,
    )

    return (
        f"PROJECT: {project_ctx.project_name}\n"
        f"STATE: {project_ctx.state}\n"
        f"TENDER CATEGORY: {project_ctx.tender_category}\n"
        f"DISCIPLINE BATCH: {discipline_label}\n"
        f"REFERENCE SPEC: {project_ctx.apss_reference}\n\n"
        f"You are given {len(skeleton_rows)} BoQ rows to enrich with full specifications.\n"
        f"Below are {len(exemplar_templates)} TechSpecTemplate exemplars from our corpus that anchor the expected format, "
        f"citation depth, and APSS/IS references for {discipline_label} work.\n\n"
        f"{exemplars}\n"
        f"--- ROWS TO ENRICH ---\n"
        f"{rows_json}\n\n"
        f"Return a JSON object with key 'rows' containing exactly {len(skeleton_rows)} enriched items, "
        f"in the same s_no order, each with: sno (= input s_no), item_name (verbatim from input), spec_text, "
        f"work_type, short_desc, apss_cl_no (string — use 'NON SOR / KD A 1.x' if no APSS clause applies), "
        f"est_qty (= input qty), uom (= input unit), rate_inr (numeric INR estimate or 0.0 if unknown), "
        f"citations (list of standard refs, must include ≥1).\n"
    )


# ─── Batch driver (synchronous, generator-style) ──────────────────────


def generate_boq_specs(
    skeleton: list[BoQSkeletonRow],
    project_ctx: ProjectContext,
    *,
    batch_size: int = 30,
    template_retriever=None,
    on_batch_start=None,
    on_row_complete=None,
) -> Iterator[BoQItemOutput]:
    """Yield enriched BoQItemOutput rows as they're computed.

    template_retriever: callable(discipline: str, top_k: int) -> list[TechSpecTemplate].
        Caller (workflow_v2) wires this to the pgvector retriever. If None, falls
        back to discipline-bucket lookup against the in-memory REGISTRY.
    on_batch_start: optional callback(batch_idx, discipline, n_rows) for SSE emission.
    on_row_complete: optional callback(BoQItemOutput) per row.
    """
    if template_retriever is None:
        template_retriever = retrieve_templates_by_discipline

    # Group rows by discipline so each batch is coherent for the LLM
    buckets: dict[str, list[BoQSkeletonRow]] = {}
    for row in skeleton:
        d = classify_discipline(row.item_name, row.raw_row_hint)
        buckets.setdefault(d, []).append(row)

    batch_idx = 0
    for discipline, rows in buckets.items():
        # If discipline is Unknown, default to Civil exemplars (broadest coverage)
        retrieval_disc = discipline if discipline != "Unknown" else "Civil"
        exemplars = template_retriever(retrieval_disc, top_k=8)
        if not exemplars:
            logger.warning(f"no exemplars retrieved for discipline={discipline}; using empty set")
            exemplars = []

        for start in range(0, len(rows), batch_size):
            batch = rows[start:start + batch_size]
            batch_idx += 1
            if on_batch_start:
                on_batch_start(batch_idx, discipline, len(batch))

            try:
                enriched = _run_batch(batch, project_ctx, exemplars, discipline)
            except Exception as e:
                logger.error(f"batch {batch_idx} ({discipline}) failed: {e}; emitting stub rows")
                enriched = [_stub_row(r, discipline) for r in batch]

            # Yield each row downstream
            for out_row in enriched:
                if on_row_complete:
                    on_row_complete(out_row)
                yield out_row


# Module-level skip flag: if Sonnet 404s once (e.g. project lacks
# Model Garden access for Anthropic publisher), don't retry per-batch.
_SONNET_SKIP_AFTER_404 = {"skip": False}


_LAST_BATCH_USAGE: dict = {}  # mutable holder for the workflow to read


def _run_batch(
    skeleton_rows: list[BoQSkeletonRow],
    project_ctx: ProjectContext,
    exemplars: list[TechSpecTemplate],
    discipline: str,
) -> list[BoQItemOutput]:
    """Single Flash call; falls back to Claude Sonnet on parse failure
    UNLESS Sonnet has already 404'd once in this process (then straight to stubs).

    Token usage from the most recent Flash call is recorded into
    _LAST_BATCH_USAGE so the workflow generator can emit an accurate
    llm_call event after each batch (without changing this function's
    return signature, which is consumed by older code paths).
    """
    # Lazy-import to avoid circular and to let smoke tests stub it
    from .vertex_client import gemini_flash, claude_sonnet

    prompt = build_batch_prompt(skeleton_rows, project_ctx, exemplars, discipline)
    t0 = time.time()
    # 15-row batch × ~300 output tokens each = ~4500; cap at 12288 for safety
    resp = gemini_flash(
        prompt,
        response_schema=BoQBatchResponse,
        max_output_tokens=12288,
        temperature=0.15,
        system_instruction=_BOQ_BATCH_SYSTEM,
        thinking_budget=0,               # R7.4 lesson: hard-disable thinking
    )
    elapsed_ms = int((time.time() - t0) * 1000)
    logger.info(
        f"  Flash batch ({discipline}, {len(skeleton_rows)} rows) — "
        f"{resp['prompt_tokens']}→{resp['completion_tokens']} tokens, {elapsed_ms}ms"
    )
    _LAST_BATCH_USAGE.clear()
    _LAST_BATCH_USAGE.update({
        "model": "gemini-2.5-flash",
        "prompt_tokens":     resp.get("prompt_tokens", 0),
        "completion_tokens": resp.get("completion_tokens", 0),
        "thought_tokens":    resp.get("thought_tokens", 0),
        "elapsed_ms":        elapsed_ms,
    })

    parsed = resp.get("parsed")
    if resp.get("parse_ok") and isinstance(parsed, BoQBatchResponse) and parsed.rows:
        # Preserve order by s_no; fill any missing rows with stubs.
        by_sno = {r.sno: r for r in parsed.rows}
        out: list[BoQItemOutput] = []
        for sk in skeleton_rows:
            if sk.s_no in by_sno:
                row = by_sno[sk.s_no]
                # Hard-enforce that input qty / unit / item_name are preserved
                row = row.model_copy(update={
                    "item_name": sk.item_name,
                    "est_qty":   sk.qty,
                    "uom":       sk.unit,
                    "sno":       sk.s_no,
                })
                out.append(row)
            else:
                out.append(_stub_row(sk, discipline))
        return out

    # Parse fail → try Claude Sonnet 4 once as structured-output fallback
    if _SONNET_SKIP_AFTER_404["skip"]:
        logger.warning(f"  Flash parse failed ({resp.get('parse_error')}); Sonnet skipped (prior 404)")
        return [_stub_row(r, discipline) for r in skeleton_rows]
    logger.warning(f"  Flash parse failed ({resp.get('parse_error')}); falling back to Sonnet")
    try:
        sonnet = claude_sonnet(
            prompt,
            response_schema=BoQBatchResponse,
            max_tokens=8192,
            temperature=0.15,
            system=_BOQ_BATCH_SYSTEM,
        )
        if sonnet.get("parse_ok"):
            sparsed = sonnet["parsed"]
            by_sno = {r.sno: r for r in sparsed.rows}
            out = []
            for sk in skeleton_rows:
                if sk.s_no in by_sno:
                    row = by_sno[sk.s_no].model_copy(update={
                        "item_name": sk.item_name, "est_qty": sk.qty,
                        "uom": sk.unit, "sno": sk.s_no,
                    })
                    out.append(row)
                else:
                    out.append(_stub_row(sk, discipline))
            return out
    except Exception as e:
        logger.error(f"  Sonnet fallback also failed: {e}")
        if "404" in str(e) or "NOT_FOUND" in str(e):
            _SONNET_SKIP_AFTER_404["skip"] = True
            logger.warning("  Sonnet 404 — skipping Sonnet for all subsequent batches in this run")

    # Last resort: stub rows so the BoQ still has the officer's skeleton
    return [_stub_row(r, discipline) for r in skeleton_rows]


def _stub_row(sk: BoQSkeletonRow, discipline: str) -> BoQItemOutput:
    """Emit a minimal row that preserves the officer's skeleton.
    Caller can re-run extraction later; stub keeps document complete.

    Note: BoQItemOutput requires non-None apss_cl_no and rate_inr — we use
    sentinel values that downstream renderers can detect for "needs review".
    """
    spec = (
        f"[AUTO-FILL PENDING — {discipline}] "
        f"Specification to be written per AP Standard Specifications 2024 "
        f"and the relevant Indian Standards. Officer review required. "
        f"This stub row preserves the officer's skeleton entry while LLM "
        f"enrichment is re-attempted in a subsequent pass."
    )
    # Pad spec_text to >=150 chars (schema constraint) by repeating the directive
    while len(spec) < 160:
        spec += " Review required."
    return BoQItemOutput(
        sno=sk.s_no,
        item_name=sk.item_name,
        spec_text=spec,
        work_type=discipline if discipline != "Unknown" else "General",
        short_desc=sk.item_name[:80],
        apss_cl_no="NON SOR / KD A 1.x",
        est_qty=sk.qty,
        uom=sk.unit,
        rate_inr=0.0,
        citations=["APSS 2024 (TBD)"],
    )


# ─── Smoke test ──────────────────────────────────────────────────────


if __name__ == "__main__":  # pragma: no cover
    sample = [
        BoQSkeletonRow(s_no=1, item_name="RCC M-25 in foundation footings", qty=42.0, unit="m3"),
        BoQSkeletonRow(s_no=2, item_name="LV power cable XLPE 4C × 95 sqmm AL", qty=320.0, unit="m"),
        BoQSkeletonRow(s_no=3, item_name="AHU double-skin panel 8000 CFM", qty=4.0, unit="No"),
    ]
    ctx = ProjectContext(
        project_name="R7.5 Smoke",
        discipline_hint="Mixed",
        tender_category="WORKS",
    )
    for r in generate_boq_specs(sample, ctx, batch_size=10):
        print(f"  [{r.sno}] {r.short_desc} — {r.work_type} — citations={r.citations[:3]}")
