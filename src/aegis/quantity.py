"""Quantity normalization (architecture §4.F): 0.18 MPa == 180 bar."""
from __future__ import annotations
import re
from typing import Optional
import pint

ureg = pint.UnitRegistry()
_ALIAS = {"bar": "bar", "mpa": "MPa", "psi": "psi", "v": "volt", "vdc": "volt", "vac": "volt", "volt": "volt", "volts": "volt",
          "ma": "milliampere", "s": "second", "sec": "second", "second": "second", "seconds": "second",
          "month": "month", "months": "month", "°c": "degC", "c": "degC"}
_DIM = {"bar": "pressure", "MPa": "pressure", "psi": "pressure", "volt": "voltage", "milliampere": "current",
        "second": "time", "month": "time", "degC": "temperature"}
_CANON = {"pressure": "bar", "voltage": "volt", "current": "milliampere", "time": "second", "temperature": "degC"}

_QRX = re.compile(r"(-?\d+(?:\.\d+)?)\s*(bar|MPa|psi|VDC|VAC|V|mA|seconds?|sec|s|months?|°\s?C)(?![A-Za-z])")

def canon_unit(u: str) -> Optional[str]:
    return _ALIAS.get(u.replace(" ", "").lower())

def dimension_of(unit: str) -> Optional[str]:
    return _DIM.get(unit)

def normalize(value: float, unit: str) -> tuple[float, str]:
    """Return (value in canonical unit of the dimension, canonical unit)."""
    u = canon_unit(unit) or unit
    dim = _DIM.get(u)
    if dim is None: return value, u
    cu = _CANON[dim]
    q = ureg.Quantity(value, u).to(cu)
    return round(float(q.magnitude), 6), cu

def parse_quantities(text: str) -> list[tuple[float, str]]:
    """All normalized quantities in a text, e.g. 'reaches 200 bar' -> [(200.0, 'bar')]."""
    out = []
    for m in _QRX.finditer(text):
        u = canon_unit(m.group(2))
        if u: out.append(normalize(float(m.group(1)), u))
    return out

def qeq(a: tuple[float, str], b: tuple[float, str], tol: float = 1e-6) -> bool:
    return a[1] == b[1] and abs(a[0] - b[0]) <= tol * max(1.0, abs(a[0]))

def fmt(value: float, unit: str) -> str:
    v = int(value) if float(value).is_integer() else value
    return f"{v} {'°C' if unit == 'degC' else 'V' if unit == 'volt' else 's' if unit == 'second' else unit}"
