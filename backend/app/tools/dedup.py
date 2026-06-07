"""Smart output de-duplication.

Splits a model's output into items and drops duplicates, preserving order:
  - If the text parses as a JSON array, items are compared structurally
    (objects/arrays by canonical JSON with sorted keys; scalars by value).
  - Otherwise it's treated as a line/Markdown list: each line's bullet/number
    prefix is stripped for comparison, so "- foo", "1. foo", "foo" dedupe
    together. Blank lines are preserved.
Comparison is case-insensitive and whitespace-normalized for the line mode.
"""
from __future__ import annotations

import json
import re

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth.deps import get_current_user

router = APIRouter(prefix="/api/tools", tags=["tools"])

_PREFIX_RE = re.compile(r"^(\s*(?:[-*+]|\d+[.)])\s+)(.*)$")


def _dedup_json_array(data: list) -> tuple[list, int]:
    seen: set[str] = set()
    out: list = []
    for item in data:
        if isinstance(item, (dict, list)):
            key = json.dumps(item, sort_keys=True, ensure_ascii=False)
        else:
            key = f"{type(item).__name__}:{item!r}"
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out, len(data)


def dedup_text(text: str) -> dict:
    s = (text or "").strip()
    # 1) JSON array
    if s.startswith("["):
        try:
            data = json.loads(s)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, list):
            out, original = _dedup_json_array(data)
            return {
                "format": "json",
                "original_count": original,
                "deduped_count": len(out),
                "removed": original - len(out),
                "result": json.dumps(out, ensure_ascii=False, indent=2),
            }

    # 2) line / Markdown list
    seen: set[str] = set()
    out_lines: list[str] = []
    original = 0
    for ln in (text or "").split("\n"):
        if not ln.strip():
            out_lines.append(ln)  # keep blank lines / spacing
            continue
        original += 1
        m = _PREFIX_RE.match(ln)
        content = (m.group(2) if m else ln).strip()
        key = re.sub(r"\s+", " ", content).lower()
        if key in seen:
            continue
        seen.add(key)
        out_lines.append(ln)
    kept = sum(1 for ln in out_lines if ln.strip())
    return {
        "format": "lines",
        "original_count": original,
        "deduped_count": kept,
        "removed": original - kept,
        "result": "\n".join(out_lines).strip("\n"),
    }


class DedupIn(BaseModel):
    text: str


@router.post("/dedup")
def dedup(body: DedupIn, _: object = Depends(get_current_user)):
    return dedup_text(body.text)
