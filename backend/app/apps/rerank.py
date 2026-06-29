"""Rerank app template — Jina/Cohere-compatible /v1/rerank endpoint.

Backed by the unified model layer's rerank() (alias → provider, with fallback),
so a port bound to a reranker model becomes a drop-in relevance-scoring endpoint
for RAG pipelines (retrieve with embeddings, then rerank here). Full
metrics/logging.
"""
from __future__ import annotations

import asyncio
import time

from fastapi import FastAPI, HTTPException, Request

from app.apps.base import AppTemplate, PortConfig
from app.db.session import SessionLocal
from app.models_layer.router import AliasNotFound, ModelRouter, resolve_alias
from app.monitor.metrics import metrics


def build_rerank_app(config: PortConfig) -> FastAPI:
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

    @app.post("/v1/rerank")
    async def rerank(request: Request):
        body = await request.json()
        query = body.get("query")
        docs = body.get("documents")
        if not isinstance(query, str) or not query:
            raise HTTPException(400, "'query' (non-empty string) is required")
        if not isinstance(docs, list) or not docs:
            raise HTTPException(400, "'documents' (non-empty array) is required")
        documents = [str(x) for x in docs]
        top_n = body.get("top_n")
        if top_n is not None:
            try:
                top_n = int(top_n)
            except (TypeError, ValueError):
                raise HTTPException(400, "'top_n' must be an integer")
        return_documents = bool(body.get("return_documents", False))

        try:
            resolved = _resolve()
        except AliasNotFound as e:
            raise HTTPException(400, str(e))

        started = time.perf_counter()
        excerpt = query[:200]
        router = ModelRouter(timeout=config.timeout, max_retries=config.max_retries)
        try:
            async with sem:
                results, usage = await router.rerank(resolved, query, documents, top_n)
        except Exception as e:  # noqa: BLE001
            metrics.record(
                config.id, False, (time.perf_counter() - started) * 1000,
                model=config.model_alias, request_excerpt=excerpt, error=str(e),
                logging_enabled=config.logging_enabled, log_keep=config.log_keep,
            )
            raise HTTPException(502, str(e))

        if top_n is not None:
            results = results[:max(top_n, 0)]
        out = []
        for r in results:
            entry = {"index": r["index"], "relevance_score": r["relevance_score"]}
            if return_documents:
                idx = r["index"]
                entry["document"] = {"text": documents[idx]} if 0 <= idx < len(documents) else None
            out.append(entry)

        metrics.record(
            config.id, True, (time.perf_counter() - started) * 1000,
            model=config.model_alias, prompt_tokens=usage.prompt_tokens,
            request_excerpt=excerpt, response_excerpt=f"{len(out)} result(s)",
            logging_enabled=config.logging_enabled, log_keep=config.log_keep,
        )
        return {
            "object": "list",
            "model": config.model_alias,
            "results": out,
            "usage": {"prompt_tokens": usage.prompt_tokens,
                      "total_tokens": usage.prompt_tokens},
        }

    if config.path_alias:
        app.add_api_route(config.path_alias, rerank, methods=["POST"])

    return app


class RerankTemplate(AppTemplate):
    app_type = "rerank"
    title = "重排序 / Rerank"
    description = "Jina/Cohere 兼容的 /v1/rerank 端点，按相关性给文档打分排序（RAG 检索后精排）。需绑定 reranker 模型。"
    default_prompt = ""
    io_format = {
        "endpoints": ["POST /v1/rerank", "GET /info", "GET /health"],
        "input": {"example": {"model": "（端口别名，可省略）", "query": "查询语句",
                              "documents": ["文档1", "文档2", "文档3"], "top_n": 3},
                  "fields": "Jina/Cohere 兼容 rerank 请求。"},
        "output": {"example": {"object": "list", "model": "…", "results": [
            {"index": 0, "relevance_score": 0.97}, {"index": 2, "relevance_score": 0.41}],
            "usage": {"prompt_tokens": 0, "total_tokens": 0}},
            "note": "本模板不使用提示词；相关性分数由绑定的 reranker 模型决定。"},
    }

    def build_app(self, config: PortConfig):
        return build_rerank_app(config)
