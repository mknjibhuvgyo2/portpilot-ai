"""API key management.

Keys are shown in full exactly once (at creation). Only a SHA-256 hash and an
8-char prefix are stored. The gateway validates incoming keys against these.
A key may be scoped to a single port and given a token quota (0 = unlimited).
"""
from __future__ import annotations

import hashlib
import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.deps import require_admin
from app.db.models import ApiKey, PortService
from app.db.session import get_db

router = APIRouter(prefix="/api/keys", tags=["keys"])


class ApiKeyIn(BaseModel):
    name: str
    port_id: int | None = None
    quota_tokens: int = 0


class ApiKeyOut(BaseModel):
    id: int
    name: str
    key_prefix: str
    port_id: int | None
    quota_tokens: int
    used_tokens: int
    enabled: bool

    class Config:
        from_attributes = True


def _serialize(k: ApiKey) -> dict:
    d = ApiKeyOut.model_validate(k).model_dump()
    d["masked"] = f"{k.key_prefix}…"
    return d


@router.get("")
def list_keys(db: Session = Depends(get_db), _: object = Depends(require_admin)):
    return [_serialize(k) for k in db.query(ApiKey).order_by(ApiKey.id.desc()).all()]


@router.post("")
def create_key(body: ApiKeyIn, db: Session = Depends(get_db), _: object = Depends(require_admin)):
    if body.port_id and not db.get(PortService, body.port_id):
        raise HTTPException(404, "port not found")
    full = "sk-hub-" + secrets.token_urlsafe(24)
    key = ApiKey(
        name=body.name,
        key_prefix=full[:8],
        key_hash=hashlib.sha256(full.encode()).hexdigest(),
        port_id=body.port_id,
        quota_tokens=body.quota_tokens,
    )
    db.add(key)
    db.commit()
    db.refresh(key)
    out = _serialize(key)
    out["key"] = full  # shown once
    return out


@router.patch("/{key_id}")
def toggle_key(key_id: int, db: Session = Depends(get_db), _: object = Depends(require_admin)):
    k = db.get(ApiKey, key_id)
    if not k:
        raise HTTPException(404, "not found")
    k.enabled = not k.enabled
    db.commit()
    db.refresh(k)
    return _serialize(k)


@router.delete("/{key_id}")
def delete_key(key_id: int, db: Session = Depends(get_db), _: object = Depends(require_admin)):
    k = db.get(ApiKey, key_id)
    if not k:
        raise HTTPException(404, "not found")
    db.delete(k)
    db.commit()
    return {"ok": True}
