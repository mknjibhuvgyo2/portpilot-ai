"""Offline unit tests for the unified model layer."""
import asyncio

from app.models_layer.providers.ollama import _split_content
from app.models_layer.router import ModelRouter, ResolvedAlias, ResolvedTarget
from app.models_layer.types import ChatMessage, ChatRequest


def test_split_content_text_only():
    text, imgs = _split_content("hello")
    assert text == "hello" and imgs == []


def test_split_content_with_image():
    content = [
        {"type": "text", "text": "describe"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,ABC123"}},
    ]
    text, imgs = _split_content(content)
    assert text == "describe"
    assert imgs == ["ABC123"]  # base64 payload extracted from data URL


def test_chat_falls_back_to_text_when_all_targets_fail():
    # Target points to an unused local port -> connection refused fast.
    resolved = ResolvedAlias(
        alias="x",
        targets=[ResolvedTarget(kind="openai_compat", base_url="http://127.0.0.1:5",
                                api_key="", model="m", label="dead/m")],
        fallback_text="[兜底输出]",
    )
    router = ModelRouter(timeout=2, max_retries=0)
    req = ChatRequest(messages=[ChatMessage(role="user", content="hi")])
    result = asyncio.run(router.chat(resolved, req))
    assert result.text == "[兜底输出]"
    assert result.model == "fallback"


def test_stream_falls_back_to_text_when_all_targets_fail():
    resolved = ResolvedAlias(
        alias="x",
        targets=[ResolvedTarget(kind="openai_compat", base_url="http://127.0.0.1:5",
                                api_key="", model="m", label="dead/m")],
        fallback_text="[兜底输出]",
    )
    router = ModelRouter(timeout=2, max_retries=0)
    req = ChatRequest(messages=[ChatMessage(role="user", content="hi")], stream=True)

    async def collect():
        return [c async for c in router.stream(resolved, req)]

    chunks = asyncio.run(collect())
    assert "".join(chunks) == "[兜底输出]"
