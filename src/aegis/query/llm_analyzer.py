"""LLM analyzer: maps a question onto the CLOSED predicate list. It never answers. Entities are resolved by the DETERMINISTIC resolver
(the model only supplies mention strings), predicates/policies are schema enums, and any invalid output falls back to the rule analyzer."""
from __future__ import annotations
from typing import Optional
from pydantic import ValidationError
from ..ingest.entities import Resolver
from ..llm import AnalysisProposal, Client, LLMUnavailable, SYSTEM_ANALYZE
from ..models import Analysis, QuestionScope, RequiredFact
from ..quantity import parse_quantities
from ..scope import ver_eq, ver_ge, ver_lt

def llm_analyze(question: str, R: Resolver, client: Client) -> Optional[Analysis]:
    try: p = AnalysisProposal.model_validate(client.json(SYSTEM_ANALYZE, f"<question>{question}</question>", AnalysisProposal.model_json_schema()))
    except (ValidationError, LLMUnavailable, RuntimeError, KeyError, StopIteration): return None
    if not p.slots: return None
    slots, unresolved = [], []
    for s in p.slots:
        if s.entity_mentions_pair and len(s.entity_mentions_pair) == 2:
            ids = [R.eid(m) for m in s.entity_mentions_pair]
            if not all(ids): unresolved += [m for m, i in zip(s.entity_mentions_pair, ids) if not i]; slots.append(RequiredFact(predicate=s.predicate)); continue
            slots.append(RequiredFact(predicate=s.predicate, entity_id=ids[0], entity_alternatives=ids, value_filter={"pair": ids})); continue
        eid = R.eid(s.entity_mention) if s.entity_mention else None
        if s.entity_mention and not eid: unresolved.append(s.entity_mention)
        vf = {"op": s.value_op, "quantity": {"value": s.value_number, "unit": s.value_unit or "bar"}} if (s.value_op and s.value_number is not None) else None
        slots.append(RequiredFact(predicate=s.predicate, entity_id=eid, entity_mention=s.entity_mention, value_filter=vf))
    rev = "CURRENT" if p.asks_current else "UNSPECIFIED"
    if p.revision_op and p.revision_version: rev = {"ge": ver_ge, "lt": ver_lt, "eq": ver_eq}[p.revision_op](p.revision_version)
    q = parse_quantities(question)
    ments = [{k: m[k] for k in ("surface", "entity_id", "status")} for m in R.find_mentions(question)]
    return Analysis(question=question, intent=p.intent, policy=p.policy, slots=slots, scope=QuestionScope(software_revision=rev), mentions=ments,
                    unresolved_targets=unresolved, focus_value={"value": q[0][0], "unit": q[0][1]} if q else None, analyzer="llm")
