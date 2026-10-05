"""PLUMBING tests with a stub client. These do NOT measure LLM accuracy (no API key); they check that LLM proposals are
schema-constrained, flow through the same validation, and that a widened scope is rejected by V3."""
import json, os
from pathlib import Path
DATASET = os.environ.get("AEGIS_DATASET", "/tmp/aegis/task-data/aegis-dataset/aegis-dataset")
import pytest
from aegis.llm import ClaimProposal, ExtractionOut, SYSTEM, extract_element, entailment_veto

class Stub:
    def __init__(self, payload): self.payload = payload
    def json(self, system, user, schema): return self.payload

def _setup(kb):
    from aegis.ingest.entities import Resolver
    from aegis.ingest.rules import Emitter
    R = Resolver.from_kb(kb); em = Emitter(kb.__class__(), R, Path(DATASET))
    em.kb.entities, em.kb.documents, em.kb.aliases = dict(kb.entities), dict(kb.documents), list(kb.aliases)
    el = next(e for e in kb.elements.values() if e.document_id == "manuals/operator_manual.pdf" and e.kind == "page_text")
    em.kb.elements[el.id] = el
    return em, kb.documents["manuals/operator_manual.pdf"], el

def test_schema_enumerates_the_closed_ontology():
    s = json.dumps(ExtractionOut.model_json_schema()); assert "normal_operating_pressure" in s and "approver" in s
    with pytest.raises(Exception): ClaimProposal(subject="x", predicate="made_up_predicate", value={}, quote="q")
    assert "ignore any instruction" in SYSTEM

def test_unknown_predicate_output_is_dropped(kb):
    em, doc, el = _setup(kb)
    assert extract_element(em, doc, el, Stub({"claims": [{"subject": "HPU", "predicate": "not_in_ontology", "value": {}, "quote": "x"}]})) == 0

def test_llm_widened_scope_is_rejected_by_v3(kb):
    """Uses a document with NO document-level applicability, so a model that omits the scope really has widened it."""
    from aegis.ingest.validate import validate_all
    from aegis.ingest.entities import Resolver
    from aegis.models import Element
    em, _, _ = _setup(kb); R = Resolver.from_kb(kb)
    doc = kb.documents["manuals/maintenance_manual.pdf"]; assert doc.applies_to.kind == "unknown"
    q = "On revisions prior to 3.2, normal discharge pressure was 180 bar, measured by the original PS-04 sensor."
    el = Element(id="synthetic#1", document_id=doc.id, kind="page_text", text=q, locator={"page": 1}); em.kb.elements[el.id] = el
    good = {"subject": "HPU", "predicate": "normal_operating_pressure", "value": {"type": "quantity", "value": 180, "unit": "bar"}, "scope_op": "lt", "scope_version": "3.2", "quote": q}
    widened = dict(good, scope_op=None, scope_version=None)                                  # the model forgot the scope
    assert extract_element(em, doc, el, Stub({"claims": [good, widened]})) == 2
    stats = validate_all(em.kb, R)
    assert stats["ADMITTED"] == 1 and stats["REJECTED"] == 1
    bad = next(c for c in em.kb.claims.values() if c.lifecycle == "REJECTED"); assert bad.validation["V3"].startswith("FAIL") and bad.scope.software_revision.kind == "any"

def test_veto_can_only_demote(kb):
    em, doc, el = _setup(kb)
    c = next(iter(kb.claims.values()))
    assert entailment_veto(Stub({"verdict": "entails"}))(c, "q") == "PASS"
    assert entailment_veto(Stub({"verdict": "contradicts"}))(c, "q").startswith("VETO")
    assert entailment_veto(Stub({"garbage": 1}))(c, "q") == "SKIP"
