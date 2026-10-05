"""Runtime configuration from environment variables (12-factor). No secrets in code."""
from __future__ import annotations
import os
from dataclasses import dataclass, field

def _b(name: str, default: bool) -> bool: return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")
def _i(name: str, default: int) -> int: return int(os.environ.get(name, default))
def _f(name: str, default: float) -> float: return float(os.environ.get(name, default))

@dataclass(frozen=True)
class Settings:
    db_path: str = field(default_factory=lambda: os.environ.get("AEGIS_DB", "data/aegis.db"))
    dataset: str = field(default_factory=lambda: os.environ.get("AEGIS_DATASET", ""))
    sidecars: str = field(default_factory=lambda: os.environ.get("AEGIS_SIDECARS", "data/transcriptions"))
    api_key: str = field(default_factory=lambda: os.environ.get("AEGIS_API_KEY", ""))          # empty = auth disabled (dev only)
    cors_origins: tuple = field(default_factory=lambda: tuple(x for x in os.environ.get("AEGIS_CORS_ORIGINS", "").split(",") if x))
    rate_per_min: int = field(default_factory=lambda: _i("AEGIS_RATE_PER_MIN", 60))
    trust_proxy: bool = field(default_factory=lambda: _b("AEGIS_TRUST_PROXY", False))
    max_question_chars: int = field(default_factory=lambda: _i("AEGIS_MAX_QUESTION_CHARS", 600))
    trace_cache: int = field(default_factory=lambda: _i("AEGIS_TRACE_CACHE", 500))
    log_level: str = field(default_factory=lambda: os.environ.get("AEGIS_LOG_LEVEL", "INFO"))
    # LLM
    llm_enabled: bool = field(default_factory=lambda: _b("AEGIS_LLM_ENABLED", True))            # still requires ANTHROPIC_API_KEY
    llm_models: tuple = field(default_factory=lambda: tuple(os.environ.get("AEGIS_LLM_MODELS", "claude-sonnet-5-5,claude-haiku-4-5-20251001").split(",")))
    llm_timeout_s: float = field(default_factory=lambda: _f("AEGIS_LLM_TIMEOUT_S", 30.0))
    llm_max_retries: int = field(default_factory=lambda: _i("AEGIS_LLM_MAX_RETRIES", 3))
    llm_breaker_failures: int = field(default_factory=lambda: _i("AEGIS_LLM_BREAKER_FAILURES", 5))
    llm_breaker_cooldown_s: float = field(default_factory=lambda: _f("AEGIS_LLM_BREAKER_COOLDOWN_S", 30.0))
    llm_hourly_token_cap: int = field(default_factory=lambda: _i("AEGIS_LLM_HOURLY_TOKEN_CAP", 2_000_000))
    llm_cache_path: str = field(default_factory=lambda: os.environ.get("AEGIS_LLM_CACHE", "data/llm_cache.sqlite"))
    analyzer_mode: str = field(default_factory=lambda: os.environ.get("AEGIS_ANALYZER_MODE", "rules_then_llm"))   # rules | rules_then_llm | llm
    price_in_per_mtok: float = field(default_factory=lambda: _f("AEGIS_PRICE_IN_PER_MTOK", 0.0))     # optional, for cost estimates; 0 = report tokens only
    price_out_per_mtok: float = field(default_factory=lambda: _f("AEGIS_PRICE_OUT_PER_MTOK", 0.0))
