"""Shared types for the unified model layer."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator


@dataclass
class ChatMessage:
    role: str  # system | user | assistant
    content: Any  # str, or OpenAI-style list of content parts (text/image_url)


@dataclass
class ChatRequest:
    messages: list[ChatMessage]
    params: dict[str, Any] = field(default_factory=dict)  # temperature, top_p, max_tokens...
    stream: bool = False


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class ChatResult:
    text: str
    model: str
    usage: Usage = field(default_factory=Usage)
    raw: dict[str, Any] | None = None


# A streaming provider yields text deltas (str chunks).
StreamIterator = AsyncIterator[str]
