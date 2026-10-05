"""Scope algebra (architecture §5): versions, dimensions, ANY != UNKNOWN, overlap/meet, cue grammar."""
from __future__ import annotations
import re
from typing import Annotated, Literal, Optional, Union
from pydantic import BaseModel, Field

# ----------------------------------------------------------------------------- versions
def parse_version(s: str) -> tuple[int, ...]:
    return tuple(int(x) for x in s.strip().split("."))

def vkey(t: tuple[int, ...]) -> tuple[int, ...]:
    """Comparison key: pads with zeros so (3,2) == (3,2,0) < (3,2,1) < (3,10). Never a float."""
    return tuple(list(t) + [0] * (4 - len(t)))

def vstr(t: tuple[int, ...]) -> str:
    return ".".join(map(str, t))

# ----------------------------------------------------------------------------- dimensions
class AnyDim(BaseModel):
    kind: Literal["any"] = "any"
    def describe(self) -> str: return "any"

class UnknownDim(BaseModel):
    kind: Literal["unknown"] = "unknown"
    def describe(self) -> str: return "unknown"

class VerDim(BaseModel):
    kind: Literal["ver"] = "ver"
    lo: Optional[str] = None
    hi: Optional[str] = None
    lo_inc: bool = True
    hi_inc: bool = False

    def _lo(self): return vkey(parse_version(self.lo)) if self.lo else None
    def _hi(self): return vkey(parse_version(self.hi)) if self.hi else None

    def contains(self, v: str) -> bool:
        k = vkey(parse_version(v)); lo, hi = self._lo(), self._hi()
        if lo is not None and (k < lo or (k == lo and not self.lo_inc)): return False
        if hi is not None and (k > hi or (k == hi and not self.hi_inc)): return False
        return True

    def is_empty(self) -> bool:
        lo, hi = self._lo(), self._hi()
        if lo is None or hi is None: return False
        return lo > hi or (lo == hi and not (self.lo_inc and self.hi_inc))

    def intersect(self, o: "VerDim") -> Optional["VerDim"]:
        lo, lo_inc = self.lo, self.lo_inc
        if o.lo is not None and (lo is None or vkey(parse_version(o.lo)) > vkey(parse_version(lo))
                                 or (vkey(parse_version(o.lo)) == vkey(parse_version(lo)) and not o.lo_inc)):
            lo, lo_inc = o.lo, o.lo_inc
        hi, hi_inc = self.hi, self.hi_inc
        if o.hi is not None and (hi is None or vkey(parse_version(o.hi)) < vkey(parse_version(hi))
                                 or (vkey(parse_version(o.hi)) == vkey(parse_version(hi)) and not o.hi_inc)):
            hi, hi_inc = o.hi, o.hi_inc
        r = VerDim(lo=lo, hi=hi, lo_inc=lo_inc, hi_inc=hi_inc)
        return None if r.is_empty() else r

    def describe(self) -> str:
        if self.lo and self.hi and self.lo == self.hi and self.lo_inc and self.hi_inc: return f"={self.lo}"
        if self.lo and not self.hi: return f"{'≥' if self.lo_inc else '>'}{self.lo}"
        if self.hi and not self.lo: return f"{'≤' if self.hi_inc else '<'}{self.hi}"
        if self.lo and self.hi: return f"{'[' if self.lo_inc else '('}{self.lo}, {self.hi}{']' if self.hi_inc else ')'}"
        return "all versions"

class EntDim(BaseModel):
    kind: Literal["ent"] = "ent"
    ids: list[str]
    def describe(self) -> str: return "{" + ", ".join(sorted(self.ids)) + "}"

Dim = Annotated[Union[AnyDim, UnknownDim, VerDim, EntDim], Field(discriminator="kind")]

def ver_ge(v: str) -> VerDim: return VerDim(lo=v, lo_inc=True)
def ver_lt(v: str) -> VerDim: return VerDim(hi=v, hi_inc=False)
def ver_eq(v: str) -> VerDim: return VerDim(lo=v, hi=v, lo_inc=True, hi_inc=True)
def ver_between(a: str, b: str) -> VerDim: return VerDim(lo=a, hi=b, lo_inc=True, hi_inc=True)

ScopeBasis = Literal["explicit_cue", "section_cue", "document_applies_to", "document_default",
                     "instance_observation", "unknown"]

