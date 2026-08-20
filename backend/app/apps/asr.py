"""ASR app template — OpenAI-compatible /v1/audio/transcriptions endpoint.

Backed by the unified model layer's transcribe() (alias -> provider, with
fallback), so a port bound to a speech-to-text model (SenseVoice, Whisper,
Paraformer...) becomes a drop-in transcription endpoint. Full metrics/logging.

Unlike every other template here the request is multipart/form-data, not JSON:
the audio arrives as an uploaded file. Everything downstream -- alias
resolution, the fallback chain, metrics -- is the same as embedding.
"""
from __future__ import annotations

import asyncio
import time

from fastapi import FastAPI, File, Form, HTTPException, UploadFile

from app.apps.base import AppTemplate, PortConfig
from app.db.session import SessionLocal
from app.models_layer.router import AliasNotFound, ModelRouter, resolve_alias
from app.monitor.metrics import metrics

# One minute of 16k mono wav is ~2MB and a dialogue turn is seconds, so this sits
# far above any real request. It exists so a stray huge upload is refused here
# rather than buffered in memory on its way to a model that would reject it.
MAX_AUDIO_BYTES = 32 * 1024 * 1024


def build_asr_app(config: PortConfig) -> FastAPI:
    app = FastAPI(title=f"{config.name} ({config.app_type})")
    sem = asyncio.Semaphore(max(config.concurrency, 1))

    def _resolve():
        db = SessionLocal()
        try:
            return resolve_alias(db, config.model_alias)
        finally:
            db.close()

    def _defaults() -> dict:
        """Per-port audio defaults (ports.extra.audio), e.g. {"language": "zh"}.

        Pinning the language on the port matters: left to auto-detect, a short
        noisy turn gets mislabelled and comes back transcribed into the wrong
        language entirely.
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

    @app.post("/v1/audio/transcriptions")
    async def transcriptions(
        file: UploadFile = File(...),
        model: str = Form(default=""),
        language: str = Form(default=""),
        prompt: str = Form(default=""),
        response_format: str = Form(default=""),
        temperature: str = Form(default=""),
    ):
        # `model` is accepted so stock OpenAI clients work unchanged, then
        # ignored: the port is bound to one alias, same as embedding.
        audio = await file.read()
        if not audio:
            raise HTTPException(400, "'file' is empty")
        if len(audio) > MAX_AUDIO_BYTES:
            raise HTTPException(413, f"audio exceeds {MAX_AUDIO_BYTES // 1048576}MB")

        params = _defaults()
        for key, value in (("language", language), ("prompt", prompt),
                           ("response_format", response_format),
                           ("temperature", temperature)):
            if value:
                params[key] = value

        try:
            resolved = _resolve()
        except AliasNotFound as e:
            raise HTTPException(400, str(e))

        started = time.perf_counter()
        excerpt = f"{file.filename or 'audio'} ({len(audio)} bytes)"
        router = ModelRouter(timeout=config.timeout, max_retries=config.max_retries)
        try:
            async with sem:
                result = await router.transcribe(
                    resolved, audio, file.filename or "audio.wav", params)
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
            response_excerpt=str(result.get("text", ""))[:200],
            logging_enabled=config.logging_enabled, log_keep=config.log_keep,
        )
        return result

    return app


class AsrTemplate(AppTemplate):
    app_type = "asr"
    title = "语音转写 / ASR"
    description = ("OpenAI 兼容的 /v1/audio/transcriptions 端点，把音频转成文字。"
                   "需绑定语音识别模型（SenseVoice / Whisper / Paraformer）。"
                   "可在端口的 extra.audio 里固定 language，避免短音频被认成别的语种。")
    default_prompt = ""
    category = "audio"

    def build_app(self, config: PortConfig):
        return build_asr_app(config)
