"""Native Ollama provider (/api/chat).

Uses Ollama's native API which handles vision models by attaching base64
images on the message `images` array. Vendor-agnostic content (OpenAI-style
list with image_url) is converted here.
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from app.models_layer.providers.base import BaseProvider
from app.models_layer.types import ChatMessage, ChatRequest, ChatResult, StreamIterator, Usage


def _split_content(content: Any) -> tuple[str, list[str]]:
    """Return (text, [base64_images]) from a str or OpenAI-style content list."""
    if isinstance(content, str):
        return content, []
    text_parts: list[str] = []
    images: list[str] = []
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                text_parts.append(part.get("text", ""))
            elif part.get("type") == "image_url":
                url = (part.get("image_url") or {}).get("url", "")
                if url.startswith("data:"):
                    # data:image/png;base64,XXXX  -> keep only the base64 payload
                    images.append(url.split(",", 1)[-1])
                else:
                    images.append(url)
    return "\n".join(text_parts), images


def _msg_to_ollama(m: ChatMessage) -> dict[str, Any]:
    text, images = _split_content(m.content)
    d: dict[str, Any] = {"role": m.role, "content": text}
    if images:
        d["images"] = images
    return d


class OllamaProvider(BaseProvider):
    kind = "ollama"

    def _options(self, req: ChatRequest) -> dict[str, Any]:
        opts: dict[str, Any] = {}
        p = req.params
        if p.get("temperature") is not None:
            opts["temperature"] = p["temperature"]
        if p.get("top_p") is not None:
            opts["top_p"] = p["top_p"]
        if p.get("max_tokens") is not None:
            opts["num_predict"] = p["max_tokens"]
        # provider advanced params (num_ctx, num_gpu, num_thread, repeat_penalty,
        # top_k, seed, num_predict, mirostat, ...) go into Ollama `options`.
        for k, v in self.advanced().items():
            if k == "keep_alive" or v in (None, ""):
                continue
            opts[k] = v
        return opts

    def _keep_alive(self) -> Any:
        ka = self.advanced().get("keep_alive")
        return ka if ka not in (None, "") else None

    async def chat(self, model: str, req: ChatRequest) -> ChatResult:
        body: dict[str, Any] = {
            "model": model,
            "messages": [_msg_to_ollama(m) for m in req.messages],
            "stream": False,
            "options": self._options(req),
        }
        if self._keep_alive() is not None:
            body["keep_alive"] = self._keep_alive()
        async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
            r = await client.post(f"{self.base_url}/api/chat", json=body)
            r.raise_for_status()
            data = r.json()
        text = (data.get("message") or {}).get("content") or ""
        return ChatResult(
            text=text,
            model=data.get("model", model),
            usage=Usage(
                prompt_tokens=int(data.get("prompt_eval_count", 0) or 0),
                completion_tokens=int(data.get("eval_count", 0) or 0),
            ),
            raw=data,
        )

    async def stream(self, model: str, req: ChatRequest,
                     usage_out: dict | None = None) -> StreamIterator:
        body: dict[str, Any] = {
            "model": model,
            "messages": [_msg_to_ollama(m) for m in req.messages],
            "stream": True,
            "options": self._options(req),
        }
        if self._keep_alive() is not None:
            body["keep_alive"] = self._keep_alive()
        async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
            async with client.stream("POST", f"{self.base_url}/api/chat", json=body) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    delta = (chunk.get("message") or {}).get("content")
                    if delta:
                        yield delta
                    if chunk.get("done"):
                        # Ollama's final chunk carries real token counts.
                        if usage_out is not None:
                            usage_out["prompt_tokens"] = int(chunk.get("prompt_eval_count", 0) or 0)
                            usage_out["completion_tokens"] = int(chunk.get("eval_count", 0) or 0)
                        break

    async def embed(self, model: str, inputs: list[str]):
        async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
            r = await client.post(f"{self.base_url}/api/embed",
                                  json={"model": model, "input": inputs})
            r.raise_for_status()
            data = r.json()
        vectors = data.get("embeddings") or []
        pt = int(data.get("prompt_eval_count", 0) or 0)
        return vectors, Usage(prompt_tokens=pt)

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
                r = await client.get(f"{self.base_url}/api/tags")
                return r.status_code < 500
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        try:
            async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
                r = await client.get(f"{self.base_url}/api/tags")
                r.raise_for_status()
                data = r.json()
            return [m.get("name", "") for m in (data.get("models") or []) if m.get("name")]
        except Exception:
            return []
