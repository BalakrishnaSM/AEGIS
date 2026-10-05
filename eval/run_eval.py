"""Run the whole evaluation and write eval/results/*. Usage: python eval/run_eval.py <dataset_root> [--heldout-first-run]"""
from __future__ import annotations
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src")); sys.path.insert(0, str(Path(__file__).resolve().parent))
from aegis.ingest.pipeline import ingest
from aegis.query.pipeline import Engine, replay
import expect, metrics, baselines, mutations

HERE = Path(__file__).resolve().parent; ROOT = HERE.parent
def main(dataset: str):
    out = HERE / "results"; out.mkdir(exist_ok=True)
    gold = json.loads((HERE / "golden_traces.json").read_text())["traces"]; qtext = {t["question_id"]: t["question_text"] for t in gold}
    gold_ev = {}
    for t in gold:
        p, a = set(), set()
        for e in t["expected_evidence"]:
            if e["document"].startswith("*"): continue
            if e["role"] == "primary": p.add(e["document"])
            if e["role"] not in metrics.NON_GOLD_ROLES: a.add(e["document"])
        gold_ev[t["question_id"]] = {"primary": p, "all": a}
    t0 = time.perf_counter(); kb, rep = ingest(dataset, str(ROOT / "data/aegis.db"), str(ROOT / "data/transcriptions")); ing_s = round(time.perf_counter() - t0, 2)
    eng = Engine(kb); envs = {q: eng.ask(t) for q, t in qtext.items()}
    rows = [metrics.score_official(q, expect.OFFICIAL[q], envs[q], kb, gold_ev) for q in qtext]
    res = {"ingest": {"seconds": ing_s, "files": rep["files"], "claims": rep["claims"], "store_hash": rep["store_hash"], "relations": rep["relations"], "inference": rep["inference"]},
           "official": {"full_system": {"agg": metrics.aggregate(rows), "rows": rows}}}
    # ablations
    for name, ab in (("ablate_scope_algebra", {"scope"}), ("ablate_admissibility_policy", {"admissibility"})):
        e2 = Engine(kb, ablate=ab); r2 = [metrics.score_official(q, expect.OFFICIAL[q], e2.ask(t), kb, gold_ev) for q, t in qtext.items()]
        res["official"][name] = {"agg": metrics.aggregate(r2), "critical_by_q": {r["id"]: r["critical"] for r in r2 if r["critical"]}}
    # baseline
    bl = baselines.BM25Baseline(kb); brow = []
    for q, t in qtext.items():
        txt, doc = bl.ask(t); spec = expect.OFFICIAL[q]
        class Fake:  # same checks, answer treated as SOURCE text
            status = "ANSWERED"; contract_verified = True; sentences = []; evidence = [{"document": doc, "role": "primary"}]; claim_ids = []; trace = {"slots": [], "analysis": {"slots": []}, "t_total_ms": 0}
            def text(self): return txt
        f = Fake(); from aegis.models import Sentence; f.sentences = [Sentence(text=txt, kind="SOURCE")]
        miss, forb = metrics.check_text({**spec, "must": spec.get("must", []) + spec.get("must_derived", []), "must_derived": []}, f)
        crit, major = metrics.severity(spec, "ANSWERED", miss, forb, True)
        brow.append(dict(id=q, status="ANSWERED", expected=spec["status"], status_ok=spec["status"] == "ANSWERED", class_ok=metrics.CLASS[spec["status"]] == "A", critical=crit, major=major,
                         must_hit=len(spec.get("must", [])) + len(spec.get("must_derived", [])) - len(miss), must_total=len(spec.get("must", [])) + len(spec.get("must_derived", [])), answer=txt[:160]))
    res["official"]["baseline_bm25_extractive"] = {"agg": {"n": len(brow), "status_exact": sum(r["status_ok"] for r in brow), "answerability_class": sum(r["class_ok"] for r in brow),
        "critical_items": sum(1 for r in brow if r["critical"]), "major_items": sum(1 for r in brow if r["major"]), "must_hit": (sum(r["must_hit"] for r in brow), sum(r["must_total"] for r in brow))}}
    res["official"]["baseline_full_context_llm"] = "NOT RUN (requires an LLM; no API key in this environment)"
    # held-out: first run is recorded verbatim
    hrows = [metrics.score_heldout(h, eng.ask(h["q"])) for h in expect.HELDOUT]
    res["heldout"] = {"agg": metrics.aggregate(hrows), "rows": hrows,
        "by_cat": {c: {"n": sum(1 for r in hrows if r["cat"] == c), "status_ok": sum(1 for r in hrows if r["cat"] == c and r["status_ok"]), "critical": sum(1 for r in hrows if r["cat"] == c and r["critical"])} for c in sorted({r["cat"] for r in hrows})},
        "right_reason": {"n": sum(1 for r in hrows if r["right_reason"] is not None), "ok": sum(1 for r in hrows if r["right_reason"])}}
    # mutations + replay
    res["corpus_mutations"] = mutations.corpus_mutations(Path(dataset), str(ROOT / "data/transcriptions"), qtext, envs)
    res["extraction_mutations"] = mutations.extraction_mutations(kb, eng.R)
    res["trace_replay"] = {q: replay(eng, envs[q].trace) for q in ("Q2", "Q9", "Q13", "Q23")}
    (out / ("results.json" if "--heldout-first-run" not in sys.argv else "results_first_run.json")).write_text(json.dumps(res, indent=1, default=str, ensure_ascii=False))
    print(json.dumps({"ingest": res["ingest"], "official_full": res["official"]["full_system"]["agg"], "ablations": {k: v["agg"] for k, v in res["official"].items() if k.startswith("ablate")},
                      "baseline": res["official"]["baseline_bm25_extractive"]["agg"], "heldout": res["heldout"]["agg"], "heldout_by_cat": res["heldout"]["by_cat"], "right_reason": res["heldout"]["right_reason"]}, indent=1, default=str))
if __name__ == "__main__": main(sys.argv[1])
