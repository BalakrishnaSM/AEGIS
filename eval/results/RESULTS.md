# Evaluation results

All numbers are produced by `python eval/run_eval.py <dataset>`; counts are shown with n (small samples: do not read percentages as precision).

## 1. Ingestion

- files: 20; claims: {'ADMITTED': 191, 'DEMOTED': 1, 'REJECTED': 0}; ingest wall time: 39.41 s (dominated by Tesseract on 5 rasters)
- relations: {'conflicts_with': 0, 'version_scoped_with': 20, 'corroborates': 71, 'derived_from': 5, 'supersedes': 5}; inference rules fired: {'R-CFGKEY-01': 4, 'R-TAG-RESOLVE': 1, 'ambiguous': 0}; store hash `d598a1de4e537d79`

## 2. Official 23 questions (gold traces: `eval/golden_traces.json`, draft pending reviewer sign-off D1–D9)

| system | status exact | answerability class | CRITICAL items | MAJOR items | required content | required claims |
|---|---|---|---|---|---|---|
| **full system** | 23/23 | 23/23 | 0 | 0 | 60/60 | 33/33 |
| − scope algebra | 21/23 | 23/23 | 3 | 0 | 60/60 | 33/33 |
| − admissibility policy | 23/23 | 23/23 | 0 | 0 | 60/60 | 33/33 |
| BM25 chunk + extractive top-1 (never abstains) | 15/23 | 18/23 | 5 | 22 | 8/60 | n/a |
| full-context LLM stuffing | NOT RUN (requires an LLM; no API key in this environment) | | | | | |

Other stage metrics (full system): candidate recall via slot retrieval 30/33 (the other 3 expected claims enter by deterministic expansion: procedure steps, pair supersession, closed-world diff); unexpected (off-slot) claims 0/37; evidence recall 0.7 (primary evidence only) / 0.85 (with corroborating sources); evidence precision 0.906; contract-verified 23/23; latency p50 4.79 ms, p95 8.51 ms (no LLM calls).

Reading the ablations honestly: removing the scope algebra produces CRITICAL errors on Q2, Q9, Q18 (wrong-revision value in the answer). Removing the admissibility policy changes **nothing measurable** on this corpus (the one field-note value is already kept out by predicate-family advisory routing), so this component is not justified by these 23 questions.

| Q | expected | got | evidence quality | ms |
|---|---|---|---|---|
| Q1 | ANSWERED | ANSWERED | STRONG | 37.71 |
| Q2 | ANSWERED_SCOPED | ANSWERED_SCOPED | STRONG | 8.51 |
| Q3 | ANSWERED | ANSWERED | STRONG | 5.11 |
| Q4 | ANSWERED | ANSWERED | STRONG | 3.79 |
| Q5 | ANSWERED | ANSWERED | STRONG | 3.33 |
| Q6 | ANSWERED | ANSWERED | REDUCED | 3.29 |
| Q7 | ANSWERED | ANSWERED | STRONG | 3.49 |
| Q8 | ANSWERED | ANSWERED | STRONG | 3.38 |
| Q9 | ANSWERED | ANSWERED | STRONG | 4.97 |
| Q10 | ANSWERED | ANSWERED | STRONG | 3.75 |
| Q11 | ANSWERED | ANSWERED | STRONG | 3.23 |
| Q12 | ANSWERED_SCOPED | ANSWERED_SCOPED | STRONG | 4.11 |
| Q13 | ANSWERED | ANSWERED | REDUCED | 3.37 |
| Q14 | ANSWERED | ANSWERED | STRONG | 3.32 |
| Q15 | ANSWERED | ANSWERED | REDUCED | 4.63 |
| Q16 | ANSWERED_SCOPED | ANSWERED_SCOPED | STRONG | 6.93 |
| Q17 | ANSWERED | ANSWERED | STRONG | 5.52 |
| Q18 | ANSWERED | ANSWERED | STRONG | 6.15 |
| Q19 | UNANSWERABLE | UNANSWERABLE | N/A | 5.47 |
| Q20 | UNANSWERABLE | UNANSWERABLE | N/A | 5.52 |
| Q21 | UNANSWERABLE | UNANSWERABLE | N/A | 4.79 |
| Q22 | UNANSWERABLE | UNANSWERABLE | N/A | 5.29 |
| Q23 | PARTIAL | PARTIAL | REDUCED | 6.93 |

## 3. Held-out set (32 questions, authored before any scoring; never tuned against)

