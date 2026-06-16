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


def list_templates() -> list[dict]:
    return [
        {"app_type": t.app_type, "title": t.title, "description": t.description,
         "default_prompt": t.default_prompt}
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
