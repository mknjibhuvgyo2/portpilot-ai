"""Generic chat / scoring app template.

Exposes an OpenAI-compatible chat endpoint backed by the unified model router,
with the port's system prompt injected and full metrics/logging.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.apps.base import AppTemplate, PortConfig
from app.models_layer.router import (
    AliasNotFound,
    ModelRouter,
    messages_from_payload,
    resolve_alias,
)
from app.models_layer.types import ChatMessage, ChatRequest
from app.monitor.metrics import metrics
from app.db.session import SessionLocal


def _estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) for streamed responses that lack usage."""
    return max(len(text) // 4, 0)


def _prompt_chars(messages: list[ChatMessage]) -> int:
    n = 0
    for m in messages:
        c = m.content
        if isinstance(c, str):
            n += len(c)
        elif isinstance(c, list):
            for part in c:
                if isinstance(part, dict) and part.get("type") == "text":
                    n += len(str(part.get("text", "")))
    return n


def _excerpt(messages: list[ChatMessage]) -> str:
    for m in reversed(messages):
        if m.role == "user":
            c = m.content
            if isinstance(c, str):
                return c[:500]
            if isinstance(c, list):
                for part in c:
                    if isinstance(part, dict) and part.get("type") == "text":
                        return str(part.get("text", ""))[:500]
    return ""


def build_generic_chat_app(config: PortConfig) -> FastAPI:
    app = FastAPI(title=f"{config.name} ({config.app_type})")
    # Semaphore is rebuilt lazily when config.concurrency is hot-swapped, so the
    # in-flight limit tracks live config without restarting the port.
    _sem_state: dict = {"size": 0, "sem": asyncio.Semaphore(1)}

    def _sem() -> asyncio.Semaphore:
        size = max(config.concurrency, 1)
        if size != _sem_state["size"]:
            _sem_state["size"] = size
            _sem_state["sem"] = asyncio.Semaphore(size)
        return _sem_state["sem"]

    def _resolve():
        db = SessionLocal()
        try:
            return resolve_alias(db, config.model_alias)
        finally:
            db.close()

    @app.get("/health")
    async def health():
        return {"status": "ok", "slug": config.slug, "app_type": config.app_type,
                "model_alias": config.model_alias}

    @app.get("/info")
    async def info():
        return {
            "name": config.name, "slug": config.slug, "port": config.port,
            "app_type": config.app_type, "model_alias": config.model_alias,
            "streaming": config.streaming, "concurrency": config.concurrency,
        }

    @app.get("/v1/models")
    async def list_models():
        return {"object": "list", "data": [{"id": config.model_alias, "object": "model"}]}

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        body = await request.json()
        incoming = body.get("messages") or []
        msgs = messages_from_payload(incoming)
        if config.system_prompt and not any(m.role == "system" for m in msgs):
            msgs.insert(0, ChatMessage(role="system", content=config.system_prompt))

        params = {k: body.get(k) for k in ("temperature", "top_p", "max_tokens")
                  if body.get(k) is not None}
        want_stream = bool(body.get("stream", False)) and config.streaming
        router = ModelRouter(timeout=config.timeout, max_retries=config.max_retries)

        try:
            resolved = _resolve()
        except AliasNotFound as e:
            raise HTTPException(status_code=400, detail=str(e))

        req = ChatRequest(messages=msgs, params=params, stream=want_stream)
        started = time.perf_counter()
        dbg = bool(getattr(config, "debug", False))
        req_excerpt = "\n".join(
            f"--- {m.role} ---\n{m.content if isinstance(m.content, str) else _excerpt([m])}" for m in msgs
        ) if dbg else _excerpt(msgs)

        if want_stream:
            async def gen():
                collected = []
                ok = True
                err = ""
                real_usage: dict = {}
                cmpl_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
                try:
                    async with _sem():
                        async for delta in router.stream(resolved, req, usage_out=real_usage):
                            collected.append(delta)
                            chunk = {
                                "id": cmpl_id, "object": "chat.completion.chunk",
                                "model": config.model_alias,
                                "choices": [{"index": 0, "delta": {"content": delta},
                                             "finish_reason": None}],
                            }
                            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                    done = {"id": cmpl_id, "object": "chat.completion.chunk",
                            "model": config.model_alias,
                            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
                    yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n"
                    # Emit a final usage chunk (OpenAI include_usage style) so
                    # downstream clients and the gateway can record real tokens.
                    text = "".join(collected)
                    pt = real_usage.get("prompt_tokens")
                    ct = real_usage.get("completion_tokens")
                    if pt is None:
                        pt = max(_prompt_chars(msgs) // 4, 0)
                    if ct is None:
                        ct = _estimate_tokens(text)
                    usage_chunk = {
                        "id": cmpl_id, "object": "chat.completion.chunk",
                        "model": config.model_alias, "choices": [],
                        "usage": {"prompt_tokens": pt, "completion_tokens": ct,
                                  "total_tokens": pt + ct},
                    }
                    yield f"data: {json.dumps(usage_chunk, ensure_ascii=False)}\n\n"
                    yield "data: [DONE]\n\n"
                except Exception as e:  # noqa: BLE001
                    ok = False
                    err = str(e)
                    yield f"data: {json.dumps({'error': err}, ensure_ascii=False)}\n\n"
                finally:
                    text = "".join(collected)
                    # Prefer real token counts reported by the engine; fall back
                    # to a character-based estimate when unavailable.
                    pt = real_usage.get("prompt_tokens")
                    ct = real_usage.get("completion_tokens")
                    if pt is None:
                        pt = max(_prompt_chars(msgs) // 4, 0)
                    if ct is None:
                        ct = _estimate_tokens(text)
                    metrics.record(
                        config.id, ok, (time.perf_counter() - started) * 1000,
                        model=config.model_alias, request_excerpt=req_excerpt,
                        response_excerpt=text, error=err,
                        prompt_tokens=pt, completion_tokens=ct,
                        logging_enabled=config.logging_enabled, log_keep=config.log_keep,
                        debug=dbg,
                    )

            return StreamingResponse(gen(), media_type="text/event-stream")

        # Non-streaming
        try:
            async with _sem():
                result = await router.chat(resolved, req)
        except Exception as e:  # noqa: BLE001
            metrics.record(
                config.id, False, (time.perf_counter() - started) * 1000,
                model=config.model_alias, request_excerpt=req_excerpt, error=str(e),
                logging_enabled=config.logging_enabled, log_keep=config.log_keep,
                debug=dbg,
            )
            raise HTTPException(status_code=502, detail=str(e))

        metrics.record(
            config.id, True, (time.perf_counter() - started) * 1000,
            model=result.model, prompt_tokens=result.usage.prompt_tokens,
            completion_tokens=result.usage.completion_tokens,
            request_excerpt=req_excerpt, response_excerpt=result.text,
            logging_enabled=config.logging_enabled, log_keep=config.log_keep,
            debug=dbg,
        )
        return JSONResponse({
            "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
            "object": "chat.completion",
            "model": result.model,
            "choices": [{"index": 0,
                         "message": {"role": "assistant", "content": result.text},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": result.usage.prompt_tokens,
                      "completion_tokens": result.usage.completion_tokens,
                      "total_tokens": result.usage.prompt_tokens + result.usage.completion_tokens},
        })

    # also serve the main endpoint at a user-defined custom path, if any
    if config.path_alias:
        app.add_api_route(config.path_alias, chat_completions, methods=["POST"])

    return app


class GenericChatTemplate(AppTemplate):
    app_type = "generic_chat"
    title = "通用聊天 / 评分"
    description = "OpenAI 兼容的通用对话端点，注入系统提示词，支持图像输入与流式输出。"

    def build_app(self, config: PortConfig):
        return build_generic_chat_app(config)
