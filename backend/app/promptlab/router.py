"""Prompt reverse-inference API.

Given example input→output pairs (+ optional requirements and preset
constraints), infer a system prompt via the unified model layer. Also lets the
user test a candidate prompt against sample inputs to see if it reproduces the
desired outputs.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.db.session import get_db
from app.models_layer.router import AliasNotFound, ModelRouter, resolve_alias
from app.models_layer.types import ChatMessage, ChatRequest
from app.promptlab.meta import MAX_EXAMPLES, MAX_TOTAL_CHARS, build_messages
from app.promptlab.presets import PRESET_CATALOG, render_constraints

router = APIRouter(prefix="/api/promptlab", tags=["promptlab"])


class Example(BaseModel):
    input: str = ""
    output: str = ""
    images: list[str] = []  # data URLs (for vision-task prompt inference)


class InferIn(BaseModel):
    alias: str
    examples: list[Example] = []
    requirements: str = ""
    presets: dict = {}


class TestInput(BaseModel):
    text: str = ""
    images: list[str] = []  # data URLs (for testing a vision prompt)


class TestIn(BaseModel):
    alias: str
    system_prompt: str
    inputs: list[TestInput] = []

    @field_validator("inputs", mode="before")
    @classmethod
    def _coerce_inputs(cls, v):
        # Backward compatible: accept a plain list[str] as well as [{text, images}].
        if isinstance(v, list):
            return [{"text": x} if isinstance(x, str) else x for x in v]
        return v


def _user_content(text: str, images: list[str]):
    """Plain string for text-only inputs; OpenAI-style multimodal parts when the
    input carries image(s), so vision prompts can be tested too."""
    if not images:
        return text
    parts: list[dict] = []
    if text.strip():
        parts.append({"type": "text", "text": text})
    for url in images:
        parts.append({"type": "image_url", "image_url": {"url": url}})
    return parts


def _check_budget(examples: list[Example]) -> None:
    if not examples:
        raise HTTPException(400, "at least one input/output example is required")
    valid = [e for e in examples if (e.input.strip() or e.output.strip() or e.images)]
    if not valid:
        raise HTTPException(400, "examples must contain input/output text or an image")
    if len(valid) > MAX_EXAMPLES:
        raise HTTPException(400, f"too many examples (max {MAX_EXAMPLES})")
    total = sum(len(e.input) + len(e.output) for e in valid)
    if total > MAX_TOTAL_CHARS:
        raise HTTPException(400, f"examples too long (max {MAX_TOTAL_CHARS} chars)")


def _resolve(db: Session, alias: str):
    try:
        return resolve_alias(db, alias)
    except AliasNotFound as e:
        raise HTTPException(400, str(e))


@router.get("/presets")
def list_presets(_: object = Depends(get_current_user)):
    return PRESET_CATALOG


@router.post("/infer")
async def infer(body: InferIn, db: Session = Depends(get_db),
                _: object = Depends(get_current_user)):
    _check_budget(body.examples)
    constraints = render_constraints(body.presets or {})
    examples = [e.model_dump() for e in body.examples
                if (e.input.strip() or e.output.strip() or e.images)]
    msgs = build_messages(examples, body.requirements, constraints)
    resolved = _resolve(db, body.alias)
    mr = ModelRouter()
    req = ChatRequest(messages=msgs, params={"temperature": 0.4}, stream=False)
    try:
        result = await mr.chat(resolved, req)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"inference failed: {e}")
    return {"system_prompt": (result.text or "").strip(), "model": result.model,
            "constraints": constraints}


@router.post("/test")
async def test_prompt(body: TestIn, db: Session = Depends(get_db),
                      _: object = Depends(get_current_user)):
    if not body.system_prompt.strip():
        raise HTTPException(400, "system_prompt is required")
    valid = [i for i in body.inputs if (i.text.strip() or i.images)]
    if not valid:
        raise HTTPException(400, "at least one input (text or image) is required")
    resolved = _resolve(db, body.alias)
    mr = ModelRouter()
    outputs: list[str] = []
    for inp in body.inputs:
        msgs = [ChatMessage(role="system", content=body.system_prompt),
                ChatMessage(role="user", content=_user_content(inp.text, inp.images))]
        req = ChatRequest(messages=msgs, params={}, stream=False)
        try:
            r = await mr.chat(resolved, req)
            outputs.append((r.text or "").strip())
        except Exception as e:  # noqa: BLE001
            outputs.append(f"[error] {e}")
    return {"outputs": outputs}
