"""
modules/validator/shacl_validator.py

SHACLValidator — alternative to the regex-only RuleVerificationEngine.

Two-stage pipeline:

    Part A — RDF EXTRACTION
    ───────────────────────
    Read the document text, extract numeric / boolean tender properties
    via regex, build an `rdflib.Graph` data graph rooted at a TenderDocument
    instance:

        ap:emdPercentage              float
        ap:pbgPercentage              float
        ap:bidValidityDays            int
        ap:estimatedValue             float
        ap:hasIntegrityPact           bool
        ap:hasPriceVariationClause    bool
        ap:hasAntiCollusionForm       bool
        ap:hasJudicialPreview         bool
        ap:hasReverseTender           bool
        ap:hasOpenTender              bool
        ap:isEProcurement             bool
        ap:isApTender                 bool
        ap:tenderType                 string

    Part B — SHACL VALIDATION
    ─────────────────────────
    Load the shapes from ontology/shacl_shapes/p1_auto.ttl, run
    pyshacl.validate(), and convert the validation report into a flat list
    of `SHACLViolation` objects.

Usage:
    v = SHACLValidator()
    violations = v.validate(open(path).read())
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel
from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, XSD


REPO = Path(__file__).resolve().parent.parent.parent
DEFAULT_SHAPES_FILE = REPO / "ontology" / "shacl_shapes" / "p1_auto.ttl"

AP = Namespace("https://procureai.in/ns#")
SH = Namespace("http://www.w3.org/ns/shacl#")


# ─── Output model ──────────────────────────────────────────────────────────

class SHACLViolation(BaseModel):
    shape_id: str
    rule_id: str
    typology_code: str | None = None
    message: str
    severity: str        # Violation / Warning / Info
    focus_node: str | None = None
    result_path: str | None = None
    value: str | None = None


# ─── RDF extraction (Part A) ────────────────────────────────────────────────

@dataclass
class _ExtractedFacts:
    emd_pct: float | None = None
    pbg_pct: float | None = None
    bid_validity_days: int | None = None
    estimated_value: float | None = None

    has_integrity_pact: bool = False
    has_price_variation_clause: bool = False
    has_anti_collusion_form: bool = False
    has_judicial_preview: bool = False
    has_reverse_tender: bool = False
    has_open_tender: bool = False
    is_e_procurement: bool = False

    is_ap_tender: bool = False
    tender_type: str = "Works"


_MD_ESCAPE = re.compile(r"\\([.,;:!?(){}\[\]<>~_*\-])")


def _clean(text: str) -> str:
    return _MD_ESCAPE.sub(r"\1", text)


def _pct_near(text: str, kw_re: str) -> float | None:
    pat = re.compile(
        rf"(?:{kw_re})[^\n]{{0,80}}?(\d+(?:\.\d+)?)\s*%"
        rf"|(\d+(?:\.\d+)?)\s*%[^\n]{{0,40}}?(?:{kw_re})",
        re.IGNORECASE,
    )
    found: list[float] = []
    for m in pat.finditer(text):
        for grp in m.groups():
            if grp:
                try:
                    found.append(float(grp))
                except ValueError:
                    continue
                break
    return min(found) if found else None


def _days_near(text: str, kw_re: str) -> int | None:
    pat = re.compile(
        rf"(?:{kw_re})[^\n]{{0,80}}?(\d{{1,4}})\s*(?:days|day)\b",
        re.IGNORECASE,
    )
    for m in pat.finditer(text):
        try:
            return int(m.group(1))
        except (ValueError, IndexError):
            continue
    return None


def _value_inr(text: str) -> float | None:
    """First labelled value: 'estimated cost / contract value / project cost'."""
    pat = re.compile(
        r"(?:estimated\s+(?:cost|value)|contract\s+value|tender\s+value|project\s+cost)"
        r"[^\n]{0,80}?(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d+)?)\s*(crore|lakh|cr|lac)?",
        re.IGNORECASE,
    )
    m = pat.search(text)
    if not m:
        return None
    raw = m.group(1).replace(",", "")
    try:
        v = float(raw)
    except ValueError:
        return None
    unit = (m.group(2) or "").lower()
    if unit in ("crore", "cr"):
        v *= 1_00_00_000
    elif unit in ("lakh", "lac"):
        v *= 1_00_000
    return v


def extract_facts(text: str) -> _ExtractedFacts:
    """Extract all the document-level tender facts the SHACL shapes require."""
    t = _clean(text)
    f = _ExtractedFacts()

    f.emd_pct           = _pct_near(t, r"emd|earnest\s+money|bid\s+security")
    f.pbg_pct           = _pct_near(t, r"performance\s+(?:guarantee|security)|pbg")
    f.bid_validity_days = _days_near(t, r"bid\s+valid|validity\s+of\s+bid|tender\s+valid")
    f.estimated_value   = _value_inr(t)

    f.has_integrity_pact         = bool(re.search(r"integrity\s+pact", t, re.I))
    f.has_anti_collusion_form    = bool(re.search(r"anti[-\s]?collusion|form\s+3N", t, re.I))
    f.has_price_variation_clause = bool(re.search(
        r"price\s+(?:adjustment|variation)|pvc|escalation\s+formula", t, re.I))
    f.has_judicial_preview       = bool(re.search(r"judicial\s+preview|hon'?ble\s+judge", t, re.I))
    f.has_reverse_tender         = bool(re.search(r"reverse\s+(?:tender|auction)", t, re.I))
    f.has_open_tender            = bool(re.search(
        r"open\s+tender|advertised\s+tender|public\s+advertisement", t, re.I))
    f.is_e_procurement           = bool(re.search(
        r"e[-\s]?procurement|apeprocurement\.gov\.in|gepnic|cppp", t, re.I))

    f.is_ap_tender = bool(re.search(
        r"andhra\s+pradesh|apeprocurement\.gov\.in|GO\s+Ms|apss|apcrda|agicl", t, re.I))

    if re.search(r"\bEPC\b|engineering\s+procurement\s+construction|turnkey|lump[-\s]?sum", t, re.I):
        f.tender_type = "EPC"
    elif re.search(r"qcbs|technical\s+proposal.*financial\s+proposal|consulting\s+services", t, re.I):
        f.tender_type = "Consultancy"
    elif re.search(r"supply\s+of|procurement\s+of|rate\s+contract", t, re.I):
        f.tender_type = "Goods"
    else:
        f.tender_type = "Works"

    return f


def build_data_graph(facts: _ExtractedFacts, doc_id: str = "tender-1") -> Graph:
    """Return an rdflib Graph containing one TenderDocument with all its facts."""
    g = Graph()
    g.bind("ap", AP)
    g.bind("xsd", XSD)
    s = URIRef(AP[doc_id])
    g.add((s, RDF.type, AP.TenderDocument))

    if facts.emd_pct is not None:
        g.add((s, AP.emdPercentage, Literal(facts.emd_pct, datatype=XSD.decimal)))
    if facts.pbg_pct is not None:
        g.add((s, AP.pbgPercentage, Literal(facts.pbg_pct, datatype=XSD.decimal)))
    if facts.bid_validity_days is not None:
        g.add((s, AP.bidValidityDays, Literal(facts.bid_validity_days, datatype=XSD.integer)))
    if facts.estimated_value is not None:
        g.add((s, AP.estimatedValue, Literal(facts.estimated_value, datatype=XSD.decimal)))

    g.add((s, AP.hasIntegrityPact,         Literal(facts.has_integrity_pact, datatype=XSD.boolean)))
    g.add((s, AP.hasPriceVariationClause,  Literal(facts.has_price_variation_clause, datatype=XSD.boolean)))
    g.add((s, AP.hasAntiCollusionForm,     Literal(facts.has_anti_collusion_form, datatype=XSD.boolean)))
    g.add((s, AP.hasJudicialPreview,       Literal(facts.has_judicial_preview, datatype=XSD.boolean)))
    g.add((s, AP.hasReverseTender,         Literal(facts.has_reverse_tender, datatype=XSD.boolean)))
    g.add((s, AP.hasOpenTender,            Literal(facts.has_open_tender, datatype=XSD.boolean)))
    g.add((s, AP.isEProcurement,           Literal(facts.is_e_procurement, datatype=XSD.boolean)))
    g.add((s, AP.isApTender,               Literal(facts.is_ap_tender, datatype=XSD.boolean)))
    g.add((s, AP.tenderType,               Literal(facts.tender_type)))

    # Sentinel — present on every TenderDocument so "minCount 1" sanity shapes pass.
    # The 22 typologies whose template is "documentArtefactPresent" check this
    # marker; without it, every such shape would always violate.
    g.add((s, AP.documentArtefactPresent,  Literal("present")))
    return g


# ─── SHACL validation (Part B) ──────────────────────────────────────────────

def _convert_validation_report(report_graph: Graph, shapes_graph: Graph) -> list[SHACLViolation]:
    """Walk the SHACL validation report and emit SHACLViolation rows.

    For each sh:result we look up the source-shape's ap:ruleId and ap:typologyCode
    in the shapes graph, then construct a clean SHACLViolation."""
    out: list[SHACLViolation] = []

    for result in report_graph.subjects(RDF.type, SH.ValidationResult):
        source_shape  = report_graph.value(result, SH.sourceShape)
        focus_node    = report_graph.value(result, SH.focusNode)
        result_path   = report_graph.value(result, SH.resultPath)
        value         = report_graph.value(result, SH.value)
        message       = report_graph.value(result, SH.resultMessage) or ""
        sev_uri       = report_graph.value(result, SH.resultSeverity)

        # Severity name
        sev = "Violation"
        if sev_uri is not None:
            sev = sev_uri.split("#")[-1] if "#" in str(sev_uri) else str(sev_uri).split("/")[-1]

        # Look up parent shape (the property shape we generated). The
        # generated shapes attach ap:ruleId on the parent NodeShape.
        rule_id = ""
        typology = ""
        shape_id = str(source_shape) if source_shape else ""

        # Walk up: find the NodeShape that has this property shape inline
        for parent in shapes_graph.subjects(SH.property, source_shape):
            rid = shapes_graph.value(parent, AP.ruleId)
            tcode = shapes_graph.value(parent, AP.typologyCode)
            if rid:
                rule_id = str(rid)
                shape_id = str(parent).split("#")[-1] if "#" in str(parent) else str(parent).split("/")[-1]
            if tcode:
                typology = str(tcode)
            break

        out.append(SHACLViolation(
            shape_id=shape_id,
            rule_id=rule_id or "(unknown)",
            typology_code=typology or None,
            message=str(message),
            severity=sev,
            focus_node=str(focus_node) if focus_node else None,
            result_path=str(result_path).split("#")[-1] if result_path else None,
            value=str(value) if value is not None else None,
        ))
    return out


class SHACLValidator:
    """Orchestrates extraction + SHACL validation + violation conversion."""

    def __init__(self, shapes_path: Path | str | None = None):
        from pyshacl import validate as _shacl_validate
        self._shacl_validate = _shacl_validate
        path = Path(shapes_path or DEFAULT_SHAPES_FILE)
        self.shapes_graph = Graph().parse(path.as_posix(), format="turtle")
        self.shapes_path = path

    def validate(self, document_text: str, doc_id: str = "tender-1") -> list[SHACLViolation]:
        facts = extract_facts(document_text)
        data_graph = build_data_graph(facts, doc_id=doc_id)

        conforms, report_graph, _txt = self._shacl_validate(
            data_graph,
            shacl_graph=self.shapes_graph,
            inference="rdfs",
            advanced=True,
            debug=False,
        )
        if conforms:
            return []
        return _convert_validation_report(report_graph, self.shapes_graph)
