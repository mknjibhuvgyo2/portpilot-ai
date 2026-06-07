"""Preset constraint catalog for prompt reverse-inference.

The catalog is sent to the frontend (which renders i18n labels by id), while
`render_constraints` turns the user's selections into English constraint
sentences that get injected into the meta-prompt. Keeping the rendered text in
English (regardless of UI language) makes the meta-prompt behave consistently;
the *output* language is itself controlled by the `language` constraint.
"""
from __future__ import annotations

# Catalog shape consumed by the frontend. `single` = one choice; `text` = free input.
PRESET_CATALOG: list[dict] = [
    {"id": "language", "type": "single",
     "options": [{"id": "zh"}, {"id": "en"}, {"id": "ja"}, {"id": "same"}]},
    {"id": "tone", "type": "single",
     "options": [{"id": "formal"}, {"id": "casual"}, {"id": "professional"}, {"id": "friendly"}]},
    {"id": "format", "type": "single",
     "options": [{"id": "plain"}, {"id": "markdown"}, {"id": "json"}, {"id": "bullets"}]},
    {"id": "length", "type": "single",
     "options": [{"id": "concise"}, {"id": "detailed"}, {"id": "under_100"}, {"id": "under_500"}]},
    {"id": "persona", "type": "text"},
    {"id": "must_include", "type": "text"},
    {"id": "avoid", "type": "text"},
]

# Map (category, option) -> English constraint sentence.
_SINGLE_MAP: dict[tuple[str, str], str] = {
    ("language", "zh"): "The assistant must always respond in Chinese (简体中文).",
    ("language", "en"): "The assistant must always respond in English.",
    ("language", "ja"): "The assistant must always respond in Japanese (日本語).",
    ("language", "same"): "The assistant must respond in the same language as the user's input.",
    ("tone", "formal"): "Use a formal, polite tone.",
    ("tone", "casual"): "Use a casual, conversational tone.",
    ("tone", "professional"): "Use a professional, business-appropriate tone.",
    ("tone", "friendly"): "Use a warm, friendly tone.",
    ("format", "plain"): "Output plain text only.",
    ("format", "markdown"): "Format the output using Markdown.",
    ("format", "json"): "Output MUST be a single valid JSON object and nothing else.",
    ("format", "bullets"): "Present the output as a concise bulleted list.",
    ("length", "concise"): "Keep responses concise and to the point.",
    ("length", "detailed"): "Provide thorough, detailed responses.",
    ("length", "under_100"): "Keep each response under about 100 words.",
    ("length", "under_500"): "Keep each response under about 500 words.",
}

_TEXT_TEMPLATES: dict[str, str] = {
    "persona": "Adopt this role / persona: {v}",
    "must_include": "Every output must include: {v}",
    "avoid": "Never do / avoid the following: {v}",
}

_VALID_IDS = {p["id"] for p in PRESET_CATALOG}


def render_constraints(selected: dict) -> list[str]:
    """Turn a {category: value} selection dict into constraint sentences."""
    out: list[str] = []
    if not isinstance(selected, dict):
        return out
    for cat, val in selected.items():
        if cat not in _VALID_IDS or val in (None, "", []):
            continue
        if cat in _TEXT_TEMPLATES:
            text = str(val).strip()
            if text:
                out.append(_TEXT_TEMPLATES[cat].format(v=text))
        else:
            sentence = _SINGLE_MAP.get((cat, str(val)))
            if sentence:
                out.append(sentence)
    return out
