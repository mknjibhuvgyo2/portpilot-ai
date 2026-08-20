"""TTS app template — OpenAI-compatible /v1/audio/speech endpoint.

Backed by the unified model layer's speak() (alias -> provider, with fallback),
so a port bound to a speech model (CosyVoice, Kokoro, F5-TTS...) becomes a
drop-in synthesis endpoint. Full metrics/logging.

The one thing that makes this template unlike every other one here: **the
response body is binary audio, not JSON**. So it returns a raw Response carrying
the upstream's content-type -- handing bytes back as a dict would have FastAPI
JSON-encode them and corrupt the audio.
"""
from __future__ import annotations

import asyncio
import time

from fastapi import FastAPI, HTTPException, Request, Response

from app.apps.base import AppTemplate, PortConfig
from app.db.session import SessionLocal
from app.models_layer.router import AliasNotFound, ModelRouter, resolve_alias
from app.monitor.metrics import metrics

# Synthesis cost scales with input length, and a runaway prompt ties up the GPU
# for minutes. A spoken turn is a sentence or two; 4000 chars is already several
# minutes of audio.
MAX_INPUT_CHARS = 4000


def build_tts_app(config: PortConfig) -> FastAPI:
    app = FastAPI(title=f"{config.name} ({config.app_type})")
    sem = asyncio.Semaphore(max(config.concurrency, 1))

    def _resolve():
        db = SessionLocal()
        try:
            return resolve_alias(db, config.model_alias)
        finally:
            db.close()

    def _defaults() -> dict:
        """Per-port synthesis defaults (ports.extra.audio), e.g.
        {"voice": "中文女", "response_format": "mp3", "speed": 1.0}.

        Pinning the voice on the port is what keeps one researcher persona
        sounding like the same person across every interview.
        """
        d = config.extra.get("audio") if isinstance(config.extra, dict) else None
        return dict(d) if isinstance(d, dict) else {}

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

    @app.post("/v1/audio/speech")
    async def speech(request: Request):
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            # A body that isn't valid UTF-8 JSON is a client error. Letting the
            # decode error escape reports 500, which reads as "synthesis broke"
            # when the caller simply sent the wrong encoding.
            raise HTTPException(400, "body must be UTF-8 encoded JSON")
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be a JSON object")
        text = body.get("input")
        if not isinstance(text, str) or not text.strip():
            raise HTTPException(400, "'input' (non-empty string) is required")
        if len(text) > MAX_INPUT_CHARS:
            raise HTTPException(400, f"'input' exceeds {MAX_INPUT_CHARS} characters")

        params = _defaults()
        for key in ("voice", "response_format", "speed"):
            if body.get(key) not in (None, ""):
                params[key] = body[key]

        try:
            resolved = _resolve()
        except AliasNotFound as e:
            raise HTTPException(400, str(e))

        started = time.perf_counter()
        excerpt = text[:200]
        router = ModelRouter(timeout=config.timeout, max_retries=config.max_retries)
        try:
            async with sem:
                audio, content_type = await router.speak(resolved, text, params)
        except Exception as e:  # noqa: BLE001
            metrics.record(
                config.id, False, (time.perf_counter() - started) * 1000,
                model=config.model_alias, request_excerpt=excerpt, error=str(e),
                logging_enabled=config.logging_enabled, log_keep=config.log_keep,
            )
            raise HTTPException(502, str(e))

        metrics.record(
            config.id, True, (time.perf_counter() - started) * 1000,
            model=config.model_alias, request_excerpt=excerpt,
            response_excerpt=f"{len(audio)} bytes {content_type}",
            logging_enabled=config.logging_enabled, log_keep=config.log_keep,
        )
        return Response(content=audio, media_type=content_type)

    return app


class TtsTemplate(AppTemplate):
    app_type = "tts"
    title = "语音合成 / TTS"
    description = ("OpenAI 兼容的 /v1/audio/speech 端点，把文字读成音频（返回二进制）。"
                   "需绑定语音合成模型（CosyVoice / Kokoro / F5-TTS）。"
                   "可在端口的 extra.audio 里固定 voice，让同一个角色始终是同一把嗓子。")
    default_prompt = ""
    category = "audio"

    def build_app(self, config: PortConfig):
        return build_tts_app(config)
