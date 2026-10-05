"""Offline tests of the production LLM layer (injected call functions / stubs). NOT live-API tests."""
import copy, os
from pathlib import Path
import pytest
from aegis.config import Settings
from aegis.llm import AnthropicClient, CircuitBreaker, LLMUnavailable, ResponseCache
DATASET = os.environ.get("AEGIS_DATASET", "/tmp/aegis/task-data/aegis-dataset/aegis-dataset")

class RateLimitError(Exception): status_code = 429
class BadRequest(Exception): status_code = 400
def S(**kw): return Settings(llm_models=kw.pop("models", ("m1", "m2")), llm_max_retries=kw.pop("retries", 3), llm_breaker_failures=kw.pop("bf", 3), llm_hourly_token_cap=kw.pop("cap", 10_000))
def client(fn, **kw): return AnthropicClient(S(**kw), call_fn=fn, cache=ResponseCache(":memory:"))

def test_breaker_opens_and_half_opens():
    t = [0.0]; b = CircuitBreaker(3, 30, clock=lambda: t[0])
    for _ in range(3): b.fail()
    assert not b.allow() and b.state == "open"
    t[0] = 31; assert b.allow()                                   # half-open probe
    b.ok(); assert b.state == "closed" and b.allow()

def test_transient_errors_are_retried_then_succeed():
    calls = []
    def fn(m, s, u, sc):
        calls.append(m)
        if len(calls) < 3: raise RateLimitError()
        return {"ok": 1}, (10, 5, 0)
    c = client(fn); assert c.json("s", "u", {}) == {"ok": 1} and calls == ["m1"] * 3 and c.usage.calls == 1 and c.usage.input_tokens == 10

def test_non_retryable_falls_back_to_next_model():
    def fn(m, s, u, sc):
        if m == "m1": raise BadRequest()
        return {"from": m}, (1, 1, 0)
    c = client(fn); assert c.json("s", "u", {}) == {"from": "m2"} and c.usage.failures == 1

def test_cache_hit_avoids_a_second_call():
    n = []
    def fn(m, s, u, sc): n.append(1); return {"v": 1}, (1, 1, 0)
    c = client(fn); c.json("s", "u", {}); c.json("s", "u", {}); assert len(n) == 1 and c.usage.cache_hits == 1

def test_all_models_fail_raises_unavailable_and_trips_breaker():
    def fn(m, s, u, sc): raise BadRequest()
    c = client(fn, bf=2)
    for i in range(2):
        with pytest.raises(LLMUnavailable): c.json("s", f"u{i}", {})
    with pytest.raises(LLMUnavailable, match="breaker"): c.json("s", "u-new", {})
    assert c.stats()["breaker"] == "open"

def test_hourly_token_cap():
    c = client(lambda m, s, u, sc: ({"v": 1}, (6000, 6000, 0)), cap=10_000)
    c.json("s", "a", {})
    with pytest.raises(LLMUnavailable, match="cap"): c.json("s", "b", {})

# ---------------------------------------------------------------- analyzer cascade
class Stub:
    def __init__(self, payload): self.payload = payload; self.n = 0
    def json(self, system, user, schema):
        self.n += 1
        if isinstance(self.payload, Exception): raise self.payload
        return self.payload

def test_rules_first_then_llm_only_for_unmapped(kb):
    from aegis.query.pipeline import Engine
    stub = Stub({"intent": "ALARM_LOOKUP", "policy": "SAFETY_PROCEDURE", "slots": [{"predicate": "alarm_note", "entity_mention": "A17"}]})
    eng = Engine(kb, llm=stub, analyzer_mode="rules_then_llm")
    eng.ask("What is the location of IV-21?"); assert stub.n == 0                      # rules mapped it: no model call
    env = eng.ask("What happens to alarm A17 if it clears by itself?")
    assert stub.n == 1 and env.trace["analysis"]["analyzer"] == "llm" and env.status == "ANSWERED" and env.contract_verified

def test_invalid_or_failing_model_output_falls_back_to_rules(kb):
    from aegis.query.pipeline import Engine
    for payload in ({"intent": "x", "policy": "PARAMETER", "slots": [{"predicate": "made_up_predicate"}]}, LLMUnavailable("down"), {"garbage": 1}):
        env = Engine(kb, llm=Stub(payload), analyzer_mode="rules_then_llm").ask("What happens to alarm A17 if it clears by itself?")
        assert env.trace["analysis"]["analyzer"] == "rules:none" and env.status == "UNANSWERABLE"

