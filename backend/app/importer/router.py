"""Import wizard: turn an existing AI-service codebase into PORTHUB port configs.

The user pastes their existing service's source (Python / Node / anything). An
LLM reads it, extracts the system/role prompts, the model(s) used and the
pipeline stages, and emits a JSON describing one or more PORTHUB port services
(with task flows). The user can review/edit, then one-click apply — empty fields
are auto-filled (slug from name, next free port, default app_type / alias).
"""
from __future__ import annotations

import json
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.apps.registry import list_templates
from app.auth.deps import get_current_user, require_admin
from app.db.models import ModelAlias, PortService, PortStatus
from app.db.session import get_db
from app.models_layer.router import AliasNotFound, ModelRouter, resolve_alias
from app.models_layer.types import ChatMessage, ChatRequest
from app.ports.router import _apply_tasks

router = APIRouter(prefix="/api/importer", tags=["importer"])

MAX_CODE_CHARS = 60000

STRATEGY = (
    "把你已有 AI 服务的核心源码贴进来（如 main.py / server.py / server.js，或多个文件拼在一起）。"
    "AI 会读出其中的系统/角色提示词、调用的模型、处理流程（几段、是否调用方传 model），"
    "生成 PORTHUB 端口服务配置。生成后可手动微调，再一键导入；留空的字段（slug/端口/模型）导入时自动补默认。"
)


def _extraction_prompt() -> str:
    types = "\n".join(f"- {t['app_type']}: {t['title']} — {t['description']}" for t in list_templates())
    return f"""你是 PORTHUB 的"服务迁移分析器"。用户会给你一段已有 AI 服务的源码或项目（任意语言）。
读懂它，提取其中的【系统/角色提示词】【调用的模型】【处理流程阶段】，生成可一键导入 PORTHUB 的端口服务配置 JSON。

PORTHUB 概念：
- 一个"端口服务"绑定一个应用模板(app_type)，监听一个端口对外提供接口。
- 每个端口有一条"任务流"：有序任务列表，每个任务=一次独立模型调用，可带自己的提示词，上一步输出喂下一步。
- 任务两种模式：fixed（固定用配置的别名）/ pool（调用方请求体的 model 字段选模型）。

可用 app_type（尽量匹配；匹配不上用 generic_chat 或 custom）：
{types}

只输出一个 JSON 对象（第一个字符必须是 {{），禁止 markdown、禁止解释。结构：
{{
  "ports": [
    {{
      "name": "中文服务名",
      "slug": "lowercase-slug",
      "port": 9001,
      "app_type": "...",
      "tasks": [
        {{"name": "阶段名", "alias": "", "prompt": "从源码提取的该阶段系统提示词", "mode": "fixed", "pool": []}}
      ]
    }}
  ]
}}

规则：
- 一个源码若同时开多个端口，拆成多个 ports。
- prompt 尽量原样提取源码里的系统/角色提示词；找不到就按用途写一句合适的中文系统提示词。
- 源码若是"调用方传 model"（读 payload['model'] / model_map 等），该任务 mode="pool"、pool 留空。
- 写死某个模型的用 mode="fixed"；alias 可留空（导入时自动选默认）。
- port 能从源码看出就填，看不出填 0（导入时自动分配空闲端口）。
- slug 用英文小写短横线；看不出就根据 name 起一个。"""


class AnalyzeIn(BaseModel):
    code: str
    model_alias: str = ""


def _extract_json(text: str) -> dict:
    s = (text or "").strip()
    s = re.sub(r"^\s*```(?:json)?\s*", "", s, flags=re.I)
    s = re.sub(r"\s*```\s*$", "", s)
    try:
        obj = json.loads(s)
    except Exception:
        i, j = s.find("{"), s.rfind("}")
        if i < 0 or j <= i:
            raise HTTPException(502, "模型未返回 JSON")
        obj = json.loads(s[i:j + 1])
    if not isinstance(obj, dict) or "ports" not in obj:
        raise HTTPException(502, "模型输出缺少 ports 字段")
    return obj


@router.get("/guide")
def guide(_: object = Depends(get_current_user)):
    return {"strategy": STRATEGY, "extraction_prompt": _extraction_prompt()}


