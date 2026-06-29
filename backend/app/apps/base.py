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
    # Optional human-readable declaration of this template's request/response
    # contract, surfaced read-only in the UI ("默认输入/输出格式"). The actual
    # output shape is decided by the prompt, so this is documentation, not config.
    # Shape: {"endpoints": [str, ...],
    #         "input":  {"example": <obj> | "fields": str, ...},
    #         "output": {"example": <obj> | "schema": str, "note": str}}
    io_format: dict[str, Any] | None = None
    # Per-stage prompt declaration for the task-flow editor: one entry per pipeline
    # stage this template runs, each with its complete default prompt so the UI can
    # show/edit every step (not just stage 0). Index matches stage_prompt(config, i).
    # Shape: [{"name": str, "default_prompt": str, "description": str?}]
    stages: list[dict[str, Any]] | None = None
    # Built-in endpoint handlers this template can serve (modular routing). Static
    # metadata for the route editor; actual mounting reads config.extra["routes"]
    # and falls back to these native paths. Shape: [{handler, method, path,
    # description, main}]. None = the frontend derives paths from io_format only.
    routes: list[dict[str, Any]] | None = None

    @abc.abstractmethod
    def build_app(self, config: PortConfig):
        """Return an ASGI application instance for this port."""
