"""LIVE evaluation of the LLM path against the deterministic path. Requires ANTHROPIC_API_KEY (NOT available where this was authored: the script
has never been run live). `--plumbing` runs the same code with a client that always fails, to prove the harness itself works (results must equal
the deterministic path). Usage: python eval/run_live_llm_eval.py <dataset_root> [--plumbing]"""
import json, os, sys, tempfile
import dotenv
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src")); sys.path.insert(0, str(Path(__file__).resolve().parent))
from aegis.config import Settings
from aegis.ingest.pipeline import ingest
from aegis.llm import AnthropicClient, LLMUnavailable, entailment_veto
from aegis.query.pipeline import Engine
import expect, metrics

dotenv.load_dotenv()
class Down:
    def json(self, *a, **k): raise LLMUnavailable("plumbing run: model deliberately unavailable")
    def stats(self): return {"calls": 0, "note": "plumbing run"}

def main():
    root = sys.argv[1]; plumbing = "--plumbing" in sys.argv; HERE = Path(__file__).resolve().parent; ROOT = HERE.parent
    if not plumbing and not AnthropicClient.available(): print("ANTHROPIC_API_KEY is not set. Set it (or use --plumbing for a harness self-test)."); sys.exit(2)
    llm = Down() if plumbing else AnthropicClient(Settings())
    gold = json.loads((HERE / "golden_traces.json").read_text())["traces"]; qtext = {t["question_id"]: t["question_text"] for t in gold}
    base = json.loads((HERE / "results/results.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as td:
        kb, rep = ingest(root, str(Path(td) / "k.db"), str(ROOT / "data/transcriptions"), llm=llm, veto=None if plumbing else entailment_veto(llm))
    eng = Engine(kb, llm=llm, analyzer_mode="rules_then_llm"); gold_ev = {}
    for t in gold:
        gold_ev[t["question_id"]] = {"primary": {e["document"] for e in t["expected_evidence"] if e["role"] == "primary" and not e["document"].startswith("*")},
                                     "all": {e["document"] for e in t["expected_evidence"] if e["role"] not in metrics.NON_GOLD_ROLES and not e["document"].startswith("*")}}
    rows = [metrics.score_official(q, expect.OFFICIAL[q], eng.ask(t), kb, gold_ev) for q, t in qtext.items()]
    hrows = [metrics.score_heldout(h, eng.ask(h["q"])) for h in expect.HELDOUT]
    bo, bh = base["official"]["full_system"]["agg"], base["heldout"]["agg"]; lo, lh = metrics.aggregate(rows), metrics.aggregate(hrows)
    regress = [r["id"] for r, b in zip(rows, base["official"]["full_system"]["rows"]) if r["status"] != b["status"]]
    flipped = [(h["id"], b["status"], h["status"]) for h, b in zip(hrows, base["heldout"]["rows"]) if h["status"] != b["status"]]
    out = {"mode": "plumbing" if plumbing else "live", "llm_stats": llm.stats(), "ingest": {k: rep.get(k) for k in ("llm_extract", "dual_extraction", "claims")},
           "official": {"deterministic": {k: bo[k] for k in ("status_exact", "critical_items", "major_items")}, "llm": {k: lo[k] for k in ("status_exact", "critical_items", "major_items")}, "status_regressions": regress},
           "heldout": {"deterministic": {k: bh[k] for k in ("status_exact", "critical_items", "major_items")}, "llm": {k: lh[k] for k in ("status_exact", "critical_items", "major_items")}, "status_changes": flipped}}
    (HERE / "results" / ("results_live_llm.json" if not plumbing else "results_llm_plumbing.json")).write_text(json.dumps(out, indent=1, default=str)); print(json.dumps(out, indent=1, default=str))
    if plumbing and (regress or flipped): print("PLUMBING SELF-TEST FAILED: results differ from the deterministic path"); sys.exit(1)
if __name__ == "__main__": main()
