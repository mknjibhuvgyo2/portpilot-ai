"""Auto-unload local models after a run to free VRAM.

The machine can only hold one model at a time, so by default we tell local
engines to drop the model once the request is done:

- **Ollama**: `keep_alive=0`. The native provider sets it in the request; for the
  eval templates that call Ollama's OpenAI-compatible `/v1` path (which ignores
  keep_alive), we fire a native `POST /api/generate {model, keep_alive:0}` after
  the call — verified to unload.
- **LM Studio**: it has no HTTP "unload now"; the only lever is the JIT `ttl`
  (idle seconds) field, injected into the request body so LM Studio auto-unloads
  after the run.
- **llama.cpp / cloud (OneAPI, etc.)**: no-op (single-model server / not local).

Controlled by the `auto_unload` setting (`{enabled, lms_ttl}`), default enabled,
edited from Settings. Read with a short cache so it isn't a DB hit per call.
"""
from __future__ import annotations

import time

import httpx

from app.db.models import Setting
from app.db.session import SessionLocal

SETTING_KEY = "auto_unload"
_DEFAULTS = {"enabled": True, "lms_ttl": 60}
_cache: dict = {"t": 0.0, **_DEFAULTS}
_CACHE_TTL = 5.0


def _refresh() -> None:
    now = time.monotonic()
    if now - _cache["t"] < _CACHE_TTL:
        return
    db = SessionLocal()
    try:
        row = db.get(Setting, SETTING_KEY)
        v = row.value if row and isinstance(row.value, dict) else {}
        _cache["enabled"] = bool(v.get("enabled", True))
        _cache["lms_ttl"] = int(v.get("lms_ttl", 60) or 60)
    except Exception:
        pass
    finally:
        db.close()
    _cache["t"] = now


def enabled() -> bool:
    _refresh()
    return _cache["enabled"]


def lms_ttl() -> int:
    _refresh()
    return _cache["lms_ttl"]


def get_config() -> dict:
    _refresh()
    return {"enabled": _cache["enabled"], "lms_ttl": _cache["lms_ttl"]}


def invalidate() -> None:
    _cache["t"] = 0.0


def _ollama_native_base(base_url: str) -> str:
    base = (base_url or "").rstrip("/")
    return base[:-3].rstrip("/") if base.endswith("/v1") else base


def inject_request_unload(body: dict, kind: str) -> None:
    """Pre-call: LM Studio JIT auto-unload via `ttl` (its only HTTP lever)."""
    if not enabled() or not isinstance(body, dict):
        return
    if (kind or "").lower() == "lmstudio":
        body.setdefault("ttl", lms_ttl())


def native_unload_sync(kind: str, base_url: str, model: str) -> None:
    """Post-call (sync): free VRAM for Ollama via a native keep_alive:0 unload.
    Best-effort and swallowed — never fail the request over an unload."""
    if not enabled() or not model or (kind or "").lower() != "ollama":
        return
    try:
        with httpx.Client(timeout=10, trust_env=False) as c:
            c.post(f"{_ollama_native_base(base_url)}/api/generate",
                   json={"model": model, "keep_alive": 0})
    except Exception:
        pass
