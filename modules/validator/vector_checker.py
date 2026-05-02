"""
modules/validator/vector_checker.py

VectorChecker — semantic-similarity based detection of mandatory clause
concepts using section-based chunking + SAC summaries + BGE-M3 + Qdrant.

The strategy (per 2025-2026 legal-RAG research) is to chunk by SECTION
rather than fixed-size windows because procurement documents are heavily
structured. Each section gets a "Section And Concept" summary (the SAC)
that surfaces the section's actual topic. Embedding heading + SAC +
section text gives the model both lexical and structural cues so semantic
queries like "Integrity Pact" can find clauses written as "Pre-bid
Integrity Agreement" or "Schedule IP".

Pipeline:
    raw markdown
        │
        ▼  ─────────  Step 1: split on ##/###/numbered/ALL-CAPS headings
    [sections]                with min/max word bounds, paragraph splits
        │
        ▼  ─────────  Step 2: SAC = heading + first-sentence + clause-numbers
    [section + SAC]
        │
        ▼  ─────────  Step 3: embed_text = heading + ' ' + SAC + ' ' + body
    [embed_texts]
        │
        ▼  ─────────  Step 4: BGE-M3 encode, normalize=True
    [1024-dim vectors]
        │
        ▼  ─────────  Step 5: upsert into Qdrant with metadata
    Qdrant collection
        │
        ▼  ─────────  Query: for each concept, encode (canonical + aliases),
    Findings                   top-k search, threshold check, mandatory check
"""
from __future__ import annotations

import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field


# ────────────────────────────────────────────────────────────────────────────
# Concept catalogue — what we look for in any tender
# ────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Concept:
    concept_id: str
    canonical: str
    aliases: tuple[str, ...]
    threshold: float
    severity: str            # HARD_BLOCK / WARNING / ADVISORY
    mandatory_when: str = "always"  # one of: always, value_gte_5cr, ap_works_pa, ap_jp, ap_reverse


CONCEPTS: tuple[Concept, ...] = (
    Concept(
        concept_id="integrity-pact",
        canonical="Integrity Pact mandatory anti-corruption clause",
        aliases=("pre-bid integrity agreement", "IP clause",
                 "anti-corruption undertaking", "vigilance pact",
                 "integrity agreement per CVC", "Schedule IP"),
        threshold=0.72,
        severity="HARD_BLOCK",
        mandatory_when="value_gte_5cr",
    ),
    Concept(
        concept_id="anti-collusion",
        canonical="Anti-collusion certificate Form 3N",
        aliases=("no cartel declaration", "anti-collusion undertaking",
                 "bid rigging declaration", "Form 3N"),
        threshold=0.70,
        severity="WARNING",
        mandatory_when="always",
    ),
    Concept(
        concept_id="price-variation-clause",
        canonical="Price variation clause PVC price adjustment",
        aliases=("escalation clause", "price adjustment formula",
                 "variation in input prices", "PVC formula",
                 "price fluctuation clause"),
        threshold=0.70,
        severity="HARD_BLOCK",
        mandatory_when="ap_works_pa",
    ),
    Concept(
        concept_id="judicial-preview",
        canonical="Judicial Preview clearance HC judge",
        aliases=("judicial preview certificate",
                 "infrastructure transparency review",
                 "pre-publication judicial review",
                 "High Court judge review"),
        threshold=0.75,
        severity="HARD_BLOCK",
        mandatory_when="ap_jp",
    ),
    Concept(
        concept_id="performance-security",
        canonical="Performance Security Bank Guarantee PBG",
        aliases=("performance bond", "contract performance guarantee",
                 "security deposit performance", "PBG amount"),
        threshold=0.80,
        severity="HARD_BLOCK",
        mandatory_when="always",
    ),
    Concept(
        concept_id="earnest-money",
        canonical="Earnest Money Deposit EMD bid security",
        aliases=("bid bond", "tender deposit", "earnest deposit",
                 "bid security amount", "EMD percentage"),
        threshold=0.80,
        severity="HARD_BLOCK",
        mandatory_when="always",
    ),
    Concept(
        concept_id="reverse-tendering",
        canonical="Reverse tendering mandatory electronic reverse auction",
        aliases=("reverse auction", "e-reverse auction",
                 "descending price auction", "reverse bidding",
                 "Konugolu portal"),
        threshold=0.72,
        severity="HARD_BLOCK",
        mandatory_when="ap_reverse",
    ),
    Concept(
        concept_id="mobilisation-advance",
        canonical="Mobilisation advance payment contractor",
        aliases=("mobilization advance", "advance payment",
                 "mobilisation payment", "contract advance"),
        threshold=0.68,
        severity="WARNING",
        mandatory_when="always",
    ),
)


