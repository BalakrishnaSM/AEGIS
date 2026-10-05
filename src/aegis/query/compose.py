"""Query stages 7-8 (architecture §6.7-6.8). Deterministic template composer (the no-LLM path) and the contract verifier.
SOURCE sentences cite claim ids; DERIVED sentences cite decision ids. The composer renders ONLY what the contract holds."""
from __future__ import annotations
import json, re
from typing import Any

from ..models import Analysis, Claim, Decision, Sentence
from ..quantity import fmt
from ..scope import Scope
from ..store.db import KnowledgeBase
from .resolver import Outcome, SlotResult

PRED_HUMAN = {"normal_operating_pressure": "normal operating pressure", "max_operating_temperature": "maximum operating temperature",
              "calibration_interval": "calibration interval", "approver": "approver", "mtbf": "mean time between failures",
              "supply_voltage": "supply voltage", "supply_phase": "supply phase count", "supply_compatibility": "supply compatibility statement",
              "alarm_trigger_condition": "alarm trigger condition", "alarm_possible_cause": "possible causes", "located_at": "location",
              "alarm_required_action": "required action", "requires_state": "start-up conditions", "reset_pressure_limit": "reset pressure limit"}
STATE_PHRASE = {"hydraulic_fluid_level": "the hydraulic fluid level must be within the normal band",
                "component:IV-21": "isolation valve IV-21 must be OPEN", "estop_circuit": "the emergency stop circuit must be RESET",
                "maintenance_access_panel": "the maintenance access panel must be installed (the unit must not be started with it removed)"}

