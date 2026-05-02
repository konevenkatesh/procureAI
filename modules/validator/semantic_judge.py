"""
modules/validator/semantic_judge.py

SemanticJudge — handles P4 (semantic-judgment) rules using the Claude API.

Pipeline per call:
    1. Fetch P4 + TYPE_1_ACTIONABLE rules from Supabase (cached on the
       instance for the lifetime of the process).
    2. For each rule (capped at MAX_RULES_PER_CALL):
         a. Locate the most-relevant section(s) of the document. Two modes:
              • Default — keyword-overlap on heading + first 1200 chars
                (`_find_relevant_section`). No Qdrant dependency.
              • Production — embedding-based retrieval against the shared
                `tender_sections` Qdrant collection, filtered by doc_id
                (`_find_relevant_section_via_vector`). Reuses the BGE-M3
                vectors VectorChecker already indexed; top-3 sections
                are concatenated up to ~800 words.
         b. Build the SYSTEM + USER prompt described in the spec.
         c. Call Claude (anthropic.Anthropic, claude-sonnet-4-5) and
            parse the JSON response.
         d. Skip if verdict=PASS, evidence is empty, or confidence=LOW.
         e. Otherwise build a SemanticFinding.
    3. Return the list + token-usage record.

The class accepts an optional `judgement_fn` callable so a caller can
inject deterministic judgments for testing / no-API-key environments —
the rest of the project deliberately avoids the Anthropic API and uses
"Claude Code in conversation" as the LLM.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, Literal

import requests
from pydantic import BaseModel, Field

from builder.config import settings
from engines.classifier import TenderClassification
from engines.parameter_cascade import TenderParameters


MODEL              = "claude-sonnet-4-5"
MAX_RULES_PER_CALL = 10
SECTION_WINDOW_WORDS = 500


# ───────────────────────────────────────────────────────────────────────────
# Output model
# ───────────────────────────────────────────────────────────────────────────

class SemanticFinding(BaseModel):
    rule_id: str
    typology_code: str
    severity: str
    verdict: str                 # PASS / FAIL
    evidence: str                # verbatim quote from the document
    confidence: str              # HIGH / MEDIUM / LOW
    reasoning: str
    detected_by: Literal["LLM"] = "LLM"


@dataclass
class TokenUsage:
    input_tokens:  int = 0
    output_tokens: int = 0
    calls:         int = 0
    rules_judged:  int = 0
    failures:      int = 0


# ───────────────────────────────────────────────────────────────────────────
# Section locator
# ───────────────────────────────────────────────────────────────────────────

_HEADING_RE = re.compile(r"^(?:#{1,6}\s+\S.*|\d+(?:\.\d+){0,5}\s+\S.*)$", re.MULTILINE)
_STOP_WORDS = {
    "the", "and", "for", "with", "that", "this", "shall", "must", "are", "any",
    "all", "from", "have", "has", "into", "than", "such", "their", "where",
    "which", "when", "they", "been", "may", "not", "but", "also", "above",
    "below", "rule", "section", "of", "in", "to", "as", "be", "is", "by",
    "or", "an", "a", "on", "at", "it",
}


def _keywords(text: str, k: int = 12) -> list[str]:
    """Pick top-k content words from a text snippet."""
    toks = re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", text.lower())
    out: list[str] = []
    seen: set[str] = set()
    for t in toks:
        if t in _STOP_WORDS or t in seen:
            continue
        out.append(t)
        seen.add(t)
        if len(out) >= k:
            break
    return out


def _split_into_sections(text: str) -> list[tuple[str, str, int]]:
    """Cheap section split: chunks bounded by the next heading line.
    Returns (heading, body, char_start)."""
    if not text.strip():
        return []
    matches = list(_HEADING_RE.finditer(text))
    sections: list[tuple[str, str, int]] = []
    if not matches:
        sections.append(("(document)", text, 0))
        return sections
    # Pre-text
    if matches[0].start() > 0:
        sections.append(("(preamble)", text[: matches[0].start()], 0))
    for i, m in enumerate(matches):
        start, end = m.start(), m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append((m.group(0).strip(), text[end:body_end], start))
    return sections


def _find_relevant_section(text: str, rule_text: str, window_words: int = SECTION_WINDOW_WORDS) -> str:
    """Pick the section whose heading + first-200-chars-body share the most
    keywords with the rule text. Trim to ~window_words.

    NOTE: kept as the deterministic, no-API fallback. Production callers
    should prefer SemanticJudge._find_relevant_section_via_vector() which
    uses BGE-M3 embedding similarity on the already-indexed Qdrant
    collection — far higher recall on rules whose vocabulary doesn't
    overlap with the document's phrasing."""
    rule_kws = set(_keywords(rule_text, k=20))
    if not rule_kws:
        # No usable keywords — fall back to first window
        return " ".join(text.split()[:window_words])
    best: tuple[int, str] = (-1, "")
    for heading, body, _start in _split_into_sections(text):
        chunk = (heading + "\n" + body[:1200]).lower()
        score = sum(1 for kw in rule_kws if kw in chunk)
        if score > best[0]:
            words = body.split()
            excerpt = " ".join(words[:window_words])
            best = (score, f"{heading}\n{excerpt}")
    return best[1] or " ".join(text.split()[:window_words])


