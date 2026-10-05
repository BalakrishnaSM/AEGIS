"""Ingest orchestration (architecture §2 ingest): adapters -> seed entities -> geometry/rasters -> extraction ->
E1/E2 validation -> inference -> relations -> mention index -> SQLite."""
from __future__ import annotations
import re
from pathlib import Path
from typing import Optional

from ..store.db import KnowledgeBase
from .adapters import link_documents, load_document, norm
from .entities import Resolver
from .relate import build_relations, infer
from .rules import Emitter, extract_document, seed_glossary, seed_register
from .validate import validate_all
from .vision import build_schematic, ingest_raster

_DOCNO = re.compile(r"Document\s+(AEG-[A-Z]{2}-\d{3})")
_TYPES = ("Operator Manual", "Maintenance Manual", "Alarm Reference", "Terminology Glossary")

def _files(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and not any(x.startswith(".") for x in p.parts))

def ingest(root: str | Path, db_path: Optional[str] = None, sidecar_dir: str | Path = "data/transcriptions",
           overrides: Optional[dict] = None, veto=None, llm=None) -> tuple[KnowledgeBase, dict]:
    root = Path(root); kb = KnowledgeBase(); R = Resolver(kb); em = Emitter(kb, R, root); report: dict = {"files": 0, "raster": {}, "geometry": {}}
    texts: dict[str, str] = {}
    for rel in _files(root):
        doc, els = load_document(root, rel, overrides)
        kb.documents[rel] = doc
        for e in els: kb.elements[e.id] = e
        texts[rel] = " ".join(e.text for e in els); report["files"] += 1
    link_documents(kb.documents, texts)
    # document entities (by document number and by title keyword)
    for rel, t in texts.items():
        m = _DOCNO.search(t)
        if m:
            ty0 = next((ty for ty in _TYPES if ty in t[:300]), None)
            de = R.ensure_entity(f"document:{m.group(1)}", ty0 or m.group(1)).id; R.add_alias(de, m.group(1), "doc_stated", rel)
            for ty in _TYPES:
                if ty in t[:300]: R.add_alias(de, ty, "doc_stated", rel)
    # system entity + per-document / per-screen entities so questions can name them
    se = R.ensure_entity("system:Aegis Series-7 HCS", "Aegis Series-7 HCS").id
    for a in ("Aegis Series-7 HCS", "Series-7 HCS", "Aegis HCS", "HCS"): R.add_alias(se, a, "doc_stated", "(seed)")
    for rel, d in kb.documents.items():
        stem = Path(rel).stem
        if d.family == "slides":
            de = R.ensure_entity(f"document:{stem}", "Training slide excerpt").id
            for a in ("training slide deck", "training slides", "slide deck", "training deck", stem): R.add_alias(de, a, "doc_stated", rel)
        if d.family == "notes":
            de = R.ensure_entity(f"document:{stem}", "Site survey notes").id
            for a in ("site survey notes", "site notes", "field notes", stem): R.add_alias(de, a, "doc_stated", rel)
        if d.family == "screenshot":
            key = stem.split("_", 2)[-1]; sc = R.ensure_entity(f"screen:{key}", f"{key.capitalize()} screen").id
            for a in (f"{key} screenshot", f"{key} screen"): R.add_alias(sc, a, "doc_stated", rel)
    # seed entities from structured reference sources FIRST so IDs resolve everywhere
    for rel, doc in kb.documents.items():
        els = [e for e in kb.elements.values() if e.document_id == rel]
        seed_register(em, doc, els)
    for rel, doc in kb.documents.items():
        if doc.family == "reference" and rel.endswith(".docx"): seed_glossary(em, doc, [e for e in kb.elements.values() if e.document_id == rel])
    # geometry and rasters
    for rel, doc in kb.documents.items():
        els = [e for e in kb.elements.values() if e.document_id == rel]
        if doc.family == "diagram" and rel.endswith(".pdf"):
            info = build_schematic(em, doc, root)
            if info["edges"]: report["geometry"][rel] = {"boxes": len(info["boxes"]), "edges": info["edges"]}
        if not els and (rel.lower().endswith((".png", ".jpg", ".jpeg")) or (rel.endswith(".pdf") and doc.family == "scan")):
            report["raster"][rel] = ingest_raster(em, doc, root, Path(sidecar_dir))
    # extraction
    for rel, doc in kb.documents.items():
        extract_document(em, doc, [e for e in kb.elements.values() if e.document_id == rel])
    for eid, ent in list(kb.entities.items()):                      # config keys are addressable by their printed name
        if ent.kind == "config": R.add_alias(eid, ent.canonical_name, "exact", "configuration")
    if llm is not None:                                             # second, independent reader (V7); proposals pass the same V1-V5
        from .dual import llm_extract_all
        report["llm_extract"] = llm_extract_all(em, kb, llm)
    R.build_mention_index()
    report["validation"] = validate_all(kb, R, veto)
    if llm is not None:
        from .dual import dual_agreement
        report["dual_extraction"] = dual_agreement(kb)
    report["inference"] = infer(kb, R, em)
    report["validation_inferred"] = validate_all(kb, R, veto)
    report["relations"] = build_relations(kb)
    R.build_mention_index()
    kb.resolver = R                                                 # convenience handle (not persisted)
    if db_path: kb.save(db_path)
    report["store_hash"] = kb.store_hash()
    report["claims"] = {s: sum(1 for c in kb.claims.values() if c.lifecycle == s) for s in ("ADMITTED", "DEMOTED", "REJECTED")}
    return kb, report