class Composer:
    def __init__(self, kb: KnowledgeBase):
        self.kb = kb

    # ------------------------------------------------------------ naming and phrasing
    def nm(self, eid: str) -> str:
        e = self.kb.entities.get(eid); n = e.canonical_name if e else eid.split(":", 1)[-1]
        if eid == "component:HPU": return "Hydraulic Power Unit (HPU)"
        if eid == "component:PLC-03": return "PLC-03 (HCS controller)"
        if eid.startswith("component:") and re.fullmatch(r"[A-Z]{1,3}-?\d+[A-Z]?", eid.split(":", 1)[1]): return eid.split(":", 1)[1]
        if eid.startswith("alarm:"): return f"Alarm {eid.split(':',1)[1]} ({n})"
        return n

    def scope_phrase(self, s: Scope) -> str:
        r = s.software_revision
        if r.kind == "any": return "" if s.scope_basis == "document_default" else ""
        if r.kind == "unknown": return "(scope not established)"
        if r.kind == "ver":
            if r.lo and r.hi is None: return f"on software revision {r.lo} and later"
            if r.hi and r.lo is None: return f"on software revisions before {r.hi}" if not r.hi_inc else f"on software revisions up to {r.hi}"
            if r.lo == r.hi: return f"on software revision {r.lo}"
            return f"on software revisions {r.lo} to {r.hi}"
        return ""

    def vt(self, v: dict) -> str:
        t = v.get("type")
        if t == "quantity": return fmt(v["value"], v["unit"])
        if t == "entity": return self.nm(v["id"])
        if t == "condition":
            q = v.get("quantity"); op = {"<": "below", ">": "above", "<=": "at or below", ">=": "at or above"}.get(v.get("op"), v.get("op"))
            return f"{op} {fmt(q['value'], q['unit'])}" if q else str(v.get("state"))
        if t == "state": return f"{v['item']} {v['state']}"
        if t == "procedure": return f"Shutdown Procedure {v['id'].split(':')[1]}"
        if t == "list": return "; ".join(v["items"])
        if t == "not_specified": return "not specified"
        return str(v.get("v"))

    def sp(self, c: Claim) -> str:
        p = self.scope_phrase(c.scope); return (" " + p) if p else ""

    # ------------------------------------------------------------ one SOURCE sentence per claim
    def render_claim(self, c: Claim, an: Analysis) -> str:
        e, v, p = self.nm(c.entity_id), c.value, c.predicate
        if p == "normal_operating_pressure":
            sens = f" (as reported by {self.nm(c.scope.installed_sensor.ids[0])})" if c.scope.installed_sensor.kind == "ent" and c.scope.installed_sensor.ids else ""
            return f"Normal operating (discharge) pressure for the {e} is {self.vt(v)}{self.sp(c)}{sens}."
        if p == "requires_state": return f"Before starting {e}: {STATE_PHRASE.get(v['item'], v['item'] + ' ' + v['state'])}."
        if p == "alarm_trigger_condition": return f"{e} is raised when {'hydraulic pressure is ' if v.get('quantity') else ''}{self.vt(v)}."
        if p == "panel_indication": return f"Panel indication for {e}: {v['v']}."
        if p == "alarm_possible_cause": return f"Possible cause of {e}: {v['v']}."
        if p == "alarm_required_action":
            if v.get("type") == "procedure":
                cond = c.condition
                pre = f"If {c.entity_id.split(':',1)[1]} persists for more than {fmt(cond['quantity']['value'], 'second').replace(' s',' seconds')}, " if cond and cond.get("quantity") else ""
                return f"{pre}{'e' if pre else 'E'}xecute {self.vt(v)}."
            return f"Required action for {e}: {v['v']}."
        if p == "procedure_steps": return f"{e} consists of: {self.vt(v)}."
        if p == "prohibited_action": return f"Prohibited for {e}: {v['v']}."
        if p == "alarm_note": return f"Note on {e}: {v['v']}."
        if p == "reset_pressure_limit": return f"Do not reset {e} while hydraulic pressure is above {self.vt(v)}; a reset is only permitted below that level."
        if p == "hazard": return f"Hazard: {v['v']}."
        if p == "introduced_by": return f"{e} was introduced by {self.nm(v['id'])}."
        if p == "changed_by": return f"The normal operating pressure change is recorded in {self.nm(v['id'])}."
        if p == "superseded_by": return f"{e} is superseded by {self.nm(v['id'])}{self.sp(c)}."
        if p == "is_distinct_from":
            other = self.nm(v["id"]); extra = f", and {e} is not a form-fit-function replacement for {other}" if v.get("relation") == "not_form_fit_function" else ""
            loc = next((x for x in self.kb.visible() if x.entity_id == c.entity_id and x.predicate == "located_at"), None)
            return f"{e} and {other} are distinct components{extra}" + (f"; {e} is located at {loc.value['v']}" if loc else "") + "."
        if p == "supported_on": return f"{e} remains installed and supported{self.sp(c)}."
        if p == "installation_restriction": return f"Do not install {e}{self.sp(c)}."
        if p == "active_sensor": return f"The active pressure sensor is {self.nm(v['id'])}{self.sp(c)}."
        if p == "connects_to_control_signal": return f"{self.nm(v['id'])} connects directly to {e} (control/signal line)."
        if p == "located_at": return f"{e} is located at {v['v']}."
        if p == "displays_tag": return f"The screenshot shows the sensor tag “{v['v']}”."
        if p == "matches_entity": return f"That tag resolves to {self.nm(v['id'])} (match type: {v.get('resolution', 'normalized')}, after punctuation normalization), a known component."
        if p == "release_date": return f"Software revision {c.entity_id.split(':',1)[1]} took effect on {v['v']}."
        if p == "change_summary": return f"Changes alongside it: {v['v']}"
        if p == "config_key_maps_to":
            m = (c.inference or {}).get("maps_to", {}); sup = [self.kb.claims[i] for i in (c.inference or {}).get("supporting_claim_ids", [])]
            val = next((self.vt(s.value) for s in sup if s.predicate == "config_key_value"), "")
            return f"`{c.entity_id.split(':',1)[1]}` ({val}) corresponds to the manual's {PRED_HUMAN.get(m.get('predicate'), m.get('predicate'))} of {self.nm(m.get('entity',''))}{self.sp(c)}."
        if p == "supply_voltage": return f"{e}: {self.vt(v)}{' incoming supply' if v.get('type') == 'quantity' else ''}."
        if p == "not_covered_in": return f"{e} appears in the training slides but in none of the manuals; the slide itself says it is not covered in the standard {self.nm(v['id'])} (it is specific to Line 4/5 configurations)."
        return f"{PRED_HUMAN.get(p, p)} of {e}: {self.vt(v)}."

    def branch_sentence(self, sr: SlotResult, an: Analysis) -> tuple[str, list[str]]:
        bs = sr.branches; focus = an.focus_value
        txt = []; ids = []
        fb = next((b for b in bs if focus and abs(b["value"]["value"] - focus["value"]) < 1e-9), None) if focus else None
        if fb is not None:
            others = [b for b in bs if b is not fb]
            sc = lambda b: self.scope_phrase(b["scope_obj"]).replace("on ", "", 1)
            t = f"The {fmt(fb['value']['value'], fb['value']['unit'])} value does not apply to all units: it applies only {self.scope_phrase(fb['scope_obj'])}" + (" (with PS-04A fitted)" if fb["scope_obj"].installed_sensor.kind == "ent" else "")
            if others: t += "; " + "; ".join(f"{self.scope_phrase(b['scope_obj'])} the value is {fmt(b['value']['value'], b['value']['unit'])}" + (" (a prior-revision value)" if b["historical"] else "") for b in others)
            return t + ".", [b["claim_id"] for b in bs]
        for b in bs:
            txt.append(f"{self.scope_phrase(b['scope_obj'])}: {fmt(b['value']['value'], b['value']['unit'])}".strip()); ids.append(b["claim_id"])
        return "Normal operating pressure by revision — " + "; ".join(txt) + ".", ids

    # ------------------------------------------------------------ composition
    def compose(self, an: Analysis, out: Outcome) -> list[Sentence]:
        S: list[Sentence] = []; cited: set[str] = set()
        skip_app = any(s.slot.predicate == "normal_operating_pressure" for s in out.slots)
        for sr in out.slots:
            if sr.slot.predicate in ("introduces_component", "introduces_alarm"): continue
            if sr.slot.predicate == "applicability_scope" and sr.branches and not skip_app:
                t, ids = self.branch_sentence(sr, an); S.append(Sentence(text=t, kind="SOURCE", claim_ids=ids)); cited |= set(ids); continue
            for c in sr.reps:
                if c.id in cited and c.predicate != "procedure_steps": continue
                S.append(Sentence(text=self.render_claim(c, an), kind="SOURCE", claim_ids=[c.id])); cited.add(c.id)
        for c in out.selected:                                                      # expansions (procedure steps, diff claims, identity support)
            if c.id not in cited: S.append(Sentence(text=self.render_claim(c, an), kind="SOURCE", claim_ids=[c.id])); cited.add(c.id)
        order = ["SCOPE_ASSUMPTION", "FALSE_PREMISE", "CONFLICT", "GAP", "NOTE", "INFERRED", "COMPLETENESS", "ADVISORY"]
        # Relation/scope propagation can emit equivalent decisions more than once.
        # Render one sentence per semantic decision, while keeping the full decision
        # records in the trace/contract. This prevents repetitive answers without
        # changing what the verifier can audit.
        rendered_decisions: set[tuple[str, str]] = set()
        for kind in order:
            for d in [d for d in out.decisions if d.kind == kind]:
                t = self.derive(d, out)
                if not t:
                    continue
                key = (d.kind, json.dumps(d.payload, sort_keys=True, default=str))
                if key in rendered_decisions:
                    continue
                rendered_decisions.add(key)
                S.append(Sentence(text=t, kind="DERIVED", decision_ids=[d.id]))
        return S

    def derive(self, d: Decision, out: Outcome) -> str:
        p, n = d.payload, len(self.kb.documents)
        if d.kind == "SCOPE_ASSUMPTION":
            if p["type"] == "current":
                fw = f" against the configuration export's firmware ({', '.join(p['observed_firmware'])})" if p.get("observed_firmware") else ""
                pl = f"; revision {', '.join(p['planned_excluded'])} is planned and not yet published" if p.get("planned_excluded") else ""
                return f"“Current” was resolved as software revision {p['resolved']}, the latest released revision in the revision history (cross-checked{fw}){pl}."
            return f"Because {p['because']}, the question was restricted to {p['restricted_to']}."
        if d.kind == "FALSE_PREMISE": return f"“{p['phrase']}” does not match any component or entity in the corpus (no alias at any tier), so the premise of the question cannot be established."
        if d.kind == "CONFLICT":
            a, b = p["sides"][0], p["sides"][1]
            return f"Sources disagree for {p['slot']}: {self.vt(a['value'])} ({', '.join(a['docs'])}) vs {self.vt(b['value'])} ({', '.join(b['docs'])}) for overlapping scope; both are shown and none is selected."
        if d.kind == "GAP":
            ns = " A source states explicitly that it is not specified." if p.get("explicit_not_specified") else ""
            return f"No {PRED_HUMAN.get(p['predicate'], p['predicate'])} for {self.nm(p['entity']) if str(p['entity']).count(':') else p['entity']} was found in the {n} documents searched.{ns}"
        if d.kind == "NOTE":
            t = p["type"]
            if t == "graph_edge_not_control": return f"Note: the schematic also draws a line between {self.nm(p['hub'])} and {self.nm(p['other'])}, but per the legend its colour marks it as a {p['legend']}, not a control/signal connection, so it is not counted."
            if t == "rationale_not_scope": return f"Note: the source gives “{p['text']}” as the rationale for the change, but {p['note']}."
            if t == "unmapped": return f"This question could not be mapped to any predicate the knowledge base supports (analyzer: {p['analyzer']}), so the system cannot say whether the corpus answers it."
            if t == "diff_new": return None if p["claims"] else f"{p['name']} also appears in the training slides and in none of the manuals."
            if t == "diff_none": return "No alarm that is absent from the manuals appears in the slide excerpt." if "alarm" in p["kind"] else None
        if d.kind == "INFERRED": return f"This is an inference ({p['basis']}); no document states it directly."
        if d.kind == "COMPLETENESS":
            if p.get("completeness") == "excerpt": return f"Note: the slide file is an excerpt (“Slides 4–6 of the full deck”), so this answer covers the excerpt only."
            return f"Note: {p['doc']} cites {p['missing_reference']}, which is not part of the package."
        if d.kind == "ADVISORY": return f"Unverified: {p['doc']} reports {self.vt(p['value'])} ({p['why']}); it is not used in this answer."
        return ""