# ───────────────────────────────────────────────────────────────────────────
# Prompt builder
# ───────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are a procurement compliance expert reviewing Indian government "
    "tender documents. You assess whether specific compliance rules are "
    "satisfied. You must cite verbatim evidence from the document. You "
    "never fabricate evidence."
)


def build_user_prompt(
    rule: dict,
    classification: TenderClassification,
    parameters: TenderParameters,
    relevant_section: str,
) -> str:
    ev = (
        getattr(classification, "estimated_value", None)
        or getattr(parameters, "estimated_value", None)
        or 0
    )
    is_ap = (
        getattr(classification, "is_ap_tender", None)
        if hasattr(classification, "is_ap_tender")
        else getattr(parameters, "is_ap_tender", False)
    )
    return f"""Rule: {rule['natural_language']}
Source: {rule.get('source_clause') or '(unknown)'}
Typology: {rule['typology_code']}
Severity if violated: {rule['severity']}

Tender context:
Type: {classification.primary_type}
Value: Rs. {ev}
AP Tender: {is_ap}

Document excerpt (most relevant section):
\"\"\"
{relevant_section}
\"\"\"

Assessment required:
1. Is this rule satisfied? Answer PASS or FAIL
2. If FAIL: quote the exact text that shows the violation (verbatim, under 50 words)
3. Confidence: HIGH | MEDIUM | LOW
4. Reasoning: one sentence explanation

Return JSON only:
{{
  "verdict": "PASS" or "FAIL",
  "evidence": "verbatim quote or null",
  "confidence": "HIGH|MEDIUM|LOW",
  "reasoning": "one sentence"
}}"""


# ───────────────────────────────────────────────────────────────────────────
# SemanticJudge
# ───────────────────────────────────────────────────────────────────────────

JudgementFn = Callable[[str, str], dict]   # (system_prompt, user_prompt) -> result_dict


