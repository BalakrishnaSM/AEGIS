"""Deterministic baseline: chunk + BM25 retrieval, extractive answer (top-1 chunk). It never abstains.
NOT representative of an LLM RAG system. The full-context-stuffing baseline needs an LLM and was NOT RUN (no API key)."""
from __future__ import annotations
import re
from rank_bm25 import BM25Okapi

def _tok(s): return re.findall(r"[a-z0-9][a-z0-9.\-]*", s.lower())

class BM25Baseline:
    def __init__(self, kb):
        self.chunks = []
        for el in kb.elements.values():
            if el.kind in ("page_text", "paragraph", "slide_text"):
                sents = re.split(r"(?<=[.!?])\s+", el.text)
                for i in range(len(sents)): self.chunks.append((" ".join(sents[i:i + 2]), el.document_id))
            elif el.kind in ("row", "table_row", "json_leaf", "raster_region", "edge"): self.chunks.append((el.text, el.document_id))
        self.bm = BM25Okapi([_tok(c[0]) for c in self.chunks])

    def ask(self, q):
        scores = self.bm.get_scores(_tok(q)); i = max(range(len(scores)), key=scores.__getitem__)
        return self.chunks[i]
