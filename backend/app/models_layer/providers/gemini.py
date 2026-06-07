"""Native Google Gemini provider — generateContent / streamGenerateContent.

Gemini's API differs from OpenAI: messages are `contents` with `parts`, the
assistant role is `model`, the system prompt is `systemInstruction`, sampling
goes in `generationConfig`, and auth is the `x-goog-api-key` header. Text-only.
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from app.models_layer.providers.base import BaseProvider
from app.models_layer.types import ChatMessage, ChatRequest, ChatResult, StreamIterator, Usage

API_PATH = "/v1beta"


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(p.get("text", "")) for p in content
            if isinstance(p, dict) and p.get("type") == "text")
    return str(content or "")


def _inline_data(url: str, default_mime: str) -> dict | None:
    """OpenAI-style data URL → Gemini inlineData (base64 only). Used for both
    image_url and video_url parts."""
    if url.startswith("data:"):
        head, _, data = url.partition(",")
        mime = head[5:].split(";")[0] or default_mime
        return {"inlineData": {"mimeType": mime, "data": data}}
    return None  # remote URLs need the Files API; skip inline


def _parts(content: Any) -> list[dict]:
    if isinstance(content, str):
        return [{"text": content}]
    if isinstance(content, list):
        out: list[dict] = []
        for p in content:
            if not isinstance(p, dict):
                continue
            if p.get("type") == "text":
                out.append({"text": p.get("text", "")})
            elif p.get("type") == "image_url":
                part = _inline_data((p.get("image_url") or {}).get("url", ""), "image/png")
                if part:
                    out.append(part)
            elif p.get("type") == "video_url":
                part = _inline_data((p.get("video_url") or {}).get("url", ""), "video/mp4")
                if part:
                    out.append(part)
        return out or [{"text": ""}]
    return [{"text": str(content or "")}]


def _split(messages: list[ChatMessage]) -> tuple[str, list[dict]]:
    system_parts: list[str] = []
    contents: list[dict] = []
    for m in messages:
        if m.role == "system":
            system_parts.append(_text_of(m.content))
        else:
            role = "model" if m.role == "assistant" else "user"
            contents.append({"role": role, "parts": _parts(m.content)})
    return "\n".join(p for p in system_parts if p), contents


class GeminiProvider(BaseProvider):
    kind = "gemini"

    def _base(self) -> str:
        b = self.base_url
        return b[: -len(API_PATH)] if b.endswith(API_PATH) else b

    def _headers(self) -> dict[str, str]:
        h = {"content-type": "application/json", "x-goog-api-key": self.api_key}
        h.update(self.extra.get("headers") or {})
        return h

    def _body(self, req: ChatRequest) -> dict[str, Any]:
        system, contents = _split(req.messages)
        p = req.params
        cfg: dict[str, Any] = {}
        if p.get("temperature") is not None:
            cfg["temperature"] = p["temperature"]
        if p.get("top_p") is not None:
            cfg["topP"] = p["top_p"]
        if p.get("max_tokens") is not None:
            cfg["maxOutputTokens"] = p["max_tokens"]
        body: dict[str, Any] = {"contents": contents}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if cfg:
            body["generationConfig"] = cfg
        return body

    def _extract(self, data: dict) -> str:
        cands = data.get("candidates") or []
        if not cands:
            return ""
        parts = ((cands[0].get("content") or {}).get("parts") or [])
        return "".join(str(p.get("text", "")) for p in parts if "text" in p)

    async def chat(self, model: str, req: ChatRequest) -> ChatResult:
        url = f"{self._base()}{API_PATH}/models/{model}:generateContent"
        async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
            r = await client.post(url, headers=self._headers(), json=self._body(req))
            r.raise_for_status()
            data = r.json()
        um = data.get("usageMetadata") or {}
        return ChatResult(text=self._extract(data), model=model,
                          usage=Usage(prompt_tokens=int(um.get("promptTokenCount", 0) or 0),
                                      completion_tokens=int(um.get("candidatesTokenCount", 0) or 0)),
                          raw=data)

    async def stream(self, model: str, req: ChatRequest,
                     usage_out: dict | None = None) -> StreamIterator:
        url = f"{self._base()}{API_PATH}/models/{model}:streamGenerateContent?alt=sse"
        pt = ct = 0
        async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
            async with client.stream("POST", url, headers=self._headers(),
                                     json=self._body(req)) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[len("data:"):].strip()
                    if not payload:
                        continue
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    delta = self._extract(chunk)
                    if delta:
                        yield delta
                    um = chunk.get("usageMetadata") or {}
                    if um:
                        pt = int(um.get("promptTokenCount", pt) or pt)
                        ct = int(um.get("candidatesTokenCount", ct) or ct)
        if usage_out is not None and (pt or ct):
            usage_out["prompt_tokens"] = pt
            usage_out["completion_tokens"] = ct

    async def list_models(self) -> list[str]:
        try:
            async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
                r = await client.get(f"{self._base()}{API_PATH}/models", headers=self._headers())
                r.raise_for_status()
                data = r.json()
            out = []
            for m in (data.get("models") or []):
                name = (m.get("name") or "").split("/")[-1]
                if name:
                    out.append(name)
            return out
        except Exception:
            return []

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
                r = await client.get(f"{self._base()}{API_PATH}/models", headers=self._headers())
                return r.status_code < 500
        except Exception:
            return False
