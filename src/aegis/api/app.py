"""Production HTTP service (read-only). Hardening: optional API-key auth (constant-time compare), per-client token-bucket rate limit, strict input
validation, request-size cap, security headers + strict CSP, JSON structured logs with request ids, Prometheus metrics, health/readiness probes,
no internal error details in responses. The engine is read-only, so there is no mutating endpoint to protect."""
from __future__ import annotations
import hmac, json, logging, re, threading, time, uuid
from collections import OrderedDict
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from ..config import Settings
from ..llm import from_env
from ..query.pipeline import Engine
from ..store.db import KnowledgeBase

VERSION = "1.0.0"
log = logging.getLogger("aegis.api")
CSP = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"

class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    @field_validator("question")
    @classmethod
    def clean(cls, v: str) -> str:
        v = v.strip()
        if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", v): raise ValueError("control characters are not allowed")
        if len(v) < 3: raise ValueError("question too short")
        return v

class RateLimiter:
    """Token bucket per client key; bounded memory."""
    def __init__(self, per_min: int, clock=time.monotonic):
        self.rate, self.cap, self.clock = per_min / 60.0, float(per_min), clock; self.b: dict[str, tuple[float, float]] = {}; self.lk = threading.Lock()
    def allow(self, key: str) -> bool:
        if self.cap <= 0: return True
        with self.lk:
            now = self.clock(); tokens, ts = self.b.get(key, (self.cap, now)); tokens = min(self.cap, tokens + (now - ts) * self.rate)
            if len(self.b) > 10_000: self.b.clear()
            if tokens < 1: self.b[key] = (tokens, now); return False
            self.b[key] = (tokens - 1, now); return True

class Metrics:
    BUCKETS = (0.005, 0.01, 0.05, 0.1, 0.5, 1, 2.5, 5, 10, 30)
    def __init__(self): self.lk = threading.Lock(); self.req: dict = {}; self.ask: dict = {}; self.hist = [0] * (len(self.BUCKETS) + 1); self.sum = 0.0; self.n = 0
    def request(self, path: str, status: int):
        with self.lk: self.req[(path, status)] = self.req.get((path, status), 0) + 1
    def asked(self, status: str, secs: float):
        with self.lk:
            self.ask[status] = self.ask.get(status, 0) + 1; self.sum += secs; self.n += 1
            for i, b in enumerate(self.BUCKETS):
                if secs <= b: self.hist[i] += 1
            self.hist[-1] += 1
    def render(self, kb: Optional[KnowledgeBase], llm) -> str:
        L = ["# TYPE aegis_requests_total counter"] + [f'aegis_requests_total{{path="{p}",status="{s}"}} {n}' for (p, s), n in sorted(self.req.items())]
        L += ["# TYPE aegis_ask_total counter"] + [f'aegis_ask_total{{status="{s}"}} {n}' for s, n in sorted(self.ask.items())]
        L += ["# TYPE aegis_ask_latency_seconds histogram"] + [f'aegis_ask_latency_seconds_bucket{{le="{b}"}} {self.hist[i]}' for i, b in enumerate(self.BUCKETS)]
        L += [f'aegis_ask_latency_seconds_bucket{{le="+Inf"}} {self.hist[-1]}', f"aegis_ask_latency_seconds_sum {self.sum:.6f}", f"aegis_ask_latency_seconds_count {self.n}",
              f"aegis_ready {1 if kb else 0}"]
        if kb: L.append(f"aegis_claims_visible {len(kb.visible())}")
        if llm is not None:
            st = llm.stats(); L += [f"aegis_llm_calls_total {st['calls']}", f"aegis_llm_cache_hits_total {st['cache_hits']}", f"aegis_llm_failures_total {st['failures']}",
                                    f'aegis_llm_tokens_total{{type="input"}} {st["input_tokens"]}', f'aegis_llm_tokens_total{{type="output"}} {st["output_tokens"]}', f"aegis_llm_breaker_open {1 if st['breaker'] == 'open' else 0}"]
        return "\n".join(L) + "\n"

def _vtxt(v: dict) -> str:
    t = v.get("type")
    if t == "quantity": return f"{v['value']:g} {v['unit']}"
    if t == "entity": return v["id"]
    if t == "condition": q = v.get("quantity"); return f"{v.get('op')} {q['value']:g} {q['unit']}" if q else str(v.get("state"))
    if t == "state": return f"{v['item']} = {v['state']}"
    if t == "procedure": return v["id"]
    if t == "list": return "; ".join(v["items"])
    return str(v.get("v", v))

