"""Generate eval/results/RESULTS.md from results.json (+ results_first_run.json)."""
import json
from pathlib import Path
R = Path(__file__).resolve().parent / "results"
r = json.loads((R / "results.json").read_text()); f = json.loads((R / "results_first_run.json").read_text())
fa = r["official"]["full_system"]["agg"]
def pct(t): return f"{t[0]}/{t[1]}"
L = ["# Evaluation results", "", "All numbers are produced by `python eval/run_eval.py <dataset>`; counts are shown with n (small samples: do not read percentages as precision).", "",
     "## 1. Ingestion", "", f"- files: {r['ingest']['files']}; claims: {r['ingest']['claims']}; ingest wall time: {r['ingest']['seconds']} s (dominated by Tesseract on 5 rasters)",
     f"- relations: {r['ingest']['relations']}; inference rules fired: {r['ingest']['inference']}; store hash `{r['ingest']['store_hash']}`", "",
     "## 2. Official 23 questions (gold traces: `eval/golden_traces.json`, draft pending reviewer sign-off D1–D9)", "",
     "| system | status exact | answerability class | CRITICAL items | MAJOR items | required content | required claims |", "|---|---|---|---|---|---|---|",
     f"| **full system** | {pct((fa['status_exact'], 23))} | {pct((fa['answerability_class'], 23))} | {fa['critical_items']} | {fa['major_items']} | {pct(fa['must_hit'])} | {pct(fa['claim_recall'])} |"]
for k, lab in (("ablate_scope_algebra", "− scope algebra"), ("ablate_admissibility_policy", "− admissibility policy")):
    a = r["official"][k]["agg"]; L.append(f"| {lab} | {pct((a['status_exact'], 23))} | {pct((a['answerability_class'], 23))} | {a['critical_items']} | {a['major_items']} | {pct(a['must_hit'])} | {pct(a['claim_recall'])} |")
b = r["official"]["baseline_bm25_extractive"]["agg"]
L += [f"| BM25 chunk + extractive top-1 (never abstains) | {pct((b['status_exact'], 23))} | {pct((b['answerability_class'], 23))} | {b['critical_items']} | {b['major_items']} | {pct(b['must_hit'])} | n/a |",
      f"| full-context LLM stuffing | {r['official']['baseline_full_context_llm']} | | | | | |", "",
      f"Other stage metrics (full system): candidate recall via slot retrieval {pct(fa['candidate_recall'])} (the other 3 expected claims enter by deterministic expansion: procedure steps, pair supersession, closed-world diff); "
      f"unexpected (off-slot) claims {pct(fa['unexpected_claims'])}; evidence recall {fa['evidence_recall_primary_only_mean']} (primary evidence only) / {fa['evidence_recall_with_corroboration_mean']} (with corroborating sources); "
      f"evidence precision {fa['evidence_precision_mean']}; contract-verified {fa['verified']}/23; latency p50 {fa['latency_ms_p50']} ms, p95 {fa['latency_ms_p95']} ms (no LLM calls).", "",
      "Reading the ablations honestly: removing the scope algebra produces CRITICAL errors on Q2, Q9, Q18 (wrong-revision value in the answer). Removing the admissibility policy changes **nothing measurable** on this corpus "
      "(the one field-note value is already kept out by predicate-family advisory routing), so this component is not justified by these 23 questions.", "",
      "| Q | expected | got | evidence quality | ms |", "|---|---|---|---|---|"]
for row in r["official"]["full_system"]["rows"]: L.append(f"| {row['id']} | {row['expected']} | {row['status']} | {row['quality']} | {row['ms']} |")
ha, hf = r["heldout"]["agg"], f["heldout"]["agg"]
L += ["", "## 3. Held-out set (32 questions, authored before any scoring; never tuned against)", "",
      f"- **First run, as originally scored:** status exact {hf['status_exact']}/32, CRITICAL items {hf['critical_items']}, MAJOR {hf['major_items']}. The scorer then counted over-abstention as CRITICAL, which contradicts the severity taxonomy "
      "(CRITICAL = wrong entity/number/unit/revision, answering an unanswerable item, field-note value stated as fact); that is a scorer bug, corrected afterwards.",
      f"- **Post-fix** (gate bug fix + unmapped-intent message + scorer taxonomy fix; **analyzer rules unchanged**): status exact {ha['status_exact']}/32, CRITICAL items {ha['critical_items']}, MAJOR {ha['major_items']}.",
      f"- One genuine CRITICAL was found by the first run: H09 (an explicit \"not specified\" statement was treated as covering the slot, so the system answered). Fixed in the gate; regression-tested.",
      f"- Remaining {32 - ha['status_exact']} misses are all `rules:none`: the **rule-based analyzer does not generalize to paraphrases/new intents** (e.g. 'stays active longer than ten seconds', 'Before I start', flash point). They abstain with an explicit "
      "'could not be mapped' message rather than guess; the LLM analyzer is the designed remedy and is **untested here**.", "", "| category | status ok / n | critical |", "|---|---|---|"]
for c, v in r["heldout"]["by_cat"].items(): L.append(f"| {c} | {v['status_ok']}/{v['n']} | {v['critical']} |")
rr = r["heldout"]["right_reason"]; L += ["", f"Abstention for the *right reason* (analyzer understood the question and found a gap/false premise): {rr['ok']}/{rr['n']} of the abstain items that declare it.", ""]
L += ["## 4. Extraction-validator mutation test (known-good claims corrupted; V1–V5 only, V6/V7 not available)", "", "| corruption | n | caught | catch rate | catching layer |", "|---|---|---|---|---|"]
for k, v in r["extraction_mutations"].items(): L.append(f"| {k} | {v['n']} | {v['caught']} | {v['catch_rate']} | {', '.join(a for a, b in v['by_layer'].items() if b) or '—'} |")
L += ["", "Residual risk, stated plainly: a modality weakening with no cue in the quote (0 caught) and near-ID subject swaps where the surrounding text legitimately names both entities (V2b catches ~59%) "
      "can only be caught by the LLM layers (V6/V7), which are not exercised here. V2b was added *because* this test showed 0/32 on subject swaps.", "", "## 5. Corpus mutation tests", ""]
for k, v in r["corpus_mutations"].items(): L.append(f"- **{k}**: " + "; ".join(f"{a}={json.dumps(b, ensure_ascii=False)[:160]}" for a, b in v.items() if a not in ("Q2_text", "sentences_lost", "Q2_docs")))
L += ["", "## 6. Trace replay", "", f"{json.dumps(r['trace_replay'])}", "", "## 7. Not run / not claimed", "",
      "- Full-context LLM baseline, LLM extractor/analyzer/composer accuracy, V6/V7 marginal value, injection resistance of an LLM extractor: **no API key in the build environment**.",
      "- Raster content (5 files) is read from sidecar transcriptions authored by the assistant from viewing the images, cross-checked against Tesseract, marked `reviewed: false`.",
      "- **The official 23 are a development set**: the system was iterated against them (several bugs were found and fixed by running them), so 23/23 is not a generalization estimate. The held-out table in §3 is the only generalization signal, and it is builder-authored.",
      "- Gold labels were authored by the same party that built the system (mitigated by `verify_gold.py` and by freezing before building, but not independent)."]
(R / "RESULTS.md").write_text("\n".join(L)); print("\n".join(L[:30]))
