"""Native Anthropic (Claude) provider — POST /v1/messages.

Anthropic's API differs from OpenAI: the system prompt is a top-level `system`
field (not a message), `max_tokens` is required, auth is `x-api-key`, and the
response/stream shapes differ. Text-only here (image blocks can be added later).
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from app.models_layer.providers.base import BaseProvider
from app.models_layer.types import ChatMessage, ChatRequest, ChatResult, StreamIterator, Usage

ANTHROPIC_VERSION = "2023-06-01"


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(p.get("text", "")) for p in content
            if isinstance(p, dict) and p.get("type") == "text")
    return str(content or "")


def _img_block(url: str) -> dict:
    """OpenAI image_url → Anthropic image block (base64 data URL or remote URL)."""
    if url.startswith("data:"):
        head, _, data = url.partition(",")
        media = head[5:].split(";")[0] or "image/png"  # data:image/png;base64,...
        return {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}}
    return {"type": "image", "source": {"type": "url", "url": url}}


def _blocks(content: Any):
    """Anthropic message content: keep plain strings, else build text/image blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out: list[dict] = []
        for p in content:
            if not isinstance(p, dict):
                continue
            if p.get("type") == "text":
                out.append({"type": "text", "text": p.get("text", "")})
            elif p.get("type") == "image_url":
                url = (p.get("image_url") or {}).get("url", "")
                if url:
                    out.append(_img_block(url))
        return out or ""
    return str(content or "")


def _split(messages: list[ChatMessage]) -> tuple[str, list[dict]]:
    system_parts: list[str] = []
    msgs: list[dict] = []
    for m in messages:
        if m.role == "system":
            system_parts.append(_text_of(m.content))  # system is plain text
        else:
            role = "assistant" if m.role == "assistant" else "user"
            msgs.append({"role": role, "content": _blocks(m.content)})
    return "\n".join(p for p in system_parts if p), msgs


class AnthropicProvider(BaseProvider):
    kind = "anthropic"

    def _base(self) -> str:
        b = self.base_url
        return b[:-3] if b.endswith("/v1") else b  # we add /v1 ourselves

    def _headers(self) -> dict[str, str]:
        h = {
            "content-type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        }
        h.update(self.extra.get("headers") or {})
        return h

    def _body(self, model: str, req: ChatRequest, stream: bool) -> dict[str, Any]:
        system, msgs = _split(req.messages)
        p = req.params
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": int(p.get("max_tokens") or 1024),
            "messages": msgs,
            "stream": stream,
        }
        if system:
            body["system"] = system
        if p.get("temperature") is not None:
            body["temperature"] = p["temperature"]
        if p.get("top_p") is not None:
            body["top_p"] = p["top_p"]
        for k, v in self.advanced().items():
            if k not in body and v not in (None, ""):
                body[k] = v
        return body

    async def chat(self, model: str, req: ChatRequest) -> ChatResult:
        async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
            r = await client.post(f"{self._base()}/v1/messages",
                                  headers=self._headers(), json=self._body(model, req, False))
            r.raise_for_status()
            data = r.json()
        text = "".join(b.get("text", "") for b in (data.get("content") or [])
                       if b.get("type") == "text")
        u = data.get("usage") or {}
        return ChatResult(text=text, model=data.get("model", model),
                          usage=Usage(prompt_tokens=int(u.get("input_tokens", 0) or 0),
                                      completion_tokens=int(u.get("output_tokens", 0) or 0)),
                          raw=data)

    async def stream(self, model: str, req: ChatRequest,
                     usage_out: dict | None = None) -> StreamIterator:
        pt = ct = 0
        async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
            async with client.stream("POST", f"{self._base()}/v1/messages",
                                     headers=self._headers(),
                                     json=self._body(model, req, True)) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[len("data:"):].strip()
                    if not payload:
                        continue
                    try:
                        ev = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    t = ev.get("type")
                    if t == "content_block_delta":
                        delta = (ev.get("delta") or {}).get("text")
                        if delta:
                            yield delta
                    elif t == "message_start":
                        pt = int(((ev.get("message") or {}).get("usage") or {}).get(
                            "input_tokens", 0) or 0)
                    elif t == "message_delta":
                        ct = int((ev.get("usage") or {}).get("output_tokens", ct) or ct)
        if usage_out is not None and (pt or ct):
            usage_out["prompt_tokens"] = pt
            usage_out["completion_tokens"] = ct

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
                r = await client.get(f"{self._base()}/v1/models", headers=self._headers())
                return r.status_code < 500
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        try:
            async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
                r = await client.get(f"{self._base()}/v1/models", headers=self._headers())
                r.raise_for_status()
                data = r.json()
            return [m.get("id", "") for m in (data.get("data") or []) if m.get("id")]
        except Exception:
            return []
