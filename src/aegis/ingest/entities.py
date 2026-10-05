"""Stage G: entity resolution (architecture §4.G). Tiered, deterministic, NO fuzzy matching on identifiers.
Hard negatives are enforced by collision detection: two different entities may never share a normalization key."""
from __future__ import annotations
import re
from typing import Optional
from ..models import Alias, Entity
from ..store.db import KnowledgeBase

def norm_key(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", s.upper())

GENERIC = {"THEUNIT", "UNIT", "THESENSOR", "SENSOR", "THEVALVE", "VALVE"}      # rejected: too generic to resolve

def kind_of(entity_id: str) -> str:
    return entity_id.split(":", 1)[0]

class Resolver:
    def __init__(self, kb: KnowledgeBase):
        self.kb = kb
        self._keys: dict[str, Alias] = {}

    @classmethod
    def from_kb(cls, kb: KnowledgeBase) -> "Resolver":
        r = cls(kb)
        for a in kb.aliases: r._keys.setdefault(a.norm_key, a)
        return r

    # ---- building
    def ensure_entity(self, entity_id: str, name: str = "") -> Entity:
        if entity_id not in self.kb.entities:
            self.kb.entities[entity_id] = Entity(id=entity_id, kind=kind_of(entity_id), canonical_name=name or entity_id.split(":", 1)[1])
        return self.kb.entities[entity_id]

    def add_alias(self, entity_id: str, surface: str, status: str, source_doc: str = "") -> Optional[Alias]:
        surface = surface.strip()
        if not surface or surface in ("—", "-", "N/A"): return None
        k = norm_key(surface)
        if not k: return None
        if k in GENERIC and status != "rejected":
            status = "rejected"
        self.ensure_entity(entity_id)
        canon = self.kb.entities[entity_id].canonical_name
        if status == "register" and surface == entity_id.split(":", 1)[1]: status = "exact"
        elif status in ("register", "glossary", "doc_stated", "asserted") and k == norm_key(entity_id.split(":", 1)[1]) and surface != entity_id.split(":", 1)[1]:
            status = "normalized"
        prior = self._keys.get(k)
        if prior and prior.entity_id != entity_id and prior.status != "rejected" and status != "rejected":
            raise ValueError(f"alias collision: {surface!r} -> {entity_id} but key {k} already maps to {prior.entity_id}")
        a = Alias(entity_id=entity_id, surface=surface, norm_key=k, status=status, source_doc=source_doc)
        if prior and prior.entity_id == entity_id: return prior          # keep the strongest/first status
        self._keys[k] = a; self.kb.aliases.append(a)
        return a

    # ---- resolution
    def resolve(self, surface: str) -> Optional[Alias]:
        a = self._keys.get(norm_key(surface))
        return a if a and a.status != "rejected" else None

    def is_rejected(self, surface: str) -> bool:
        a = self._keys.get(norm_key(surface)); return bool(a and a.status == "rejected")

    def surface_status(self, surface: str) -> tuple[str, str]:
        """(entity_id, status) for the SURFACE actually seen: exact | normalized | <alias tier>; ('', 'unresolved') if none."""
        a = self.resolve(surface)
        if not a: return "", "unresolved"
        name = a.entity_id.split(":", 1)[1]
        if surface.strip() == name: return a.entity_id, "exact"
        if norm_key(surface) == norm_key(name): return a.entity_id, "normalized"
        return a.entity_id, a.status

    def eid(self, surface: str) -> Optional[str]:
        a = self.resolve(surface); return a.entity_id if a else None

    # ---- mention detection in free text (questions and element text)
    _ID = re.compile(r"(?<![A-Za-z0-9])((?:[A-Za-z]\.){1,3}[A-Za-z]?\.?\d{1,3}(?:-?[A-Za-z])?|[A-Z]{1,4}-?\d{1,3}[A-Z]?)(?![A-Za-z0-9])")

    def _surface_rx(self):
        surfs = sorted({a.surface for a in self.kb.aliases if a.status != "rejected" and len(a.surface) > 1}, key=len, reverse=True)
        if not hasattr(self, "_rx_cache") or self._rx_cache[0] != len(surfs):
            rx = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(re.escape(s) for s in surfs) + r")(?![A-Za-z0-9])", re.I) if surfs else None
            self._rx_cache = (len(surfs), rx)
        return self._rx_cache[1]

    def find_mentions(self, text: str) -> list[dict]:
        out, seen = [], []
        rx = self._surface_rx()
        spans = []
        if rx:
            for m in rx.finditer(text):
                a = self.resolve(m.group(1))
                if a: spans.append((m.start(), m.end(), m.group(1), a))
        for m in self._ID.finditer(text):
            if any(s <= m.start() and m.end() <= e for s, e, _, _ in spans): continue
            a = self.resolve(m.group(1))
            spans.append((m.start(), m.end(), m.group(1), a))
        for s, e, surf, a in sorted(spans, key=lambda x: x[0]):
            eid, st = self.surface_status(surf)
            out.append({"surface": surf, "entity_id": eid, "status": st, "start": s, "end": e})
        return out

    def build_mention_index(self):
        """element -> entities mentioned (used for closed-world diffs, e.g. 'new in slides vs manuals')."""
        self.kb.mentions.clear()
        for el in self.kb.elements.values():
            for m in self.find_mentions(el.text):
                if m["entity_id"]: self.kb.mentions.append((el.id, m["entity_id"], m["surface"]))
