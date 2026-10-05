"""Query pipeline (architecture §2 query): analyzer -> applicability resolver -> gate -> contract -> composer -> verifier,
with a replayable trace. A query always returns an answer that has passed the deterministic output-contract verifier
('contract-verified'); this is NOT a claim that the answer is true."""
from __future__ import annotations
import hashlib, json, time, uuid
from typing import Optional

from ..ingest.entities import Resolver
from ..ingest.relate import independent
from ..models import AnswerEnvelope, Sentence
from ..store.db import KnowledgeBase
from .analyzer import analyze
from .compose import Composer, verify
from .resolver import AppResolver

QUALITY_RANK = {"STRONG": 0, "REDUCED": 1, "WEAK": 2}

class Engine:
    def __init__(self, kb: KnowledgeBase, ablate: Optional[set] = None, llm=None, analyzer_mode: str = "rules"):
        self.kb, self.llm, self.analyzer_mode = kb, llm, analyzer_mode
        self.R = getattr(kb, "resolver", None) or Resolver.from_kb(kb)
        self.ablate = ablate or set()

    def _analyze(self, question: str):
        """Deterministic first; the model handles only the long tail the rules cannot map (or everything in 'llm' mode). Invalid model output falls back."""
        from .llm_analyzer import llm_analyze
        mode = self.analyzer_mode if self.llm is not None else "rules"
        if mode == "llm":
            an = llm_analyze(question, self.R, self.llm); return an or analyze(question, self.R)
        an = analyze(question, self.R)
        if mode == "rules_then_llm" and an.intent == "UNKNOWN":
            return llm_analyze(question, self.R, self.llm) or an
        return an

    def ask(self, question: str) -> AnswerEnvelope:
        t0 = time.perf_counter(); trace: dict = {"question": question, "store_hash": self.kb.store_hash(), "ablate": sorted(self.ablate)}
        an = self._analyze(question); trace["trace_id"] = uuid.uuid4().hex[:12]; trace["analysis"] = an.model_dump(mode="json"); trace["t_analyze_ms"] = round((time.perf_counter() - t0) * 1000, 2)
        res = AppResolver(self.kb, self.R, self.ablate); out = res.resolve(an)
        trace["slots"] = [{"slot": f"{r.slot.predicate}({r.slot.entity_id or r.slot.entity_mention})", "candidates": [c.id for c in r.candidates],
                           "selected": [c.id for c in r.reps], "excluded": [(c.id, why) for c, why in r.excluded], "supporting": [c.id for c in r.supporting],
                           "branches": [b["claim_id"] for b in r.branches], "conflicts": r.conflicts, "covered": r.covered} for r in out.slots]
        trace["decisions"] = [d.model_dump(mode="json") for d in out.decisions]
        trace["scope"] = out.qscope.describe(); trace["status"] = out.status
        comp = Composer(self.kb); sents = comp.compose(an, out); fails = verify(self.kb, an, out, sents)
        trace["verifier_failures"] = fails
        trace["answer_sha"] = hashlib.sha256(" ".join(x.text for x in sents).encode()).hexdigest()[:12]
        trace["t_total_ms"] = round((time.perf_counter() - t0) * 1000, 2)
        ev, seen = [], set()
        def add(c, e, role):
            k = (e.document_id, e.element_id, e.quote)
            if k in seen: return
            seen.add(k); ev.append({"claim_id": c.id, "role": role, "document": e.document_id, "page": e.page, "bbox": e.bbox, "locator": e.locator, "quote": e.quote, "method": e.method, "raster_agreement": e.raster_agreement})
        for c in out.selected:
            for eid in c.evidence_ids: add(c, self.kb.evidence[eid], "primary")
        for sr in out.slots:                                                    # corroboration: equal-valued claims from other documents
            for rep in sr.reps:
                for m in sr.members.get(rep.id, []):
                    if m.id != rep.id and m.doc_id != rep.doc_id:
                        role = "corroborating" if independent(self.kb, rep, m) else "derived"
                        for eid in m.evidence_ids[:1]: add(rep, self.kb.evidence[eid], role)
        qual = "N/A" if not out.profiles else max((p.quality for p in out.profiles.values()), key=lambda q: QUALITY_RANK[q])
        dd = lambda k: [d.payload | {"id": d.id} for d in out.decisions if d.kind == k]
        env = AnswerEnvelope(question=question, status=out.status, sentences=sents, claim_ids=[c.id for c in out.selected], evidence=ev,
                             scope_branches=[{"claim_id": b["claim_id"], "scope": b["scope"], "value": b["value"]} for r in out.slots for b in r.branches],
                             conflicts=dd("CONFLICT"), advisories=dd("ADVISORY"), gaps=dd("GAP") + dd("FALSE_PREMISE"), assumptions=dd("SCOPE_ASSUMPTION"),
                             searched=out.searched, profiles=list(out.profiles.values()), quality=qual, contract_verified=not fails, verifier_failures=fails, trace=trace)
        return env

def replay(engine: Engine, trace: dict) -> dict:
    """Deterministic replay: re-run the question against the same store version and compare."""
    env = engine.ask(trace["question"])
    return {"same_store": trace["store_hash"] == engine.kb.store_hash(), "same_status": trace["status"] == env.status,
            "same_answer": trace["answer_sha"] == env.trace["answer_sha"]}
