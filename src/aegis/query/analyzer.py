"""Query stage 1-2: analyzer + mention resolution (architecture §6). Emits intent + RequiredFacts over the CLOSED predicate
ontology. It never answers. Rule-based here (the LLM analyzer in llm.py targets the same schema and falls back to this)."""
from __future__ import annotations
import re
from typing import Callable, Optional

from ..models import Analysis, QuestionScope, RequiredFact
from ..ontology import PREDICATES
from ..quantity import parse_quantities
from ..scope import VerDim, parse_cues
from ..ingest.entities import Resolver

HPU = "component:HPU"

def _first(ments: list[dict], kinds: set[str]) -> Optional[dict]:
    return next((m for m in ments if m["entity_id"] and m["entity_id"].split(":", 1)[0] in kinds), None)

def _qty(question: str) -> Optional[dict]:
    q = parse_quantities(question)
    return {"value": q[0][0], "unit": q[0][1]} if q else None

class Ctx:
    def __init__(self, question: str, ments: list[dict], R: Resolver):
        self.q, self.low, self.ments, self.R = question, question.lower(), ments, R
        self.res = [m for m in ments if m["entity_id"]]
    def has(self, rx: str) -> bool: return bool(re.search(rx, self.q, re.I))
    def ent(self, *kinds: str) -> Optional[dict]: return _first(self.res, set(kinds))
    def all(self, *kinds: str) -> list[str]: return [m["entity_id"] for m in self.res if m["entity_id"].split(":", 1)[0] in kinds]

def _slot(pred: str, ent: Optional[str] = None, mention: Optional[str] = None, alts: Optional[list[str]] = None, vf=None) -> RequiredFact:
    assert pred in PREDICATES, pred
    return RequiredFact(predicate=pred, entity_id=ent, entity_mention=mention, entity_alternatives=alts or [], value_filter=vf)

def _target(c: Ctx, kinds: tuple, pred: str) -> tuple[list[RequiredFact], list[str]]:
    m = c.ent(*kinds)
    if m: return [_slot(pred, m["entity_id"], m["surface"])], []
    mm = re.search(r"\b(?:of|for)\s+(?:the\s+)?(.+?)\s*\??$", c.q.strip(), re.I)
    return [_slot(pred, None, mm.group(1) if mm else None)], [mm.group(1) if mm else c.q]

# ordered: first matching rule wins. Each returns (intent, policy, slots, unresolved_targets)
RULES: list[tuple[str, Callable[[Ctx], Optional[tuple]]]] = []
def rule(name):
    def d(f): RULES.append((name, f)); return f
    return d

@rule("mtbf")
def _(c):
    if c.has(r"mean time between failures|\bMTBF\b"):
        s, u = _target(c, ("component",), "mtbf"); return "PARAMETER_QUERY", "PARAMETER", s, u

@rule("calibration_interval")
def _(c):
    if c.has(r"calibration interval|recalibration (?:interval|frequency)"):
        s, u = _target(c, ("component",), "calibration_interval"); return "PARAMETER_QUERY", "PARAMETER", s, u

@rule("max_temperature")
def _(c):
    if c.has(r"(?:maximum|max)\b.*temperature|temperature (?:limit|rating)"):
        s, u = _target(c, ("component",), "max_operating_temperature"); return "PARAMETER_QUERY", "PARAMETER", s, u

@rule("approver")
def _(c):
    if c.has(r"who approved|approved by|\bapprover\b|who signed off"):
        s, u = _target(c, ("document",), "approver"); return "FIELD_LOOKUP", "PARAMETER", s, u

@rule("supply")
def _(c):
    if c.has(r"compatib") and c.has(r"supply|volt|phase|\d+\s*V\b"):
        return "COMPATIBILITY_QUERY", "PARAMETER", [_slot("supply_voltage", "system:Aegis Series-7 HCS"), _slot("supply_phase", "system:Aegis Series-7 HCS"),
                                                     _slot("supply_compatibility", "system:Aegis Series-7 HCS")], []

@rule("topology")
def _(c):
    if c.has(r"connect(?:s|ed)? directly|directly connect") :
        m = c.ent("component"); return "DIAGRAM_TOPOLOGY", "TOPOLOGY", [_slot("connects_to_control_signal", m["entity_id"] if m else "component:PLC-03", m["surface"] if m else "controller")], []

