"""Stage E (architecture §4.E). E1 = evidence verification (V1: does the quote exist?).
E2 = extraction validation (V2-V5 deterministic: does the claim faithfully represent the quote?).
V6/V7 are LLM checks that may only DEMOTE (veto), never approve; they are hooks, off without an LLM.
Lifecycle: PROPOSED -> (V1..V5 FAIL -> REJECTED) | (veto / raster disagreement -> DEMOTED, needs_review) | ADMITTED."""
from __future__ import annotations
import re
from typing import Callable, Optional
from ..models import Claim
from ..ontology import PREDICATES
from ..quantity import dimension_of, parse_quantities, qeq
from ..scope import parse_cues
from ..store.db import KnowledgeBase
from .adapters import norm
from .entities import Resolver
from .rules import strip_parens

ACTION_PREDS = {"requires_state", "reset_pressure_limit", "alarm_required_action", "prohibited_action", "installation_restriction"}
COMPARATOR_PREDS = {"alarm_trigger_condition", "reset_pressure_limit", "alarm_required_action"}
SKIP_V2 = {"release_status", "common_name"}                      # derived-by-absence / name text
_OPS = {"above": ">", "more than": ">", "beyond": ">", "exceeds": ">", "exceed": ">", "over": ">", ">": ">",
        "below": "<", "under": "<", "<": "<", ">=": ">=", "<=": "<="}
_CMP = re.compile(r"(above|below|more than|beyond|exceeds?|over|under|>=|<=|>|<)\s*(\d+(?:\.\d+)?)\s*(bar|seconds?|s|V|°C)?", re.I)
_STATE_KW = {"OPEN": ["open"], "RESET": ["reset"], "INSTALLED": ["installed", "removed", "closed"],
             "within normal band": ["normal band", "normal", "sight glass band"]}

def _quotes(kb: KnowledgeBase, c: Claim) -> str:
    return " ".join(norm(kb.evidence[e].quote) for e in c.evidence_ids)

def v1_quote(kb, c) -> str:
    for eid in c.evidence_ids:
        ev = kb.evidence[eid]; el = kb.elements.get(ev.element_id)
        if el is None: return f"FAIL:element {ev.element_id} missing"
        if norm(ev.quote) not in norm(el.text): return f"FAIL:quote not in source element {el.id}"
    return "PASS"

def v2_value(kb, R: Resolver, c: Claim) -> str:
    if c.predicate in SKIP_V2: return "SKIP"
    q = _quotes(kb, c); v = c.value; t = v.get("type")
    if t == "quantity":
        nums = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", q)]
        if v.get("raw") is not None and float(v["raw"]) not in nums: return f"FAIL:value {v['raw']} not in quote"
        same_dim = [x for x in parse_quantities(q) if x[1] == v["unit"] or dimension_of(x[1]) == dimension_of(v["unit"])]
        if same_dim and not any(qeq((v["value"], v["unit"]), x) for x in same_dim): return f"FAIL:quantity {v['value']} {v['unit']} != quote {same_dim}"
    elif t == "version":
        if v["v"] not in q: return f"FAIL:version {v['v']} not in quote"
    elif t == "date":
        if v["v"] not in q: return f"FAIL:date {v['v']} not in quote"
    elif t == "person":
        if v["v"].split()[-1].lower() not in q.lower(): return "FAIL:person not in quote"
    elif t == "enum":
        if v["v"].lower() not in q.lower(): return f"FAIL:enum {v['v']} not in quote"
    elif t == "state":
        kws = _STATE_KW.get(v["state"], [str(v["state"]).lower()])
        if not any(k in q.lower() for k in kws): return f"FAIL:state {v['state']} not supported by quote"
    elif t == "procedure":
        if v["id"].split(":")[1] not in q: return f"FAIL:procedure id {v['id']} not in quote"
    elif t == "list":
        if not all(i.lower() in q.lower() for i in v["items"]): return "FAIL:list item not in quote"
    elif t == "entity":
        eid = v["id"]
        if eid.startswith("document:"):
            doc_text = " ".join(e.text for e in kb.elements.values() if e.document_id == c.doc_id)
            if eid.split(":", 1)[1] in doc_text or eid.split(":", 1)[1] in q: return "PASS"
        ments = {m["entity_id"] for m in R.find_mentions(q)}
        if eid not in ments: return f"FAIL:entity {eid} not mentioned in quote"
    elif t == "not_specified":
        if not re.search(r"null|not specified|no .* specified|not included", q, re.I): return "FAIL:'not specified' unsupported"
    return "PASS"

SELF_DESCRIBING = {"located_at", "last_calibrated", "performed_by", "signal_type", "calibration_reference", "supply_voltage", "lifecycle_status", "introduced_by",
                   "is_distinct_from", "superseded_by", "supported_on", "installation_restriction", "corresponds_to", "requires_software_revision"}

