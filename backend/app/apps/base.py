"""AppTemplate abstraction.

An AppTemplate knows how to build a standalone ASGI app (FastAPI) for a single
port service, given its runtime config. Every template exposes at least a
`/health` endpoint and an OpenAI-compatible `/v1/chat/completions` so internal
clients can talk to any port with the same protocol.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PortConfig:
    """Plain snapshot of a PortService, safe to pass across threads."""

    id: int
    name: str
    slug: str
    port: int
    app_type: str
    model_alias: str
    system_prompt: str = ""
    streaming: bool = True
    concurrency: int = 8
    timeout: float = 120.0
    max_retries: int = 2
    logging_enabled: bool = True
    log_keep: int = 10
    auth_required: bool = False
    debug: bool = False  # verbose logging: store full (untruncated) request/response + each task-flow stage
    path_alias: str = ""  # optional extra URL path the main endpoint is also served at (e.g. "/myapi")
    extra: dict[str, Any] = field(default_factory=dict)


class AppTemplate(abc.ABC):
    app_type: str = "base"
    title: str = "Base App"
    description: str = ""
    # Suggested system prompt the frontend pre-fills when this template is picked
    # (only if the user hasn't typed one). Empty = no suggestion.
    default_prompt: str = ""

    @abc.abstractmethod
    def build_app(self, config: PortConfig):
        """Return an ASGI application instance for this port."""
