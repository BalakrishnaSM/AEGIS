"""Stages B1/B2(rule form): deterministic extraction (architecture §4.B).

* Structured extractors: register, revision history, config, glossary, alarm table, slide table.
* Sentence-pattern library: applies to PROSE in ANY document (not per-file code), so an injected or modified
  document is treated identically. Every claim carries a verbatim quote (a substring of its element).
LLM extraction (llm.py) targets the same Spec contract; here the deterministic path is the one exercised."""
from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from ..models import Claim, Document, Element, Evidence
from ..quantity import normalize
from ..scope import (AnyDim, EntDim, Scope, UnknownDim, VerDim, parse_cues, ver_eq, ver_ge, ver_lt)
from ..store.db import KnowledgeBase
from .adapters import locate_bbox, norm
from .entities import Resolver

# ----------------------------------------------------------------------------- value constructors
def Q(v: float, unit: str) -> dict:
    nv, nu = normalize(float(v), unit); return {"type": "quantity", "value": nv, "unit": nu, "raw": float(v), "raw_unit": unit}
def E(eid: str, **kw) -> dict: return {"type": "entity", "id": eid, **kw}
def S(item: str, state: str) -> dict: return {"type": "state", "item": item, "state": state}
def T(text: str) -> dict: return {"type": "text", "v": text}
def V(v: str) -> dict: return {"type": "version", "v": v}
def D(v: str) -> dict: return {"type": "date", "v": v}
def EN(v: str) -> dict: return {"type": "enum", "v": v}
def L(items: list[str]) -> dict: return {"type": "list", "items": items}
def PROC(pid: str) -> dict: return {"type": "procedure", "id": pid}
def NS() -> dict: return {"type": "not_specified"}
def PERSON(n: str) -> dict: return {"type": "person", "v": n}
def C(op: str, q: dict, **kw) -> dict: return {"type": "condition", "op": op, "quantity": q, **kw}

def strip_parens(s: str) -> str:
    return re.sub(r"\([^)]*\)", " ", s)

# ----------------------------------------------------------------------------- scope assignment (architecture §5.3)
def assign_scope(doc: Document, cue_text: str, *, rev: Optional[VerDim | UnknownDim] = None, basis: Optional[str] = None,
                 sensor: Optional[list[str]] = None, line: Optional[list[str]] = None, cue_i: int = 0, instance: bool = False, use_cues: bool = True) -> Scope:
    cues = parse_cues(cue_text) if use_cues else []          # LLM-proposed claims keep THEIR OWN scope so V3 can judge it (never silently repaired)
    if rev is not None: r, b = rev, (basis or "section_cue")
    elif cues: r, b = cues[min(cue_i, len(cues) - 1)], "explicit_cue"
    elif isinstance(doc.applies_to, VerDim): r, b = doc.applies_to, "document_applies_to"
    elif instance or doc.tier == "field_observation": r, b = UnknownDim(), "unknown" if doc.tier == "field_observation" else "instance_observation"
    else: r, b = AnyDim(), "document_default"
    hist = bool(b == "explicit_cue" and isinstance(doc.applies_to, VerDim) and isinstance(r, VerDim) and r.intersect(doc.applies_to) is None)
    unk = doc.tier == "field_observation"
    return Scope(software_revision=r,
                 installed_sensor=EntDim(ids=sensor) if sensor else (UnknownDim() if unk else AnyDim()),
                 line=EntDim(ids=line) if line else AnyDim(),
                 scope_basis=b, historical_reference=hist)

# ----------------------------------------------------------------------------- emitter
class Emitter:
    def __init__(self, kb: KnowledgeBase, R: Resolver, root: Path):
        self.kb, self.R, self.root = kb, R, root
        self.nc = self.ne = 0

    def evidence(self, doc: Document, el: Element, quote: str, method: str, rule_id: str, agree: str = "n/a") -> Evidence:
        self.ne += 1
        page = el.locator.get("page")
        bbox = locate_bbox(self.root, doc.id, page, quote) if (el.kind == "page_text" and page and doc.id.endswith(".pdf")) else el.bbox
        ev = Evidence(id=f"E{self.ne:04d}", document_id=doc.id, element_id=el.id, page=page, bbox=bbox, locator=dict(el.locator, **{}),
                      quote=quote, method=method, rule_id=rule_id, source_sha256=doc.sha256, raster_agreement=agree)
        ev.locator.pop("cells", None)
        self.kb.evidence[ev.id] = ev
        return ev

    def emit(self, doc: Document, el: Element, quote: str, entity: str, predicate: str, value: dict, *, rule_id: str,
             rev=None, basis=None, sensor=None, line=None, cue_i=0, instance=False, scope_span: Optional[str] = None,
             modality="fact", kind="stated", condition=None, extra: Optional[list[tuple[Element, str]]] = None,
             method="rule", agree="n/a") -> Optional[Claim]:
        if quote not in el.text:
            self.kb.audit.append(("-", "emit", "SKIP", f"{rule_id}: quote not in element {el.id}")); return None
        cue_text = scope_span if scope_span is not None else strip_parens(quote)
        scope = assign_scope(doc, cue_text, rev=rev, basis=basis, sensor=sensor, line=line, cue_i=cue_i, instance=instance, use_cues=(method != "llm"))
        self.R.ensure_entity(entity)
        evs = [self.evidence(doc, el, quote, method, rule_id, agree)]
        for el2, q2 in (extra or []): evs.append(self.evidence(doc, el2, q2, method, rule_id, agree))
        self.nc += 1
        c = Claim(id=f"C{self.nc:04d}", entity_id=entity, predicate=predicate, value=value, condition=condition, scope=scope,
                  modality=modality, assertion_kind=kind, evidence_ids=[e.id for e in evs], doc_id=doc.id, tier=doc.tier)
        c.validation["scope_span"] = scope_span if scope_span is not None else "<quote minus parentheticals>"
        self.kb.claims[c.id] = c
        return c

