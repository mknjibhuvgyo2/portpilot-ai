"""Provider adapter base class."""
from __future__ import annotations

import abc
from typing import AsyncIterator

from app.models_layer.types import ChatRequest, ChatResult, StreamIterator, Usage


class BaseProvider(abc.ABC):
    kind: str = "base"

    def __init__(self, base_url: str, api_key: str = "", timeout: float = 120.0,
                 extra: dict | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.extra = extra or {}

    def advanced(self) -> dict:
        """Engine-specific advanced params configured on the provider."""
        adv = self.extra.get("advanced") if isinstance(self.extra, dict) else None
        return adv if isinstance(adv, dict) else {}

    @abc.abstractmethod
    async def chat(self, model: str, req: ChatRequest) -> ChatResult:
        """Non-streaming chat completion."""

    @abc.abstractmethod
    async def stream(self, model: str, req: ChatRequest,
                     usage_out: dict | None = None) -> StreamIterator:
        """Streaming chat completion; yields text deltas.

        When the upstream reports real token counts (e.g. an OpenAI usage chunk
        or Ollama's final eval counts) and `usage_out` is provided, the provider
        fills it with {"prompt_tokens": int, "completion_tokens": int} so callers
        can record exact usage instead of a character-based estimate.
        """

    async def health(self) -> bool:
        """Lightweight reachability check; override per provider."""
        return True

    async def list_models(self) -> list[str]:
        """Return available model ids (best-effort); override per provider."""
        return []

    async def embed(self, model: str, inputs: list[str]) -> tuple[list[list[float]], Usage]:
        """Return one embedding vector per input. Override per provider."""
        raise NotImplementedError("this provider does not support embeddings")

    async def rerank(self, model: str, query: str, documents: list[str],
                     top_n: int | None = None) -> tuple[list[dict], Usage]:
        """Score `documents` against `query`. Returns (results, usage) where each
        result is {"index": int, "relevance_score": float}, sorted by score desc.
        Override per provider."""
        raise NotImplementedError("this provider does not support rerank")

    async def raw_chat(self, model: str, body: dict) -> dict:
        """Transparent passthrough: forward the full OpenAI request body (model
        overridden) to /v1/chat/completions and return the raw JSON response.
        Preserves tools, response_format, seed, etc. Override per provider."""
        raise NotImplementedError("this provider does not support raw passthrough")

    async def raw_stream(self, model: str, body: dict) -> AsyncIterator[bytes]:
        """Transparent streaming passthrough: forward the body (stream=true) and
        yield the upstream SSE bytes verbatim. Override per provider."""
        raise NotImplementedError("this provider does not support raw passthrough")
        yield b""  # pragma: no cover - marks this as an async generator
