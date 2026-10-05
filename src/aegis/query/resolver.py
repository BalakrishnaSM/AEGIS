"""Query stages 3-6 (architecture §6.3-6.7): candidate retrieval, Applicability Resolver (R1-R6), answerability gate, contract.
Principles enforced in code:
* quality is never used to choose between claims (no `if quality == STRONG: choose`); only categorical admissibility (policy) is.
* tier never resolves a conflict; conflicts come from the scope algebra (kb.relations) and are returned, not adjudicated.
* absence is a statement about the SEARCH, recorded as SearchRecord + GAP decisions."""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from ..ingest.entities import Resolver
from ..models import (Analysis, AnswerContract, Claim, Decision, EvidenceProfile, RequiredFact, SearchRecord)
from ..ontology import EVENT_PREDICATES, FAMILY, POLICY, SINGLE_VALUED, Policy
from ..quantity import qeq
from ..scope import AnyDim, EntDim, Scope, VerDim, parse_version, scope_rel, vkey, ver_eq, ver_lt, vstr
from ..store.db import KnowledgeBase
from ..ingest.relate import independent, slot_key

KIND_ORDER = {"stated": 0, "structural": 1, "inferred": 2}
TIER_ORDER = {"primary": 0, "reference": 1, "derived": 2, "field_observation": 3}      # DISPLAY ORDER ONLY
DERIVED_SOURCE = {"applicability_scope": "normal_operating_pressure"}

@dataclass
class SlotResult:
    slot: RequiredFact
    candidates: list[Claim] = field(default_factory=list)
    applicable: list[Claim] = field(default_factory=list)
    excluded: list[tuple[Claim, str]] = field(default_factory=list)
    reps: list[Claim] = field(default_factory=list)                  # answer-bearing representatives
    members: dict[str, list[Claim]] = field(default_factory=dict)    # rep id -> equal-valued answer-bearing claims
    supporting: list[Claim] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    branches: list[dict] = field(default_factory=list)
    covered: bool = False
    negative: bool = False
    diff_new: list[dict] = field(default_factory=list)
    subject: list[str] = field(default_factory=list)

@dataclass
class Outcome:
    status: str
    slots: list[SlotResult]
    decisions: list[Decision]
    searched: list[SearchRecord]
    selected: list[Claim]
    forbidden: list[dict]
    qscope: Scope
    profiles: dict[str, EvidenceProfile]