def _claim_view(kb: KnowledgeBase, c) -> dict:
    return {"id": c.id, "entity": c.entity_id, "predicate": c.predicate, "value": _vtxt(c.value), "scope": c.scope.describe(), "scope_basis": c.scope.scope_basis,
            "assertion_kind": c.assertion_kind, "tier": c.tier, "document": c.doc_id, "lifecycle": c.lifecycle, "needs_review": c.needs_review,
            "historical_reference": c.scope.historical_reference, "modality": c.modality}

def _public_trace(kb: KnowledgeBase, tr: dict) -> dict:
    slots = []
    for s in tr["slots"]:
        slots.append({"slot": s["slot"], "candidates": len(s["candidates"]), "covered": s["covered"], "selected": [_claim_view(kb, kb.claims[i]) for i in s["selected"]],
                      "excluded": [{**_claim_view(kb, kb.claims[i]), "why": why} for i, why in s["excluded"]]})
    a = tr["analysis"]
    return {"trace_id": tr["trace_id"], "analyzer": a["analyzer"], "intent": a["intent"], "policy": a["policy"], "scope": tr["scope"], "status": tr["status"],
            "slots": slots, "store_hash": tr["store_hash"], "ms": tr["t_total_ms"], "answer_sha": tr["answer_sha"]}

