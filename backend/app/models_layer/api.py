"""Provider + model alias management API."""
from __future__ import annotations

import json

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user, require_admin
from app.db.models import ModelAlias, Provider
from app.db.session import get_db
from app.models_layer.presets import PROVIDER_PRESETS
from app.models_layer.router import build_provider

router = APIRouter(prefix="/api/models", tags=["models"])

# One-click local engine connections (name, kind, base_url).
LOCAL_ENGINES = {
    "ollama": ("Local Ollama", "ollama", "http://127.0.0.1:11434"),
    "lmstudio": ("Local LM Studio", "lmstudio", "http://127.0.0.1:1234/v1"),
    "llamacpp": ("Local llama.cpp", "llamacpp", "http://127.0.0.1:8080/v1"),
}


@router.get("/provider-presets")
def provider_presets(_: object = Depends(get_current_user)):
    """Well-known vendor presets (kind + base_url) for the provider editor."""
    return PROVIDER_PRESETS


# ---------------- Providers ----------------
class ProviderIn(BaseModel):
    name: str
    kind: str = "openai_compat"
    base_url: str
    api_key: str = ""
    gpu_index: str = ""
    weight: int = 1
    enabled: bool = True
    extra: dict = {}


class ProviderOut(ProviderIn):
    id: int

    class Config:
        from_attributes = True


@router.get("/providers")
def list_providers(db: Session = Depends(get_db), _: object = Depends(get_current_user)):
    rows = db.query(Provider).order_by(Provider.id).all()
    out = []
    for p in rows:
        d = ProviderOut.model_validate(p).model_dump()
        d["api_key"] = "***" if p.api_key else ""  # never leak keys
        out.append(d)
    return out


@router.post("/providers", response_model=ProviderOut)
def create_provider(body: ProviderIn, db: Session = Depends(get_db),
                    _: object = Depends(require_admin)):
    if db.query(Provider).filter(Provider.name == body.name).first():
        raise HTTPException(409, "provider name exists")
    p = Provider(**body.model_dump())
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@router.patch("/providers/{pid}", response_model=ProviderOut)
def update_provider(pid: int, body: ProviderIn, db: Session = Depends(get_db),
                    _: object = Depends(require_admin)):
    p = db.get(Provider, pid)
    if not p:
        raise HTTPException(404, "not found")
    data = body.model_dump()
    if data.get("api_key") in ("", "***"):
        data.pop("api_key", None)  # keep existing key if blank/masked
    for k, v in data.items():
        setattr(p, k, v)
    db.commit()
    db.refresh(p)
    return p


@router.delete("/providers/{pid}")
def delete_provider(pid: int, db: Session = Depends(get_db), _: object = Depends(require_admin)):
    p = db.get(Provider, pid)
    if not p:
        raise HTTPException(404, "not found")
    db.delete(p)
    db.commit()
    return {"ok": True}


@router.post("/providers/{pid}/test")
async def test_provider(pid: int, db: Session = Depends(get_db),
                        _: object = Depends(require_admin)):
    p = db.get(Provider, pid)
    if not p:
        raise HTTPException(404, "not found")
    # pass provider.extra (custom headers / advanced) so health & model listing
    # behave like real calls — otherwise self-hosted/custom vendors needing a
    # custom auth header would always fail the health check.
    provider = build_provider(p.kind, p.base_url, p.api_key, timeout=10, extra=p.extra)
    healthy = await provider.health()
    models = await provider.list_models() if healthy else []
    return {"healthy": healthy, "models": models,
            "base_url": p.base_url, "gpu_index": p.gpu_index}


# ---------------- Local engines: one-click connect + model management ----------------

@router.post("/connect-local")
async def connect_local(kind: str = Body(..., embed=True), db: Session = Depends(get_db),
                        _: object = Depends(require_admin)):
    """One-click connect a local engine (ollama / lmstudio / llamacpp). Creates the
    provider at its default localhost URL if absent; reports reachability."""
    if kind not in LOCAL_ENGINES:
        raise HTTPException(400, "unknown local engine")
    name, k, base = LOCAL_ENGINES[kind]
    healthy = await build_provider(k, base, "", timeout=8).health()
    existing = db.query(Provider).filter(Provider.base_url == base).first()
    if existing:
        return {"created": False, "id": existing.id, "name": existing.name, "healthy": healthy}
    p = Provider(name=name, kind=k, base_url=base, weight=1)
    db.add(p)
    db.commit()
    db.refresh(p)
    return {"created": True, "id": p.id, "name": name, "healthy": healthy}


def _require_ollama(db: Session, pid: int) -> Provider:
    p = db.get(Provider, pid)
    if not p:
        raise HTTPException(404, "not found")
    if p.kind != "ollama":
        raise HTTPException(400, "model management is only supported for Ollama providers")
    return p


