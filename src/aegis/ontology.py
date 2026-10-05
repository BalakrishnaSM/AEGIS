"""Closed predicate ontology (architecture §3) with domain/range types (used by V5) and the per-intent
admissibility policy (architecture §6.5). Tier gates ADMISSIBILITY categorically; it never ranks or picks winners."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

@dataclass(frozen=True)
class PredSpec:
    kinds: frozenset            # allowed subject entity kinds
    vtypes: frozenset           # allowed value types
    dim: Optional[str] = None   # required dimension when value is a quantity

def P(kinds, vtypes, dim=None):
    return PredSpec(frozenset(kinds.split("|")), frozenset(vtypes.split("|")), dim)

PREDICATES: dict[str, PredSpec] = {
    "requires_state": P("component|system", "state"),
    "normal_operating_pressure": P("component|system", "quantity", "pressure"),
    "observed_pressure": P("component", "quantity", "pressure"),
    "applicability_scope": P("component|system", "text"),            # derived at query time
    "alarm_trigger_condition": P("alarm", "condition"),
    "alarm_possible_cause": P("alarm", "text"),
    "alarm_required_action": P("alarm", "procedure|text"),
    "panel_indication": P("alarm", "enum"),
    "alarm_note": P("alarm", "text"),
    "evaluated_against": P("alarm", "text"),
    "prohibited_action": P("alarm|component", "text"),
    "procedure_steps": P("procedure", "list"),
    "reset_pressure_limit": P("component", "quantity", "pressure"),
    "reset_min_pressure": P("component", "not_specified|quantity", "pressure"),
    "hazard": P("component|alarm", "text"),
    "superseded_by": P("component", "entity"),
    "is_distinct_from": P("component", "entity"),
    "introduced_by": P("component", "entity"),
    "changed_by": P("component|system", "entity"),
    "active_sensor": P("component", "entity"),
    "corresponds_to": P("component", "entity"),
    "not_covered_in": P("component", "entity"),
    "connects_to_control_signal": P("component", "entity"),
    "connects_to_fluid_path": P("component", "entity"),
    "connects_to_reservoir_interconnect": P("component", "entity"),
    "located_at": P("component", "text"),
    "lifecycle_status": P("component|alarm", "enum"),
    "common_name": P("component|alarm", "text"),
    "requires_software_revision": P("component", "version"),
    "installation_restriction": P("component", "text"),
    "supported_on": P("component", "text"),
    "introduces_component": P("document", "entity"),               # derived (mention diff)
    "introduces_alarm": P("document", "entity"),                   # derived (mention diff)
    "displays_tag": P("screen", "text"),
    "matches_entity": P("screen", "entity"),
    "release_date": P("software_revision", "date"),
    "release_status": P("software_revision", "enum"),
    "change_summary": P("software_revision", "text"),
    "config_key_value": P("config", "quantity|text|number"),
    "config_key_maps_to": P("config", "text"),                       # inferred only
    "max_operating_temperature": P("component", "quantity", "temperature"),
    "calibration_interval": P("component", "not_specified|quantity", "time"),
    "approver": P("document", "person"),
    "mtbf": P("component", "quantity", "time"),
    "supply_voltage": P("component|system", "quantity|text"),
    "supply_phase": P("component|system", "text"),
    "supply_compatibility": P("component|system", "text"),
    "flash_point": P("material", "quantity", "temperature"),
    "pour_point": P("material", "quantity", "temperature"),
    "last_calibrated": P("component", "date"),
    "calibration_reference": P("component", "quantity", "pressure"),
    "performed_by": P("component", "person"),
    "observed_firmware": P("component", "version"),
    "signal_type": P("component", "text"),
    "change_rationale": P("component|system|document", "text"),
    "change_reason": P("component|system|document", "text"),
    "effective_from": P("document", "version|text"),
    "document_title": P("document", "text"),
    "document_status": P("document", "enum"),
    "references": P("document", "text"),
    "definition": P("component|alarm|system|software_revision", "text"),
}

# Advisory routing: a field-observation claim of predicate X is surfaced as an advisory for slots of predicate Y.
FAMILY = {"observed_pressure": "normal_operating_pressure"}

@dataclass(frozen=True)
class Policy:
    allowed_kinds: frozenset        # assertion kinds that may be answer-bearing
    sole_tiers: frozenset           # tiers that may be sole support
    supporting_tiers: frozenset     # tiers that may only corroborate (never cover a slot alone)
    scope_filter: bool = True       # False: the artifact itself is the subject (screenshots), instance dims are not filtered

def pol(kinds, sole="primary|reference", sup="derived", scope_filter=True):
    return Policy(frozenset(kinds.split("|")), frozenset(sole.split("|")), frozenset(x for x in sup.split("|") if x), scope_filter)

POLICY = {
    "SAFETY_PROCEDURE": pol("stated|structural"),
    "PARAMETER": pol("stated|structural"),
    "HISTORY": pol("stated|structural"),
    "TOPOLOGY": pol("structural", sole="primary|reference", sup=""),
    "KEY_MAPPING": pol("stated|inferred|structural"),
    "ARTIFACT": pol("stated|structural", sole="primary|reference|field_observation", scope_filter=False),
    "IDENTITY": pol("stated|structural|inferred"),
    "DIFF": pol("stated|structural", sole="primary|reference|derived", sup=""),
    "PRESENCE": pol("stated|structural"),
}
# field_observation is never in sole/supporting for any family except ARTIFACT (the artifact is the subject).

# Predicates with exactly one true value per (entity[, item]) in a given scope: differing values on OVERLAP => conflict.
SINGLE_VALUED = {"normal_operating_pressure", "reset_pressure_limit", "release_date", "release_status", "located_at", "lifecycle_status",
                 "flash_point", "pour_point", "config_key_value", "displays_tag", "alarm_trigger_condition", "panel_indication",
                 "document_status", "observed_pressure", "requires_state"}

# Predicates that state an EVENT (a transition), true of questions about either side of it: exempt from revision scope filtering.
EVENT_PREDICATES = {"changed_by", "introduced_by", "release_date", "change_summary", "release_status", "document_title", "document_status", "effective_from", "references"}
