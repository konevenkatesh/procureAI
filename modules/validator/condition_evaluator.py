"""
modules/validator/condition_evaluator.py

Evaluate a rule's `condition_when` string against a tender_facts dict
under three-valued logic.

Returns one of:
    FIRE     — every condition is satisfied; rule applies
    SKIP     — at least one condition is verifiably false; rule does NOT apply
    UNKNOWN  — required fact missing; caller should treat findings as ADVISORY

Why three-valued?
    The rules table populates condition_when on every TYPE_1 rule, but we
    cannot reliably extract every fact from every document. Two-valued
    logic forces a choice:
       - default-true  → every Services rule fires on every Works tender
       - default-false → many real violations get suppressed when one
                         optional fact (e.g. ContractAwarded) is missing
    Three-valued logic keeps both honest: the system tells the reviewer
    "I would have fired this rule but I don't know <fact>" rather than
    silently dropping it.

Supported syntax (matches the populated condition_when values seen in
the rules table — covers 100% of TYPE_1 rules):

    expr   := and_term ( OR and_term )*
    and_term := clause ( AND clause )*
    clause := key OP value
            | key IN '[' val_list ']'
    OP     := = | != | > | >= | < | <=
    value  := identifier | quoted_string | number | true | false

Top-level grouping with parentheses is NOT seen in the corpus and is
deliberately NOT supported — adding it later if needed is one new rule
in the parser, but for v0.3-clean we keep the grammar minimal so the
parser is easy to audit.

Public API:
    evaluate(condition_when, tender_facts) -> EvaluationResult
    parse(condition_when) -> AST
    summarise_facts_used(condition_when) -> set[str]
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── Verdict + result types ────────────────────────────────────────────

class Verdict(str, Enum):
    FIRE    = "FIRE"
    SKIP    = "SKIP"
    UNKNOWN = "UNKNOWN"


@dataclass
class EvaluationResult:
    verdict: Verdict
    # Facts that were resolved during evaluation
    facts_resolved: dict[str, Any] = field(default_factory=dict)
    # Facts the condition wanted but couldn't find
    facts_missing: list[str]       = field(default_factory=list)
    # Per-clause trace, useful for debugging:
    #   [(clause_text, "TRUE"|"FALSE"|"UNKNOWN", reason), ...]
    trace: list[tuple[str, str, str]] = field(default_factory=list)


# ── Fact-key mapping ──────────────────────────────────────────────────

# condition_when uses the LHS keys below. tender_facts uses the RHS.
# When a key is mapped to None it means "we have no extractor for this
# fact yet — always UNKNOWN". The caller may pre-populate any of these
# explicitly to override this default.
FACT_KEY_MAP: dict[str, str | None] = {
    # Reliable
    "TenderType":           "tender_type",
    "TenderState":          "_tender_state_synthesized",   # synthesised below
    # Unreliable / not yet extracted (default UNKNOWN)
    "ContractAwarded":      None,
    "ContractInProgress":   None,
    "PSRequiredAsPerSBD":   None,
    "BidsReceived":         None,
    "EstimatedValue":       None,   # we have a value but it's flagged unreliable
    "ContractValue":        None,
    "MobilisationAdvance":  None,
    "PriceAdjustmentApplicable": None,
    "IntegrityPactSigned":  None,
    "ServiceCategory":      None,
    "ProcurementMode":      None,
    "ProcurementMethod":    None,
    "OrgType":              None,
    "ContractType":         None,
    "PlatformUsed":         None,
    "SelectionMethod":      None,
    "DisposalMode":         None,
    "ExecutionAgency":      None,
    "ContractClosure":      None,
    "BidderFromLandBorderCountry": None,
    "Material":             None,
    "Sector":               None,
    "BidderState":          None,
    "ItemAvailableOnGeM":   None,
    "RFPIssued":            None,
    # … any unmapped LHS key falls through to UNKNOWN by default.
}


_UNKNOWN = object()   # sentinel returned when fact is unavailable


def _resolve_fact(key: str, facts: dict) -> Any:
    """Look up a condition_when LHS key in the tender_facts dict.
    Returns either the resolved value or the _UNKNOWN sentinel.

    Special cases:
      • TenderState: synthesised from is_ap_tender (true → "AndhraPradesh").
        If is_ap_tender is False, returns None to mean "not AP" — clauses
        like `TenderState=AndhraPradesh` will then evaluate to False, not
        UNKNOWN.
      • TenderType=ANY is handled at clause level (see _eval_clause)."""
    # Allow callers to override by populating tender_facts directly with
    # the condition_when key spelling.
    if key in facts:
        return facts[key]

    # Synthesised TenderState
    if key == "TenderState":
        if "is_ap_tender" in facts:
            return "AndhraPradesh" if facts["is_ap_tender"] else None
        return _UNKNOWN

    # Mapped key
    mapped = FACT_KEY_MAP.get(key)
    if mapped is None:
        return _UNKNOWN
    return facts.get(mapped, _UNKNOWN)


# ── Tokeniser + parser ───────────────────────────────────────────────

# A token is one of:
#   ident / number / string-literal / operator / keyword / bracket
# Identifiers in condition_when are alphanumeric + dot/underscore/hyphen.
_TOKEN_RE = re.compile(
    r"""
      \s+                                  # whitespace (skipped)
    | (?P<lp>\[)                           # [
    | (?P<rp>\])                           # ]
    | (?P<comma>,)                         # ,
    | (?P<op>!=|>=|<=|=|>|<)               # comparison operators
    | (?P<kw>\b(?:AND|OR|IN)\b)            # keywords
    | (?P<num>-?\d+(?:\.\d+)?)             # number
    | (?P<str>"[^"]*"|'[^']*')             # quoted string
    | (?P<ident>[A-Za-z_][A-Za-z0-9_\-/]*) # identifier (TenderType, AndhraPradesh, GO_Ms, …)
    """,
    re.VERBOSE,
)


def _tokenise(s: str) -> list[tuple[str, str]]:
    """Split the condition string into [(kind, text), …]."""
    out: list[tuple[str, str]] = []
    i = 0
    while i < len(s):
        m = _TOKEN_RE.match(s, i)
        if not m:
            raise SyntaxError(f"unexpected char at offset {i}: {s[i]!r}")
        i = m.end()
        for k, v in m.groupdict().items():
            if v is not None:
                out.append((k, v))
                break
        # whitespace branch matches but no group fires → skip
    return out


# AST nodes
@dataclass
class ClauseNode:
    raw_text: str
    key: str
    op: str           # =, !=, >, >=, <, <=, IN
    value: Any        # str | float | bool | list[str|float|bool]


@dataclass
class AndNode:
    children: list   # of ClauseNode | AndNode | OrNode

@dataclass
class OrNode:
    children: list


def parse(condition_when: str):
    """Tokenise + parse the string into a tree."""
    tokens = _tokenise(condition_when)

    pos = [0]

    def peek():
        return tokens[pos[0]] if pos[0] < len(tokens) else (None, None)

    def consume():
        t = tokens[pos[0]]
        pos[0] += 1
        return t

    def parse_value():
        kind, text = consume()
        if kind == "num":
            return float(text)
        if kind == "str":
            return text[1:-1]
        if kind == "ident":
            if text.lower() == "true":
                return True
            if text.lower() == "false":
                return False
            return text
        raise SyntaxError(f"expected value, got {kind!r}={text!r}")

    def parse_clause():
        # Capture raw text for the trace
        start_pos = pos[0]
        kind, text = peek()
        if kind != "ident":
            raise SyntaxError(f"expected identifier (LHS key), got {kind!r}={text!r}")
        key = consume()[1]
        kind, text = peek()
        if kind == "kw" and text.upper() == "IN":
            consume()  # IN
            kind2, _ = peek()
            if kind2 != "lp":
                raise SyntaxError(f"expected '[' after IN, got {kind2!r}")
            consume()  # [
            values: list = []
            while True:
                values.append(parse_value())
                kind3, _ = peek()
                if kind3 == "comma":
                    consume()
                    continue
                break
            kind4, _ = peek()
            if kind4 != "rp":
                raise SyntaxError("expected ']' to close IN list")
            consume()  # ]
            raw = _slice_tokens(tokens, start_pos, pos[0])
            return ClauseNode(raw_text=raw, key=key, op="IN", value=values)
        if kind != "op":
            raise SyntaxError(f"expected comparison operator after {key!r}, got {kind!r}")
        op = consume()[1]
        value = parse_value()
        raw = _slice_tokens(tokens, start_pos, pos[0])
        return ClauseNode(raw_text=raw, key=key, op=op, value=value)

    def parse_and():
        nodes = [parse_clause()]
        while True:
            kind, text = peek()
            if kind == "kw" and text.upper() == "AND":
                consume()
                nodes.append(parse_clause())
            else:
                break
        if len(nodes) == 1:
            return nodes[0]
        return AndNode(children=nodes)

    def parse_or():
        nodes = [parse_and()]
        while True:
            kind, text = peek()
            if kind == "kw" and text.upper() == "OR":
                consume()
                nodes.append(parse_and())
            else:
                break
        if len(nodes) == 1:
            return nodes[0]
        return OrNode(children=nodes)

    tree = parse_or()
    if pos[0] != len(tokens):
        kind, text = tokens[pos[0]]
        raise SyntaxError(f"unexpected trailing token {kind!r}={text!r}")
    return tree


def _slice_tokens(tokens, start: int, end: int) -> str:
    return " ".join(t[1] for t in tokens[start:end])


# ── Evaluator (3-valued: True / False / UNKNOWN) ──────────────────────

def _eval_clause(node: ClauseNode, facts: dict, result: EvaluationResult):
    """Returns one of (True, False, _UNKNOWN). Records trace."""
    # ANY-wildcard handled specially: TenderType=ANY (and friends)
    if node.op == "=" and node.value == "ANY":
        result.trace.append((node.raw_text, "TRUE", "ANY wildcard"))
        return True

    fact = _resolve_fact(node.key, facts)
    if fact is _UNKNOWN:
        result.trace.append((node.raw_text, "UNKNOWN", f"fact {node.key!r} not in tender_facts"))
        if node.key not in result.facts_missing:
            result.facts_missing.append(node.key)
        return _UNKNOWN
    result.facts_resolved[node.key] = fact

    op = node.op
    val = node.value

    try:
        if op == "=":
            ok = _eq(fact, val)
        elif op == "!=":
            ok = not _eq(fact, val)
        elif op in (">", ">=", "<", "<="):
            f = float(fact)
            v = float(val)
            ok = (
                f >  v if op == ">"  else
                f >= v if op == ">=" else
                f <  v if op == "<"  else
                f <= v
            )
        elif op == "IN":
            ok = any(_eq(fact, v) for v in val)
        else:
            raise ValueError(f"unsupported operator: {op}")
    except (TypeError, ValueError) as e:
        # Fact present but uncomparable to value (e.g. string vs number).
        # Treat as UNKNOWN — fact existed but couldn't be tested honestly.
        result.trace.append((node.raw_text, "UNKNOWN", f"compare error: {e}"))
        return _UNKNOWN

    result.trace.append((node.raw_text, "TRUE" if ok else "FALSE", f"{node.key}={fact!r}"))
    return ok


def _eq(a, b) -> bool:
    """Equality across the value types we see in condition_when. Matches
    bool to bool, number to number, string to string with case-insensitive
    compare for typical procurement enums (Works/works/WORKS)."""
    if isinstance(a, bool) or isinstance(b, bool):
        # avoid bool-as-int weirdness: only compare bool↔bool
        if isinstance(a, bool) and isinstance(b, bool):
            return a == b
        # bool↔string: "true"/"false" tolerated
        if isinstance(a, bool) and isinstance(b, str):
            return a == (b.lower() == "true")
        if isinstance(b, bool) and isinstance(a, str):
            return b == (a.lower() == "true")
        return False
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) == float(b)
    if isinstance(a, str) and isinstance(b, str):
        return a.casefold() == b.casefold()
    # mixed numeric/string — try float compare
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return str(a).casefold() == str(b).casefold()


def _eval_node(node, facts: dict, result: EvaluationResult):
    if isinstance(node, ClauseNode):
        return _eval_clause(node, facts, result)
    if isinstance(node, AndNode):
        any_unknown = False
        for c in node.children:
            v = _eval_node(c, facts, result)
            if v is False:
                # Short-circuit: False AND anything = False.
                return False
            if v is _UNKNOWN:
                any_unknown = True
        return _UNKNOWN if any_unknown else True
    if isinstance(node, OrNode):
        any_unknown = False
        for c in node.children:
            v = _eval_node(c, facts, result)
            if v is True:
                # Short-circuit: True OR anything = True.
                return True
            if v is _UNKNOWN:
                any_unknown = True
        return _UNKNOWN if any_unknown else False
    raise TypeError(f"unknown AST node type: {type(node)}")


# ── Public API ────────────────────────────────────────────────────────

def evaluate(condition_when: str, tender_facts: dict) -> EvaluationResult:
    """Evaluate one condition string against a tender_facts dict.

    `tender_facts` is a flat dict whose keys are EITHER the lowercase
    snake_case names this codebase uses (e.g. `tender_type`,
    `is_ap_tender`) OR the condition_when LHS spellings directly
    (e.g. `TenderType`). The latter takes precedence — useful for
    callers that already have facts in condition_when's spelling."""
    if not condition_when or not condition_when.strip():
        # Empty condition → vacuously true → FIRE
        return EvaluationResult(verdict=Verdict.FIRE)

    result = EvaluationResult(verdict=Verdict.UNKNOWN)
    try:
        tree = parse(condition_when)
    except SyntaxError as e:
        # Conservative: if we can't parse the condition, declare UNKNOWN
        # rather than silently SKIPping or FIRING a malformed rule.
        result.trace.append((condition_when, "UNKNOWN", f"parse error: {e}"))
        return result

    v = _eval_node(tree, tender_facts, result)
    if v is True:
        result.verdict = Verdict.FIRE
    elif v is False:
        result.verdict = Verdict.SKIP
    else:
        result.verdict = Verdict.UNKNOWN
    return result


def summarise_facts_used(condition_when: str) -> set[str]:
    """Return the set of fact-keys this condition would query. Useful
    for pre-flight: caller can warn when condition_when wants a fact
    we don't extract, before running validation."""
    out: set[str] = set()
    try:
        tree = parse(condition_when)
    except SyntaxError:
        return out

    def walk(n):
        if isinstance(n, ClauseNode):
            out.add(n.key)
        elif isinstance(n, (AndNode, OrNode)):
            for c in n.children:
                walk(c)
    walk(tree)
    return out
