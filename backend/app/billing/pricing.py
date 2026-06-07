"""Pricing config + cost computation, shared by metrics and the usage API.

Pricing is stored in the `settings` table under key "pricing":

    {
      "currency": "USD",
      "default": {"in": 0.0, "out": 0.0},     # price per 1M tokens
      "models":  {"gpt-4o": {"in": 2.5, "out": 10.0}, ...}
    }

`in` = prompt price, `out` = completion price, both per 1,000,000 tokens.
"""
from __future__ import annotations

import threading
import time

from sqlalchemy.orm import Session

from app.db.models import Setting

PRICING_KEY = "pricing"
DEFAULT_PRICING: dict = {
    "currency": "USD",
    "default": {"in": 0.0, "out": 0.0},
    "models": {},
}

_lock = threading.Lock()
_cache: dict | None = None
_cache_ts: float = 0.0
_TTL = 20.0


def get_pricing(db: Session, *, fresh: bool = False) -> dict:
    global _cache, _cache_ts
    now = time.time()
    if not fresh and _cache is not None and (now - _cache_ts) < _TTL:
        return _cache
    row = db.get(Setting, PRICING_KEY)
    data = dict(DEFAULT_PRICING)
    if row and isinstance(row.value, dict):
        data = {**DEFAULT_PRICING, **row.value}
        data.setdefault("default", DEFAULT_PRICING["default"])
        data.setdefault("models", {})
    with _lock:
        _cache = data
        _cache_ts = now
    return data


def set_pricing(db: Session, data: dict) -> dict:
    clean = {
        "currency": str(data.get("currency", "USD"))[:8],
        "default": {
            "in": float(data.get("default", {}).get("in", 0) or 0),
            "out": float(data.get("default", {}).get("out", 0) or 0),
        },
        "models": {},
    }
    for name, rate in (data.get("models") or {}).items():
        if not name:
            continue
        clean["models"][str(name)[:160]] = {
            "in": float((rate or {}).get("in", 0) or 0),
            "out": float((rate or {}).get("out", 0) or 0),
        }
    row = db.get(Setting, PRICING_KEY)
    if row:
        row.value = clean
    else:
        db.add(Setting(key=PRICING_KEY, value=clean))
    db.commit()
    invalidate()
    return clean


def invalidate() -> None:
    global _cache, _cache_ts
    with _lock:
        _cache = None
        _cache_ts = 0.0


def rate_for(model: str, pricing: dict) -> dict:
    models = pricing.get("models") or {}
    if model in models:
        return models[model]
    return pricing.get("default") or DEFAULT_PRICING["default"]


def compute_cost(model: str, prompt_tokens: int, completion_tokens: int, pricing: dict) -> float:
    rate = rate_for(model, pricing)
    return round(
        prompt_tokens / 1_000_000 * float(rate.get("in", 0) or 0)
        + completion_tokens / 1_000_000 * float(rate.get("out", 0) or 0),
        6,
    )