# ----------------------------------------------------------------------------- sentence-pattern rule library
@dataclass
class Rule:
    id: str
    rx: re.Pattern
    fn: Callable
    families: Optional[tuple] = None

RULES: list[Rule] = []
def rule(rid: str, pattern: str, flags: int = 0, families: Optional[tuple] = None):
    def deco(fn): RULES.append(Rule(rid, re.compile(pattern, flags), fn, families)); return fn
    return deco

PS = r"(PS-\d+[A-Z]?)"
VER = r"(\d+(?:\.\d+)*)"
HPU = "component:HPU"
_SENT_END = re.compile(r"[.!?](?=\s|$)")

@dataclass
class Ctx:
    em: Emitter; doc: Document; el: Element; m: re.Match; quote: str
    def eid(self, s: str) -> Optional[str]: return self.em.R.eid(s)
    @property
    def doc_entity(self) -> Optional[str]:
        mm = re.search(r"ECN Number:\s*(ECN-\d+)", " ".join(e.text for e in self.em.kb.elements.values() if e.document_id == self.doc.id))
        return self.em.R.ensure_entity(f"document:{mm.group(1)}").id if mm else None
    def eff_lo(self) -> Optional[str]: return self.doc.applies_to.lo if isinstance(self.doc.applies_to, VerDim) else None

def sp(entity, predicate, value, **kw): return dict(entity=entity, predicate=predicate, value=value, **kw)

@rule("R-PRESS-NOW", rf"Normal (?:HPU )?discharge pressure is (\d+) bar(?:, as measured by (?:pressure sensor )?{PS})?")
def _(m, c):
    s = [c.eid(m.group(2))] if m.group(2) else None
    return [sp(HPU, "normal_operating_pressure", Q(m.group(1), "bar"), sensor=s)]

@rule("R-PRESS-WAS", rf"On revisions prior to {VER}, normal discharge pressure was (\d+) bar, measured by the original {PS} sensor")
def _(m, c): return [sp(HPU, "normal_operating_pressure", Q(m.group(2), "bar"), sensor=[c.eid(m.group(3))])]

@rule("R-PRESS-REACH", rf"[Vv]erify (?:discharge )?pressure(?:, as reported by {PS},)? reaches (\d+) bar\.(?: This is the normal operating pressure for software revision {VER} and later)?")
def _(m, c):
    s = [c.eid(m.group(1))] if m.group(1) else None
    return [sp(HPU, "normal_operating_pressure", Q(m.group(2), "bar"), sensor=s)]

@rule("R-PRESS-REVISED", r"the normal HPU discharge pressure setpoint is revised from (\d+) bar to (\d+) bar", families=("ecn",))
def _(m, c):
    lo, de = c.eff_lo(), c.doc_entity; out = []
    out.append(sp(HPU, "normal_operating_pressure", Q(m.group(2), "bar")))                 # new value: document effective scope
    if lo: out.append(sp(HPU, "normal_operating_pressure", Q(m.group(1), "bar"), rev=ver_lt(lo), basis="document_applies_to"))
    if de: out.append(sp(HPU, "changed_by", E(de)))
    return out

@rule("R-SUPERSEDED-ASOF", rf"As of software revision {VER}, {PS} is superseded by {PS}")
def _(m, c): return [sp(c.eid(m.group(2)), "superseded_by", E(c.eid(m.group(3))))]

@rule("R-REPLACED-EFF", rf"Effective software revision {VER}, pressure sensor {PS} is replaced by pressure sensor {PS}")
def _(m, c):
    out = [sp(c.eid(m.group(2)), "superseded_by", E(c.eid(m.group(3))))]
    if c.doc_entity: out.append(sp(c.eid(m.group(3)), "introduced_by", E(c.doc_entity)))
    return out

@rule("R-NOT-FFF", rf"{PS} uses an updated signal conditioning circuit and is not a form-fit-function replacement for {PS}")
def _(m, c): return [sp(c.eid(m.group(1)), "is_distinct_from", E(c.eid(m.group(2)), relation="not_form_fit_function"))]

