"""Embedding app template — OpenAI-compatible /v1/embeddings endpoint.

Backed by the unified model layer's embed() (alias → provider, with fallback),
so a port bound to an embedding model alias (e.g. nomic-embed-text) becomes a
drop-in vectorization endpoint for RAG pipelines. Full metrics/logging.
"""
from __future__ import annotations

import asyncio
import time

from fastapi import FastAPI, HTTPException, Request

from app.apps.base import AppTemplate, PortConfig
from app.db.session import SessionLocal
from app.models_layer.router import AliasNotFound, ModelRouter, resolve_alias
from app.monitor.metrics import metrics


def build_embedding_app(config: PortConfig) -> FastAPI:
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
                "concurrency": config.concurrency}

    @app.get("/v1/models")
    async def list_models():
        return {"object": "list", "data": [{"id": config.model_alias, "object": "model"}]}

    @app.post("/v1/embeddings")
    async def embeddings(request: Request):
        body = await request.json()
        inp = body.get("input")
        if inp is None:
            raise HTTPException(400, "'input' is required")
        if isinstance(inp, str):
            inp = [inp]
        elif not isinstance(inp, list):
            raise HTTPException(400, "'input' must be a string or array of strings")
        texts = [str(x) for x in inp]

        try:
            resolved = _resolve()
        except AliasNotFound as e:
            raise HTTPException(400, str(e))

        started = time.perf_counter()
        excerpt = (texts[0] if texts else "")[:200]
        router = ModelRouter(timeout=config.timeout, max_retries=config.max_retries)
        try:
            async with sem:
                vectors, usage = await router.embed(resolved, texts)
        except Exception as e:  # noqa: BLE001
            metrics.record(
                config.id, False, (time.perf_counter() - started) * 1000,
                model=config.model_alias, request_excerpt=excerpt, error=str(e),
                logging_enabled=config.logging_enabled, log_keep=config.log_keep,
            )
            raise HTTPException(502, str(e))

        metrics.record(
            config.id, True, (time.perf_counter() - started) * 1000,
            model=config.model_alias, prompt_tokens=usage.prompt_tokens,
            request_excerpt=excerpt, response_excerpt=f"{len(vectors)} vector(s)",
            logging_enabled=config.logging_enabled, log_keep=config.log_keep,
        )
        return {
            "object": "list",
            "data": [{"object": "embedding", "embedding": v, "index": i}
                     for i, v in enumerate(vectors)],
            "model": config.model_alias,
            "usage": {"prompt_tokens": usage.prompt_tokens,
                      "total_tokens": usage.prompt_tokens},
        }

    return app


class EmbeddingTemplate(AppTemplate):
    app_type = "embedding"
    title = "向量化 / Embedding"
    description = "OpenAI 兼容的 /v1/embeddings 端点，用于文本向量化（RAG）。需绑定 embedding 模型。"
    default_prompt = ""

    def build_app(self, config: PortConfig):
        return build_embedding_app(config)