class AppResolver:
    def __init__(self, kb: KnowledgeBase, R: Resolver, ablate: Optional[set] = None):
        self.kb, self.R, self.ablate = kb, R, ablate or set()
        self._d = 0
        self._rel = {}
        for r in kb.relations:
            self._rel.setdefault(r.kind, []).append(r)

    # ----------------------------------------------------------------- helpers
    def did(self) -> str:
        self._d += 1; return f"D{self._d}"

    def name(self, eid: str) -> str:
        e = self.kb.entities.get(eid); return e.canonical_name if e else eid

    def corroboration(self, c: Claim, pool: list[Claim]) -> tuple[int, int]:
        ind = der = 0; seen_docs = {c.doc_id}
        for o in pool:
            if o.id == c.id or o.doc_id in seen_docs or o.vkey() != c.vkey(): continue
            if scope_rel(c.scope, o.scope) != "OVERLAP": continue
            seen_docs.add(o.doc_id)
            if independent(self.kb, c, o): ind += 1
            else: der += 1
        return ind, der

    # ----------------------------------------------------------------- R1 scope derivation (+ R2 propagation)
    def current_revision(self) -> Optional[dict]:
        rel, planned = [], []
        for c in self.kb.visible():
            if c.predicate == "release_status" and c.entity_id.startswith("software_revision:"):
                (rel if c.value["v"] == "released" else planned).append(c.entity_id.split(":", 1)[1])
        if not rel: return None
        latest = max(rel, key=lambda v: vkey(parse_version(v)))
        fw = [c.value["v"] for c in self.kb.visible() if c.predicate == "observed_firmware"]
        conflict = [f for f in fw if vkey(parse_version(f)) > vkey(parse_version(latest))]
        return {"resolved": latest, "planned_excluded": sorted(planned), "observed_firmware": sorted(set(fw)), "export_newer_than_history": conflict}

    def derive_scope(self, an: Analysis, decisions: list[Decision]) -> tuple[Scope, dict]:
        meta = {"current": False, "r2": False, "unspecified": an.scope.software_revision == "UNSPECIFIED"}
        rev: Any = an.scope.software_revision
        if rev == "CURRENT":
            cur = self.current_revision()
            if cur:
                rev = ver_eq(cur["resolved"]); meta["current"] = True; meta["current_info"] = cur
                decisions.append(Decision(id=self.did(), kind="SCOPE_ASSUMPTION", payload={"type": "current", **cur}))
            else: rev = "UNSPECIFIED"; meta["unspecified"] = True
        # R2: entity <-> scope propagation through supersession (PS-04A implies >=3.2; PS-04 implies <3.2)
        if rev == "UNSPECIFIED" and an.policy == "PARAMETER" and any(s.predicate in ("normal_operating_pressure", "applicability_scope") for s in an.slots):
            for m in an.mentions:
                for c in self.kb.visible():
                    if c.predicate != "superseded_by" or c.needs_review: continue
                    if c.entity_id == m["entity_id"] and isinstance(c.scope.software_revision, VerDim) and c.scope.software_revision.lo:
                        rev = ver_lt(c.scope.software_revision.lo); meta["r2"] = True; meta["unspecified"] = False
                        decisions.append(Decision(id=self.did(), kind="SCOPE_ASSUMPTION", payload={"type": "propagated", "entity": c.entity_id, "restricted_to": rev.describe(), "because": f"{self.name(c.entity_id)} is superseded from revision {c.scope.software_revision.lo}"}))
                    elif c.value.get("id") == m["entity_id"]:
                        rev = c.scope.software_revision; meta["r2"] = True; meta["unspecified"] = False
                        decisions.append(Decision(id=self.did(), kind="SCOPE_ASSUMPTION", payload={"type": "propagated", "entity": m["entity_id"], "restricted_to": rev.describe(), "because": f"{self.name(m['entity_id'])} is installed from revision {rev.describe()}"}))
        qs = Scope(software_revision=AnyDim() if rev == "UNSPECIFIED" else rev, scope_basis="explicit_cue")
        return qs, meta

    # ----------------------------------------------------------------- candidate retrieval (typed lookup first)
    def candidates(self, slot: RequiredFact) -> list[Claim]:
        pred = DERIVED_SOURCE.get(slot.predicate, slot.predicate)
        ents = {e for e in [slot.entity_id, *slot.entity_alternatives] if e}
        system = slot.entity_id and slot.entity_id.startswith("system:")
        out = []
        for c in self.kb.visible():
            if c.predicate != pred: continue
            if slot.value_filter and "pair" in slot.value_filter:
                a, b = slot.value_filter["pair"]
                if {c.entity_id, c.value.get("id")} != {a, b}: continue
            elif slot.value_filter:
                v = c.value; q = slot.value_filter["quantity"]
                if not (v.get("type") == "condition" and v.get("op") == slot.value_filter["op"] and v.get("quantity") and qeq((v["quantity"]["value"], v["quantity"]["unit"]), (q["value"], q["unit"]))): continue
            elif system: pass
            elif ents and c.entity_id not in ents: continue
            elif not ents: continue
            out.append(c)
        return out

    def advisory_pool(self, slot: RequiredFact) -> list[Claim]:
        src = DERIVED_SOURCE.get(slot.predicate, slot.predicate); ents = {e for e in [slot.entity_id, *slot.entity_alternatives] if e}
        return [c for c in self.kb.visible() if (FAMILY.get(c.predicate) == src or (c.predicate == src and (c.needs_review or c.scope.software_revision.kind == "unknown")))
                and (not ents or c.entity_id in ents)]

    # ----------------------------------------------------------------- per-slot resolution (R3-R6)
    def resolve_slot(self, an: Analysis, slot: RequiredFact, qs: Scope, pol: Policy, decisions: list[Decision], meta: dict) -> SlotResult:
        sr = SlotResult(slot=slot)
        if slot.predicate in ("introduces_component", "introduces_alarm"): return self.resolve_diff(an, slot, sr, decisions)
        sr.candidates = self.candidates(slot)
        for c in sr.candidates:
            if c.value.get("type") == "not_specified": sr.excluded.append((c, "explicit 'not specified' statement (a gap, not a value)")); continue
            if c.needs_review: sr.excluded.append((c, "needs_review (unverified reading)")); continue
            if pol.scope_filter and "scope" not in self.ablate and c.predicate not in EVENT_PREDICATES:
                rel = scope_rel(c.scope, qs)
                if rel == "DISJOINT": sr.excluded.append((c, f"scope {c.scope.describe()} disjoint from question scope {qs.software_revision.describe()}")); continue
                if rel == "UNKNOWN": sr.excluded.append((c, "unknown scope (advisory only)")); continue
            if c.tier == "field_observation" and "field_observation" not in pol.sole_tiers and "admissibility" not in self.ablate:
                sr.excluded.append((c, "field observation (never answer-bearing)")); continue
            if c.assertion_kind not in pol.allowed_kinds and "admissibility" not in self.ablate:
                sr.supporting.append(c); continue
            if c.tier in pol.sole_tiers or "admissibility" in self.ablate: sr.applicable.append(c)
            else: sr.supporting.append(c)
        # representatives. Equal-valued claims are merged (corroboration is counted from the members); KIND/TIER only order EQUAL values
        # for display. A slot with ONE value collapses to one representative; branches survive only where the values actually differ.
        def pick(g):
            maximal = [c for c in g if not any(o is not c and self._contains(o.scope, c.scope) for o in g)] or g
            return sorted(maximal, key=lambda c: (KIND_ORDER[c.assertion_kind], 0 if c.scope.installed_sensor.kind == "ent" else 1, TIER_ORDER[c.tier], c.id))[0]
        by_slot: dict[tuple, list[Claim]] = {}
        for c in sr.applicable: by_slot.setdefault(slot_key(c), []).append(c)
        reps: list[Claim] = []
        for sk, cs in by_slot.items():
            by_v: dict[str, list[Claim]] = {}
            for c in cs: by_v.setdefault(c.vkey(), []).append(c)
            if len(by_v) == 1 or sk[1] not in SINGLE_VALUED:
                for g in by_v.values(): r = pick(g); reps.append(r); sr.members[r.id] = g
            else:
                for g in by_v.values():
                    sc: dict[tuple, list[Claim]] = {}
                    for c in g: sc.setdefault((c.scope.software_revision.describe(), c.scope.line.describe()), []).append(c)
                    cand = [(pick(x), x) for x in sc.values()]
                    for r, x in cand:
                        if any(o is not r and self._contains(o.scope, r.scope) for o, _ in cand): continue
                        reps.append(r); sr.members[r.id] = [m for _, xs in cand for m in xs if m is r or self._contains(r.scope, m.scope) or m in x]
        sr.reps = sorted(reps, key=lambda c: (c.predicate, str(slot_key(c)), c.id))
        # R4 conflicts: from the scope algebra (kb.relations), never adjudicated
        ids = {c.id for r in sr.reps for c in sr.members.get(r.id, [r])}
        agg: dict[tuple, dict] = {}
        for rel in self._rel.get("conflicts_with", []):
            if rel.src in ids and rel.dst in ids:
                x, y = self.kb.claims[rel.src], self.kb.claims[rel.dst]; key = tuple(sorted([x.vkey(), y.vkey()]))
                g = agg.setdefault(key, {"sides": {}, "claims": set(), "scope": set()})
                for cl in (x, y):
                    sd = g["sides"].setdefault(cl.vkey(), {"value": cl.value, "docs": set()}); sd["docs"].add(cl.doc_id); g["claims"].add(cl.id); g["scope"].add(cl.scope.describe())
        for g in agg.values():
            sr.conflicts.append({"sides": [{"value": v["value"], "docs": sorted(v["docs"])} for v in g["sides"].values()], "claims": sorted(g["claims"]), "scope": sorted(g["scope"])})
        # branches: same slot key, different values, disjoint scopes (single-valued predicates only)
        byslot: dict[tuple, list[Claim]] = {}
        for r in sr.reps:
            if r.predicate in SINGLE_VALUED: byslot.setdefault(slot_key(r), []).append(r)
        for k, rs in byslot.items():
            if len({r.vkey() for r in rs}) > 1 and not sr.conflicts:
                sr.branches += [{"claim_id": r.id, "scope": r.scope.describe(), "scope_obj": r.scope, "value": r.value, "historical": r.scope.historical_reference} for r in rs]
        sr.covered = bool(sr.reps)
        if slot.predicate == "applicability_scope" and sr.reps and not sr.branches:
            sr.branches = [{"claim_id": r.id, "scope": r.scope.describe(), "scope_obj": r.scope, "value": r.value, "historical": r.scope.historical_reference} for r in sr.reps]
        return sr

    @staticmethod
    def _contains(outer: Scope, inner: Scope) -> bool:
        a, b = outer.software_revision, inner.software_revision
        if a.kind == "any": return b.kind != "any"
        if a.kind == "ver" and b.kind == "ver":
            m = a.intersect(b); return m is not None and m == b and a != b
        return False

    # ----------------------------------------------------------------- closed-world diff (introduces_*): mention index vs manuals
    def resolve_diff(self, an: Analysis, slot: RequiredFact, sr: SlotResult, decisions: list[Decision]) -> SlotResult:
        want_kind = "component" if slot.predicate == "introduces_component" else "alarm"
        stem = (slot.entity_id or "").split(":", 1)[-1]
        subject = [d for d in self.kb.documents.values() if re.sub(r"\W", "", d.id).endswith(re.sub(r"\W", "", stem)[-12:]) or stem in d.id]
        subj_ids = {d.id for d in subject}
        manual_ids = {d.id for d in self.kb.documents.values() if d.family == "manual"}
        els = self.kb.elements
        in_subject, in_manuals = {}, set()
        for el_id, eid, surf in self.kb.mentions:
            if eid.split(":", 1)[0] != want_kind: continue
            d = els[el_id].document_id
            if d in subj_ids: in_subject.setdefault(eid, []).append((el_id, surf))
            if d in manual_ids: in_manuals.add(eid)
        for eid, refs in sorted(in_subject.items()):
            if eid not in in_manuals:
                sr.diff_new.append({"entity": eid, "name": self.name(eid), "mentions": refs[:2],
                                    "claims": [c.id for c in self.kb.visible() if c.entity_id == eid and c.predicate == "not_covered_in"]})
        sr.subject = sorted(subj_ids); sr.covered = True; sr.negative = not sr.diff_new
        return sr

    # ----------------------------------------------------------------- full resolution
    def resolve(self, an: Analysis) -> Outcome:
        decisions: list[Decision] = []; searched: list[SearchRecord] = []
        pol = POLICY[an.policy]
        qs, meta = self.derive_scope(an, decisions) if an.slots else (Scope(), {"current": False, "r2": False, "unspecified": True})
        results: list[SlotResult] = []
        if an.intent == "UNKNOWN":
            decisions.append(Decision(id=self.did(), kind="NOTE", payload={"type": "unmapped", "analyzer": an.analyzer}))
        for u in an.unresolved_targets:
            decisions.append(Decision(id=self.did(), kind="FALSE_PREMISE", payload={"phrase": u, "note": "matches no entity in the corpus (no alias at any tier)"}))
        for slot in an.slots:
            sr = self.resolve_slot(an, slot, qs, pol, decisions, meta); results.append(sr)
            sname = f"{slot.predicate}({slot.entity_id or slot.entity_mention})"
            searched.append(SearchRecord(slot=sname, documents_searched=len(self.kb.documents), predicates_queried=[DERIVED_SOURCE.get(slot.predicate, slot.predicate)],
                                         n_candidates=len(sr.candidates), n_applicable=len(sr.reps)))
            # advisories (R5): unverified/unknown-scope/field-observation claims for the same slot family
            for c in self.advisory_pool(slot):
                if any(d.kind == "ADVISORY" and d.payload["claim_id"] == c.id for d in decisions): continue
                decisions.append(Decision(id=self.did(), kind="ADVISORY", payload={"claim_id": c.id, "predicate": c.predicate, "value": c.value, "doc": c.doc_id,
                    "why": "needs_review (OCR and transcription disagree)" if c.needs_review else "unreviewed field observation / scope not established" if c.tier == "field_observation" else "scope not established"}))
            for dn in sr.diff_new:
                decisions.append(Decision(id=self.did(), kind="NOTE", payload={"type": "diff_new", "entity": dn["entity"], "name": dn["name"], "claims": dn["claims"], "kind": slot.predicate}))
            if sr.negative:
                decisions.append(Decision(id=self.did(), kind="NOTE", payload={"type": "diff_none", "kind": slot.predicate}))
            if not sr.covered:
                decisions.append(Decision(id=self.did(), kind="GAP", payload={"slot": sname, "predicate": slot.predicate, "entity": slot.entity_id or slot.entity_mention,
                    "explicit_not_specified": [c.id for c in sr.candidates if c.value.get("type") == "not_specified"]}))
            for cf in sr.conflicts:
                if not any(d.kind == "CONFLICT" and d.payload["claims"] == cf["claims"] for d in decisions): decisions.append(Decision(id=self.did(), kind="CONFLICT", payload={"slot": sname, **cf}))
            for r in sr.reps:
                if r.assertion_kind == "inferred": decisions.append(Decision(id=self.did(), kind="INFERRED", payload={"claim_id": r.id, "rule": (r.inference or {}).get("rule_id"), "basis": (r.inference or {}).get("basis", "")}))
            for did_ in sr.subject:
                d = self.kb.documents[did_]
                if d.completeness == "excerpt" and not any(x.kind == "COMPLETENESS" and x.payload.get("doc") == did_ and x.payload.get("completeness") == "excerpt" for x in decisions):
                    decisions.append(Decision(id=self.did(), kind="COMPLETENESS", payload={"doc": did_, "completeness": "excerpt"}))
        # selected claims; expansion: procedure values pull in their steps
        selected: list[Claim] = []
        for sr in results:
            for r in sr.reps:
                if r.id not in {c.id for c in selected}: selected.append(r)
        for sr in results:
            for dn in sr.diff_new:
                for cid in dn["claims"]:
                    if cid not in {c.id for c in selected}: selected.append(self.kb.claims[cid])
        for c in list(selected):
            if c.value.get("type") == "procedure":
                for p in self.kb.visible():
                    if p.predicate == "procedure_steps" and p.entity_id == c.value["id"] and p.id not in {x.id for x in selected} and not p.needs_review: selected.append(p); break
        if an.policy == "IDENTITY":                                    # explain the relationship between the pair (supersession, support window)
            pair = {e for sl in an.slots for e in sl.entity_alternatives}
            has_succession = any(p.predicate == "superseded_by" and {p.entity_id, p.value["id"]} == pair for p in self.kb.visible())
            for p in self.kb.visible():
                if not has_succession: break
                if p.needs_review or p.tier not in ("primary", "reference") or p.id in {x.id for x in selected}: continue
                if (p.predicate == "superseded_by" and {p.entity_id, p.value["id"]} == pair) or (p.predicate in ("supported_on", "installation_restriction") and p.entity_id in pair):
                    if not any(x.predicate == p.predicate and x.entity_id == p.entity_id and x.vkey() == p.vkey() and x.scope.describe() == p.scope.describe() for x in selected):
                        selected.append(p)
        # supersession explanation / graph note / rationale note / completeness refs
        self.notes(an, results, selected, decisions)
        # forbidden values: excluded-by-scope values for the same predicate that are NOT themselves selected
        sel_vals = {c.vkey() for c in selected}; forb = []
        for sr in results:
            for c, why in sr.excluded:
                if why.startswith("scope") and c.value.get("type") == "quantity" and c.vkey() not in sel_vals:
                    f = {"predicate": c.predicate, "value": c.value["value"], "unit": c.value["unit"], "why": why}
                    if f not in forb: forb.append(f)
        status = self.gate(an, results, decisions, meta)
        profiles = {c.id: self.profile(c, results) for c in selected}
        return Outcome(status, results, decisions, searched, selected, forb, qs, profiles)

    # ----------------------------------------------------------------- notes (derived statements the contract may require)
    def notes(self, an, results, selected, decisions):
        sel_docs = {c.doc_id for c in selected}
        if an.policy == "TOPOLOGY":
            for sr in results:
                hub = sr.slot.entity_id
                for c in self.kb.visible():
                    if c.predicate == "connects_to_fluid_path" and c.entity_id == hub:
                        decisions.append(Decision(id=self.did(), kind="NOTE", payload={"type": "graph_edge_not_control", "hub": hub, "other": c.value["id"], "legend": "hydraulic fluid path"}))
        if any(s.slot.predicate == "applicability_scope" for s in results):
            for c in self.kb.visible():
                if c.predicate == "change_rationale" and "manufactured after" in c.value.get("v", ""):
                    decisions.append(Decision(id=self.did(), kind="NOTE", payload={"type": "rationale_not_scope", "claim_id": c.id, "text": c.value["v"],
                        "note": "the corpus does not say whether manufacture date independently restricts applicability"})); break
        if an.policy == "SAFETY_PROCEDURE":
            seen = set()
            for sr in results:
                for rep in sr.reps:
                    for c in sr.members.get(rep.id, [rep]):
                        for ref in self.window_refs(c):
                            if (c.doc_id, ref) not in seen:
                                seen.add((c.doc_id, ref)); decisions.append(Decision(id=self.did(), kind="COMPLETENESS", payload={"doc": c.doc_id, "missing_reference": ref, "claim_id": rep.id}))

    def window_refs(self, c: Claim) -> list[str]:
        """A missing reference matters only if the sentence citing it is near the claim AND shares a number or entity with it."""
        d = self.kb.documents[c.doc_id]
        if not d.references_missing: return []
        ev = self.kb.evidence[c.evidence_ids[0]]; el = self.kb.elements.get(ev.element_id)
        if not el: return []
        sents = re.split(r"(?<=[.!?])\s+", el.text); qi = next((i for i, s in enumerate(sents) if ev.quote[:30] in s), None)
        if qi is None: return []
        nums = lambda t: set(re.findall(r"\d+(?:\.\d+)?", t))
        ents = lambda t: {m["entity_id"] for m in self.R.find_mentions(t) if m["entity_id"]}
        out = []
        for i in range(max(0, qi - 2), min(len(sents), qi + 3)):
            for r in d.references_missing:
                if r.startswith("Section") and r in sents[i] and (nums(sents[i].replace(r, "")) & nums(ev.quote) or ents(sents[i]) & ents(ev.quote)): out.append(r)
        return sorted(set(out))

    # ----------------------------------------------------------------- gate
    def gate(self, an: Analysis, results: list[SlotResult], decisions: list[Decision], meta: dict) -> str:
        if an.unresolved_targets or not an.slots: return "UNANSWERABLE"
        req = [r for r in results if r.slot.required]
        if any(r.conflicts for r in req): return "CONFLICTED"
        cov = [r for r in req if r.covered]
        if len(cov) == len(req):
            scoped = bool(meta.get("current") or meta.get("r2") or any(len({b["claim_id"] for b in r.branches}) > 1 for r in req)
                          or any(d.kind == "COMPLETENESS" and d.payload.get("completeness") == "excerpt" for d in decisions))
            return "ANSWERED_SCOPED" if scoped else "ANSWERED"
        return "PARTIAL" if cov else "UNANSWERABLE"

    # ----------------------------------------------------------------- evidence profile (quality = extraction/read quality ONLY)
    def profile(self, c: Claim, results: list[SlotResult]) -> EvidenceProfile:
        evs = [self.kb.evidence[e] for e in c.evidence_ids]
        pool = [m for sr in results for rs in sr.members.values() for m in rs] + [m for sr in results for m in sr.supporting]
        ind, der = self.corroboration(c, pool)
        agree = "disagree" if any(e.raster_agreement == "disagree" for e in evs) else "agree" if any(e.raster_agreement == "agree" for e in evs) else "n/a"
        methods = sorted({e.method for e in evs})
        if c.needs_review or agree == "disagree": q = "WEAK"
        elif "transcription" in methods or c.assertion_kind in ("structural", "inferred"): q = "REDUCED"
        else: q = "STRONG"
        return EvidenceProfile(claim_id=c.id, assertion_kind=c.assertion_kind, method="+".join(methods), validation={k: v.split(":")[0] for k, v in c.validation.items() if k.startswith("V") or k == "RASTER"},
                               raster_agreement=agree, corroboration_independent=ind, corroboration_derived=der, tier=c.tier, scope_basis=c.scope.scope_basis,
                               open_conflicts=sum(len(sr.conflicts) for sr in results), quality=q)
