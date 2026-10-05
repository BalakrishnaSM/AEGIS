"""V7 dual-extraction agreement (architecture §4.E2). The LLM is a SECOND, independent reader. It can corroborate or demote, never add
answer-bearing knowledge on its own:
  agree     -> the LLM duplicate is dropped; the rule claim records V7 PASS (llm agrees)
  disagree  -> both claims are DEMOTED (needs_review): the sources are read two ways
  llm_only  -> the LLM claim is DEMOTED (needs_review): unconfirmed by the deterministic reader
Rule-only claims are NOT demoted (a model's recall failure must not degrade verified knowledge)."""
from __future__ import annotations
from ..ontology import SINGLE_VALUED
from ..scope import scope_rel
from ..store.db import KnowledgeBase
from .relate import slot_key

def _is_llm(kb: KnowledgeBase, c) -> bool: return any(kb.evidence[e].method == "llm" for e in c.evidence_ids)

def llm_extract_all(em, kb: KnowledgeBase, client) -> dict:
    from ..llm import extract_element
    n = calls = 0
    for el in list(kb.elements.values()):
        if el.kind not in ("page_text", "paragraph", "slide_text"): continue
        doc = kb.documents[el.document_id]
        if doc.tier == "field_observation": continue
        calls += 1; n += extract_element(em, doc, el, client)
    return {"elements_sent": calls, "claims_proposed": n}

def dual_agreement(kb: KnowledgeBase) -> dict:
    st = {"agree": 0, "disagree": 0, "llm_only": 0, "rule_only": 0}
    rule = [c for c in kb.claims.values() if c.lifecycle == "ADMITTED" and not _is_llm(kb, c)]
    llm = [c for c in kb.claims.values() if c.lifecycle == "ADMITTED" and _is_llm(kb, c)]
    touched = set()
    for c in llm:
        same_pred = [r for r in rule if r.entity_id == c.entity_id and r.predicate == c.predicate and scope_rel(r.scope, c.scope) == "OVERLAP"]
        eq = [r for r in same_pred if r.vkey() == c.vkey()]
        single = c.predicate in SINGLE_VALUED
        diff = [r for r in same_pred if r.vkey() != c.vkey() and slot_key(r) == slot_key(c)] if single else []
        if diff:
            c.lifecycle = "DEMOTED"; c.needs_review = True; c.validation["V7"] = f"VETO:disagrees with rule claim(s) {[r.id for r in diff]}"
            for r in diff: r.lifecycle = "DEMOTED"; r.needs_review = True; r.validation["V7"] = f"VETO:LLM read this slot differently ({c.id})"; touched.add(r.id)
            st["disagree"] += 1
        elif eq:
            c.lifecycle = "REJECTED"; c.validation["V7"] = f"DUPLICATE:agrees with rule claim {eq[0].id}"
            for r in eq: r.validation["V7"] = f"PASS:llm agrees ({c.id})"; touched.add(r.id)
            st["agree"] += 1
        else:
            c.lifecycle = "DEMOTED"; c.needs_review = True; c.validation["V7"] = "VETO:llm-only (unconfirmed by the deterministic reader)"; st["llm_only"] += 1
        kb.audit.append((c.id, "V7", c.validation["V7"].split(":")[0], c.validation["V7"]))
    st["rule_only"] = sum(1 for r in rule if r.id not in touched and r.lifecycle == "ADMITTED")
    return st
