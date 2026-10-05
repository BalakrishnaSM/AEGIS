"""Scoring (architecture §10): stage-wise and severity-weighted. CRITICAL: forbidden content in a SOURCE/answer sentence, answering an
UNANSWERABLE/PARTIAL item, or UNANSWERABLE on an answerable item. MAJOR: missing required content/claim, scope-class mismatch, verifier fail."""
from __future__ import annotations
import json, re, statistics
CLASS = {"ANSWERED": "A", "ANSWERED_SCOPED": "A", "PARTIAL": "P", "UNANSWERABLE": "U", "CONFLICTED": "C"}
EXPANSION = {"procedure_steps", "superseded_by", "supported_on", "installation_restriction", "not_covered_in"}
NON_GOLD_ROLES = {"distractor", "disregard_wrong_entity", "checked_no_fact", "illustrative", "advisory", "rationale_not_scope"}

def texts(env):
    src = " ".join(s.text for s in env.sentences if s.kind == "SOURCE"); der = " ".join(s.text for s in env.sentences if s.kind == "DERIVED")
    return src, der, src + " " + der

def match_claim(c, spec) -> bool:
    ent, pred, vre, rev = spec
    if c.entity_id != ent or c.predicate != pred: return False
    if vre and not re.search(vre, json.dumps(c.value, ensure_ascii=False)): return False
    d = c.scope.software_revision
    if rev == "any": return d.kind == "any"
    if rev: return d.describe() == rev
    return True

def check_text(spec, env):
    src, der, allt = texts(env)
    miss = [r for r in spec.get("must", []) if not re.search(r, src)] + [r for r in spec.get("must_derived", []) if not re.search(r, der)]
    forb = [r for r in spec.get("must_not", []) if re.search(r, src)] + [r for r in spec.get("must_not_all", []) if re.search(r, allt)]
    return miss, forb

def severity(spec, status, miss, forb, verified, missing_claims=0) -> tuple[list, list]:
    exp = spec["status"] if isinstance(spec["status"], str) else spec["status"][0]
    expset = {spec["status"]} if isinstance(spec["status"], str) else set(spec["status"])
    crit, major = [], []
    if forb: crit.append(f"forbidden content: {forb}")
    ec, gc = CLASS[exp], CLASS[status]
    if ec in ("U", "P") and gc == "A": crit.append("answered an item the corpus cannot fully answer")
    if ec == "A" and gc == "U": major.append("over-abstention: UNANSWERABLE on an answerable item (recall failure, not a safety error)")
    if status not in expset and not crit and ec == gc: major.append(f"status {status} != {sorted(expset)}")
    elif status not in expset and not crit: major.append(f"status class mismatch {status} vs {sorted(expset)}")
    if miss: major.append(f"missing required content: {miss}")
    if missing_claims: major.append(f"{missing_claims} required claim(s) not selected")
    if not verified: major.append("contract verifier failed")
    return crit, major

def score_official(qid, spec, env, kb, gold_ev):
    miss, forb = check_text(spec, env)
    sel = [kb.claims[i] for i in env.claim_ids]
    cand = {i for s in env.trace["slots"] for i in s["candidates"]}
    found = [any(match_claim(c, sp) for c in sel) for sp in spec["claims"]]
    cfound = [any(match_claim(kb.claims[i], sp) for i in cand) for sp in spec["claims"]]
    slot_preds = {s["predicate"] for s in env.trace["analysis"]["slots"]} | EXPANSION | {"applicability_scope"}
    unexpected = [c.id for c in sel if c.predicate not in slot_preds and not (c.predicate == "normal_operating_pressure" and "applicability_scope" in slot_preds)]
    crit, major = severity(spec, env.status, miss, forb, env.contract_verified, missing_claims=found.count(False))
    sysdocs = {e["document"] for e in env.evidence}; sysprim = {e["document"] for e in env.evidence if e.get("role", "primary") == "primary"}
    pr = gold_ev.get(qid, {}).get("primary", set()); al = gold_ev.get(qid, {}).get("all", set())
    ev_recall = (len(sysdocs & pr) / len(pr)) if pr else None; ev_recall_p = (len(sysprim & pr) / len(pr)) if pr else None
    ev_prec = (len(sysdocs & al) / len(sysdocs)) if (sysdocs and al) else None
    total_req = len(spec.get("must", [])) + len(spec.get("must_derived", []))
    return dict(id=qid, status=env.status, expected=spec["status"], status_ok=env.status == spec["status"], class_ok=CLASS[env.status] == CLASS[spec["status"]],
                must_hit=total_req - len(miss), must_total=total_req, claim_recall=(sum(found), len(found)), cand_recall=(sum(cfound), len(cfound)),
                unexpected=len(unexpected), n_selected=len(sel), ev_recall=ev_recall, ev_recall_primary_only=ev_recall_p, ev_precision=ev_prec, critical=crit, major=major,
                verified=env.contract_verified, quality=env.quality, ms=env.trace["t_total_ms"], answer=env.text())

def score_heldout(spec, env):
    miss, forb = check_text(spec, env)
    crit, major = severity(spec, env.status, miss, forb, env.contract_verified)
    rr = None
    if spec.get("right_reason"):
        an = env.trace["analysis"]["analyzer"]; kinds = {d["kind"] for d in env.trace["decisions"]}
        rr = (an != "rules:none") and bool(kinds & {"GAP", "FALSE_PREMISE"})
    exp = {spec["status"]} if isinstance(spec["status"], str) else set(spec["status"])
    return dict(id=spec["id"], cat=spec["cat"], q=spec["q"], status=env.status, expected=sorted(exp), status_ok=env.status in exp, class_ok=CLASS[env.status] in {CLASS[x] for x in exp},
                analyzer=env.trace["analysis"]["analyzer"], right_reason=rr, critical=crit, major=major, answer=env.text())

def aggregate(rows):
    n = len(rows); f = lambda k: sum(1 for r in rows if r[k])
    def ratio(key):
        a = sum(r[key][0] for r in rows if key in r); b = sum(r[key][1] for r in rows if key in r); return (a, b)
    ms = sorted(r["ms"] for r in rows if "ms" in r)
    out = dict(n=n, status_exact=f("status_ok"), answerability_class=f("class_ok"), critical_items=sum(1 for r in rows if r["critical"]),
               critical_total=sum(len(r["critical"]) for r in rows), major_items=sum(1 for r in rows if r["major"]), verified=f("verified") if "verified" in rows[0] else None)
    if "must_total" in rows[0]:
        out["must_hit"] = (sum(r["must_hit"] for r in rows), sum(r["must_total"] for r in rows)); out["claim_recall"] = ratio("claim_recall"); out["candidate_recall"] = ratio("cand_recall")
        out["unexpected_claims"] = (sum(r["unexpected"] for r in rows), sum(r["n_selected"] for r in rows))
        er = [r["ev_recall"] for r in rows if r["ev_recall"] is not None]; ep = [r["ev_precision"] for r in rows if r["ev_precision"] is not None]
        erp = [r["ev_recall_primary_only"] for r in rows if r["ev_recall_primary_only"] is not None]
        out["evidence_recall_with_corroboration_mean"] = round(statistics.mean(er), 3) if er else None; out["evidence_recall_primary_only_mean"] = round(statistics.mean(erp), 3) if erp else None; out["evidence_precision_mean"] = round(statistics.mean(ep), 3) if ep else None
    if ms: out["latency_ms_p50"] = ms[len(ms) // 2]; out["latency_ms_p95"] = ms[min(len(ms) - 1, int(len(ms) * 0.95))]
    return out
