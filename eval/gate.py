"""Release gate (architecture §10). Exits non-zero if any criterion fails. The statement it supports is about THIS build on THESE sets only."""
import json, sys
from pathlib import Path
r = json.loads((Path(__file__).resolve().parent / "results/results.json").read_text()); fails = []
def need(ok, msg): 
    print(("PASS " if ok else "FAIL ") + msg); 
    if not ok: fails.append(msg)
o = r["official"]["full_system"]["agg"]; h = r["heldout"]["agg"]
need(o["critical_items"] == 0, f"official: zero CRITICAL items (got {o['critical_items']})")
need(o["major_items"] == 0, f"official: zero MAJOR items (got {o['major_items']})")
need(o["status_exact"] == o["n"], f"official: answerability status exact {o['status_exact']}/{o['n']}")
need(o["verified"] == o["n"], f"official: every answer contract-verified ({o['verified']}/{o['n']})")
need(h["critical_items"] == 0, f"held-out: zero CRITICAL items (got {h['critical_items']})")
need(r["official"]["ablate_scope_algebra"]["agg"]["critical_items"] > 0, "scope ablation must be detectable (the test discriminates)")
for k in ("scope_widened", "scope_flipped", "value_swapped", "unit_swapped", "condition_dropped"):
    need(r["extraction_mutations"][k]["catch_rate"] == 1.0, f"extraction mutation '{k}' fully caught (rate {r['extraction_mutations'][k]['catch_rate']})")
cm = r["corpus_mutations"]
need(cm["M3_inject_contradiction"]["Q2"] == "CONFLICTED" and cm["M5_poisoned_parseable_claim"]["Q2"] == "CONFLICTED", "same-scope contradiction surfaces as CONFLICTED")
need(not cm["M4_instruction_only_document"]["status_changes"] and not cm["M4_instruction_only_document"]["any_999"], "instruction-only poisoned document is inert")
need(not cm["M2_remove_glossary"]["status_changes"], "aliasing survives glossary removal")
need(all(all(v.values()) for v in r["trace_replay"].values()), "trace replay deterministic")
print("\nRELEASE GATE:", "OK" if not fails else f"{len(fails)} FAILED"); sys.exit(1 if fails else 0)
