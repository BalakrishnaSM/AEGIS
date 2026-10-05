"""Data contracts (architecture §3)."""
from __future__ import annotations
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field
from .scope import Scope, VerDim, AnyDim, UnknownDim, EntDim

Tier = Literal["primary", "reference", "derived", "field_observation"]       # DESCRIPTIVE
Completeness = Literal["complete", "excerpt", "references_missing"]
Lifecycle = Literal["PROPOSED", "VALIDATED", "ADMITTED", "DEMOTED", "REJECTED"]
Kind = Literal["stated", "inferred", "structural"]
Modality = Literal["fact", "requirement", "warning"]
Quality = Literal["STRONG", "REDUCED", "WEAK"]
Status = Literal["ANSWERED", "ANSWERED_SCOPED", "CONFLICTED", "PARTIAL", "UNANSWERABLE"]


class Document(BaseModel):
    id: str                                  # relative path
    sha256: str
    family: str                              # manual | ecn | reference | diagram | slides | notes | sds | screenshot | scan | config
    tier: Tier
    title: str = ""
    revision: str = ""
    applies_to: VerDim | UnknownDim = Field(default_factory=UnknownDim)
    supersedes: list[str] = Field(default_factory=list)
    derived_from: list[str] = Field(default_factory=list)
    completeness: Completeness = "complete"
    references_missing: list[str] = Field(default_factory=list)


class Element(BaseModel):
    id: str
    document_id: str
    kind: str                                # paragraph | row | cell | slide_text | json_leaf | raster_region | box_label
    text: str
    locator: dict[str, Any] = Field(default_factory=dict)   # page/bbox | sheet/row_key | slide | json_path | region
    bbox: Optional[tuple[float, float, float, float]] = None


class Evidence(BaseModel):
    id: str
    document_id: str
    element_id: str
    page: Optional[int] = None
    bbox: Optional[tuple[float, float, float, float]] = None
    locator: dict[str, Any] = Field(default_factory=dict)
    quote: str
    method: Literal["rule", "llm", "ocr", "vlm", "geometry", "transcription"]
    model_id: str = ""
    rule_id: str = ""
    source_sha256: str = ""
    raster_agreement: Literal["agree", "disagree", "n/a"] = "n/a"


class Claim(BaseModel):
    id: str
    entity_id: str
    predicate: str
    value: dict[str, Any]                    # {"type": quantity|entity|enum|text|version|date|condition|list|state|procedure|not_specified|person, ...}
    condition: Optional[dict[str, Any]] = None
    scope: Scope
    modality: Modality = "fact"
    assertion_kind: Kind = "stated"
    evidence_ids: list[str]
    doc_id: str
    tier: Tier
    lifecycle: Lifecycle = "PROPOSED"
    needs_review: bool = False
    inference: Optional[dict[str, Any]] = None
    validation: dict[str, str] = Field(default_factory=dict)   # layer -> PASS | FAIL:<why> | VETO:<why> | SKIP

    def vkey(self) -> str:
        """Canonical value identity for equality/conflict checks."""
        v = self.value
        t = v.get("type")
        if t == "quantity": return f"q:{v['value']}:{v['unit']}"
        if t == "entity": return f"e:{v['id']}"
        if t == "condition":
            q = v.get("quantity") or {}
            return f"c:{v.get('op')}:{q.get('value')}:{q.get('unit')}:{v.get('state')}"
        if t in ("enum", "text", "date", "version", "person"): return f"{t}:{str(v.get('v')).strip().lower()}"
        if t == "state": return f"s:{v.get('item')}:{str(v.get('state')).lower()}"
        if t == "procedure": return f"p:{v.get('id')}"
        if t == "list": return "l:" + "|".join(v.get("items", []))
        if t == "not_specified": return "n/s"
        return repr(sorted(v.items()))


class Relation(BaseModel):
    kind: Literal["supersedes", "version_scoped_with", "conflicts_with", "corroborates", "derived_from", "alias_of"]
    src: str
    dst: str
    independent: bool = True
    note: str = ""


class Entity(BaseModel):
    id: str
    kind: str
    canonical_name: str


class Alias(BaseModel):
    entity_id: str
    surface: str
    norm_key: str
    status: Literal["exact", "normalized", "register", "glossary", "doc_stated", "asserted", "rejected"]
    source_doc: str = ""


# ---------------------------------------------------------------------------- query side
class RequiredFact(BaseModel):
    entity_mention: Optional[str] = None
    entity_id: Optional[str] = None
    predicate: str
    entity_alternatives: list[str] = Field(default_factory=list)
    value_filter: Optional[dict[str, Any]] = None     # reverse lookup, e.g. {"op": "<", "quantity": {"value": 150, "unit": "bar"}}
    required: bool = True


class QuestionScope(BaseModel):
    software_revision: VerDim | Literal["CURRENT", "UNSPECIFIED"] = "UNSPECIFIED"
    installed_sensor: EntDim | Literal["UNSPECIFIED"] = "UNSPECIFIED"
    line: EntDim | Literal["UNSPECIFIED"] = "UNSPECIFIED"


class Analysis(BaseModel):
    question: str
    intent: str
    policy: str
    slots: list[RequiredFact]
    scope: QuestionScope
    mentions: list[dict[str, str]] = Field(default_factory=list)
    unresolved_targets: list[str] = Field(default_factory=list)
    focus_value: Optional[dict[str, Any]] = None
    analyzer: str = "rules"


class Decision(BaseModel):
    id: str
    kind: Literal["GAP", "CONFLICT", "INFERRED", "SCOPE_ASSUMPTION", "ADVISORY", "SEARCHED", "COMPLETENESS", "NOTE", "FALSE_PREMISE", "EXCLUDED"]
    payload: dict[str, Any] = Field(default_factory=dict)


class SearchRecord(BaseModel):
    slot: str
    documents_searched: int
    predicates_queried: list[str]
    n_candidates: int
    n_applicable: int


class EvidenceProfile(BaseModel):
    claim_id: str
    assertion_kind: Kind
    method: str
    validation: dict[str, str]
    raster_agreement: str
    corroboration_independent: int
    corroboration_derived: int
    tier: Tier
    scope_basis: str
    open_conflicts: int
    quality: Quality


class Sentence(BaseModel):
    text: str
    kind: Literal["SOURCE", "DERIVED"]
    claim_ids: list[str] = Field(default_factory=list)
    decision_ids: list[str] = Field(default_factory=list)


class AnswerContract(BaseModel):
    status: Status
    required_claim_ids: list[str]
    forbidden_values: list[dict[str, Any]] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    searched: list[SearchRecord] = Field(default_factory=list)
    must_label_inferred: bool = False
    branches: list[dict[str, Any]] = Field(default_factory=list)


class AnswerEnvelope(BaseModel):
    question: str
    status: Status
    sentences: list[Sentence]
    claim_ids: list[str]
    evidence: list[dict[str, Any]]
    scope_branches: list[dict[str, Any]]
    conflicts: list[dict[str, Any]]
    advisories: list[dict[str, Any]]
    gaps: list[dict[str, Any]]
    assumptions: list[dict[str, Any]]
    searched: list[SearchRecord]
    profiles: list[EvidenceProfile]
    quality: Quality | Literal["N/A"]
    contract_verified: bool
    verifier_failures: list[str] = Field(default_factory=list)
    trace: dict[str, Any] = Field(default_factory=dict)

    def text(self) -> str:
        return " ".join(s.text for s in self.sentences)
