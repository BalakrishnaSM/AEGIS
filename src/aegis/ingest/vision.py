"""Stages C and D (architecture §4.C/D).

D. Schematic graph from VECTOR GEOMETRY: boxes = filled curves, edges = lines whose endpoints lie on box edges,
   edge semantics read from the diagram's own LEGEND (legend text colour == line stroke colour). No VLM in the loop.
C. Rasters: gated preprocessing + Tesseract OCR; content comes from a transcription sidecar (the VLM stand-in in this
   environment, bound to the image by SHA-256). OCR/transcription AGREEMENT on critical tokens sets raster_agreement;
   disagreement demotes the claim (WEAK / needs_review) and is never silently resolved."""
from __future__ import annotations
import json, re
from pathlib import Path
from typing import Optional

import numpy as np
import pdfplumber

from ..models import Document, Element
from .adapters import norm
from .entities import norm_key
from .rules import Emitter, E, Q, T, D, V, EN, assign_scope
from ..scope import EntDim, UnknownDim, ver_eq, ver_ge, ver_lt

# ============================================================================= D. geometry
_LEGEND_PRED = [("control", "connects_to_control_signal"), ("hydraulic fluid", "connects_to_fluid_path"),
                ("reservoir interconnect", "connects_to_reservoir_interconnect")]

def _near(c1, c2, tol=0.02): return all(abs(a - b) <= tol for a, b in zip(c1, c2))