@rule("R-FW-REQ", rf"controller firmware must be at revision {VER} or later to read {PS} correctly")
def _(m, c): return [sp(c.eid(m.group(2)), "requires_software_revision", V(m.group(1)))]

@rule("R-SUPPORTED", rf"{PS} remains installed and supported on units running software revisions prior to {VER}")
def _(m, c): return [sp(c.eid(m.group(1)), "supported_on", T("installed and supported"))]

@rule("R-NOINSTALL", rf"Do not install {PS} on a controller running firmware older than {VER}")
def _(m, c): return [sp(c.eid(m.group(1)), "installation_restriction", T("do not install"), modality="requirement")]

@rule("R-RATIONALE", r"the higher setpoint improves press cycle time on Series-7 units manufactured after (\d{4})")
def _(m, c): return [sp(HPU, "change_rationale", T("improves press cycle time on Series-7 units manufactured after " + m.group(1)))]

@rule("R-REASON", rf"{PS}'s signal drift exceeded tolerance after approximately (\d+) months")
def _(m, c): return [sp(c.eid(m.group(1)), "change_reason", T(f"signal drift exceeded tolerance after approximately {m.group(2)} months"))]

@rule("R-ACTIVE-SENSOR", rf"\({PS}\s+(?:on revisions?\s+)?prior to\s+(?:revision\s+)?{VER},\s*{PS}\s+on revisions?\s+{VER}\s+and later\)")
def _(m, c):
    q = m.group(0); a, b = q.split(",", 1)
    return [sp(HPU, "active_sensor", E(c.eid(m.group(1))), scope_span=a),
            sp(HPU, "active_sensor", E(c.eid(m.group(3))), scope_span=b)]

@rule("R-RESET-LIMIT", r"Do (?:not|NOT) reset the (PLC-03) controller while hydraulic pressure is above (\d+) bar")
def _(m, c):
    q = Q(m.group(2), "bar")
    return [sp(c.eid(m.group(1)), "reset_pressure_limit", q, condition=C(">", q), modality="requirement")]

@rule("R-RESET-PERMIT", r"Controller reset is only permitted when pressure, as reported by the active pressure sensor \([^)]*\), reads below (\d+) bar")
def _(m, c):
    q = Q(m.group(1), "bar")
    return [sp(c.eid("PLC-03"), "reset_pressure_limit", q, condition=C("<", q), modality="requirement")]

@rule("R-RESET-HAZ1", r"Resetting the controller under pressure can cause an uncommanded valve transition")
def _(m, c): return [sp(c.eid("PLC-03"), "hazard", T("reset under pressure can cause an uncommanded valve transition"))]

@rule("R-RESET-HAZ2", rf"if the circuit is still pressurized, this can produce a momentary uncommanded actuation of Isolation Valve (IV-21)")
def _(m, c): return [sp(c.eid("PLC-03"), "hazard", T("reset under pressure can cause an uncommanded valve transition"))]

@rule("R-A17-PERSIST", rf"[Ii]f alarm (A\d+) persists for more than (\d+) seconds, execute Shutdown Procedure {VER}")
def _(m, c):
    return [sp(c.eid(m.group(1)), "alarm_required_action", PROC(f"procedure:{m.group(3)}"),
               condition=C(">", Q(m.group(2), "s"), subject="persistence"), modality="requirement")]

@rule("R-A17-APPLIES", rf"Shutdown Procedure {VER} applies only when (A\d+) persists beyond (\d+) seconds")
def _(m, c):
    return [sp(c.eid(m.group(2)), "alarm_required_action", PROC(f"procedure:{m.group(1)}"),
               condition=C(">", Q(m.group(3), "s"), subject="persistence"), modality="requirement")]

@rule("R-PROC-STEPS", rf"Shutdown Procedure {VER}: (.+?)\.(?=\s|$)")
def _(m, c):
    steps = [s.strip() for s in re.split(r",\s*(?:then\s+)?", m.group(2)) if s.strip()]
    pid = f"procedure:{m.group(1)}"; c.em.R.ensure_entity(pid, f"Shutdown Procedure {m.group(1)}")
    return [sp(pid, "procedure_steps", L(steps))]

@rule("R-NO-SILENCE", r"Do not attempt to clear a persistent (A\d+) by silencing the alarm alone")
def _(m, c): return [sp(c.eid(m.group(1)), "prohibited_action", T("silence the alarm alone"), modality="warning")]

@rule("R-TRANSIENT-MM", r"Alarm (A\d+) \([^)]*\) may clear on its own within a few seconds if it was triggered by a transient pressure dip")
def _(m, c): return [sp(c.eid(m.group(1)), "alarm_note", T("a transient occurrence may clear on its own and does not require the shutdown procedure"))]

@rule("R-TRANSIENT-AL", rf"A transient (A\d+) that clears on its own during normal valve transition does not require Procedure {VER}")
def _(m, c): return [sp(c.eid(m.group(1)), "alarm_note", T("a transient occurrence may clear on its own and does not require the shutdown procedure"))]