@rule("diff")
def _(c):
    if c.has(r"introduce any component|not found in the manuals|new component|new alarm"):
        d = c.ent("document")
        e = d["entity_id"] if d else None
        return "CROSS_DOCUMENT_DIFF", "DIFF", [_slot("introduces_component", e, d["surface"] if d else None), _slot("introduces_alarm", e, d["surface"] if d else None)], ([] if d else ["document"])

@rule("artifact_tag")
def _(c):
    if c.has(r"screenshot|screen\b") and c.has(r"sensor id|tag|id appears"):
        s = c.ent("screen")
        if s: return "IDENTITY_FROM_IMAGE", "ARTIFACT", [_slot("displays_tag", s["entity_id"], s["surface"]), _slot("matches_entity", s["entity_id"], s["surface"])], []

@rule("key_mapping")
def _(c):
    m = re.search(r"\b(sensor_\w+)", c.q)
    if m or c.has(r"configuration export.*correspond|config key"):
        k = c.ent("config")
        if k: return "KEY_MAPPING", "KEY_MAPPING", [_slot("config_key_maps_to", k["entity_id"], k["surface"])], []
        return "KEY_MAPPING", "KEY_MAPPING", [_slot("config_key_maps_to", None, m.group(1) if m else None)], [m.group(1) if m else "config key"]

@rule("revision_history")
def _(c):
    if c.has(r"take effect|took effect|release date|revision history|when did software revision"):
        r = c.ent("software_revision")
        if r: return "TABLE_LOOKUP", "HISTORY", [_slot("release_date", r["entity_id"], r["surface"]), _slot("change_summary", r["entity_id"], r["surface"])], []

@rule("introduced_by")
def _(c):
    if c.has(r"which document introduced|introduced the change|what introduced"):
        comps = c.all("component")
        return "PROVENANCE_LOOKUP", "HISTORY", [_slot("introduced_by", comps[-1] if comps else None, None, alts=comps)], ([] if comps else ["component"])

@rule("identity")
def _(c):
    if c.has(r"same (?:component|sensor|part)?\s*as|the same|identical to|same thing"):
        comps = c.all("component")
        if len(comps) >= 2:
            return "IDENTITY", "IDENTITY", [_slot("is_distinct_from", comps[0], None, alts=[comps[0], comps[1]], vf={"pair": [comps[0], comps[1]]})], []

@rule("alarm_reverse")
def _(c):
    m = re.search(r"which alarm.*?(below|above|under|over)\s+(\d+)\s*bar", c.q, re.I)
    if m:
        op = "<" if m.group(1).lower() in ("below", "under") else ">"
        return "ALARM_LOOKUP_REVERSE", "PARAMETER", [_slot("alarm_trigger_condition", None, None, vf={"op": op, "quantity": {"value": float(m.group(2)), "unit": "bar"}})], []

@rule("alarm_action")
def _(c):
    a = c.ent("alarm")
    if a and c.has(r"persist|what action|required action|should .* do|respond"):
        return "PROCEDURE_LOOKUP", "SAFETY_PROCEDURE", [_slot("alarm_required_action", a["entity_id"], a["surface"])], []

@rule("alarm_meaning")
def _(c):
    a = c.ent("alarm")
    if a and c.has(r"indicate|mean|cause|what is alarm|what does"):
        return "ALARM_LOOKUP", "SAFETY_PROCEDURE", [_slot("alarm_trigger_condition", a["entity_id"], a["surface"]), _slot("alarm_possible_cause", a["entity_id"], a["surface"])], []

@rule("location")
def _(c):
    if c.has(r"location of|where is|where are|located"):
        m = c.ent("component", "alarm")
        if m: return "REGISTER_LOOKUP", "PARAMETER", [_slot("located_at", m["entity_id"], m["surface"])], []

@rule("reset")
def _(c):
    if c.has(r"\breset\b"):
        m = c.ent("component"); return "SAFETY_CONSTRAINT", "SAFETY_PROCEDURE", [_slot("reset_pressure_limit", m["entity_id"] if m else "component:PLC-03", m["surface"] if m else "controller")], []

