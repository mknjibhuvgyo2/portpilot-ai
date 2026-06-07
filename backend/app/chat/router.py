"""Built-in test chat.

Two modes:
  - mode="port":  send the conversation to a running port service's
                  /v1/chat/completions (tests the real deployed endpoint).
  - mode="model": run the unified model router directly against a model alias
                  (test a model/fallback chain without deploying a port).

Messages use OpenAI-style content, so image input works by sending content
parts of type image_url with a data: URL. Voice input is transcribed in the
browser (Web Speech API) and arrives here as plain text.
"""
from __future__ import annotations

import json

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.db.models import PortService
from app.db.session import SessionLocal, get_db
from app.models_layer.router import (
    AliasNotFound,
    ModelRouter,
    messages_from_payload,
    resolve_alias,
)
from app.models_layer.types import ChatMessage, ChatRequest
from app.ports.manager import manager

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatBody(BaseModel):
    mode: str = "model"  # "port" | "model"
    port_id: int | None = None
    model_alias: str | None = None
    system_prompt: str = ""
    messages: list[dict]
    params: dict = {}
    stream: bool = True


@router.post("")
async def chat(body: ChatBody, db: Session = Depends(get_db),
               _: object = Depends(get_current_user)):
    if body.mode == "port":
        return await _chat_via_port(body, db)
    return await _chat_via_model(body)


async def _chat_via_port(body: ChatBody, db: Session):
    if not body.port_id:
        raise HTTPException(400, "port_id required for mode=port")
    port = db.get(PortService, body.port_id)
    if not port:
        raise HTTPException(404, "port not found")
    if not manager.is_running(port.id):
        # Port not started — still let the tester try the port's configuration by
        # routing through its own model alias + system prompt directly.
        if not port.model_alias:
            raise HTTPException(409, "port is not running and has no model alias")
        fallback = ChatBody(
            mode="model", model_alias=port.model_alias, system_prompt=port.system_prompt,
            messages=body.messages, params=body.params, stream=body.stream,
        )
        return await _chat_via_model(fallback)

    payload = {"messages": body.messages, "stream": body.stream, **body.params}
    target = f"http://127.0.0.1:{port.port}/v1/chat/completions"

    if not body.stream:
        async with httpx.AsyncClient(timeout=port.timeout, trust_env=False) as client:
            r = await client.post(target, json=payload)
            return r.json()

    async def gen():
        async with httpx.AsyncClient(timeout=port.timeout, trust_env=False) as client:
            async with client.stream("POST", target, json=payload) as r:
                async for line in r.aiter_lines():
                    if line:
                        yield line + "\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


async def _chat_via_model(body: ChatBody):
    if not body.model_alias:
        raise HTTPException(400, "model_alias required for mode=model")
    msgs = messages_from_payload(body.messages)
    if body.system_prompt and not any(m.role == "system" for m in msgs):
        msgs.insert(0, ChatMessage(role="system", content=body.system_prompt))

    db = SessionLocal()
    try:
        resolved = resolve_alias(db, body.model_alias)
    except AliasNotFound as e:
        raise HTTPException(400, str(e))
    finally:
        db.close()

    mr = ModelRouter()
    req = ChatRequest(messages=msgs, params=body.params, stream=body.stream)

    if not body.stream:
        result = await mr.chat(resolved, req)
        return {"model": result.model, "content": result.text,
                "usage": {"prompt_tokens": result.usage.prompt_tokens,
                          "completion_tokens": result.usage.completion_tokens}}

    async def gen():
        try:
            async for delta in mr.stream(resolved, req):
                yield f"data: {json.dumps({'delta': delta}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:  # noqa: BLE001
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")