@rule("R-EVAL-AL", rf"the condition for (A\d+) referenced sensor {PS} by name\. Per (ECN-\d+), the condition is evaluated against whichever pressure sensor is active for the installed software revision")
def _(m, c): return [sp(c.eid(m.group(1)), "evaluated_against", T("the pressure sensor active for the installed software revision"))]

@rule("R-EVAL-ECN", r"Alarm (A\d+)'s low-pressure condition is evaluated against whichever pressure sensor is active for the installed software revision", families=("ecn",))
def _(m, c): return [sp(c.eid(m.group(1)), "evaluated_against", T("the pressure sensor active for the installed software revision"))]

@rule("R-UNCHANGED", r"The (\d+) bar and (\d+) bar thresholds shown above are unchanged by (ECN-\d+)")
def _(m, c):
    return [sp(c.eid("A17"), "alarm_note", T(f"{m.group(1)} bar threshold unchanged by {m.group(3)}")),
            sp(c.eid("A18"), "alarm_note", T(f"{m.group(2)} bar threshold unchanged by {m.group(3)}"))]

@rule("R-ECN-TITLE", r"ECN Number:\s*(ECN-\d+)\s+Title:\s*(.+?)\s+(?:Effective:|References:)", families=("ecn",))
def _(m, c):
    de = c.em.R.ensure_entity(f"document:{m.group(1)}", m.group(1)).id
    c.em.R.add_alias(de, m.group(1), "register", c.doc.id)
    return [sp(de, "document_title", T(m.group(2)))]

@rule("R-ECN-EFFECTIVE", r"Effective:\s*(.+?)\s+(?:Affected Assembly:|Status:|References:)", families=("ecn",))
def _(m, c):
    de = c.doc_entity; txt = m.group(1)
    if not de: return []
    return [sp(de, "effective_from", V(parse_cues(m.group(0))[0].lo) if parse_cues(m.group(0)) else T(txt))]

@rule("R-ECN-STATUS", r"Status:\s*(Released|Draft|Superseded|Withdrawn)", families=("ecn",))
def _(m, c): return [sp(c.doc_entity, "document_status", EN(m.group(1)))] if c.doc_entity else []

@rule("R-ECN-REFS", r"References:\s*(ECN-\d+)", families=("ecn",))
def _(m, c): return [sp(c.doc_entity, "references", T(m.group(1)))] if c.doc_entity else []

@rule("R-ECN-DOCONLY", r"This is a documentation correction only", families=("ecn",))
def _(m, c): return [sp(c.doc_entity, "change_rationale", T("documentation correction only"))] if c.doc_entity else []

@rule("R-START-FLUID", r"[Hh]ydraulic fluid level is within the normal band(?: on the sight glass)?")
def _(m, c): return [sp(HPU, "requires_state", S("hydraulic_fluid_level", "within normal band"), modality="requirement")]

@rule("R-START-IV", r"[Ii]solation valve (IV-21) is (?:OPEN|open)")
def _(m, c): return [sp(HPU, "requires_state", S("component:IV-21", "OPEN"), modality="requirement")]

@rule("R-START-ESTOP", r"(?:The )?[Ee]mergency stop(?: circuit)? is (?:RESET|reset)")
def _(m, c): return [sp(HPU, "requires_state", S("estop_circuit", "RESET"), modality="requirement")]

@rule("R-START-PANEL", r"Do not (?:start|operate) the (?:Hydraulic Power Unit|unit|Hydraulic Power Pack) with(?: the)?(?: maintenance access)? panel removed")
def _(m, c): return [sp(HPU, "requires_state", S("maintenance_access_panel", "INSTALLED"), modality="warning")]

@rule("R-ALIAS-OM", r"the Hydraulic Power Unit may also be referred to as the (Hydraulic Unit) or the (HP unit)")
def _(m, c): return [dict(alias=(HPU, m.group(1), "doc_stated")), dict(alias=(HPU, m.group(2), "doc_stated"))]

@rule("R-ALIAS-MM", r"The (Hydraulic Power Pack) \(HPU\)")
def _(m, c): return [dict(alias=(HPU, m.group(1), "doc_stated"))]

@rule("R-ALIAS-SLIDE", r'call it the "([^"]+)" or just "([^"]+)"')
def _(m, c): return [dict(alias=(HPU, m.group(1), "asserted")), dict(alias=(HPU, m.group(2), "rejected"))]

@rule("R-AUX-NOTE", r'"(Auxiliary Reservoir)" is a (Line 4/5)-specific configuration detail and is not covered in the standard (Operator Manual)')
def _(m, c):
    eid = c.em.R.ensure_entity("component:Auxiliary Reservoir", "Auxiliary Reservoir").id
    c.em.R.add_alias(eid, "Auxiliary Reservoir", "exact", c.doc.id)
    om = c.eid("Operator Manual")
    return [sp(eid, "not_covered_in", E(om or "document:AEG-OM-700"), line=["line:4", "line:5"])]

