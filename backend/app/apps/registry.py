"""Registry of available app templates."""
from __future__ import annotations

from app.apps.base import AppTemplate
from app.apps.embedding import EmbeddingTemplate
from app.apps.generic_chat import GenericChatTemplate
from app.apps.passthrough import PassthroughTemplate
from app.apps.rerank import RerankTemplate
from app.apps.templates import (
    CustomTemplate,
    ScoringTemplate,
    SummarizeTemplate,
    TranslateTemplate,
    VisionTemplate,
)

_TEMPLATES: dict[str, AppTemplate] = {}


def register(template: AppTemplate) -> None:
    _TEMPLATES[template.app_type] = template


def get_template(app_type: str) -> AppTemplate | None:
    return _TEMPLATES.get(app_type)


def _category(t: AppTemplate) -> str:
    c = getattr(t, "category", "generic")
    if c == "generic" and t.app_type.endswith("_eval"):
        return "eval"
    return c


def list_templates() -> list[dict]:
    return [
        {"app_type": t.app_type, "title": t.title, "description": t.description,
         "category": _category(t),
         "default_prompt": t.default_prompt, "io_format": t.io_format,
         # single-stage templates synthesize one stage from their default_prompt
         "stages": t.stages or [{"name": "系统提示词", "default_prompt": t.default_prompt or ""}],
         "routes": t.routes, "params_schema": t.params_schema}
        for t in _TEMPLATES.values()
    ]


register(GenericChatTemplate())
register(ScoringTemplate())
register(TranslateTemplate())
register(VisionTemplate())
register(SummarizeTemplate())
register(EmbeddingTemplate())
register(RerankTemplate())
register(PassthroughTemplate())
register(CustomTemplate())