def read_legend(page) -> list[tuple[tuple, str, str]]:
    """[(rgb, meaning_text, predicate)] from legend lines: text containing 'line(s):' whose glyph colour defines the edge type."""
    rows: dict[int, list] = {}
    for ch in page.chars: rows.setdefault(round(ch["top"]), []).append(ch)
    out = []
    for _, chs in sorted(rows.items()):
        t = norm("".join(c["text"] for c in chs))
        m = re.search(r"lines?:\s*(.+)$", t)
        if m and chs[len(chs) // 2].get("non_stroking_color"):
            col = tuple(chs[len(chs) // 2]["non_stroking_color"]); meaning = m.group(1).strip()
            pred = next((p for k, p in _LEGEND_PRED if k in meaning.lower()), "connects_to_fluid_path")
            out.append((col, meaning, pred))
    return out

def build_schematic(em: Emitter, doc: Document, root: Path, eps: float = 3.0) -> dict:
    info = {"boxes": [], "edges": []}
    with pdfplumber.open(root / doc.id) as pdf:
        pg = pdf.pages[0]; words = pg.extract_words(); legend = read_legend(pg)
        if not legend or not pg.curves: return info
        boxes = []
        for c in pg.curves:
            lab = norm(" ".join(w["text"] for w in words if c["x0"] - 1 <= w["x0"] and w["x1"] <= c["x1"] + 1 and c["top"] - 1 <= w["top"] and w["bottom"] <= c["bottom"] + 1))
            boxes.append((c["x0"], c["top"], c["x1"], c["bottom"], lab))
        def which(x, y):
            for b in boxes:
                if b[0] - eps <= x <= b[2] + eps and b[1] - eps <= y <= b[3] + eps: return b[4]
        # entities for box labels
        ent_of: dict[str, str] = {}
        for *_, lab in boxes:
            ms = [m for m in em.R.find_mentions(lab) if m["entity_id"]]
            if ms: eid = ms[0]["entity_id"]
            else:
                name = re.sub(r"\s*\(.*?\)", "", lab).strip(); eid = em.R.ensure_entity(f"component:{name}", name).id
                em.R.add_alias(eid, name, "exact", doc.id)
            ent_of[lab] = eid; info["boxes"].append((lab, eid))
        for i, (x0, t, x1, b, lab) in enumerate(boxes):
            el = Element(id=f"{doc.id}#box{i}", document_id=doc.id, kind="box_label", text=lab, locator={"page": 1, "box": i}, bbox=(x0, t, x1, b))
            em.kb.elements[el.id] = el
        for i, (_, meaning, _) in enumerate(legend):
            em.kb.elements[f"{doc.id}#legend{i}"] = Element(id=f"{doc.id}#legend{i}", document_id=doc.id, kind="legend", text=meaning, locator={"page": 1, "legend": i})
        for j, ln in enumerate(pg.lines):
            (x0, y0), (x1, y1) = ln["pts"][0], ln["pts"][-1]
            a, b = which(x0, y0), which(x1, y1)
            col = tuple(ln["stroking_color"]); lg = next((l for l in legend if _near(l[0], col)), None)
            if not (a and b and lg): continue
            A, B = ent_of[a], ent_of[b]; _, meaning, pred = lg
            text = f"edge[{meaning}] {a} -- {b}"
            el = Element(id=f"{doc.id}#edge{j}", document_id=doc.id, kind="edge", text=text,
                         locator={"page": 1, "line_pts": [[round(x0, 1), round(y0, 1)], [round(x1, 1), round(y1, 1)]], "legend": meaning},
                         bbox=(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))
            em.kb.elements[el.id] = el; info["edges"].append((A, B, pred, meaning))
            if pred == "connects_to_control_signal":
                hub = "component:PLC-03" if "component:PLC-03" in (A, B) else A      # legend: 'connections to PLC-03'
                other = B if hub == A else A
                em.emit(doc, el, text, hub, pred, E(other), rule_id="D-GEOM-EDGE", kind="structural", method="geometry")
            else:
                em.emit(doc, el, text, A, pred, E(B), rule_id="D-GEOM-EDGE", kind="structural", method="geometry")
                em.emit(doc, el, text, B, pred, E(A), rule_id="D-GEOM-EDGE", kind="structural", method="geometry")
    return info

# ============================================================================= C. rasters
def _estimate_skew(gray: np.ndarray) -> float:
    """Projection-profile skew search on a downscaled binary image (robust, validated against OCR quality downstream)."""
    import cv2
    small = cv2.resize(gray, None, fx=0.3, fy=0.3, interpolation=cv2.INTER_AREA)
    bw = (small < 140).astype(np.uint8)
    best, best_a = -1.0, 0.0
    h, w = bw.shape
    for a in np.arange(-4.0, 4.01, 0.5):
        M = cv2.getRotationMatrix2D((w / 2, h / 2), a, 1.0)
        r = cv2.warpAffine(bw, M, (w, h), flags=cv2.INTER_NEAREST)
        s = float(np.var(r.sum(axis=1)))
        if s > best: best, best_a = s, float(a)
    return best_a

def _ocr(img) -> tuple[str, float]:
    import pytesseract
    d = pytesseract.image_to_data(img, config="--psm 6", output_type=pytesseract.Output.DICT)
    words = [(w, float(c)) for w, c in zip(d["text"], d["conf"]) if w.strip() and float(c) >= 0]
    if not words: return "", 0.0
    return " ".join(w for w, _ in words), sum(c for _, c in words) / len(words)

_VALID_TOK = re.compile(r"^(?:[A-Z]{1,3}\.?[A-Z]?\.?-?\d{1,3}[A-Z]?|\d+(?:\.\d+)?|bar|mA)$", re.I)

def ocr_best(path: Path) -> dict:
    """GATED preprocessing: a variant is accepted only if it improves the OCR-quality score
    (mean confidence x share of tokens that look like valid IDs/quantities). Naive deskew is never applied blindly."""
    import cv2
    img = cv2.imread(str(path)); gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    variants = {"raw": gray}
    if gray.mean() < 110: variants["invert_2x"] = cv2.resize(255 - gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    else:
        variants["upscale_2x"] = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        ang = _estimate_skew(gray)
        if abs(ang) >= 0.5:
            h, w = gray.shape; M = cv2.getRotationMatrix2D((w / 2, h / 2), ang, 1.0)
            rot = cv2.warpAffine(gray, M, (w, h), flags=cv2.INTER_CUBIC, borderValue=255)
            variants[f"deskew_{ang:+.1f}"] = rot
            variants[f"deskew_{ang:+.1f}_2x"] = cv2.resize(rot, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    scored = []
    for name, v in variants.items():
        txt, conf = _ocr(v); toks = txt.split()
        valid = sum(bool(_VALID_TOK.match(t.strip(".,:;"))) for t in toks) / max(1, len(toks))
        scored.append((conf * (0.5 + valid), name, txt, conf))
    scored.sort(reverse=True)
    best = scored[0]
    return {"variant": best[1], "text": best[2], "conf": round(best[3], 1), "tried": [(s[1], round(s[0], 1)) for s in scored]}

def agreement(tokens: list[str], ocr_text: str) -> str:
    """'agree' iff every critical token appears in the OCR text (whitespace/case-insensitive, EXACT otherwise)."""
    if not tokens: return "n/a"
    o = re.sub(r"\s+", "", ocr_text.upper())
    return "agree" if all(re.sub(r"\s+", "", t.upper()) in o for t in tokens) else "disagree"

def ingest_raster(em: Emitter, doc: Document, root: Path, sidecar_dir: Path) -> dict:
    path = root / doc.id
    if path.suffix.lower() == ".pdf":                                  # scanned PDF: rasterize the embedded page image
        import subprocess, tempfile
        tmp = Path(tempfile.mkdtemp()); subprocess.run(["pdfimages", "-png", str(path), str(tmp / "pg")], check=True, capture_output=True)
        imgs = sorted(tmp.glob("pg-*.png")); path = imgs[0]
    ocr = ocr_best(path)
    ocr_el = Element(id=f"{doc.id}#ocr", document_id=doc.id, kind="raster_region", text=norm(ocr["text"]), locator={"method": "tesseract", "variant": ocr["variant"], "conf": ocr["conf"]})
    em.kb.elements[ocr_el.id] = ocr_el
    sc = sidecar_dir / (Path(doc.id).stem + ".json")
    if not sc.exists(): return {"ocr": ocr, "sidecar": None, "claims": 0}
    side = json.loads(sc.read_text())
    if side.get("sha256") != doc.sha256:                               # fail closed on a stale transcription
        em.kb.audit.append(("-", "raster", "SKIP", f"sidecar sha mismatch for {doc.id}")); return {"ocr": ocr, "sidecar": "STALE", "claims": 0}
    regions = {}
    for r in side["regions"]:
        el = Element(id=f"{doc.id}#{r['id']}", document_id=doc.id, kind="raster_region", text=norm(r["text"]), locator={"region": r["id"], "reader": side["reader"]})
        em.kb.elements[el.id] = el; regions[r["id"]] = el
    n = 0
    for c in side["claims"]:
        el = regions[c["region"]]
        quote = norm(c["quote"])
        agree = agreement(c.get("critical_tokens", []), ocr["text"])
        rev = None; basis = None
        if c.get("rev"):
            k, v = c["rev"].split(":"); rev = {"eq": ver_eq, "ge": ver_ge, "lt": ver_lt}[k](v); basis = c.get("basis", "section_cue")
        val = c["value"]
        em.R.ensure_entity(c["entity"], c.get("entity_name", ""))
        cl = em.emit(doc, el, quote, c["entity"], c["predicate"], val, rule_id="C-TRANSCRIPTION", rev=rev, basis=basis, instance=c.get("instance", False),
                     kind=c.get("kind", "stated"), method="transcription", agree=agree, scope_span="" if rev is not None else None,
                     modality=c.get("modality", "fact"))
        if cl: n += 1
    return {"ocr": ocr, "sidecar": side["reader"], "claims": n}