@rule("R-RES-MAIN", r"pulls fluid from the (main reservoir)", re.I)
def _(m, c):
    eid = c.em.R.ensure_entity("component:Main Reservoir", "Main Reservoir").id
    c.em.R.add_alias(eid, "Main Reservoir", "exact", c.doc.id); return []

@rule("R-NOTE-175", r"read about (\d+) bar during a normal run", families=("notes",))
def _(m, c): return [sp(HPU, "observed_pressure", Q(m.group(1), "bar"))]

@rule("R-SDS-FLASH", r"Flash point \([^)]*\): approximately (\d+)\s*°C", families=("sds",))
def _(m, c):
    e = c.em.R.ensure_entity("material:HydroFluid-32", "Aegis HydroFluid-32").id
    return [sp(e, "flash_point", Q(m.group(1), "°C"))]

@rule("R-SDS-POUR", r"Pour point: (-?\d+)\s*°C", families=("sds",))
def _(m, c):
    e = c.em.R.ensure_entity("material:HydroFluid-32", "Aegis HydroFluid-32").id
    return [sp(e, "pour_point", Q(m.group(1), "°C"))]

@rule("R-WIRING", rf"the field device wired at Connector (J-\d+) / Terminal Block (TB-\d+) is the HPU discharge pressure transducer described elsewhere as {PS}")
def _(m, c): return [sp(c.eid(m.group(2)), "corresponds_to", E(c.eid(m.group(3))))]

# ----------------------------------------------------------------------------- driver for prose rules
def split_quote(text: str, m: re.Match) -> str:
    end = _SENT_END.search(text, max(m.end() - 1, m.start()))
    return text[m.start(): end.end() if end else len(text)]

def apply_prose_rules(em: Emitter, doc: Document, el: Element) -> int:
    if el.kind not in ("page_text", "paragraph", "slide_text"): return 0
    n = 0
    for r in RULES:
        if r.families and doc.family not in r.families: continue
        for m in r.rx.finditer(el.text):
            quote = split_quote(el.text, m)
            ctx = Ctx(em, doc, el, m, quote)
            try: specs = r.fn(m, ctx)
            except Exception as ex:
                em.kb.audit.append(("-", "rule", "ERROR", f"{r.id}: {ex}")); continue
            for s in specs:
                if "alias" in s:
                    eid, surf, st = s["alias"]; em.R.add_alias(eid, surf, st, doc.id); continue
                if not s["entity"] or (s["value"].get("type") == "entity" and not s["value"].get("id")):
                    em.kb.audit.append(("-", "rule", "SKIP", f"{r.id}: unresolved entity")); continue
                s = dict(s); ent, pred, val = s.pop("entity"), s.pop("predicate"), s.pop("value")
                if em.emit(doc, el, quote, ent, pred, val, rule_id=r.id, **s): n += 1
    return n

# ----------------------------------------------------------------------------- structured extractors
def _cell_quote(el: Element, hdr: str) -> str:
    return f"{hdr}: {el.locator['cells'][hdr]}" if "cells" in el.locator and isinstance(el.locator["cells"], dict) else ""

def seed_register(em: Emitter, doc: Document, els: list[Element]) -> None:
    """Entities + aliases from the component register (run BEFORE other extraction so IDs resolve)."""
    for el in els:
        cells = el.locator.get("cells")
        if el.kind != "row" or not isinstance(cells, dict) or "Component ID (as printed)" not in cells: continue
        cid = cells["Component ID (as printed)"]
        eid = ("alarm:" if re.fullmatch(r"A\d+", cid) else "component:") + cid
        name = re.sub(r"^Alarm:\s*", "", cells.get("Common Name", cid))
        em.R.ensure_entity(eid, name)
        em.R.add_alias(eid, cid, "register", doc.id)
        if cells.get("Common Name"): em.R.add_alias(eid, cells["Common Name"], "register", doc.id)
        for a in cells.get("Known Aliases", "").split(";"): em.R.add_alias(eid, a.strip(), "register", doc.id)

def seed_glossary(em: Emitter, doc: Document, els: list[Element]) -> None:
    for el in els:
        if el.kind != "row" or not isinstance(el.locator.get("cells"), list) or len(el.locator["cells"]) != 2: continue
        term, defn = el.locator["cells"]
        mm = re.search(r"^(.*?)\s*\(([^)]+)\)$", term)
        forms = [term] + ([mm.group(1), mm.group(2)] if mm else [])
        target = next((em.R.eid(f) for f in forms if em.R.eid(f)), None)
        if not target: continue
        for f in forms: em.R.add_alias(target, f, "glossary", doc.id)

