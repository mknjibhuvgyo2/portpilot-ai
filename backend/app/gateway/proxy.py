"""Built-in reverse proxy / API gateway.

Routes /gw/{slug}/<path> to the local port bound to that slug, streaming the
response back. Optionally enforces an API key when the target port has
auth_required = True (key checked against the api_keys table).
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.billing.pricing import compute_cost, get_pricing
from app.db.models import ApiKey, PortService, UsageStat
from app.db.session import SessionLocal, get_db

router = APIRouter(prefix="/gw", tags=["gateway"])

_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}


def _check_api_key(request: Request, port: PortService, db: Session) -> ApiKey | None:
    """Validate the API key (if the port requires one). Returns the key for
    usage accounting, or None when the port is open."""
    if not port.auth_required:
        return None
    raw = request.headers.get("authorization", "")
    token = raw[7:].strip() if raw.lower().startswith("bearer ") else request.headers.get(
        "x-api-key", "")
    if not token:
        raise HTTPException(401, "API key required")
    prefix = token[:8]
    h = hashlib.sha256(token.encode()).hexdigest()
    key = (db.query(ApiKey)
           .filter(ApiKey.key_prefix == prefix, ApiKey.key_hash == h, ApiKey.enabled.is_(True))
           .first())
    if not key or (key.port_id and key.port_id != port.id):
        raise HTTPException(403, "Invalid API key")
    if key.quota_tokens and key.used_tokens >= key.quota_tokens:
        raise HTTPException(429, "Quota exceeded")
    return key


def _model_of(body: bytes) -> str:
    try:
        return str(json.loads(body).get("model", "")) if body else ""
    except Exception:  # noqa: BLE001
        return ""


_PT_RE = re.compile(r'"prompt_tokens"\s*:\s*(\d+)')
_CT_RE = re.compile(r'"completion_tokens"\s*:\s*(\d+)')


def _extract_usage(text: str) -> tuple[int, int] | None:
    """Pull real token counts from an OpenAI-style response (JSON or SSE).

    Works for both a plain completion body and a streamed `usage` chunk, taking
    the last occurrence so a trailing usage object wins. Returns None when the
    upstream reported no usage (caller then falls back to a byte estimate).
    """
    pt = _PT_RE.findall(text)
    ct = _CT_RE.findall(text)
    if pt and ct:
        return int(pt[-1]), int(ct[-1])
    return None


def _bump_key_usage(key_id: int, port_id: int, model: str, req_bytes: int, resp_bytes: int,
                    real: tuple[int, int] | None = None) -> None:
    """Accumulate token usage onto the key (real counts when the upstream
    reported them, else a ~4 bytes/token estimate).

    Keeps quota enforcement meaningful and feeds per-key cost stats. Rows here
    always carry api_key_id, so the port/model summary (api_key_id IS NULL) is
    unaffected and nothing is double-counted.
    """
    if real is not None:
        pt, ct = real
    else:
        pt = max(req_bytes // 4, 0)
        ct = max(resp_bytes // 4, 0)
    db = SessionLocal()
    try:
        k = db.get(ApiKey, key_id)
        if k is None:
            return
        k.used_tokens += pt + ct
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        cost = compute_cost(model or "", pt, ct, get_pricing(db))
        row = (db.query(UsageStat)
               .filter(UsageStat.day == day, UsageStat.port_id == port_id,
                       UsageStat.api_key_id == key_id, UsageStat.model == (model or ""))
               .first())
        if row is None:
            row = UsageStat(day=day, port_id=port_id, api_key_id=key_id, model=model or "")
            db.add(row)
        row.requests += 1
        row.prompt_tokens += pt
        row.completion_tokens += ct
        row.cost = round(row.cost + cost, 6)
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
    finally:
        db.close()


@router.api_route("/{slug}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def gateway(slug: str, path: str, request: Request, db: Session = Depends(get_db)):
    port = db.query(PortService).filter(PortService.slug == slug).first()
    if not port:
        raise HTTPException(404, f"No service for slug '{slug}'")
    key = _check_api_key(request, port, db)

    target = f"http://127.0.0.1:{port.port}/{path}"
    body = await request.body()
    fwd_headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP}
    key_id = key.id if key else None
    port_id = port.id
    model = _model_of(body) if key_id else ""
    req_len = len(body)

    client = httpx.AsyncClient(timeout=port.timeout, trust_env=False)
    req = client.build_request(
        request.method, target, params=request.query_params, headers=fwd_headers, content=body,
    )
    upstream = await client.send(req, stream=True)
    resp_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _HOP_BY_HOP}

    # Only sniff token usage out of textual LLM responses; keep a bounded tail
    # so the trailing usage object is captured without buffering large bodies.
    ctype = (upstream.headers.get("content-type") or "").lower()
    sniff = key_id is not None and ("json" in ctype or "event-stream" in ctype)
    TAIL_CAP = 16 * 1024

    async def body_iter():
        resp_len = 0
        tail = bytearray()
        try:
            async for chunk in upstream.aiter_raw():
                resp_len += len(chunk)
                if sniff:
                    tail.extend(chunk)
                    if len(tail) > TAIL_CAP:
                        del tail[:-TAIL_CAP]
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()
            if key_id is not None:
                real = _extract_usage(tail.decode("utf-8", "ignore")) if sniff else None
                _bump_key_usage(key_id, port_id, model, req_len, resp_len, real=real)

    return StreamingResponse(
        body_iter(), status_code=upstream.status_code,
        headers=resp_headers, media_type=upstream.headers.get("content-type"),
    )
