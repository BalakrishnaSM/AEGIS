"""End-to-end properties on the real corpus."""
import json, re
from pathlib import Path
import pytest
GOLD = json.loads((Path(__file__).resolve().parents[1] / "eval/golden_traces.json").read_text())["traces"]

@pytest.mark.parametrize("t", GOLD, ids=[t["question_id"] for t in GOLD])
def test_status_matches_gold(engine, t):
    env = engine.ask(t["question_text"])
    assert env.status == t["expected_status"], env.text()
    assert env.contract_verified, env.verifier_failures

def test_unanswerable_has_no_source_sentences(engine):
    for q in ("What is the mean time between failures for the isolation valve IV-21?", "Who approved engineering bulletin ECN-1058?"):
        env = engine.ask(q); assert env.status == "UNANSWERABLE" and not any(s.kind == "SOURCE" for s in env.sentences)

def test_not_specified_is_a_gap_not_a_value(engine):
    env = engine.ask("What is the calibration interval for PS-04A?")
    assert env.status == "UNANSWERABLE" and any("explicitly" in s.text for s in env.sentences)

def test_current_is_resolved_not_max_version(engine):
    env = engine.ask("What is the current normal operating pressure for the HPU, and under what conditions does that apply?")
    assert env.assumptions and env.assumptions[0]["resolved"] == "3.2.1" and "3.3" in env.assumptions[0]["planned_excluded"]

def test_forbidden_value_never_in_source(engine):
    env = engine.ask("What is the current normal operating pressure for the HPU, and under what conditions does that apply?")
    assert not re.search(r"\b180\b", " ".join(s.text for s in env.sentences if s.kind == "SOURCE"))

def test_hard_negatives_have_distinct_keys(kb):
    from aegis.ingest.entities import norm_key
    assert len({norm_key(x) for x in ("PS-04", "PS-04A", "PS-40")}) == 3
    R = engine_resolver(kb)
    assert R.eid("PS-04") != R.eid("PS-04A") != R.eid("PS-40")
    assert R.surface_status("P.S.04-A") == ("component:PS-04A", "normalized") and R.surface_status("PS-04A")[1] == "exact"

def engine_resolver(kb):
    from aegis.ingest.entities import Resolver
    return Resolver.from_kb(kb)

def test_alias_collision_is_an_error(kb):
    from aegis.ingest.entities import Resolver
    from aegis.store.db import KnowledgeBase
    R = Resolver(KnowledgeBase()); R.add_alias("component:A", "PS-04A", "register")
    with pytest.raises(ValueError): R.add_alias("component:B", "PS04A", "register")

def test_trace_replay_is_deterministic(engine):
    from aegis.query.pipeline import replay
    env = engine.ask("Under what circumstances must the controller not be reset?")
    r = replay(engine, env.trace); assert all(r.values())

def test_every_selected_claim_is_cited_and_quotes_resolve(kb, engine):
    env = engine.ask("What does alarm A17 indicate, and what are its possible causes?")
    assert {i for s in env.sentences for i in s.claim_ids} == set(env.claim_ids)
    for e in env.evidence: assert e["quote"]

def test_scope_ablation_breaks_forbidden_value(kb):
    from aegis.query.pipeline import Engine
    env = Engine(kb, ablate={"scope"}).ask("What was the pressure limit before revision 3.2?")
    assert re.search(r"\b200 bar", env.text())                               # without the algebra the wrong-revision value leaks in

def test_inferred_claims_are_labelled_and_have_derivations(kb):
    inf = [c for c in kb.claims.values() if c.assertion_kind == "inferred"]
    assert inf and all(c.inference and c.inference["rule_id"] and c.inference["supporting_claim_ids"] for c in inf)
    assert {d["claim_id"] for d in kb.derivations} >= {c.id for c in inf}

def test_no_conflicts_in_the_official_corpus_and_version_scoped_present(kb):
    kinds = {r.kind for r in kb.relations}
    assert "version_scoped_with" in kinds and "conflicts_with" not in kinds

def test_sqlite_roundtrip_and_fts(kb, tmp_path):
    from aegis.store.db import KnowledgeBase
    p = str(tmp_path / "k.db"); kb.save(p); k2 = KnowledgeBase.load(p)
    assert k2.store_hash() == kb.store_hash() and len(k2.claims) == len(kb.claims) and k2.fts("shutdown procedure")

def test_paraphrase_analyzer_long_tail(kb):
    from aegis.query.analyzer import analyze
    from aegis.ingest.entities import Resolver
    R = Resolver.from_kb(kb)
    cases = [
        ("What pressure should the HPU reach during normal running on the latest software?", "normal_operating_pressure"),
        ("What should happen if A17 stays active longer than ten seconds?", "alarm_required_action"),
        ("What is the flash point of the hydraulic fluid?", "flash_point"),
        ("Which software revision introduced PS-04A?", "requires_software_revision"),
        ("What startup checks does the training deck list?", "requires_state"),
        ("What is the supply voltage for the HCS?", "supply_voltage"),
    ]
    for q, pred in cases:
        a = analyze(q, R)
        assert a.intent != "UNKNOWN", q
        assert pred in {s.predicate for s in a.slots}, (q, a)