def extract_register(em, doc, els):
    for el in els:
        cells = el.locator.get("cells")
        if el.kind != "row" or not isinstance(cells, dict) or "Component ID (as printed)" not in cells: continue
        cid = cells["Component ID (as printed)"]; eid = em.R.eid(cid)
        if not eid: continue
        if cells.get("Common Name"):
            em.emit(doc, el, _cell_quote(el, "Common Name"), eid, "common_name", T(re.sub(r"^Alarm:\s*", "", cells["Common Name"])), rule_id="S-REG-NAME")
        if cells.get("Location") and "N/A" not in cells["Location"]:
            em.emit(doc, el, _cell_quote(el, "Location"), eid, "located_at", T(cells["Location"]), rule_id="S-REG-LOC")
        if cells.get("Status"):
            em.emit(doc, el, _cell_quote(el, "Status"), eid, "lifecycle_status", EN(cells["Status"]), rule_id="S-REG-STATUS")
        note = cells.get("Notes", ""); nq = _cell_quote(el, "Notes")
        if not note: continue
        e = lambda s: em.R.eid(s)
        if m := re.search(rf"Superseded by {PS} effective software rev {VER}", note):
            em.emit(doc, el, nq, eid, "superseded_by", E(e(m.group(1))), rule_id="S-REG-NOTE", scope_span=m.group(0))
        if m := re.search(rf"Installed per (ECN-\d+)\. Requires controller firmware >= {VER}", note):
            de = em.R.ensure_entity(f"document:{m.group(1)}", m.group(1)).id; em.R.add_alias(de, m.group(1), "register", doc.id)
            em.emit(doc, el, nq, eid, "introduced_by", E(de), rule_id="S-REG-NOTE", scope_span=m.group(0))
            em.emit(doc, el, nq, eid, "requires_software_revision", V(m.group(2)), rule_id="S-REG-NOTE", scope_span=m.group(0))
        if re.search(r"Must be OPEN prior to HPU startup", note):
            em.emit(doc, el, nq, HPU, "requires_state", S(eid, "OPEN"), rule_id="S-REG-NOTE", modality="requirement")
        if "NOT related to the HPU discharge circuit" in note:
            for other in ("PS-04", "PS-04A"):
                em.emit(doc, el, nq, eid, "is_distinct_from", E(e(other)), rule_id="S-REG-NOTE")
        if m := re.search(rf"corresponds to the {PS} signal loop", note):
            em.emit(doc, el, nq, eid, "corresponds_to", E(e(m.group(1))), rule_id="S-REG-NOTE")
        if m := re.search(r"(\d+)V incoming disconnect", note):
            em.emit(doc, el, nq, eid, "supply_voltage", Q(m.group(1), "V"), rule_id="S-REG-NOTE")
        if m := re.search(r"(\d+):(\d+)V control power", note):
            em.emit(doc, el, nq, eid, "supply_voltage", T(f"{m.group(1)}:{m.group(2)} V control power"), rule_id="S-REG-NOTE")

def extract_revision_history(em, doc, els):
    for el in els:
        c = el.locator.get("cells")
        if el.kind != "row" or not isinstance(c, dict) or "Software Revision" not in c: continue
        ver = c["Software Revision"]; eid = em.R.ensure_entity(f"software_revision:{ver}", f"Software revision {ver}").id
        em.R.add_alias(eid, f"software revision {ver}", "register", doc.id)
        dq = _cell_quote(el, "Release Date")
        if m := re.search(r"\d{4}-\d{2}-\d{2}", c["Release Date"]):
            em.emit(doc, el, dq, eid, "release_date", D(m.group(0)), rule_id="S-REV-DATE")
        planned = "planned" in c["Release Date"].lower() or "not yet published" in c.get("Related Documents", "").lower()
        em.emit(doc, el, dq, eid, "release_status", EN("planned" if planned else "released"), rule_id="S-REV-STATUS")
        sq = _cell_quote(el, "Summary of Changes")
        em.emit(doc, el, sq, eid, "change_summary", T(c["Summary of Changes"]), rule_id="S-REV-SUMMARY")
        m = re.search(rf"Pressure sensor {PS} replaced by {PS}\. Normal HPU discharge pressure setpoint changed from (\d+) bar to (\d+) bar\. See (ECN-\d+)\.", c["Summary of Changes"])
        if m:
            e = em.R.eid; de = em.R.ensure_entity(f"document:{m.group(5)}", m.group(5)).id; em.R.add_alias(de, m.group(5), "register", doc.id)
            ge, lt = ver_ge(ver), ver_lt(ver)
            em.emit(doc, el, sq, e(m.group(1)), "superseded_by", E(e(m.group(2))), rule_id="S-REV-PARSE", rev=ge, basis="section_cue")
            em.emit(doc, el, sq, e(m.group(2)), "introduced_by", E(de), rule_id="S-REV-PARSE", rev=ge, basis="section_cue")
            em.emit(doc, el, sq, HPU, "normal_operating_pressure", Q(m.group(3), "bar"), rule_id="S-REV-PARSE", rev=lt, basis="section_cue")
            em.emit(doc, el, sq, HPU, "normal_operating_pressure", Q(m.group(4), "bar"), rule_id="S-REV-PARSE", rev=ge, basis="section_cue")
            em.emit(doc, el, sq, HPU, "changed_by", E(de), rule_id="S-REV-PARSE", rev=ge, basis="section_cue")