def _is_concept_mandatory(c: Concept, *, is_ap: bool, value: float, duration_months: int = 12) -> bool:
    if c.mandatory_when == "always":
        return True
    if c.mandatory_when == "value_gte_5cr":
        return value >= 5_00_00_000
    if c.mandatory_when == "ap_works_pa":
        return is_ap and value >= 40_00_000 and duration_months >= 6
    if c.mandatory_when == "ap_jp":
        return is_ap and value >= 100_00_00_000
    if c.mandatory_when == "ap_reverse":
        return is_ap and value >= 1_00_00_000
    return True


# ────────────────────────────────────────────────────────────────────────────
# Calibrated-threshold loader (used by VectorChecker on construction)
# ────────────────────────────────────────────────────────────────────────────

import json as _json   # local alias to avoid conflict with json module export


def _load_calibrated_thresholds(path: Path | str) -> dict[str, float]:
    """Read scripts/calibrate_vector_thresholds.py output if present."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        data = _json.loads(p.read_text())
        return {row["concept_id"]: float(row["new_threshold"])
                for row in data.get("calibration", [])
                if "concept_id" in row and "new_threshold" in row}
    except Exception:
        return {}


def _apply_calibration(concepts: tuple[Concept, ...],
                       overrides: dict[str, float]) -> tuple[Concept, ...]:
    if not overrides:
        return concepts
    return tuple(
        Concept(concept_id=c.concept_id,
                canonical=c.canonical,
                aliases=c.aliases,
                threshold=overrides.get(c.concept_id, c.threshold),
                severity=c.severity,
                mandatory_when=c.mandatory_when)
        for c in concepts
    )


# ────────────────────────────────────────────────────────────────────────────
# Section chunker  (Step 1)
# ────────────────────────────────────────────────────────────────────────────

# A heading is one of:
#   - "## title" / "### title"  (markdown)
#   - "1.3 Foo" / "4.2.1 Bar"   (numbered section)
#   - "PERFORMANCE SECURITY"     (ALL-CAPS line, 3-80 chars, no terminal period)
HEADING_PAT = re.compile(
    r"""(?xm)              # verbose, multiline
    ^(?:
        \#{2,6}\s+\S.*$           |  # markdown ## / ### / etc
        \d+(?:\.\d+){0,5}\s+\S.*$ |  # numbered sections
        [A-Z][A-Z0-9 \-/&,()'.]{2,80}$  # ALL-CAPS heading
    )
    """,
)
MIN_WORDS = 100
MAX_WORDS = 800

# escaped-period stripper for pymupdf4llm output
_MD_ESCAPE = re.compile(r"\\([.,;:!?(){}\[\]<>~_*\-])")


def _clean(text: str) -> str:
    return _MD_ESCAPE.sub(r"\1", text)


@dataclass
class Section:
    heading: str
    body: str
    char_start: int
    char_end: int
    word_count: int = field(init=False)

    def __post_init__(self):
        self.word_count = len(self.body.split())


def _split_oversize(s: Section) -> list[Section]:
    """If a section exceeds MAX_WORDS split at paragraph boundaries."""
    if s.word_count <= MAX_WORDS:
        return [s]
    paras = re.split(r"\n\s*\n", s.body)
    out: list[Section] = []
    cur_text = ""
    cur_words = 0
    cur_start = s.char_start
    for p in paras:
        pw = len(p.split())
        if cur_words + pw > MAX_WORDS and cur_words >= MIN_WORDS:
            out.append(Section(heading=s.heading, body=cur_text.strip(),
                                char_start=cur_start,
                                char_end=cur_start + len(cur_text)))
            cur_start = cur_start + len(cur_text)
            cur_text = ""
            cur_words = 0
        cur_text += p + "\n\n"
        cur_words += pw
    if cur_text.strip():
        out.append(Section(heading=s.heading, body=cur_text.strip(),
                            char_start=cur_start,
                            char_end=s.char_end))
    return out


def chunk_into_sections(text: str) -> list[Section]:
    """Step 1: split on heading patterns, then enforce MIN/MAX word bounds."""
    text = _clean(text)
    if not text.strip():
        return []
    # Find all heading start positions
    heads: list[tuple[int, int, str]] = []   # (start, end, heading_line)
    for m in HEADING_PAT.finditer(text):
        # filter spurious all-caps short lines that are actually unit codes
        line = m.group(0).strip()
        if not line or len(line) < 3:
            continue
        heads.append((m.start(), m.end(), line))

    # Add a sentinel end-of-document marker
    sections: list[Section] = []
    if not heads:
        sections.append(Section(heading="(document)", body=text,
                                char_start=0, char_end=len(text)))
    else:
        # Pre-text before first heading
        if heads[0][0] > 0:
            pre = text[:heads[0][0]].strip()
            if len(pre.split()) >= MIN_WORDS:
                sections.append(Section(heading="(preamble)", body=pre,
                                        char_start=0, char_end=heads[0][0]))
        for i, (start, end, line) in enumerate(heads):
            body_start = end
            body_end   = heads[i + 1][0] if i + 1 < len(heads) else len(text)
            body = text[body_start:body_end].strip()
            sections.append(Section(heading=line, body=body,
                                    char_start=start, char_end=body_end))

    # Drop tiny sections (heading-only); merge into next neighbour.
    merged: list[Section] = []
    buffer: Section | None = None
    for s in sections:
        if s.word_count < MIN_WORDS:
            if buffer is None:
                buffer = s
            else:
                buffer = Section(
                    heading=buffer.heading,
                    body=buffer.body + "\n\n" + s.heading + "\n" + s.body,
                    char_start=buffer.char_start, char_end=s.char_end,
                )
            continue
        if buffer is not None:
            # Prepend the buffered heading to this section
            s = Section(
                heading=buffer.heading,
                body=buffer.body + "\n\n" + s.heading + "\n" + s.body,
                char_start=buffer.char_start, char_end=s.char_end,
            )
            buffer = None
        merged.append(s)
    if buffer is not None and buffer.word_count >= 30 and merged:
        # Tail tiny section — append to last
        last = merged[-1]
        merged[-1] = Section(
            heading=last.heading,
            body=last.body + "\n\n" + buffer.heading + "\n" + buffer.body,
            char_start=last.char_start, char_end=buffer.char_end,
        )

    # Split oversize
    out: list[Section] = []
    for s in merged:
        out.extend(_split_oversize(s))
    return out


# ────────────────────────────────────────────────────────────────────────────
# SAC summaries  (Step 2)
# ────────────────────────────────────────────────────────────────────────────

_CLAUSE_NUM_RE = re.compile(
    r"\b(?:Clause|Rule|Section|Article|Sub[-\s]?Clause|Para|Schedule)\s*(?:No\.?\s*)?\d+(?:\.\d+)*[A-Za-z]?\b",
    re.IGNORECASE,
)
_GO_REF_RE = re.compile(r"\bGO\s*(?:Ms|Rt)\.?\s*No\.?\s*\d+\b", re.IGNORECASE)


def sac_summary(section: Section) -> str:
    """Step 2: a single sentence stating what this section covers.
       Heuristic — heading + first sentence + any clause/rule/GO refs.
    """
    head = section.heading.strip().lstrip("#").strip()
    body = section.body
    # First sentence (first '.', '?', or '!' followed by whitespace OR end-of-line)
    m = re.search(r"(.{20,300}?[.?!])\s", body)
    first_sentence = (m.group(1) if m else body[:160]).strip()
    # Pull any rule/clause/GO references
    refs = sorted(set(
        list(_CLAUSE_NUM_RE.findall(body))[:3]
        + list(_GO_REF_RE.findall(body))[:3]
    ))
    refs_part = (" References: " + ", ".join(refs) + ".") if refs else ""
    return (
        f"This section covers: {head}. "
        f"Topic indicator: {first_sentence}.{refs_part}"
    )


def make_embed_text(section: Section, sac: str) -> str:
    """Step 3: combined embed text.

    For clause-presence detection on long legal sections, embedding the
    full section body dilutes the topical signal with procedural English.
    We embed:  heading + SAC + first ~400 chars of body.
    The SAC already captures the section's topic and the first ~400 chars
    keep enough lexical context for matches like 'EMD = 2%' / '2.5%
    Performance Security' to remain searchable while keeping similarity
    scores in the 0.55–0.85 range that BGE-M3 produces for tight topical
    matches.
    """
    body_excerpt = section.body[:400]
    return f"{section.heading}\n{sac}\n{body_excerpt}"


# ────────────────────────────────────────────────────────────────────────────
# Output model
# ────────────────────────────────────────────────────────────────────────────

class VectorFinding(BaseModel):
    concept_id: str
    canonical: str
    severity: str
    mandatory: bool
    present: bool
    max_similarity: float
    threshold: float
    top_matches: list[dict]    # [{score, heading, snippet, char_start}]

    # Findings are emitted only when concept is mandatory AND not present.
    # Use `is_violation()` to filter explicitly.
    def is_violation(self) -> bool:
        return self.mandatory and not self.present


# ────────────────────────────────────────────────────────────────────────────
# VectorChecker
# ────────────────────────────────────────────────────────────────────────────

class VectorChecker:
    """End-to-end semantic clause checker."""

    EMBEDDING_MODEL  = "BAAI/bge-m3"
    QDRANT_HOST      = os.environ.get("QDRANT_HOST", "localhost")
    QDRANT_PORT      = int(os.environ.get("QDRANT_PORT", "6333"))
    SHARED_COLLECTION = "tender_sections"   # one collection, doc_id metadata for routing

    def __init__(self):
        # Defer heavy imports so importing the module is fast
        from sentence_transformers import SentenceTransformer
        from qdrant_client import QdrantClient
        from qdrant_client.http import models as qm
        self._SentenceTransformer = SentenceTransformer
        self._QdrantClient = QdrantClient

        t0 = time.perf_counter()
        self.model = SentenceTransformer(self.EMBEDDING_MODEL)
        # BGE-M3 supports up to 8192 tokens but its self-attention buffer
        # explodes on full-length inputs. Cap to 1024 tokens — section
        # chunks at MIN_WORDS-MAX_WORDS (100-800 words) fit easily.
        self.model.max_seq_length = 1024
        self.dim = self.model.get_sentence_embedding_dimension()
        self.client = QdrantClient(host=self.QDRANT_HOST, port=self.QDRANT_PORT, timeout=30)
        self._init_ms = int((time.perf_counter() - t0) * 1000)

        # Ensure the shared collection exists with a doc_id payload index for
        # fast filter-by-document scrolling/queries.
        if not self.client.collection_exists(self.SHARED_COLLECTION):
            self.client.create_collection(
                collection_name=self.SHARED_COLLECTION,
                vectors_config=qm.VectorParams(size=self.dim, distance=qm.Distance.COSINE),
            )
        try:
            self.client.create_payload_index(
                collection_name=self.SHARED_COLLECTION,
                field_name="doc_id",
                field_schema=qm.PayloadSchemaType.KEYWORD,
            )
        except Exception:
            pass    # already exists

        # Apply calibrated thresholds if data/vector_concepts_calibrated.json exists.
        # Falls back to spec-defaults silently.
        cal_path = Path(__file__).resolve().parents[2] / "data" / "vector_concepts_calibrated.json"
        overrides = _load_calibrated_thresholds(cal_path)
        self.concepts = _apply_calibration(CONCEPTS, overrides)
        self.calibration_applied = bool(overrides)

    # ── Public API ──

    # ── Cache helpers ──

    def _doc_id(self, document_text: str) -> str:
        """Stable 12-char doc fingerprint (MD5 of full text)."""
        import hashlib
        return hashlib.md5(document_text.encode("utf-8")).hexdigest()[:12]

    def _doc_already_indexed(self, doc_id: str) -> int:
        """Return number of points already stored under this doc_id."""
        from qdrant_client.http import models as qm
        try:
            res = self.client.count(
                collection_name=self.SHARED_COLLECTION,
                count_filter=qm.Filter(must=[
                    qm.FieldCondition(key="doc_id",
                                       match=qm.MatchValue(value=doc_id)),
                ]),
                exact=True,
            )
            return res.count
        except Exception:
            return 0

    def _scroll_doc_sections(self, doc_id: str) -> list[dict]:
        """Pull cached section payloads back so the report can reference them."""
        from qdrant_client.http import models as qm
        out: list[dict] = []
        offset = None
        while True:
            points, next_offset = self.client.scroll(
                collection_name=self.SHARED_COLLECTION,
                scroll_filter=qm.Filter(must=[
                    qm.FieldCondition(key="doc_id",
                                       match=qm.MatchValue(value=doc_id)),
                ]),
                limit=128,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for p in points:
                out.append(dict(p.payload))
            if not next_offset:
                break
            offset = next_offset
        return out

    # ── Public API ──

    def check_document(
        self,
        document_text: str,
        source_file: str,
        is_ap_tender: bool,
        estimated_value: float,
        duration_months: int = 12,
    ) -> dict:
        """Returns a dict:
            {
              'doc_id':           str,
              'sections':         [...],
              'findings':         [VectorFinding...],
              'concept_results':  [VectorFinding...],
              'timing_ms':        {chunk_ms, embed_sections_ms, upsert_ms,
                                    query_ms, cache_hit (bool)},
            }
        """
        from qdrant_client.http import models as qm

        timings: dict[str, int | bool] = {
            "chunk_ms": 0, "embed_sections_ms": 0,
            "upsert_ms": 0, "query_ms": 0, "cache_hit": False,
        }

        doc_id = self._doc_id(document_text)
        existing = self._doc_already_indexed(doc_id)

        sections: list[Section]
        sacs: list[str]

        if existing > 0:
            # CACHE HIT — skip chunking + embedding + upsert. Concept queries
            # below will use a doc_id filter so they only see this doc's
            # points. Build lightweight Section stubs from stored payloads
            # so the response shape is unchanged.
            timings["cache_hit"] = True
            payloads = self._scroll_doc_sections(doc_id)
            payloads.sort(key=lambda p: p.get("char_position", 0))
            sections = [
                Section(
                    heading=p["section_heading"],
                    body=p.get("section_text", ""),
                    char_start=p.get("char_position", 0),
                    char_end=p.get("char_position", 0) + len(p.get("section_text", "")),
                )
                for p in payloads
            ]
            sacs = [p.get("sac_summary", "") for p in payloads]
        else:
            # CACHE MISS — full pipeline: chunk → SAC → embed → upsert.

            # Step 1: chunk
            t0 = time.perf_counter()
            sections = chunk_into_sections(document_text)
            timings["chunk_ms"] = int((time.perf_counter() - t0) * 1000)

            if not sections:
                return {
                    "doc_id": doc_id, "sections": [], "section_objects": [],
                    "sacs": [], "findings": [],
                    "concept_results": [], "timing_ms": timings,
                }

            # Step 2 + 3: SAC + embed_text
            sacs = [sac_summary(s) for s in sections]
            embed_texts = [make_embed_text(s, sac) for s, sac in zip(sections, sacs)]

            # Step 4: embed sections (small batches keep CPU memory stable)
            t0 = time.perf_counter()
            section_vecs = self.model.encode(
                embed_texts,
                normalize_embeddings=True,
                show_progress_bar=False,
                batch_size=4,
            ).tolist()
            timings["embed_sections_ms"] = int((time.perf_counter() - t0) * 1000)

            # Step 5: upsert into the shared collection with doc_id metadata.
            # Point IDs are deterministic UUID5 of (doc_id + section_index) so
            # idempotent re-runs don't create duplicates.
            ns = uuid.uuid5(uuid.NAMESPACE_URL, "procureai/tender_sections")
            t0 = time.perf_counter()
            points = [
                qm.PointStruct(
                    id=str(uuid.uuid5(ns, f"{doc_id}:{i}")),
                    vector=section_vecs[i],
                    payload={
                        "doc_id":          doc_id,
                        "section_heading": sections[i].heading,
                        "section_text":    sections[i].body[:500],
                        "section_full_word_count": sections[i].word_count,
                        "source_file":     source_file,
                        "char_position":   sections[i].char_start,
                        "sac_summary":     sacs[i],
                    },
                )
                for i in range(len(sections))
            ]
            self.client.upsert(
                collection_name=self.SHARED_COLLECTION, points=points, wait=True,
            )
            timings["upsert_ms"] = int((time.perf_counter() - t0) * 1000)

        # Query each concept — always with a doc_id filter so we only search
        # within this document's section vectors.
        doc_filter = qm.Filter(must=[
            qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id)),
        ])

        t0 = time.perf_counter()
        results: list[VectorFinding] = []
        for c in self.concepts:
            query_text = c.canonical + " " + " ".join(c.aliases)
            qv = self.model.encode(query_text, normalize_embeddings=True).tolist()
            resp = self.client.query_points(
                collection_name=self.SHARED_COLLECTION,
                query=qv, limit=3,
                query_filter=doc_filter,
                with_payload=True,
            )
            hits = resp.points
            top_matches = [
                {
                    "score":      round(float(h.score), 4),
                    "heading":    h.payload["section_heading"],
                    "snippet":    h.payload["section_text"][:160],
                    "char_start": h.payload["char_position"],
                }
                for h in hits
            ]
            max_sim = max((h.score for h in hits), default=0.0)
            present = max_sim >= c.threshold
            mandatory = _is_concept_mandatory(
                c, is_ap=is_ap_tender, value=estimated_value, duration_months=duration_months,
            )
            results.append(VectorFinding(
                concept_id=c.concept_id,
                canonical=c.canonical,
                severity=c.severity,
                mandatory=mandatory,
                present=present,
                max_similarity=round(float(max_sim), 4),
                threshold=c.threshold,
                top_matches=top_matches,
            ))
        timings["query_ms"] = int((time.perf_counter() - t0) * 1000)

        findings = [r for r in results if r.is_violation()]
        return {
            "doc_id":           doc_id,
            "sections":         [{"heading": s.heading, "word_count": s.word_count,
                                  "char_start": s.char_start} for s in sections],
            "section_objects":  sections,
            "sacs":             sacs,
            "findings":         findings,
            "concept_results":  results,
            "timing_ms":        timings,
        }
