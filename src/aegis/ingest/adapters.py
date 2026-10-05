"""Stage A: deterministic format adapters. Every format becomes Elements with locators (architecture §4.A)."""
from __future__ import annotations
import hashlib, html as htmllib, json, re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Optional

import openpyxl, pdfplumber
from docx import Document as DocxDocument
from pptx import Presentation

from ..models import Document, Element
from ..scope import UnknownDim, VerDim, parse_cues

_Q = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u00a0": " "})

def norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s).translate(_Q)).strip()

def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()

# (path regex, family, tier). Tier is DESCRIPTIVE. Unknown files default to (ext family, 'reference').
CLASSIFY: list[tuple[str, str, str]] = [
    (r"manuals/.*", "manual", "primary"),
    (r"engineering_bulletins/.*", "ecn", "primary"),
    (r"reference/alarm_reference.*", "manual", "primary"),
    (r"configuration/.*", "config", "primary"),
    (r"reference/.*", "reference", "reference"),
    (r"diagrams/.*", "diagram", "primary"),
    (r"extra/.*", "slides", "derived"),
    (r"low_trust/.*", "notes", "field_observation"),
    (r"noise/.*", "sds", "reference"),
    (r"screenshots/.*", "screenshot", "field_observation"),
    (r"scans/.*", "scan", "reference"),
]

def classify(rel: str, overrides: Optional[dict] = None) -> tuple[str, str]:
    if overrides and rel in overrides:
        o = overrides[rel]; return o.get("family", "unknown"), o.get("tier", "reference")
    for rx, fam, tier in CLASSIFY:
        if re.fullmatch(rx, rel): return fam, tier
    return "unknown", "reference"

# ----------------------------------------------------------------------------- per-format element extraction
def _pdf(root: Path, rel: str) -> list[Element]:
    els: list[Element] = []
    with pdfplumber.open(root / rel) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            txt = norm(page.extract_text() or "")
            if txt:
                els.append(Element(id=f"{rel}#p{i}", document_id=rel, kind="page_text", text=txt, locator={"page": i}))
            for ti, tbl in enumerate(page.extract_tables()):
                if not tbl: continue
                hdr = [norm(c or "") for c in tbl[0]]
                for ri, row in enumerate(tbl[1:], 1):
                    cells = {h: norm(c or "") for h, c in zip(hdr, row)}
                    els.append(Element(id=f"{rel}#p{i}t{ti}r{ri}", document_id=rel, kind="table_row",
                                       text=" | ".join(f"{h}: {v}" for h, v in cells.items()),
                                       locator={"page": i, "table": ti, "row": ri, "cells": cells, "row_key": next(iter(cells.values()))}))
    return els

def _xlsx(root: Path, rel: str) -> list[Element]:
    els = []
    wb = openpyxl.load_workbook(root / rel, data_only=True)
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True)); hdr = [str(h) for h in rows[0]]
        for ri, r in enumerate(rows[1:], 2):
            cells = {h: ("" if v is None else str(v)) for h, v in zip(hdr, r)}     # versions kept as TEXT
            key = cells[hdr[0]]
            els.append(Element(id=f"{rel}#{ws.title}!{ri}", document_id=rel, kind="row",
                               text=" | ".join(f"{h}: {v}" for h, v in cells.items()),
                               locator={"sheet": ws.title, "row": ri, "row_key": key, "cells": cells}))
    return els

def _docx(root: Path, rel: str) -> list[Element]:
    d = DocxDocument(root / rel); els = []
    for i, p in enumerate(d.paragraphs):
        if p.text.strip(): els.append(Element(id=f"{rel}#para{i}", document_id=rel, kind="paragraph", text=norm(p.text), locator={"paragraph": i}))
    for ti, t in enumerate(d.tables):
        for ri, r in enumerate(t.rows):
            cells = [norm(c.text) for c in r.cells]
            if ri == 0: continue
            els.append(Element(id=f"{rel}#t{ti}r{ri}", document_id=rel, kind="row", text=" | ".join(cells),
                               locator={"table": ti, "row": ri, "cells": cells, "row_key": cells[0]}))
    return els