_STATE_MAP = {"valve_iv21_required_state": ("component:IV-21", None), "estop_required_state": ("estop_circuit", None),
              "maintenance_panel_required_state": ("maintenance_access_panel", {"CLOSED": "INSTALLED"}),
              "fluid_level_required_band": ("hydraulic_fluid_level", {"NORMAL": "within normal band"})}

def extract_config(em, doc, els):
    leaf = {e.locator["json_path"]: e for e in els if e.kind == "json_leaf"}
    val = lambda p: leaf[p].locator["value"] if p in leaf else None
    fw = val("export_metadata.firmware_version")
    inst = dict(rev=ver_eq(fw), basis="instance_observation") if fw else {}
    for p, e in leaf.items():
        v, q = e.locator["value"], e.text
        if p.startswith("startup_interlocks."):
            item, mp = _STATE_MAP[p.split(".")[1]]; st = (mp or {}).get(v, v)
            em.emit(doc, e, q, HPU, "requires_state", S(item, st), rule_id="S-CFG-INTERLOCK", kind="structural", modality="requirement", **inst)
    # config keys with explicit revision applicability
    app = val("revision_applicability.sensor_ps04a_threshold_bar.applies_from_firmware")
    if app and "sensors.sensor_ps04a_threshold_bar" in leaf:
        k = "config:sensor_ps04a_threshold_bar"; em.R.ensure_entity(k, "sensor_ps04a_threshold_bar")
        em.emit(doc, leaf["sensors.sensor_ps04a_threshold_bar"], leaf["sensors.sensor_ps04a_threshold_bar"].text, k, "config_key_value",
                Q(val("sensors.sensor_ps04a_threshold_bar"), "bar"), rule_id="S-CFG-KEY", kind="structural", rev=ver_ge(app), basis="section_cue",
                extra=[(leaf["revision_applicability.sensor_ps04a_threshold_bar.applies_from_firmware"], leaf["revision_applicability.sensor_ps04a_threshold_bar.applies_from_firmware"].text)])
    lb = val("revision_applicability.sensor_ps04_legacy_threshold_bar.applies_before_firmware")
    if lb:
        k = "config:sensor_ps04_legacy_threshold_bar"; em.R.ensure_entity(k, "sensor_ps04_legacy_threshold_bar")
        p1 = "revision_applicability.sensor_ps04_legacy_threshold_bar.value"
        em.emit(doc, leaf[p1], leaf[p1].text, k, "config_key_value", Q(val(p1), "bar"), rule_id="S-CFG-KEY", kind="structural",
                rev=ver_lt(lb), basis="section_cue",
                extra=[(leaf["revision_applicability.sensor_ps04_legacy_threshold_bar.applies_before_firmware"], leaf["revision_applicability.sensor_ps04_legacy_threshold_bar.applies_before_firmware"].text)])
    for p, pred_ent in (("sensors.sensor_ps04a_alarm_low_bar", "A17"), ("sensors.sensor_ps04a_alarm_high_bar", "A18")):
        if p in leaf:
            k = "config:" + p.split(".")[1]; em.R.ensure_entity(k, p.split(".")[1])
            em.emit(doc, leaf[p], leaf[p].text, k, "config_key_value", Q(val(p), "bar"), rule_id="S-CFG-KEY", kind="structural", **inst)
            op = "<" if p.endswith("low_bar") else ">"
            em.emit(doc, leaf[p], leaf[p].text, em.R.eid(pred_ent), "alarm_trigger_condition", C(op, Q(val(p), "bar")), rule_id="S-CFG-ALARM", kind="structural", **inst)
    if "sensors.sensor_ps04a_signal_type" in leaf:
        em.emit(doc, leaf["sensors.sensor_ps04a_signal_type"], leaf["sensors.sensor_ps04a_signal_type"].text, em.R.eid("PS-04A"), "signal_type",
                T(val("sensors.sensor_ps04a_signal_type")), rule_id="S-CFG-SIG", kind="structural", **inst)
    pp, pr = "alarms.A17.persistence_before_shutdown_seconds", "alarms.A17.shutdown_procedure_ref"
    if pp in leaf and pr in leaf:
        em.emit(doc, leaf[pp], leaf[pp].text, em.R.eid("A17"), "alarm_required_action", PROC(f"procedure:{val(pr)}"),
                condition=C(">", Q(val(pp), "s"), subject="persistence"), rule_id="S-CFG-ALARM", kind="structural", modality="requirement",
                extra=[(leaf[pr], leaf[pr].text)], **inst)
    mx = "controller_reset.max_pressure_bar_allowed_for_reset"
    if mx in leaf:
        q = Q(val(mx), "bar")
        em.emit(doc, leaf[mx], leaf[mx].text, em.R.eid("PLC-03"), "reset_pressure_limit", q, condition=C("<=", q), rule_id="S-CFG-RESET", kind="structural", modality="requirement", **inst)
    mn = "controller_reset.min_pressure_bar_required_for_reset"
    if mn in leaf and val(mn) is None:
        em.emit(doc, leaf[mn], leaf[mn].text, em.R.eid("PLC-03"), "reset_min_pressure", NS(), rule_id="S-CFG-RESET", kind="structural", **inst)
    if fw:
        em.emit(doc, leaf["export_metadata.firmware_version"], leaf["export_metadata.firmware_version"].text, em.R.eid("PLC-03"),
                "observed_firmware", V(fw), rule_id="S-CFG-FW", kind="structural", **inst)

