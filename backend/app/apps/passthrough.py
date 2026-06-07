"""Passthrough proxy app template — transparent OpenAI /v1/chat/completions.

Unlike generic_chat (which injects a system prompt and forwards only a few
sampling params), this template forwards the ENTIRE OpenAI request body to the
upstream model unchanged — so advanced clients can use tools / function calling,
response_format (JSON mode), seed, stop, logprobs, n, etc. It still resolves
through the model alias, so fallback / load-balancing / pinned-GPU all apply.
The upstream response (JSON or streamed SSE) is returned verbatim.
"""
from __future__ import annotations

import asyncio
import json
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.apps.base import AppTemplate, PortConfig
from app.db.session import SessionLocal
from app.models_layer.router import AliasNotFound, ModelRouter, resolve_alias
from app.monitor.metrics import metrics


def build_passthrough_app(config: PortConfig) -> FastAPI:
    app = FastAPI(title=f"{config.name} ({config.app_type})")
    sem = asyncio.Semaphore(max(config.concurrency, 1))

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
        return {"name": config.name, "slug": config.slug, "port": config.port,
                "app_type": config.app_type, "model_alias": config.model_alias,
                "streaming": config.streaming, "concurrency": config.concurrency}

    @app.get("/v1/models")
    async def list_models():
        return {"object": "list", "data": [{"id": config.model_alias, "object": "model"}]}

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        body = await request.json()
        if not isinstance(body, dict) or not body.get("messages"):
            raise HTTPException(400, "'messages' is required")
        want_stream = bool(body.get("stream", False)) and config.streaming
        try:
            resolved = _resolve()
        except AliasNotFound as e:
            raise HTTPException(400, str(e))

        started = time.perf_counter()
        router = ModelRouter(timeout=config.timeout, max_retries=config.max_retries)

        if want_stream:
            async def gen():
                ok, err = True, ""
                try:
                    async with sem:
                        async for chunk in router.raw_stream(resolved, body):
                            yield chunk
                except Exception as e:  # noqa: BLE001
                    ok, err = False, str(e)
                    yield ("data: " + json.dumps({"error": err}) + "\n\n").encode()
                finally:
                    metrics.record(
                        config.id, ok, (time.perf_counter() - started) * 1000,
                        model=config.model_alias, error=err,
                        logging_enabled=config.logging_enabled, log_keep=config.log_keep,
                    )
            return StreamingResponse(gen(), media_type="text/event-stream")

        try:
            async with sem:
                data = await router.raw_chat(resolved, body)
        except Exception as e:  # noqa: BLE001
            metrics.record(
                config.id, False, (time.perf_counter() - started) * 1000,
                model=config.model_alias, error=str(e),
                logging_enabled=config.logging_enabled, log_keep=config.log_keep,
            )
            raise HTTPException(502, str(e))

        usage = data.get("usage") or {} if isinstance(data, dict) else {}
        metrics.record(
            config.id, True, (time.perf_counter() - started) * 1000,
            model=str(data.get("model", config.model_alias)) if isinstance(data, dict)
            else config.model_alias,
            prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
            completion_tokens=int(usage.get("completion_tokens", 0) or 0),
            logging_enabled=config.logging_enabled, log_keep=config.log_keep,
        )
        return JSONResponse(data)

    return app


class PassthroughTemplate(AppTemplate):
    app_type = "passthrough"
    title = "透传代理 / Passthrough"
    description = ("透明转发完整 OpenAI 请求体到上游模型（保留 tools/函数调用、JSON 模式、"
                   "seed、stop 等全部参数），不注入系统提示词。仍走别名的 fallback / 负载均衡。")
    default_prompt = ""

    def build_app(self, config: PortConfig):
        return build_passthrough_app(config)