@router.post("/analyze")
async def analyze(body: AnalyzeIn, db: Session = Depends(get_db), _: object = Depends(require_admin)):
    code = (body.code or "").strip()
    if not code:
        raise HTTPException(400, "请粘贴源码")
    alias = body.model_alias.strip()
    if not alias:
        first = db.query(ModelAlias).filter(ModelAlias.enabled.is_(True)).order_by(ModelAlias.id).first()
        if not first:
            raise HTTPException(400, "没有可用的模型路由，请先在「模型与厂商」新建一个")
        alias = first.alias
    try:
        resolved = resolve_alias(db, alias)
    except AliasNotFound as e:
        raise HTTPException(400, str(e))
    req = ChatRequest(
        messages=[ChatMessage(role="system", content=_extraction_prompt()),
                  ChatMessage(role="user", content=code[:MAX_CODE_CHARS])],
        params={"temperature": 0}, stream=False)
    try:
        result = await ModelRouter(timeout=180).chat(resolved, req)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"模型调用失败：{e}")
    return {"config": _extract_json(result.text), "model_used": result.model}


# ---------------------------------------------------------------------------
# Apply: create the port services, auto-filling empty fields
# ---------------------------------------------------------------------------
def _slugify(name: str, idx: int) -> str:
    s = re.sub(r"[^a-z0-9_-]+", "-", (name or "").strip().lower()).strip("-_")
    return s or f"imported-{idx}"


def _next_free_port(used: set[int], start: int = 9001) -> int:
    p = start
    while p in used or p in (8000,):
        p += 1
    return p


@router.post("/apply")
def apply(body: dict, db: Session = Depends(get_db), _: object = Depends(require_admin)):
    ports_spec = body.get("ports") if isinstance(body, dict) else None
    if not isinstance(ports_spec, list) or not ports_spec:
        raise HTTPException(400, "没有可导入的端口配置")

    valid_types = {t["app_type"] for t in list_templates()}
    default_alias_row = db.query(ModelAlias).filter(ModelAlias.enabled.is_(True)).order_by(ModelAlias.id).first()
    default_alias = default_alias_row.alias if default_alias_row else ""
    used_ports = {p.port for p in db.query(PortService).all()}
    used_slugs = {p.slug for p in db.query(PortService).all()}

    created, skipped = [], []
    for i, spec in enumerate(ports_spec, 1):
        if not isinstance(spec, dict):
            continue
        name = str(spec.get("name") or f"导入服务{i}").strip()
        slug = _slugify(str(spec.get("slug") or name), i)
        base_slug, n = slug, 2
        while slug in used_slugs:
            slug = f"{base_slug}-{n}"; n += 1
        try:
            port = int(spec.get("port") or 0)
        except Exception:
            port = 0
        if port <= 0 or port in used_ports:
            port = _next_free_port(used_ports)
        app_type = spec.get("app_type") if spec.get("app_type") in valid_types else "generic_chat"

        tasks = spec.get("tasks") if isinstance(spec.get("tasks"), list) else []
        norm_tasks = []
        for tk in tasks:
            if not isinstance(tk, dict):
                continue
            norm_tasks.append({
                "name": str(tk.get("name") or ""),
                "alias": str(tk.get("alias") or "").strip() or default_alias,
                "prompt": str(tk.get("prompt") or ""),
                "mode": "pool" if str(tk.get("mode") or "").lower() == "pool" else "fixed",
                "pool": [str(x) for x in tk.get("pool", []) if str(x).strip()],
            })
        if not norm_tasks:
            norm_tasks = [{"name": "", "alias": default_alias, "prompt": "", "mode": "fixed", "pool": []}]

        data = _apply_tasks({
            "name": name, "slug": slug, "port": port, "app_type": app_type,
            "model_alias": "", "system_prompt": "", "tasks": norm_tasks,
        })
        p = PortService(**data, status=PortStatus.stopped)
        db.add(p)
        db.commit()
        db.refresh(p)
        used_ports.add(port); used_slugs.add(slug)
        created.append({"id": p.id, "name": p.name, "slug": p.slug, "port": p.port, "app_type": p.app_type})

    return {"created": created, "skipped": skipped}