def _pptx(root: Path, rel: str) -> list[Element]:
    els = []
    for si, s in enumerate(Presentation(root / rel).slides, 1):
        for shi, sh in enumerate(s.shapes):
            if sh.has_text_frame and sh.text_frame.text.strip():
                els.append(Element(id=f"{rel}#s{si}sh{shi}", document_id=rel, kind="slide_text", text=norm(sh.text_frame.text), locator={"slide": si}))
            if getattr(sh, "has_table", False) and sh.has_table:
                for ri, r in enumerate(sh.table.rows):
                    cells = [norm(c.text) for c in r.cells]
                    if ri == 0: continue
                    els.append(Element(id=f"{rel}#s{si}t{shi}r{ri}", document_id=rel, kind="row", text=" | ".join(cells),
                                       locator={"slide": si, "row": ri, "cells": cells, "row_key": cells[0]}))
    return els

class _Blocks(HTMLParser):
    BLOCK = {"p", "li", "h1", "h2", "h3", "h4", "div", "tr", "td", "th", "br", "section", "ul", "ol", "table"}
    def __init__(self): super().__init__(); self.out = [[]]; self.skip = 0
    def handle_starttag(self, t, a):
        if t in ("script", "style"): self.skip += 1
        if t in self.BLOCK: self.out.append([])
    def handle_endtag(self, t):
        if t in ("script", "style"): self.skip -= 1
        if t in self.BLOCK: self.out.append([])
    def handle_data(self, d):
        if not self.skip: self.out[-1].append(d)

def _html(root: Path, rel: str) -> list[Element]:
    p = _Blocks(); p.feed((root / rel).read_text())
    els = []
    for i, parts in enumerate(p.out):
        t = norm(htmllib.unescape("".join(parts)))
        t = re.sub(r"\s+([.,;:])", r"\1", t)
        if t: els.append(Element(id=f"{rel}#b{i}", document_id=rel, kind="paragraph", text=t, locator={"dom_block": i}))
    return els

def _json(root: Path, rel: str) -> list[Element]:
    data = json.loads((root / rel).read_text(encoding="utf-8")); els = []
    def walk(o: Any, path: str):
        if isinstance(o, dict):
            for k, v in o.items(): walk(v, f"{path}.{k}" if path else k)
        else:
            els.append(Element(id=f"{rel}#{path}", document_id=rel, kind="json_leaf", text=f"{path} = {json.dumps(o)}",
                               locator={"json_path": path, "value": o}))
    walk(data, ""); return els

def _is_text_pdf(root: Path, rel: str) -> bool:
    with pdfplumber.open(root / rel) as pdf:
        return any((p.extract_text() or "").strip() for p in pdf.pages)

# ----------------------------------------------------------------------------- document metadata (generic, not per-file)
_DOCNO = re.compile(r"Document\s+(AEG-[A-Z]{2}-\d{3})")
_REVNO = re.compile(r"(?<!Software )(?<!software )Revision\s+(\d+)(?![.\d])")