def extract_alarm_table(em, doc, els):
    for el in els:
        c = el.locator.get("cells")
        if el.kind != "table_row" or not isinstance(c, dict) or "Alarm" not in c or "Condition" not in c: continue
        eid = em.R.eid(c["Alarm"])
        if not eid: continue
        cq = f"Condition: {c['Condition']}"
        if m := re.search(r"(above|below) (\d+) bar", c["Condition"]):
            em.emit(doc, el, cq, eid, "alarm_trigger_condition", C("<" if m.group(1) == "below" else ">", Q(m.group(2), "bar")), rule_id="S-ALARM-COND")
        else:
            em.emit(doc, el, cq, eid, "alarm_trigger_condition", {"type": "condition", "op": "state", "state": c["Condition"].lower()}, rule_id="S-ALARM-COND")
        em.emit(doc, el, f"Panel Indication: {c['Panel Indication']}", eid, "panel_indication", EN(c["Panel Indication"]), rule_id="S-ALARM-PANEL")
        for cause in re.findall(r"\d\.\s*(.+?)(?=\s\d\.|$)", c["Possible Cause(s)"]):
            em.emit(doc, el, cause.strip(), eid, "alarm_possible_cause", T(cause.strip()), rule_id="S-ALARM-CAUSE")
        act = c["Required Action"]
        if m := re.search(rf"[Ii]f persistent > (\d+) s,\s*run Shutdown Proc\.? {VER}", act):
            em.emit(doc, el, m.group(0), eid, "alarm_required_action", PROC(f"procedure:{m.group(2)}"),
                    condition=C(">", Q(m.group(1), "s"), subject="persistence"), rule_id="S-ALARM-ACTION", modality="requirement")
        for sent in re.split(r"(?<=\.)\s+(?=[A-Z])", re.sub(r"[Ii]f persistent > .*$", "", act).strip()):
            if sent.strip(): em.emit(doc, el, sent.strip(), eid, "alarm_required_action", T(sent.strip().rstrip(".")), rule_id="S-ALARM-ACTION", modality="requirement")

def extract_glossary_defs(em, doc, els):
    for el in els:
        if el.kind != "row" or not isinstance(el.locator.get("cells"), list) or len(el.locator["cells"]) != 2: continue
        term, defn = el.locator["cells"]
        mm = re.search(r"\(([^)]+)\)$", term)
        tgt = em.R.eid(mm.group(1)) if mm else em.R.eid(term)
        if tgt and defn and not defn.startswith("Informal short form"):
            em.emit(doc, el, defn, tgt, "definition", T(defn), rule_id="S-GLOSS-DEF")

_SLIDE_ROWS = {"isolation valve": ("component:IV-21", "OPEN", "open"), "emergency stop": ("estop_circuit", "RESET", "reset"),
               "access panel": ("maintenance_access_panel", "INSTALLED", "installed"), "fluid level": ("hydraulic_fluid_level", "within normal band", "sight glass band")}

def extract_slide_table(em, doc, els):
    for el in els:
        c = el.locator.get("cells")
        if el.kind != "row" or not isinstance(c, list) or len(c) != 2 or "slide" not in el.locator: continue
        item = _SLIDE_ROWS.get(c[0].lower())
        if item and item[2] in c[1].lower():
            em.emit(doc, el, c[1], HPU, "requires_state", S(item[0], item[1]), rule_id="S-SLIDE-CHECK", modality="requirement")

def extract_document(em: Emitter, doc: Document, els: list[Element]) -> None:
    """Dispatch by SHAPE of the source (structured table signatures), then run the prose rule library on prose elements."""
    sig = set()
    for e in els:
        cl = e.locator.get("cells")
        if isinstance(cl, dict): sig |= set(cl.keys())
    if "Component ID (as printed)" in sig: extract_register(em, doc, els)
    if "Software Revision" in sig and "Release Date" in sig: extract_revision_history(em, doc, els)
    if "Alarm" in sig and "Condition" in sig: extract_alarm_table(em, doc, els)
    if any(e.kind == "json_leaf" for e in els): extract_config(em, doc, els)
    if doc.id.endswith(".docx") and doc.family == "reference": extract_glossary_defs(em, doc, els)
    if doc.family == "slides": extract_slide_table(em, doc, els)
    for e in els: apply_prose_rules(em, doc, e)