# Typology → distinctive violation-indicator regex patterns.
# Used by the LEXICAL leg of hybrid retrieval. A section is given a
# lexical hit count by the number of distinct patterns that match —
# NOT by raw token frequency, which is dominated by generic words
# ("contractor", "registration") that appear everywhere in long GCC
# sections. Phrase patterns target specific violation surface forms
# that ARE the smoking gun for each typology.
#
# Patterns are case-insensitive at compile time below.
TYPOLOGY_PATTERNS: dict[str, list[str]] = {
    "Geographic-Restriction": [
        r"(?:Indian|specific\s+country)\s+nationality",
        r"contractor[s]?\s+from\s+abroad",
        r"foreign\s+(?:bidder|contractor|firm)s?\s+(?:not|are\s+not|shall\s+not\s+be)\s+(?:permitted|allowed|eligible)",
        r"GST\s+registration\s+within\s+(?:Andhra\s+Pradesh|\w+)",
        r"only\s+(?:Indian|local|state|domiciled)\s+(?:firms|bidders|contractors)",
        r"registered\s+(?:in\s+the\s+state|in\s+\w+\s+state)",
    ],
    "Criteria-Restriction-Narrow": [
        r"Joint\s+Venture[^.]{0,40}not\s+allowed",
        r"\bJV\b[^.]{0,30}not\s+allowed",
        r"forming\s+Joint\s+Venture[^.]{0,80}not\s+allowed",
        r"Special\s+Class\s+\w+\s+registration",
        r"registered\s+with\s+(?:Government|Govt\.?)\s+of\s+\w+",
        r"single[-\s]?source",
        r"only\s+\w+\s+manufacturers?\s+(?:are\s+)?eligible",
    ],
    "Criteria-Restriction-Loose": [
        r"as\s+(?:may\s+be|deemed)\s+(?:appropriate|necessary|suitable)",
        r"satisfactory\s+(?:to|in\s+the\s+opinion\s+of)\s+the\s+\w+",
    ],
    "Spec-Tailoring": [
        r"\b(?:make|brand|model)[-\s]\w+\s+only",
        r"manufacturer:\s*[A-Z][\w\s]+only",
        r"part\s+(?:no|number)\.?\s*[A-Z0-9-]+\s+exclusively",
    ],
    "Bid-Splitting-Pattern": [
        r"split[^.]{0,80}(?:smaller\s+values|powers\s+of\s+junior)",
        r"divided\s+into\s+\w+\s+packages\s+to\s+(?:bring|stay)",
    ],
    "Cover-Bidding-Signal": [
        r"(?:same|identical)\s+address[^.]{0,40}(?:multiple|several)\s+bidders",
        r"bid\s+rotation\s+pattern",
    ],
    "Missing-Mandatory-Field": [
        # No phrase is "missing" by definition — vector retrieval handles this.
    ],
    "COI-PMC-Works": [
        r"same\s+(?:firm|consultant)\s+(?:as|both)\s+(?:design|PMC|engineer)",
    ],
    "Available-Bid-Capacity-Error": [
        r"available\s+bid\s+capacity\s*[=:]\s*\(?A",
        r"excessive\s+(?:provisioning|stockholding|inventory)",
    ],
}

# Compiled at import time
_TYPOLOGY_PATTERNS_COMPILED: dict[str, list[re.Pattern]] = {
    typ: [re.compile(p, re.IGNORECASE) for p in pats]
    for typ, pats in TYPOLOGY_PATTERNS.items()
}


# Typology → vocabulary aliases.
# Used by `_find_relevant_section_via_vector` to enrich the query embedding
# with words that procurement violations of this typology actually use.
# The rule's own natural_language describes the GENERAL PRINCIPLE in
# regulatory English ("denied prequalification for reasons unrelated to
# capability"); the violation in the document uses SPECIFIC vocabulary
# ("Indian nationality", "foreign", "AP GST"). Without this expansion the
# top-k retrieval consistently picks abstract qualification-criteria
# sections over the concrete violation sections.
TYPOLOGY_ALIASES: dict[str, str] = {
    "Geographic-Restriction":
        "geographic restriction nationality country origin foreign bidder "
        "state region domicile residency local-only national exclusion",
    "Criteria-Restriction-Narrow":
        "narrow eligibility specific contractor favour incumbent registration "
        "class joint venture not allowed turnover threshold proprietary",
    "Criteria-Restriction-Loose":
        "loose vague undefined eligibility broad subjective unverifiable criteria",
    "Spec-Tailoring":
        "specification tailored brand-specific make model proprietary "
        "single-vendor manufacturer unique part number",
    "Bid-Splitting-Pattern":
        "split orders smaller values financial powers junior officer "
        "fragmentation multiple smaller contracts",
    "Cover-Bidding-Signal":
        "cover bidding sham bid token bid same bidders repeat patterns "
        "rotation winner predetermined collusion",
    "Missing-Mandatory-Field":
        "mandatory field certificate declaration form undertaking missing",
    "COI-PMC-Works":
        "conflict interest consultant project management affiliate same firm "
        "design engineer supervision works execution",
    "Available-Bid-Capacity-Error":
        "bid capacity calculation provisioning excessive stock consumption "
        "outstanding dues equipment life",
}