@rule("startup")
def _(c):
    if c.has(r"before starting|before (?:you )?start|before I start|what has to be true|must be true before|startup (?:requirement|condition|interlock|check)|start-up checks?|prerequisite"):
        m = c.ent("component"); return "PRECONDITION_LIST", "SAFETY_PROCEDURE", [_slot("requires_state", m["entity_id"] if m else HPU, m["surface"] if m else "HPU")], []

@rule("applicability")
def _(c):
    if c.has(r"apply to all|only some|apply to (?:every|all)|which units|all units") and c.has(r"threshold|pressure|setpoint|\d+\s*bar"):
        return "SCOPE_QUERY", "PARAMETER", [_slot("applicability_scope", HPU, "HPU")], []

@rule("flash_point")
def _(c):
    if c.has(r"flash point|flash-point") and c.has(r"hydraulic fluid|fluid"):
        return "PARAMETER_QUERY", "PARAMETER", [_slot("flash_point", "material:HydroFluid-32", "hydraulic fluid")], []

@rule("introduced_revision")
def _(c):
    if c.has(r"which software revision.*introduc|what software revision.*introduc|revision.*introduc"):
        comps = c.all("component")
        if comps:
            return "HISTORY", "HISTORY", [_slot("requires_software_revision", comps[-1], None, alts=comps)], []
        return "HISTORY", "HISTORY", [_slot("requires_software_revision", None, None)], ["component"]

@rule("supply")
def _(c):
    if c.has(r"supply voltage|incoming.*supply|electrical supply"):
        ent = "system:Aegis Series-7 HCS"
        slots = [_slot("supply_voltage", ent)]
        if c.has(r"phase|phases"):
            slots.append(_slot("supply_phase", ent))
        if c.has(r"compatib"):
            slots.append(_slot("supply_compatibility", ent))
        return "COMPATIBILITY_QUERY", "PARAMETER", slots, []

@rule("alarm_action_paraphrase")
def _(c):
    a = c.ent("alarm")
    if a and c.has(r"stays? active|remains active|clears? by itself|clears? automatically|longer than|more than"):
        return "PROCEDURE_LOOKUP", "SAFETY_PROCEDURE", [_slot("alarm_required_action", a["entity_id"], a["surface"])], []

@rule("pressure")
def _(c):
    if c.has(r"operating pressure|pressure (?:limit|threshold|setpoint)|discharge pressure|normal pressure|pressure.*(?:before|prior)|normal running|normally run|should .* reach.*pressure|reach.*during normal"):
        m = c.ent("component", "system")
        ent = m["entity_id"] if m and m["entity_id"].startswith("component:HPU") else HPU
        slots = [_slot("normal_operating_pressure", ent, "HPU")]
        if c.has(r"under what conditions|conditions|when does"): slots.append(_slot("applicability_scope", ent, "HPU"))
        if c.has(r"what changed|changed it|why did .* change|what caused"): slots.append(_slot("changed_by", ent, "HPU"))
        return "PARAMETER_QUERY_WITH_SCOPE", "PARAMETER", slots, []

def analyze(question: str, R: Resolver) -> Analysis:
    ments = R.find_mentions(question)
    c = Ctx(question, ments, R)
    cues = parse_cues(question)
    if cues: rev = cues[0]
    elif re.search(r"\b(current|currently|now|latest|today)\b", question, re.I): rev = "CURRENT"
    else: rev = "UNSPECIFIED"
    scope = QuestionScope(software_revision=rev)
    for name, fn in RULES:
        out = fn(c)
        if out:
            intent, policy, slots, unresolved = out
            return Analysis(question=question, intent=intent, policy=policy, slots=slots, scope=scope,
                            mentions=[{k: m[k] for k in ("surface", "entity_id", "status")} for m in ments],
                            unresolved_targets=unresolved, focus_value=_qty(question), analyzer=f"rules:{name}")
    return Analysis(question=question, intent="UNKNOWN", policy="PARAMETER", slots=[], scope=scope,
                    mentions=[{k: m[k] for k in ("surface", "entity_id", "status")} for m in ments], analyzer="rules:none")