def create_app(settings: Optional[Settings] = None, engine: Optional[Engine] = None) -> FastAPI:
    s = settings or Settings()
    logging.basicConfig(level=s.log_level, format="%(message)s")
    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def lifespan(_app):
        _startup(); yield
    app = FastAPI(lifespan=lifespan, title="Aegis knowledge engine", version=VERSION, docs_url="/api/docs" if not s.api_key else None, redoc_url=None, openapi_url="/api/openapi.json" if not s.api_key else None)
    st = {"engine": engine, "traces": OrderedDict(), "metrics": Metrics(), "limiter": RateLimiter(s.rate_per_min), "lock": threading.Lock(), "llm": None}

    def _startup():
        if st["engine"] is None:
            try:
                db = Path(s.db_path)
                if not db.exists() and s.dataset:
                    from ..ingest.pipeline import ingest
                    ingest(s.dataset, s.db_path, s.sidecars)
                kb = KnowledgeBase.load(s.db_path); st["llm"] = from_env(s)
                st["engine"] = Engine(kb, llm=st["llm"], analyzer_mode=s.analyzer_mode if st["llm"] else "rules")
                log.info(json.dumps({"event": "ready", "store_hash": kb.store_hash(), "llm": st["llm"] is not None, "analyzer_mode": s.analyzer_mode}))
            except Exception as e:
                log.error(json.dumps({"event": "startup_failed", "error": type(e).__name__}))
        else: st["llm"] = getattr(st["engine"], "llm", None)

    if s.cors_origins: app.add_middleware(CORSMiddleware, allow_origins=list(s.cors_origins), allow_methods=["GET", "POST"], allow_headers=["X-API-Key", "Content-Type"], max_age=600)

    @app.middleware("http")
    async def mw(request: Request, call_next):
        rid = request.headers.get("x-request-id", uuid.uuid4().hex[:16]); t0 = time.perf_counter()
        if int(request.headers.get("content-length", "0") or 0) > 8192:
            resp = JSONResponse({"error": "request too large"}, status_code=413)
        else:
            try: resp = await call_next(request)
            except Exception as e:
                log.error(json.dumps({"event": "unhandled", "rid": rid, "path": request.url.path, "error": type(e).__name__}))
                resp = JSONResponse({"error": "internal error", "request_id": rid}, status_code=500)
        resp.headers.update({"X-Request-ID": rid, "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY", "Content-Security-Policy": CSP})
        if request.url.path.startswith("/api"): resp.headers["Cache-Control"] = "no-store"
        path = request.url.path if request.url.path.startswith(("/api", "/healthz", "/readyz", "/metrics")) else "/static"
        st["metrics"].request(path, resp.status_code)
        log.info(json.dumps({"event": "request", "rid": rid, "method": request.method, "path": request.url.path, "status": resp.status_code, "ms": round((time.perf_counter() - t0) * 1000, 1)}))
        return resp

    def auth(x_api_key: Optional[str] = Header(default=None)):
        if s.api_key and not (x_api_key and hmac.compare_digest(x_api_key.encode(), s.api_key.encode())): raise HTTPException(401, "invalid or missing API key")

    def ready():
        if st["engine"] is None: raise HTTPException(503, "knowledge base not loaded")
        return st["engine"]

    def client_key(request: Request) -> str:
        if s.trust_proxy and request.headers.get("x-forwarded-for"): return request.headers["x-forwarded-for"].split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    @app.get("/healthz")
    def healthz(): return {"status": "ok", "version": VERSION}

    @app.get("/readyz")
    def readyz():
        if st["engine"] is None: return JSONResponse({"ready": False}, status_code=503)
        return {"ready": True, "store_hash": st["engine"].kb.store_hash()}

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics(): return st["metrics"].render(st["engine"].kb if st["engine"] else None, st["llm"])

    @app.get("/api/meta", dependencies=[Depends(auth)])
    def meta():
        e = ready(); kb = e.kb
        return {"version": VERSION, "store_hash": kb.store_hash(), "documents": len(kb.documents), "entities": len(kb.entities),
                "claims": {k: sum(1 for c in kb.claims.values() if c.lifecycle == k) for k in ("ADMITTED", "DEMOTED", "REJECTED")},
                "llm": {"enabled": st["llm"] is not None, "analyzer_mode": e.analyzer_mode, **(st["llm"].stats() if st["llm"] else {})}, "auth_required": bool(s.api_key)}

    @app.get("/api/examples", dependencies=[Depends(auth)])
    def examples():
        p = Path(__file__).resolve().parents[3] / "eval/golden_traces.json"
        if not p.exists(): return {"questions": []}
        return {"questions": [{"id": t["question_id"], "text": t["question_text"]} for t in json.loads(p.read_text())["traces"]]}

    @app.get("/api/documents", dependencies=[Depends(auth)])
    def documents():
        kb = ready().kb
        return [{"id": d.id, "family": d.family, "tier": d.tier, "completeness": d.completeness, "references_missing": d.references_missing, "supersedes": d.supersedes,
                 "derived_from": d.derived_from, "applies_to": d.applies_to.describe(), "sha256": d.sha256[:16]} for d in kb.documents.values()]

    @app.post("/api/ask", dependencies=[Depends(auth)])
    def ask(req: AskRequest, request: Request):
        e = ready()
        if not st["limiter"].allow(client_key(request)): raise HTTPException(429, "rate limit exceeded", headers={"Retry-After": "10"})
        if len(req.question) > s.max_question_chars: raise HTTPException(422, f"question exceeds {s.max_question_chars} characters")
        t0 = time.perf_counter()
        try: env = e.ask(req.question)
        except Exception as ex:
            log.error(json.dumps({"event": "ask_failed", "error": type(ex).__name__})); raise HTTPException(500, "could not answer the question")
        st["metrics"].asked(env.status, time.perf_counter() - t0)
        kb = e.kb; tr = env.trace
        with st["lock"]:
            st["traces"][tr["trace_id"]] = tr
            while len(st["traces"]) > s.trace_cache: st["traces"].popitem(last=False)
        out = env.model_dump(mode="json"); out.pop("trace"); out["claims"] = [_claim_view(kb, kb.claims[i]) for i in env.claim_ids]; out["why"] = _public_trace(kb, tr)
        return out

    @app.get("/api/trace/{trace_id}", dependencies=[Depends(auth)])
    def trace(trace_id: str):
        tr = st["traces"].get(trace_id)
        if not tr: raise HTTPException(404, "trace not found (traces are kept in memory, newest first)")
        return tr

    @app.get("/api/claims/{claim_id}", dependencies=[Depends(auth)])
    def claim(claim_id: str):
        kb = ready().kb; c = kb.claims.get(claim_id)
        if not c: raise HTTPException(404, "claim not found")
        return {**_claim_view(kb, c), "validation": c.validation, "inference": c.inference, "condition": c.condition,
                "evidence": [{"document": kb.evidence[e].document_id, "page": kb.evidence[e].page, "bbox": kb.evidence[e].bbox, "quote": kb.evidence[e].quote, "method": kb.evidence[e].method,
                              "raster_agreement": kb.evidence[e].raster_agreement, "source_sha256": kb.evidence[e].source_sha256[:16]} for e in c.evidence_ids],
                "audit": [{"layer": a[1], "result": a[2], "detail": a[3]} for a in kb.audit if a[0] == claim_id]}

    web = Path(__file__).resolve().parents[1] / "web"
    if web.exists(): app.mount("/", StaticFiles(directory=str(web), html=True), name="web")
    app.state.st = st
    return app