class SemanticJudge:
    # Defaults for vector-based section retrieval. The user-visible top_k is
    # for the FUSED ranking; the vector and lexical retrievers each pull a
    # larger pool internally that the RRF then re-ranks down to top_k.
    VEC_TOP_K           = 3      # final number of sections returned
    VEC_MAX_WORDS_TOTAL = 800
    _RRF_POOL_VEC       = 8      # internal: vector pool size before fusion
    _RRF_POOL_LEX       = 8      # internal: lexical pool size before fusion
    _RRF_K              = 60     # RRF damping constant (standard value)

    def __init__(
        self,
        *,
        judgement_fn: JudgementFn | None = None,
        max_rules: int = MAX_RULES_PER_CALL,
        vector_checker=None,                # VectorChecker | None (lazy-typed to avoid heavy import)
    ):
        """If `judgement_fn` is None, the judge tries to use the Anthropic API.
        Otherwise it calls the injected function — useful for tests / runs
        without an API key.

        `vector_checker` (optional) is a VectorChecker instance used for
        embedding-based section retrieval in `judge_document(...,
        pre_indexed_doc_id=...)`. If None, a VectorChecker is constructed
        lazily the first time vector retrieval is requested."""
        self._fn = judgement_fn
        self._anthropic = None
        if judgement_fn is None:
            try:
                import anthropic
                self._anthropic = anthropic.Anthropic()
            except Exception as e:
                # API client unavailable. Caller will get a clear error if
                # they actually try to invoke .judge_document() — this lets
                # importing the module succeed even without ANTHROPIC_API_KEY.
                self._anthropic_init_error = repr(e)
        self._rules: list[dict] | None = None
        self._max_rules = max_rules
        self._vector = vector_checker
        self.token_usage = TokenUsage()

    # ── Vector-based section retrieval ──

    def _ensure_vector(self):
        """Lazy-construct a VectorChecker (loads BGE-M3, ~3-5s) on first
        use. Caller can pre-inject one via __init__ to avoid the latency."""
        if self._vector is None:
            from modules.validator.vector_checker import VectorChecker
            self._vector = VectorChecker()
        return self._vector

    def _find_relevant_section_via_vector(
        self,
        rule: dict,
        doc_id: str,
        document_text: str | None = None,
        top_k: int | None = None,
        max_words: int | None = None,
    ) -> str:
        """Embedding + lexical hybrid retrieval against the shared
        `tender_sections` Qdrant collection (filtered by `doc_id`).

        The query is built as `rule.natural_language + typology vocabulary`
        so the BGE-M3 embedding picks up violation-specific phrasing the
        rule's regulatory language doesn't itself contain.

        Two retrievers run in parallel over the same pool:
            • VECTOR     — Qdrant cosine similarity (top-_RRF_POOL_VEC)
            • LEXICAL    — typology-alias substring count over the full
                           re-sliced section body (top-_RRF_POOL_LEX)

        Their rankings are fused via Reciprocal Rank Fusion
        (RRF score = Σ 1 / (k + rank_i)) and the top `top_k` sections
        are concatenated up to `max_words`. RRF is the standard hybrid
        merger because it's order-only — no need to calibrate raw
        similarity vs. raw substring counts onto the same scale.

        Returns concatenated text. Sections are separated by '\\n\\n---\\n\\n'.

        If `document_text` is provided, full section bodies are sliced
        from the original text using `char_position` + `section_full_word_count`
        (the Qdrant payload stores only the first 500 chars per section)."""
        from qdrant_client.http import models as qm

        top_k     = top_k     or self.VEC_TOP_K
        max_words = max_words or self.VEC_MAX_WORDS_TOTAL

        vec = self._ensure_vector()

        # ── 1. Build the enriched query ────────────────────────────────
        typ = rule.get("typology_code") or ""
        typology_words = typ.replace("-", " ")
        aliases_str = TYPOLOGY_ALIASES.get(typ, "")
        query_text = (
            f"{rule['natural_language']} "
            f"Typology: {typology_words}. "
            f"Concerns: {aliases_str or typology_words}."
        )

        doc_filter = qm.Filter(must=[
            qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id)),
        ])

        # ── 2a. VECTOR pool ────────────────────────────────────────────
        qv = vec.model.encode(
            query_text, normalize_embeddings=True, show_progress_bar=False,
        ).tolist()
        vec_resp = vec.client.query_points(
            collection_name=vec.SHARED_COLLECTION,
            query=qv,
            limit=self._RRF_POOL_VEC,
            query_filter=doc_filter,
            with_payload=True,
        )
        vec_hits = vec_resp.points
        if not vec_hits:
            return ""

        # ── 2b. LEXICAL pool ───────────────────────────────────────────
        # Phrase-pattern matching on full section bodies. We score each
        # section by COUNT OF DISTINCT PATTERNS that match, not raw token
        # frequency — token frequency over long sections is dominated by
        # generic words ("contractor", "registration") and inflates
        # unrelated GCC clauses above tight violation sections.
        patterns = _TYPOLOGY_PATTERNS_COMPILED.get(typ, [])
        all_payloads = vec._scroll_doc_sections(doc_id)
        lex_scored: list[tuple[int, dict]] = []
        if patterns:
            for p in all_payloads:
                char_pos = int(p.get("char_position", 0) or 0)
                wc = int(p.get("section_full_word_count", 0) or 0)
                if document_text:
                    full = document_text[char_pos:char_pos + max(wc * 6, 800)]
                else:
                    full = p.get("section_text", "") or ""
                hits = sum(1 for pat in patterns if pat.search(full))
                if hits > 0:
                    lex_scored.append((hits, p))
            lex_scored.sort(key=lambda x: -x[0])
        lex_pool = [p for _, p in lex_scored[: self._RRF_POOL_LEX]]

        # ── 3. RRF fusion keyed by char_position (stable section ID) ──
        rrf: dict[int, float] = {}
        meta: dict[int, dict] = {}
        for rank, h in enumerate(vec_hits, start=1):
            cp = int(h.payload.get("char_position", 0) or 0)
            rrf[cp] = rrf.get(cp, 0.0) + 1.0 / (self._RRF_K + rank)
            meta[cp] = {
                "heading":    h.payload.get("section_heading", ""),
                "vector_score": float(h.score),
                "char_pos":   cp,
                "wc":         int(h.payload.get("section_full_word_count", 0) or 0),
            }
        for rank, p in enumerate(lex_pool, start=1):
            cp = int(p.get("char_position", 0) or 0)
            rrf[cp] = rrf.get(cp, 0.0) + 1.0 / (self._RRF_K + rank)
            if cp not in meta:
                meta[cp] = {
                    "heading":     p.get("section_heading", ""),
                    "vector_score": 0.0,
                    "char_pos":    cp,
                    "wc":          int(p.get("section_full_word_count", 0) or 0),
                }

        ranked = sorted(rrf.items(), key=lambda x: -x[1])[:top_k]

        # ── 4. Concatenate up to `max_words` ───────────────────────────
        parts: list[str] = []
        total_words = 0
        for cp, fused in ranked:
            m = meta[cp]
            heading    = m["heading"]
            char_start = m["char_pos"]
            word_cnt   = m["wc"]
            v_score    = m["vector_score"]

            if document_text:
                body_end = char_start + max(word_cnt * 6, 800)
                slice_text = document_text[char_start:body_end]
                body = slice_text.split("\n", 1)[1] if "\n" in slice_text else slice_text
            else:
                # Fall back to looking up payload by char_pos
                stored = next((p.get("section_text", "") for p in all_payloads
                                if int(p.get("char_position", 0) or 0) == char_start),
                              "")
                body = stored

            words = body.split()
            remaining = max_words - total_words
            if remaining <= 0:
                break
            if len(words) > remaining:
                words = words[:remaining]
            if not words:
                continue

            parts.append(
                f"[rrf {fused:.4f} | vec {v_score:.4f}] {heading}\n{' '.join(words)}"
            )
            total_words += len(words)

        return "\n\n---\n\n".join(parts)

    # ── Rule fetch (cached) ──

    def _fetch_p4_rules(self) -> list[dict]:
        if self._rules is not None:
            return self._rules
        url = f"{settings.supabase_rest_url}/rest/v1/rules"
        params = {
            "select": "rule_id,natural_language,verification_method,typology_code,"
                      "severity,layer,source_clause,pattern_type,rule_type",
            "pattern_type": "eq.P4",
            "rule_type":    "eq.TYPE_1_ACTIONABLE",
            "order":        "rule_id.asc",
        }
        headers = {
            "apikey":        settings.supabase_anon_key,
            "Authorization": f"Bearer {settings.supabase_anon_key}",
            "Range-Unit":    "items",
            "Range":         "0-999",
        }
        res = requests.get(url, params=params, headers=headers, timeout=30)
        res.raise_for_status()
        self._rules = res.json()
        return self._rules

    # ── Single-rule judgement ──

    def _judge_one(self, system: str, user: str) -> dict:
        """Returns a dict with keys verdict / evidence / confidence /
        reasoning / input_tokens / output_tokens. Falls back gracefully
        when the API client is unavailable."""
        if self._fn is not None:
            res = self._fn(system, user)
            return res

        if self._anthropic is None:
            raise RuntimeError(
                "Anthropic client not initialised: "
                + getattr(self, "_anthropic_init_error", "no API key configured")
            )
        msg = self._anthropic.messages.create(
            model=MODEL,
            max_tokens=512,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        body = "".join(b.text for b in msg.content if hasattr(b, "text")).strip()
        # Strip ```json fences if present
        body = re.sub(r"^```(?:json)?\s*|\s*```$", "", body)
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            data = {"verdict": "PASS", "evidence": "", "confidence": "LOW",
                    "reasoning": "LLM returned non-JSON; defaulted to PASS"}
        data["input_tokens"]  = msg.usage.input_tokens  if hasattr(msg, "usage") else 0
        data["output_tokens"] = msg.usage.output_tokens if hasattr(msg, "usage") else 0
        return data

    # ── Public API ──

    def judge_document(
        self,
        document_text: str,
        classification: TenderClassification,
        parameters: TenderParameters,
        rule_filter: Callable[[dict], bool] | None = None,
        pre_indexed_doc_id: str | None = None,
    ) -> list[SemanticFinding]:
        """Judge every applicable P4 rule against the document. Returns
        SemanticFindings only for FAIL verdicts with non-LOW confidence.

        Section-retrieval modes:
            • `pre_indexed_doc_id` is None  →  keyword-overlap fallback
              (`_find_relevant_section`). Fast, no Qdrant dependency,
              but recall is low for rules whose vocabulary doesn't match
              the document's phrasing.
            • `pre_indexed_doc_id` provided →  embedding-based retrieval
              via `_find_relevant_section_via_vector` against the shared
              `tender_sections` Qdrant collection. Caller must have
              previously called `VectorChecker.check_document(...)` on
              the same `document_text` so the sections are already
              indexed (the doc_id is `VectorChecker._doc_id(text)`)."""
        rules = self._fetch_p4_rules()
        if rule_filter is not None:
            rules = [r for r in rules if rule_filter(r)]
        rules = rules[: self._max_rules]

        findings: list[SemanticFinding] = []
        for r in rules:
            if pre_indexed_doc_id is not None:
                relevant = self._find_relevant_section_via_vector(
                    r, pre_indexed_doc_id, document_text=document_text,
                )
                if not relevant:
                    # Vector retrieval returned nothing — last-resort fallback
                    relevant = _find_relevant_section(document_text, r["natural_language"])
            else:
                relevant = _find_relevant_section(document_text, r["natural_language"])
            user_prompt = build_user_prompt(r, classification, parameters, relevant)
            t0 = time.perf_counter()
            try:
                resp = self._judge_one(SYSTEM_PROMPT, user_prompt)
                self.token_usage.calls += 1
                self.token_usage.rules_judged += 1
                self.token_usage.input_tokens  += int(resp.get("input_tokens", 0))
                self.token_usage.output_tokens += int(resp.get("output_tokens", 0))
            except Exception as e:
                self.token_usage.failures += 1
                continue
            verdict     = (resp.get("verdict") or "").upper()
            evidence    = (resp.get("evidence") or "").strip()
            confidence  = (resp.get("confidence") or "").upper()
            reasoning   = (resp.get("reasoning") or "").strip()
            if verdict != "FAIL":         continue
            if not evidence:              continue
            if confidence == "LOW":       continue
            findings.append(SemanticFinding(
                rule_id=r["rule_id"],
                typology_code=r["typology_code"],
                severity=r["severity"],
                verdict=verdict,
                evidence=evidence[:500],
                confidence=confidence,
                reasoning=reasoning,
            ))
        return findings