def test_llm_cannot_resolve_entities_itself(kb):
    from aegis.query.llm_analyzer import llm_analyze
    from aegis.ingest.entities import Resolver
    stub = Stub({"intent": "PARAMETER_QUERY", "policy": "PARAMETER", "slots": [{"predicate": "max_operating_temperature", "entity_mention": "the voltage sensor"}]})
    an = llm_analyze("max temperature of the voltage sensor?", Resolver.from_kb(kb), stub)
    assert an.slots[0].entity_id is None and an.unresolved_targets == ["the voltage sensor"]        # unresolved -> false premise downstream

# ---------------------------------------------------------------- V7 dual-extraction agreement
def _llm_claims(kb, proposals, doc="manuals/operator_manual.pdf", needle="200 bar"):
    from aegis.ingest.entities import Resolver
    from aegis.ingest.rules import Emitter
    from aegis.ingest.validate import validate_all
    from aegis.ingest.dual import dual_agreement
    from aegis.llm import extract_element
    k = copy.deepcopy(kb); R = Resolver.from_kb(k); em = Emitter(k, R, Path(DATASET)); em.nc, em.ne = len(k.claims), len(k.evidence)
    el = next(e for e in k.elements.values() if e.document_id == doc and e.kind == "page_text" and needle in e.text)
    for p in proposals: extract_element(em, k.documents[doc], el, Stub({"claims": [p]}))
    validate_all(k, R); return k, dual_agreement(k)

Q200 = "Normal HPU discharge pressure is 200 bar on software revision 3.2 and later."
def prop(v, q=Q200, **kw): return dict({"subject": "HPU", "predicate": "normal_operating_pressure", "value": {"type": "quantity", "value": v, "unit": "bar"}, "scope_op": "ge", "scope_version": "3.2", "quote": q}, **kw)
def llm_claims(k): return [c for c in k.claims.values() if any(k.evidence[e].method == "llm" for e in c.evidence_ids)]

def test_v7_agreement_drops_duplicate_and_marks_rule_claim(kb):
    k, st = _llm_claims(kb, [prop(200)]); assert st["agree"] == 1 and st["disagree"] == 0 and st["llm_only"] == 0
    assert any(c.validation.get("V7", "").startswith("PASS") for c in k.claims.values() if c not in llm_claims(k))

def test_a_value_the_quote_does_not_contain_never_reaches_v7(kb):
    k, st = _llm_claims(kb, [prop(190)])                                     # V2 rejects 190 against a 200-bar quote
    assert all(c.lifecycle == "REJECTED" for c in llm_claims(k)) and st == {"agree": 0, "disagree": 0, "llm_only": 0, "rule_only": st["rule_only"]}

def test_v7_disagreement_demotes_both_readings(kb):
    q = "Alongside the sensor change, the normal HPU discharge pressure setpoint is revised from 180 bar to 200 bar."
    k, st = _llm_claims(kb, [prop(180, q=q, scope_op=None, scope_version=None)], doc="engineering_bulletins/ECN-1042.pdf", needle="revised from 180 bar")
    assert st["disagree"] == 1                                              # the model read 180 as the new (>=3.2) value: passes V1-V5, contradicts the rule reader
    demoted = [c for c in k.claims.values() if c.lifecycle == "DEMOTED"]
    assert any(k.evidence[e].method == "llm" for c in demoted for e in c.evidence_ids) and any(c.validation.get("V7", "").startswith("VETO") and not any(k.evidence[e].method == "llm" for e in c.evidence_ids) for c in demoted)

def test_v7_llm_only_claim_is_demoted_never_answer_bearing(kb):
    from aegis.query.pipeline import Engine
    q = "Rotating components inside the HPU housing are exposed when the panel is off."
    k, st = _llm_claims(kb, [dict(subject="HPU", predicate="hazard", value={"type": "text", "v": "rotating components exposed when the panel is off"}, quote=q)], needle="Rotating components")
    assert st["llm_only"] == 1 and all(c.lifecycle == "DEMOTED" and c.needs_review for c in llm_claims(k))
    env = Engine(k).ask("What must be true before starting the Hydraulic Power Unit?")
    assert not set(env.claim_ids) & {c.id for c in llm_claims(k)} and env.status == "ANSWERED"