- **First run, as originally scored:** status exact 23/32, CRITICAL items 9, MAJOR 11. The scorer then counted over-abstention as CRITICAL, which contradicts the severity taxonomy (CRITICAL = wrong entity/number/unit/revision, answering an unanswerable item, field-note value stated as fact); that is a scorer bug, corrected afterwards.
- **Post-fix** (gate bug fix + unmapped-intent message + scorer taxonomy fix; **analyzer rules unchanged**): status exact 24/32, CRITICAL items 0, MAJOR 10.
- One genuine CRITICAL was found by the first run: H09 (an explicit "not specified" statement was treated as covering the slot, so the system answered). Fixed in the gate; regression-tested.
- Remaining 8 misses are all `rules:none`: the **rule-based analyzer does not generalize to paraphrases/new intents** (e.g. 'stays active longer than ten seconds', 'Before I start', flash point). They abstain with an explicit 'could not be mapped' message rather than guess; the LLM analyzer is the designed remedy and is **untested here**.

| category | status ok / n | critical |
|---|---|---|
| abstain | 7/7 | 0 |
| entity_swap | 4/4 | 0 |
| injection | 1/1 | 0 |
| paraphrase | 8/11 | 0 |
| scope | 4/4 | 0 |
| unsupported_intent | 0/5 | 0 |

Abstention for the *right reason* (analyzer understood the question and found a gap/false premise): 1/3 of the abstain items that declare it.

## 4. Extraction-validator mutation test (known-good claims corrupted; V1–V5 only, V6/V7 not available)

| corruption | n | caught | catch rate | catching layer |
|---|---|---|---|---|
| scope_widened | 23 | 23 | 1.0 | V3 |
| scope_flipped | 23 | 23 | 1.0 | V3 |
| value_swapped | 16 | 16 | 1.0 | V2 |
| unit_swapped | 15 | 15 | 1.0 | V2 |
| negation_dropped | 8 | 8 | 1.0 | V4 |
| modality_weakened_no_cue | 18 | 0 | 0.0 | — |
| condition_dropped | 8 | 8 | 1.0 | V4 |
| entity_swapped_near_id | 32 | 19 | 0.594 | V2b |

Residual risk, stated plainly: a modality weakening with no cue in the quote (0 caught) and near-ID subject swaps where the surrounding text legitimately names both entities (V2b catches ~59%) can only be caught by the LLM layers (V6/V7), which are not exercised here. V2b was added *because* this test showed 0/32 on subject swaps.

## 5. Corpus mutation tests

- **M1_remove_ECN-1042**: status_changes={}; expectation="180/200 bar survive via independent sources (manual, revision history, register) but ECN-only facts disappear (form-fit-function, rationale)"
- **M2_remove_glossary**: status_changes={}; text_changes=["Q19", "Q20", "Q21", "Q22", "Q23"]; expectation="aliasing still works: no status changes"
- **M3_inject_contradiction**: Q2="CONFLICTED"; Q9="ANSWERED"; Q18="ANSWERED"; conflicts=1; expectation="Q2 CONFLICTED with both values shown; Q9/Q18 (before 3.2) unaffected"
- **M4_instruction_only_document**: status_changes={}; text_changes=["Q19", "Q20", "Q21", "Q22", "Q23"]; any_999=false; expectation="no change (nothing extractable; instructions in documents are inert data)"; caveat="deterministic extractor: this does not test an LLM extractor's resistance to injection (not exercised, no API key)"
- **M5_poisoned_parseable_claim**: Q2="CONFLICTED"; Q2_has_999_as_SOURCE=true; conflicts=1; expectation="surfaced as CONFLICTED (both values), not silently chosen, instructions ignored"

## 6. Trace replay

{"Q2": {"same_store": true, "same_status": true, "same_answer": true}, "Q9": {"same_store": true, "same_status": true, "same_answer": true}, "Q13": {"same_store": true, "same_status": true, "same_answer": true}, "Q23": {"same_store": true, "same_status": true, "same_answer": true}}

## 7. Not run / not claimed

- Full-context LLM baseline, LLM extractor/analyzer/composer accuracy, V6/V7 marginal value, injection resistance of an LLM extractor: **no API key in the build environment**.
- Raster content (5 files) is read from sidecar transcriptions authored by the assistant from viewing the images, cross-checked against Tesseract, marked `reviewed: false`.
- **The official 23 are a development set**: the system was iterated against them (several bugs were found and fixed by running them), so 23/23 is not a generalization estimate. The held-out table in §3 is the only generalization signal, and it is builder-authored.
- Gold labels were authored by the same party that built the system (mitigated by `verify_gold.py` and by freezing before building, but not independent).