@router.get("/providers/{pid}/local-models")
async def local_models(pid: int, db: Session = Depends(get_db),
                       _: object = Depends(require_admin)):
    p = _require_ollama(db, pid)
    try:
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            r = await client.get(f"{p.base_url.rstrip('/')}/api/tags")
            r.raise_for_status()
            data = r.json()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"cannot reach Ollama: {e}")
    out = []
    for m in (data.get("models") or []):
        out.append({"name": m.get("name", ""),
                    "size_mb": round((m.get("size", 0) or 0) / 1024 / 1024),
                    "modified": m.get("modified_at", "")})
    return out


@router.post("/providers/{pid}/pull")
async def pull_model(pid: int, name: str = Body(..., embed=True), db: Session = Depends(get_db),
                     _: object = Depends(require_admin)):
    p = _require_ollama(db, pid)
    name = name.strip()
    if not name:
        raise HTTPException(400, "model name required")
    try:
        async with httpx.AsyncClient(timeout=1800, trust_env=False) as client:
            r = await client.post(f"{p.base_url.rstrip('/')}/api/pull",
                                  json={"model": name, "name": name, "stream": False})
            r.raise_for_status()
            data = r.json()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"pull failed: {e}")
    return {"status": data.get("status", "ok"), "name": name}


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/providers/{pid}/pull-stream")
async def pull_model_stream(pid: int, name: str = Body(..., embed=True),
                            db: Session = Depends(get_db), _: object = Depends(require_admin)):
    """Stream Ollama's pull progress to the client as Server-Sent Events.

    Each event is {status, total, completed, percent}; a final {done:true} marks
    success, or {error:...} on failure — so the UI can show a live progress bar.
    """
    p = _require_ollama(db, pid)
    name = name.strip()
    if not name:
        raise HTTPException(400, "model name required")
    base = p.base_url.rstrip("/")

    async def gen():
        try:
            async with httpx.AsyncClient(timeout=1800, trust_env=False) as client:
                async with client.stream("POST", f"{base}/api/pull",
                                         json={"model": name, "name": name, "stream": True}) as r:
                    if r.status_code >= 400:
                        body = (await r.aread()).decode("utf-8", "ignore")[:300]
                        yield _sse({"error": f"ollama {r.status_code}: {body}"})
                        return
                    async for line in r.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            obj = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if obj.get("error"):
                            yield _sse({"error": str(obj["error"])})
                            continue
                        total = int(obj.get("total", 0) or 0)
                        completed = int(obj.get("completed", 0) or 0)
                        pct = round(completed / total * 100, 1) if total else None
                        yield _sse({"status": obj.get("status", ""), "total": total,
                                    "completed": completed, "percent": pct})
            yield _sse({"status": "success", "done": True})
        except Exception as e:  # noqa: BLE001
            yield _sse({"error": str(e)})

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.delete("/providers/{pid}/local-models")
async def delete_model(pid: int, name: str, db: Session = Depends(get_db),
                       _: object = Depends(require_admin)):
    p = _require_ollama(db, pid)
    try:
        async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
            r = await client.request("DELETE", f"{p.base_url.rstrip('/')}/api/delete",
                                     json={"model": name, "name": name})
            r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"delete failed: {e}")
    return {"ok": True}


# ---------------- Aliases ----------------
class AliasIn(BaseModel):
    alias: str
    targets: list[dict] = []  # [{provider_id, model}]
    fallback_text: str = ""
    params: dict = {}
    enabled: bool = True


class AliasOut(AliasIn):
    id: int

    class Config:
        from_attributes = True


@router.get("/aliases")
def list_aliases(db: Session = Depends(get_db), _: object = Depends(get_current_user)):
    return db.query(ModelAlias).order_by(ModelAlias.id).all()


@router.post("/aliases", response_model=AliasOut)
def create_alias(body: AliasIn, db: Session = Depends(get_db), _: object = Depends(require_admin)):
    if db.query(ModelAlias).filter(ModelAlias.alias == body.alias).first():
        raise HTTPException(409, "alias exists")
    a = ModelAlias(**body.model_dump())
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


@router.patch("/aliases/{aid}", response_model=AliasOut)
def update_alias(aid: int, body: AliasIn, db: Session = Depends(get_db),
                 _: object = Depends(require_admin)):
    a = db.get(ModelAlias, aid)
    if not a:
        raise HTTPException(404, "not found")
    for k, v in body.model_dump().items():
        setattr(a, k, v)
    db.commit()
    db.refresh(a)
    return a


@router.delete("/aliases/{aid}")
def delete_alias(aid: int, db: Session = Depends(get_db), _: object = Depends(require_admin)):
    a = db.get(ModelAlias, aid)
    if not a:
        raise HTTPException(404, "not found")
    db.delete(a)
    db.commit()
    return {"ok": True}