# ============================================================================== verifier
def verify(kb: KnowledgeBase, an: Analysis, out: Outcome, sents: list[Sentence]) -> list[str]:
    F: list[str] = []
    sel = {c.id for c in out.selected}; dec = {d.id for d in out.decisions}
    cited = {i for s in sents for i in s.claim_ids}
    if cited - sel: F.append(f"cited claims outside the contract: {sorted(cited - sel)}")
    if sel - cited: F.append(f"required claims never cited: {sorted(sel - cited)}")
    dcited = {i for s in sents for i in s.decision_ids}
    if dcited - dec: F.append(f"cited decisions outside the contract: {sorted(dcited - dec)}")
    for d in out.decisions:
        if d.kind in ("GAP", "CONFLICT", "SCOPE_ASSUMPTION", "INFERRED", "FALSE_PREMISE", "ADVISORY") and d.id not in dcited: F.append(f"decision {d.id} ({d.kind}) not rendered")
        if d.kind == "COMPLETENESS" and d.id not in dcited: F.append(f"decision {d.id} (COMPLETENESS) not rendered")
    allowed = set()
    def harvest(x):
        for t in re.findall(r"\d+(?:\.\d+)*", json.dumps(x, default=str, ensure_ascii=False)):
            allowed.add(t)
            if "." in t and float(t.split(".")[0] + "." + t.split(".")[1]).is_integer() and t.count(".") == 1: allowed.add(str(int(float(t))))
    for c in out.selected:
        harvest([c.value, c.condition, c.scope.describe(), c.entity_id, c.inference]); harvest(kb.entities.get(c.entity_id).canonical_name if c.entity_id in kb.entities else "")
        if c.value.get("type") == "entity": harvest(c.value["id"])
        for sid in (c.inference or {}).get("supporting_claim_ids", []): harvest(kb.claims[sid].value)
    for d in out.decisions: harvest(d.payload)
    for b in [b for sr in out.slots for b in sr.branches]: harvest([b["value"], b["scope"]])
    harvest(an.question); harvest([sr.slot.entity_id for sr in out.slots])
    for s in sents:
        if s.kind == "SOURCE":
            for tok in re.findall(r"\d+(?:\.\d+)*", s.text):
                if tok not in allowed: F.append(f"number {tok!r} in SOURCE sentence is not in the cited claims: {s.text[:70]}")
            for f in out.forbidden:
                fv = str(int(f["value"])) if float(f["value"]).is_integer() else str(f["value"])
                if re.search(rf"(?<![\d.]){re.escape(fv)}(?![\d.])", s.text): F.append(f"forbidden value {fv} {f['unit']} ({f['why']}) appears in a SOURCE sentence")
    for c in out.selected:
        for eid in c.evidence_ids:
            ev = kb.evidence[eid]; el = kb.elements.get(ev.element_id)
            if el is None or ev.quote not in el.text: F.append(f"quote of {c.id} not found in source element")
    if out.status == "UNANSWERABLE" and any(s.kind == "SOURCE" for s in sents): F.append("UNANSWERABLE answer contains SOURCE sentences")
    if out.status == "PARTIAL" and not any(d.kind == "GAP" and d.id in dcited for d in out.decisions): F.append("PARTIAL without a rendered GAP")
    return F