class Scope(BaseModel):
    software_revision: Dim = Field(default_factory=AnyDim)
    installed_sensor: Dim = Field(default_factory=AnyDim)
    line: Dim = Field(default_factory=AnyDim)
    scope_basis: ScopeBasis = "document_default"
    historical_reference: bool = False

    def dims(self): return {"software_revision": self.software_revision, "installed_sensor": self.installed_sensor, "line": self.line}
    def describe(self) -> str:
        parts = [f"{k}={d.describe()}" for k, d in self.dims().items() if d.kind != "any"]
        return ", ".join(parts) if parts else "any"

# ----------------------------------------------------------------------------- algebra (architecture §5.2)
Rel = Literal["OVERLAP", "DISJOINT", "UNKNOWN"]

def rel_dim(a, b) -> Rel:
    """Per-dimension overlap. ANY x X -> OVERLAP; UNKNOWN x anything -> UNKNOWN; concrete x concrete -> intersect?"""
    if a.kind == "unknown" or b.kind == "unknown": return "UNKNOWN"
    if a.kind == "any" or b.kind == "any": return "OVERLAP"
    if a.kind == "ver" and b.kind == "ver": return "OVERLAP" if a.intersect(b) is not None else "DISJOINT"
    if a.kind == "ent" and b.kind == "ent": return "OVERLAP" if set(a.ids) & set(b.ids) else "DISJOINT"
    raise TypeError(f"incomparable dimension kinds {a.kind} vs {b.kind}")

def combine(rels: list[Rel]) -> Rel:
    """Any DISJOINT decides (a definite separation holds even if another dimension is unknown);
    else any UNKNOWN; else OVERLAP."""
    if "DISJOINT" in rels: return "DISJOINT"
    if "UNKNOWN" in rels: return "UNKNOWN"
    return "OVERLAP"

def scope_rel(a: Scope, b: Scope) -> Rel:
    da, db = a.dims(), b.dims()
    return combine([rel_dim(da[k], db[k]) for k in da])

def meet_dim(a, b):
    """Narrowing. ANY ^ X = X; UNKNOWN ^ X = UNKNOWN; concrete ^ concrete = intersection (None = empty)."""
    if a.kind == "unknown" or b.kind == "unknown": return UnknownDim()
    if a.kind == "any": return b
    if b.kind == "any": return a
    if a.kind == "ver": return a.intersect(b)
    ids = sorted(set(a.ids) & set(b.ids)); return EntDim(ids=ids) if ids else None

# ----------------------------------------------------------------------------- cue grammar (used by V3 and scope assignment)
_V = r"(\d+(?:\.\d+)*)"
_REV = r"(?:software\s+|firmware\s+|controller\s+firmware\s+)?(?:revisions?|rev\.?|firmware)"
_CUES: list[tuple[str, re.Pattern, str]] = [
    ("lt", re.compile(r"(?:prior\s+to|before|older\s+than|earlier\s+than)\s+(?:" + _REV + r"\s+)?" + _V, re.I), "lt"),
    ("only2", re.compile(_REV + r"\s+" + _V + r"\s+and\s+" + _V + r"\s+only", re.I), "between"),
    ("ge_later", re.compile(_REV + r"\s+" + _V + r"\s+(?:and|or)\s+(?:later|above|newer)", re.I), "ge"),
    ("ge_from", re.compile(r"(?:as\s+of|from|effective|starting\s+(?:at|with))\s*:?\s+(?:" + _REV + r"\s+)?" + _V, re.I), "ge"),
    ("ge_op", re.compile(r">=\s*" + _V), "ge"),
]

def parse_cues(text: str) -> list[VerDim]:
    """Version-scope cues in a sentence, in order of appearance, de-duplicated; spans nested in a longer match are dropped."""
    hits = []
    for _, rx, kind in _CUES:
        for m in rx.finditer(text):
            hits.append((m.start(), m.end(), kind, m.groups()))
    hits.sort(key=lambda h: (h[0], -(h[1] - h[0])))
    kept = []
    for h in hits:
        if any(k[0] <= h[0] and h[1] <= k[1] for k in kept): continue
        kept.append(h)
    out: list[VerDim] = []
    for _, _, kind, g in sorted(kept):
        d = ver_lt(g[0]) if kind == "lt" else ver_ge(g[0]) if kind == "ge" else ver_between(g[0], g[1])
        if d not in out: out.append(d)
    return out