def extract_meta(doc: Document, els: list[Element]) -> None:
    full = " ".join(e.text for e in els if e.kind in ("page_text", "paragraph", "slide_text"))
    m = _REVNO.search(full)
    if m: doc.revision = m.group(1)
    for e in els:
        if e.kind in ("page_text", "paragraph") and re.search(r"[Aa]pplies to", e.text):
            seg = re.search(r"[Aa]pplies to[^|.]*(?:\.\d)?[^|]*", e.text)
            cues = parse_cues(seg.group(0)) if seg else []
            if cues: doc.applies_to = cues[0]; break
    if isinstance(doc.applies_to, UnknownDim) and doc.family == "ecn":
        m = re.search(r"Effective:\s*Software Revision\s+(\d+(?:\.\d+)*)", full)
        if m: doc.applies_to = parse_cues(m.group(0))[0]
    doc.title = doc.title or (els[0].text[:80] if els else "")
    # completeness
    if re.search(r"Slides?\s+\d+\s*[–-]\s*\d+\s+of the full deck", full): doc.completeness = "excerpt"
    for mm in re.finditer(r"(AEG-[A-Z]{2}-\d{3})[^.]{0,80}?not part of this package", full):
        doc.references_missing.append(mm.group(1)); doc.completeness = "references_missing"
    heads = {m.group(2) for m in re.finditer(r"(\S+)\s+(\d+)\.\s+[A-Z][a-z]", full) if m.group(1).lower() != "section"}
    for mm in re.finditer(r"(?<!Manual )(?<!Manual, )Section\s+(\d+)(?!\s+of\s+the)", full):
        n = mm.group(1)
        if n not in heads and f"Section {n}" not in doc.references_missing and doc.id.startswith("manuals/"):
            doc.references_missing.append(f"Section {n}"); doc.completeness = "references_missing"

def link_documents(docs: dict[str, Document], texts: dict[str, str]) -> None:
    """supersedes: same document number, higher revision supersedes lower. derived_from: derived docs naming other docs."""
    byno: dict[str, list[Document]] = {}
    for d in docs.values():
        m = _DOCNO.search(texts.get(d.id, ""))
        if m: byno.setdefault(m.group(1), []).append(d)
    for lst in byno.values():
        lst.sort(key=lambda d: int(d.revision or 0))
        for i, hi in enumerate(lst):
            for lo in lst[:i]: hi.supersedes.append(lo.id)
    nos = {no: ds[-1].id for no, ds in byno.items()}
    for d in docs.values():
        if d.tier == "derived":
            for no in re.findall(r"\((AEG-[A-Z]{2}-\d{3})\)", texts.get(d.id, "")):
                if no in nos and nos[no] != d.id: d.derived_from.append(nos[no])

def load_document(root: Path, rel: str, overrides: Optional[dict] = None) -> tuple[Document, list[Element]]:
    p = root / rel; ext = p.suffix.lower()
    fam, tier = classify(rel, overrides)
    doc = Document(id=rel, sha256=sha256_file(p), family=fam, tier=tier)
    if ext == ".pdf": els = _pdf(root, rel) if _is_text_pdf(root, rel) else []
    elif ext == ".xlsx": els = _xlsx(root, rel)
    elif ext == ".docx": els = _docx(root, rel)
    elif ext == ".pptx": els = _pptx(root, rel)
    elif ext in (".html", ".htm"): els = _html(root, rel)
    elif ext == ".json": els = _json(root, rel)
    elif ext in (".png", ".jpg", ".jpeg"): els = []             # rasters: see vision.py
    else: els = []
    if els: extract_meta(doc, els)
    return doc, els

# ----------------------------------------------------------------------------- bbox location for evidence
_wcache: dict[tuple, list] = {}
def locate_bbox(root: Path, rel: str, page: int, quote: str) -> Optional[tuple[float, float, float, float]]:
    key = (str(root), rel, page)
    if key not in _wcache:
        with pdfplumber.open(root / rel) as pdf: _wcache[key] = pdf.pages[page - 1].extract_words()
    words = _wcache[key]; toks = norm(quote).split()
    if not toks: return None
    wt = [norm(w["text"]) for w in words]
    for i in range(len(wt) - len(toks) + 1):
        if wt[i:i + len(toks)] == toks:
            ws = words[i:i + len(toks)]
            return (round(min(w["x0"] for w in ws), 1), round(min(w["top"] for w in ws), 1),
                    round(max(w["x1"] for w in ws), 1), round(max(w["bottom"] for w in ws), 1))
    return None
