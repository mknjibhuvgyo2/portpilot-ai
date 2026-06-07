"""OpenAI-compatible provider.

Works for vendor APIs (OpenAI, DeepSeek, Doubao, Kimi, Qwen, Gemini-OpenAI, etc.)
and local runtimes that expose an OpenAI-compatible /v1 endpoint
(LM Studio, llama.cpp server, Ollama's /v1).
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from app.models_layer.providers.base import BaseProvider
from app.models_layer.types import ChatMessage, ChatRequest, ChatResult, StreamIterator, Usage


def _msg_to_dict(m: ChatMessage) -> dict[str, Any]:
    return {"role": m.role, "content": m.content}


class OpenAICompatProvider(BaseProvider):
    kind = "openai_compat"

    def _url(self, path: str) -> str:
        base = self.base_url
        # Allow base_url with or without /v1 suffix
        if base.endswith("/v1"):
            return f"{base}{path}"
        return f"{base}/v1{path}"

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        # custom headers for long-tail / self-hosted vendors (provider.extra.headers)
        extra_h = self.extra.get("headers") if isinstance(self.extra, dict) else None
        if isinstance(extra_h, dict):
            h.update({str(k): str(v) for k, v in extra_h.items()})
        return h

    def _body(self, model: str, req: ChatRequest, stream: bool) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "messages": [_msg_to_dict(m) for m in req.messages],
            "stream": stream,
        }
        if stream:
            # Ask compatible servers (OpenAI, vLLM, LM Studio, llama.cpp, Ollama /v1)
            # to emit a final usage chunk so we can record real token counts.
            body["stream_options"] = {"include_usage": True}
        for k in ("temperature", "top_p", "max_tokens", "presence_penalty", "frequency_penalty"):
            if k in req.params and req.params[k] is not None:
                body[k] = req.params[k]
        # provider advanced params (top_k, repeat_penalty, min_p, seed, n_predict, ...)
        # are accepted as extra fields by LM Studio / llama.cpp servers.
        for k, v in self.advanced().items():
            if k in ("keep_alive",) or v in (None, ""):
                continue
            body.setdefault(k, v)
        return body

    async def chat(self, model: str, req: ChatRequest) -> ChatResult:
        async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
            r = await client.post(
                self._url("/chat/completions"),
                headers=self._headers(),
                json=self._body(model, req, stream=False),
            )
            r.raise_for_status()
            data = r.json()
        choice = (data.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content") or ""
        usage = data.get("usage") or {}
        return ChatResult(
            text=text,
            model=data.get("model", model),
            usage=Usage(
                prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
                completion_tokens=int(usage.get("completion_tokens", 0) or 0),
            ),
            raw=data,
        )

    async def stream(self, model: str, req: ChatRequest,
                     usage_out: dict | None = None) -> StreamIterator:
        async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
            async with client.stream(
                "POST",
                self._url("/chat/completions"),
                headers=self._headers(),
                json=self._body(model, req, stream=True),
            ) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[len("data:"):].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    usage = chunk.get("usage")
                    if usage and usage_out is not None:
                        usage_out["prompt_tokens"] = int(usage.get("prompt_tokens", 0) or 0)
                        usage_out["completion_tokens"] = int(usage.get("completion_tokens", 0) or 0)
                    delta = ((chunk.get("choices") or [{}])[0].get("delta") or {}).get("content")
                    if delta:
                        yield delta

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
                r = await client.get(self._url("/models"), headers=self._headers())
                return r.status_code < 500
        except Exception:
            return False

    async def embed(self, model: str, inputs: list[str]):
        async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
            r = await client.post(self._url("/embeddings"), headers=self._headers(),
                                  json={"model": model, "input": inputs})
            r.raise_for_status()
            data = r.json()
        items = sorted(data.get("data") or [], key=lambda d: d.get("index", 0))
        vectors = [d.get("embedding") or [] for d in items]
        u = data.get("usage") or {}
        return vectors, Usage(prompt_tokens=int(u.get("prompt_tokens", 0) or 0))

    async def rerank(self, model: str, query: str, documents: list[str],
                     top_n: int | None = None):
        # Jina / Cohere / vLLM / llama.cpp compatible POST /v1/rerank.
        payload: dict[str, Any] = {"model": model, "query": query, "documents": documents}
        if top_n is not None:
            payload["top_n"] = top_n
        async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
            r = await client.post(self._url("/rerank"), headers=self._headers(), json=payload)
            r.raise_for_status()
            data = r.json()
        results = []
        for item in data.get("results") or []:
            score = item.get("relevance_score", item.get("score", 0.0))
            results.append({"index": int(item.get("index", 0)),
                            "relevance_score": float(score or 0.0)})
        results.sort(key=lambda x: x["relevance_score"], reverse=True)
        u = data.get("usage") or {}
        return results, Usage(prompt_tokens=int(u.get("prompt_tokens", 0) or 0))

    async def raw_chat(self, model: str, body: dict) -> dict:
        payload = {**body, "model": model, "stream": False}
        async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
            r = await client.post(self._url("/chat/completions"), headers=self._headers(),
                                  json=payload)
            r.raise_for_status()
            return r.json()

    async def raw_stream(self, model: str, body: dict):
        payload = {**body, "model": model, "stream": True}
        async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
            async with client.stream("POST", self._url("/chat/completions"),
                                     headers=self._headers(), json=payload) as r:
                r.raise_for_status()
                async for chunk in r.aiter_raw():  # forward upstream SSE bytes verbatim
                    yield chunk

    async def list_models(self) -> list[str]:
        try:
            async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
                r = await client.get(self._url("/models"), headers=self._headers())
                r.raise_for_status()
                data = r.json()
            items = data.get("data") if isinstance(data, dict) else data
            return [m.get("id", "") for m in (items or []) if m.get("id")]
        except Exception:
            return []