def v2b_subject(kb, R: Resolver, c: Claim) -> str:
    """The claim's SUBJECT must be named by the evidence (row key, region text or quote) when the quote names other entities of the same kind.
    Catches near-ID subject swaps (PS-04 <-> PS-04A <-> PS-40); skipped for predicates whose subject is implied by context."""
    if c.predicate not in SELF_DESCRIBING or c.assertion_kind != "stated": return "SKIP"
    ev = kb.evidence[c.evidence_ids[0]]; el = kb.elements.get(ev.element_id)
    if el is None: return "SKIP"
    kind = c.entity_id.split(":", 1)[0]
    rk = el.locator.get("row_key")
    rke = R.eid(str(rk)) if rk else None                     # the row key names the subject only if it resolves to an entity (register rows yes, revision rows no)
    if rke: return "PASS" if rke == c.entity_id else f"FAIL:row key {rk!r} resolves to {rke}, not {c.entity_id}"
    named = {m["entity_id"] for m in R.find_mentions(ev.quote) if m["entity_id"].split(":", 1)[0] == kind}
    if named and c.entity_id not in named: return f"FAIL:quote names {sorted(named)} but the claim subject is {c.entity_id}"
    if not named and el.kind == "raster_region":
        ctx = {m["entity_id"] for m in R.find_mentions(el.text) if m["entity_id"].split(":", 1)[0] == kind}
        if ctx and c.entity_id not in ctx: return f"FAIL:region names {sorted(ctx)} but the claim subject is {c.entity_id}"
    return "PASS"

def v3_scope_cue(c: Claim, quote: str) -> str:
    span = c.validation.get("scope_span", "")
    text = strip_parens(quote) if span.startswith("<quote") else span
    cues = parse_cues(text); rev = c.scope.software_revision; basis = c.scope.scope_basis
    if cues:
        if rev.kind == "any": return f"FAIL:quote carries scope cue {[x.describe() for x in cues]} but claim scope is ANY (widened)"
        if basis == "explicit_cue" and rev not in cues: return f"FAIL:claim scope {rev.describe()} != quote cue {[x.describe() for x in cues]}"
    elif basis == "explicit_cue":
        return "FAIL:claim asserts an explicit cue the quote does not contain"
    return "PASS"

def v4_condition(c: Claim, quote: str) -> str:
    if c.predicate in ACTION_PREDS and re.search(r"\b(do not|do NOT|must not|never)\b", quote, re.I) and c.modality == "fact":
        return "FAIL:prohibition in quote but modality=fact"
    if c.predicate in COMPARATOR_PREDS:
        for m in _CMP.finditer(quote):
            op = _OPS[m.group(1).lower()]; num = float(m.group(2))
            ok = False
            for cond in (c.condition, c.value if c.value.get("type") == "condition" else None):
                if cond and cond.get("quantity") and cond.get("op") in (op, "<=" if op == "<" else op) and abs(cond["quantity"].get("raw", cond["quantity"]["value"]) - num) < 1e-9:
                    ok = True
            if not ok: return f"FAIL:comparator '{m.group(0)}' not reflected in condition"
    return "PASS"

def v5_types(kb, c: Claim) -> str:
    spec = PREDICATES.get(c.predicate)
    if not spec: return f"FAIL:predicate {c.predicate} not in closed ontology"
    kind = c.entity_id.split(":", 1)[0]
    if kind not in spec.kinds: return f"FAIL:entity kind {kind} not allowed for {c.predicate}"
    t = c.value.get("type")
    if t not in spec.vtypes: return f"FAIL:value type {t} not allowed for {c.predicate}"
    if t == "quantity" and spec.dim and dimension_of(c.value["unit"]) != spec.dim: return f"FAIL:dimension {dimension_of(c.value['unit'])} != {spec.dim}"
    return "PASS"

def validate_all(kb: KnowledgeBase, R: Resolver, veto: Optional[Callable] = None, seeded: bool = True) -> dict:
    stats = {"ADMITTED": 0, "DEMOTED": 0, "REJECTED": 0}
    for c in list(kb.claims.values()):
        if c.lifecycle != "PROPOSED": continue
        q = _quotes(kb, c)
        res = {"V1": v1_quote(kb, c), "V2": v2_value(kb, R, c), "V2b": v2b_subject(kb, R, c), "V3": v3_scope_cue(c, q), "V4": v4_condition(c, q), "V5": v5_types(kb, c)}
        fails = {k: v for k, v in res.items() if v.startswith("FAIL")}
        ev = [kb.evidence[e] for e in c.evidence_ids]
        if any(e.raster_agreement == "disagree" for e in ev): res["RASTER"] = "VETO:OCR and transcription disagree on a critical token"
        else: res["RASTER"] = "PASS" if any(e.raster_agreement == "agree" for e in ev) else "SKIP"
        if veto: res["V6V7"] = veto(c, q)
        else: res["V6V7"] = "SKIP"
        for k, v in res.items(): kb.audit.append((c.id, k, v.split(":")[0], v))
        c.validation.update(res)
        if fails: c.lifecycle = "REJECTED"
        elif any(v.startswith("VETO") for v in res.values()): c.lifecycle = "DEMOTED"; c.needs_review = True
        else: c.lifecycle = "ADMITTED"
        stats[c.lifecycle] += 1
    return stats
