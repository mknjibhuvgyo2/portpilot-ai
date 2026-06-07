"""Server-side prompt library.

Lets the UI save/load reusable system prompts as files (.txt or .json) under
data/prompts/. JSON files may either be a raw string or an object with a
"system_prompt"/"content"/"prompt" field, from which the text is extracted.
"""
from __future__ import annotations

import json
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth.deps import get_current_user, require_admin
from app.core.config import DATA_DIR

router = APIRouter(prefix="/api/prompts", tags=["prompts"])

PROMPTS_DIR = DATA_DIR / "prompts"
PROMPTS_DIR.mkdir(parents=True, exist_ok=True)

_NAME_RE = re.compile(r"^[\w\-. ]{1,80}$")


def _safe_name(name: str, fmt: str) -> str:
    base = name.strip()
    if base.lower().endswith((".txt", ".json")):
        base = base.rsplit(".", 1)[0]
    if not _NAME_RE.match(base):
        raise HTTPException(400, "invalid name")
    ext = "json" if fmt == "json" else "txt"
    return f"{base}.{ext}"


def _extract_text(raw: str, fmt: str) -> str:
    if fmt == "json":
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return raw
        if isinstance(data, str):
            return data
        if isinstance(data, dict):
            for k in ("system_prompt", "content", "prompt", "text"):
                if isinstance(data.get(k), str):
                    return data[k]
        return raw
    return raw


class PromptSave(BaseModel):
    name: str
    content: str
    format: str = "txt"  # txt | json


@router.get("")
def list_prompts(_: object = Depends(get_current_user)):
    out = []
    for p in sorted(PROMPTS_DIR.glob("*")):
        if p.suffix.lower() in (".txt", ".json"):
            out.append({"name": p.name, "format": p.suffix.lower().lstrip(".")})
    return out


@router.get("/{name}")
def read_prompt(name: str, _: object = Depends(get_current_user)):
    p = PROMPTS_DIR / name
    if not p.exists() or p.suffix.lower() not in (".txt", ".json"):
        raise HTTPException(404, "not found")
    raw = p.read_text(encoding="utf-8")
    fmt = p.suffix.lower().lstrip(".")
    return {"name": name, "format": fmt, "content": _extract_text(raw, fmt), "raw": raw}


@router.post("")
def save_prompt(body: PromptSave, _: object = Depends(require_admin)):
    fname = _safe_name(body.name, body.format)
    p = PROMPTS_DIR / fname
    if body.format == "json":
        payload = json.dumps({"system_prompt": body.content}, ensure_ascii=False, indent=2)
    else:
        payload = body.content
    p.write_text(payload, encoding="utf-8")
    return {"name": fname, "format": body.format}


@router.delete("/{name}")
def delete_prompt(name: str, _: object = Depends(require_admin)):
    p = PROMPTS_DIR / name
    if not p.exists():
        raise HTTPException(404, "not found")
    p.unlink()
    return {"ok": True}
