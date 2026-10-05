import json
import pytest
from fastapi.testclient import TestClient
from aegis.api.app import RateLimiter, create_app
from aegis.config import Settings

def mk(kb, **kw):
    from aegis.query.pipeline import Engine
    return TestClient(create_app(Settings(**kw), engine=Engine(kb)))

def test_health_ready_and_security_headers(kb):
    c = mk(kb); assert c.get("/healthz").json()["status"] == "ok" and c.get("/readyz").json()["ready"] is True
    r = c.get("/api/meta"); h = r.headers
    assert "default-src 'self'" in h["content-security-policy"] and h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY" and h["cache-control"] == "no-store" and h["x-request-id"]

def test_ask_returns_verified_envelope_with_why(kb):
    d = mk(kb).post("/api/ask", json={"question": "Is PS-04 the same as PS-40?"}).json()
    assert d["status"] == "ANSWERED" and d["contract_verified"] and d["claims"] and d["why"]["slots"] and "trace" not in d and d["why"]["trace_id"]

def test_validation_rejects_bad_input(kb):
    c = mk(kb)
    assert c.post("/api/ask", json={"question": "hi"}).status_code == 422
    assert c.post("/api/ask", json={"question": "x" * 2001}).status_code == 422
    assert c.post("/api/ask", json={"question": "ok question\x00bad"}).status_code == 422
    assert c.post("/api/ask", json={"nope": 1}).status_code == 422
    assert c.post("/api/ask", content=b"x" * 9000, headers={"content-length": "9000", "content-type": "application/json"}).status_code == 413

def test_question_length_limit_is_configurable(kb):
    assert mk(kb, max_question_chars=20).post("/api/ask", json={"question": "What is the location of IV-21?"}).status_code == 422

def test_api_key_required_when_configured(kb):
    c = mk(kb, api_key="s3cret")
    assert c.get("/api/meta").status_code == 401 and c.post("/api/ask", json={"question": "Is PS-04 the same as PS-40?"}).status_code == 401
    assert c.get("/api/meta", headers={"X-API-Key": "wrong"}).status_code == 401
    assert c.post("/api/ask", json={"question": "Is PS-04 the same as PS-40?"}, headers={"X-API-Key": "s3cret"}).status_code == 200
    assert c.get("/healthz").status_code == 200                       # probes stay open

def test_rate_limit(kb):
    c = mk(kb, rate_per_min=3); codes = [c.post("/api/ask", json={"question": "Is PS-04 the same as PS-40?"}).status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200] and codes[3] == 429

def test_token_bucket_refills():
    t = [0.0]; rl = RateLimiter(60, clock=lambda: t[0])
    assert all(rl.allow("a") for _ in range(60)) and not rl.allow("a"); t[0] = 2; assert rl.allow("a") and rl.allow("a") and not rl.allow("a")

def test_trace_claim_documents_endpoints(kb):
    c = mk(kb); d = c.post("/api/ask", json={"question": "What is the location of IV-21?"}).json()
    tr = c.get(f"/api/trace/{d['why']['trace_id']}").json(); assert tr["question"] and tr["slots"]
    cl = c.get(f"/api/claims/{d['claims'][0]['id']}").json(); assert cl["evidence"] and cl["audit"]
    assert c.get("/api/claims/C9999").status_code == 404 and c.get("/api/trace/nope").status_code == 404
    docs = c.get("/api/documents").json(); assert len(docs) == 20 and any(x["references_missing"] for x in docs)

def test_metrics_exposition(kb):
    c = mk(kb); c.post("/api/ask", json={"question": "Is PS-04 the same as PS-40?"}); m = c.get("/metrics").text
    assert 'aegis_ask_total{status="ANSWERED"} 1' in m and "aegis_ready 1" in m and "aegis_ask_latency_seconds_count 1" in m

def test_not_ready_without_kb():
    c = TestClient(create_app(Settings(db_path="/nonexistent/x.db", dataset="")))
    with c: assert c.get("/readyz").status_code == 503 and c.post("/api/ask", json={"question": "valid question here"}).status_code == 503 and c.get("/healthz").status_code == 200

def test_frontend_is_served_and_contains_no_inline_script(kb):
    c = mk(kb); r = c.get("/"); assert r.status_code == 200 and "<script src=\"app.js\">" in r.text and "onclick" not in r.text and "innerHTML" not in c.get("/app.js").text
    assert c.get("/app.js").status_code == 200 and c.get("/styles.css").status_code == 200

def test_errors_do_not_leak_internals(kb):
    from aegis.query.pipeline import Engine
    eng = Engine(kb); eng.ask = lambda q: (_ for _ in ()).throw(RuntimeError("secret internal path /etc/x"))
    r = TestClient(create_app(Settings(), engine=eng)).post("/api/ask", json={"question": "Is PS-04 the same as PS-40?"})
    assert r.status_code == 500 and "secret" not in r.text and "/etc" not in r.text
