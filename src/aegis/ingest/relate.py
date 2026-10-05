"""Stage H (architecture §4.H): relations are DERIVED from the scope algebra, never hand-authored.
Also the inference rules (every inferred claim has a derivation record: rule id + supporting claims)."""
from __future__ import annotations
from itertools import combinations
from typing import Optional

from ..models import Claim, Relation
from ..ontology import SINGLE_VALUED
from ..scope import Scope, scope_rel
from ..store.db import KnowledgeBase
from .entities import Resolver
from .rules import Emitter, T, assign_scope

def slot_key(c: Claim) -> tuple:
    return (c.entity_id, c.predicate, c.value.get("item") if c.predicate == "requires_state" else None)

def independent(kb: KnowledgeBase, a: Claim, b: Claim) -> bool:
    """Independent corroboration needs different documents, neither derived from the other."""
    if a.doc_id == b.doc_id: return False
    da, db = kb.documents[a.doc_id], kb.documents[b.doc_id]
    return b.doc_id not in da.derived_from and a.doc_id not in db.derived_from

def build_relations(kb: KnowledgeBase) -> dict:
    kb.relations.clear()
    stats = {"conflicts_with": 0, "version_scoped_with": 0, "corroborates": 0, "derived_from": 0, "supersedes": 0}
    groups: dict[tuple, list[Claim]] = {}
    for c in kb.visible():
        if c.needs_review: continue
        groups.setdefault(slot_key(c), []).append(c)
    for key, cs in groups.items():
        for a, b in combinations(cs, 2):
            rel = scope_rel(a.scope, b.scope)
            same = a.vkey() == b.vkey()
            if same and rel == "OVERLAP":
                ind = independent(kb, a, b)
                kb.relations.append(Relation(kind="corroborates", src=a.id, dst=b.id, independent=ind, note="same value, overlapping scope"))
                stats["corroborates"] += 1
                if not ind and a.doc_id != b.doc_id:
                    kb.relations.append(Relation(kind="derived_from", src=a.id, dst=b.id, independent=False)); stats["derived_from"] += 1
            elif not same and key[1] in SINGLE_VALUED:
                if rel == "OVERLAP": kb.relations.append(Relation(kind="conflicts_with", src=a.id, dst=b.id, note=f"{a.vkey()} vs {b.vkey()} overlap")); stats["conflicts_with"] += 1
                elif rel == "DISJOINT": kb.relations.append(Relation(kind="version_scoped_with", src=a.id, dst=b.id, note=f"{a.scope.describe()} | {b.scope.describe()}")); stats["version_scoped_with"] += 1
                # UNKNOWN scope: no relation; the claim stays an advisory
    for c in kb.visible():
        if c.predicate == "superseded_by" and not c.needs_review:
            kb.relations.append(Relation(kind="supersedes", src=c.value["id"], dst=c.entity_id, note=f"effective {c.scope.software_revision.describe()}")); stats["supersedes"] += 1
    for d in kb.documents.values():
        for old in d.supersedes: kb.relations.append(Relation(kind="supersedes", src=d.id, dst=old, note="document revision")); stats["supersedes"] += 1
        for src in d.derived_from: kb.relations.append(Relation(kind="derived_from", src=d.id, dst=src, independent=False, note="document-level")); stats["derived_from"] += 1
    return stats

def _new_inferred(em: Emitter, kb: KnowledgeBase, entity: str, predicate: str, value: dict, scope: Scope, support: list[Claim], rule_id: str, extra: Optional[dict] = None) -> Claim:
    em.nc += 1
    evs = []
    for s in support:
        for e in s.evidence_ids:
            if e not in evs: evs.append(e)
    c = Claim(id=f"C{em.nc:04d}", entity_id=entity, predicate=predicate, value=value, scope=scope, assertion_kind="inferred",
              evidence_ids=evs, doc_id=support[0].doc_id, tier=support[0].tier, inference={"rule_id": rule_id, "supporting_claim_ids": [s.id for s in support], **(extra or {})})
    c.validation["scope_span"] = ""
    kb.claims[c.id] = c
    kb.derivations.append({"claim_id": c.id, "rule_id": rule_id, "uses": [s.id for s in support]})
    return c

MAP_PREDS = ("normal_operating_pressure", "alarm_trigger_condition", "reset_pressure_limit")

def _qkey(c: Claim) -> Optional[tuple]:
    v = c.value
    if v.get("type") == "quantity": return (v["value"], v["unit"])
    if v.get("type") == "condition" and v.get("quantity"): return (v["quantity"]["value"], v["quantity"]["unit"])
    return None

def infer(kb: KnowledgeBase, R: Resolver, em: Emitter) -> dict:
    n = {"R-CFGKEY-01": 0, "R-TAG-RESOLVE": 0, "ambiguous": 0}
    stated = [c for c in kb.visible() if c.assertion_kind == "stated" and not c.needs_review and c.predicate in MAP_PREDS]
    for k in [c for c in kb.visible() if c.predicate == "config_key_value" and c.value.get("type") == "quantity"]:
        kq = (k.value["value"], k.value["unit"])
        hits = [c for c in stated if _qkey(c) == kq and scope_rel(k.scope, c.scope) == "OVERLAP"]
        targets = {(c.predicate, c.entity_id) for c in hits}
        if len(targets) == 1:
            pred, ent = next(iter(targets))
            _new_inferred(em, kb, k.entity_id, "config_key_maps_to", T(f"{pred} of {ent}"), k.scope, [k] + hits, "R-CFGKEY-01",
                          {"maps_to": {"predicate": pred, "entity": ent}, "basis": "value and revision applicability match stated claims in the manuals"})
            n["R-CFGKEY-01"] += 1
        elif len(targets) > 1:
            kb.audit.append((k.id, "R-CFGKEY-01", "SKIP", f"ambiguous mapping {sorted(targets)}")); n["ambiguous"] += 1
    for c in [c for c in kb.visible() if c.predicate == "displays_tag" and not c.needs_review]:
        eid, status = R.surface_status(c.value["v"])
        if eid and status in ("exact", "normalized", "register", "glossary", "doc_stated"):
            _new_inferred(em, kb, c.entity_id, "matches_entity", {"type": "entity", "id": eid, "resolution": status}, c.scope, [c], "R-TAG-RESOLVE",
                          {"resolution_status": status, "surface": c.value["v"]}); n["R-TAG-RESOLVE"] += 1
    for sup in [c for c in kb.visible() if c.predicate == "superseded_by" and not c.needs_review and c.assertion_kind == "stated"]:
        old, new = sup.entity_id, sup.value["id"]
        if any(c.predicate == "is_distinct_from" and {c.entity_id, c.value.get("id")} == {old, new} for c in kb.visible()): continue
        _new_inferred(em, kb, new, "is_distinct_from", {"type": "entity", "id": old, "relation": "superseded_by"}, sup.scope, [sup], "R-SUPERSEDED-DISTINCT",
                      {"basis": "one is recorded as superseded by the other, so they are different components"}); n["R-SUPERSEDED-DISTINCT"] = n.get("R-SUPERSEDED-DISTINCT", 0) + 1
    for cid in [c.id for c in kb.claims.values() if c.assertion_kind == "inferred" and c.lifecycle == "PROPOSED"]:
        kb.claims[cid].assertion_kind = "structural" if kb.claims[cid].predicate == "matches_entity" else "inferred"
    return n
