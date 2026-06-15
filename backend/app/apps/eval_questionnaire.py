"""Ported VT questionnaire generator (was D:\\aiwenjuan, Node.js, port 18082).

Generates a Chinese test questionnaire (JSON) from {project, materials,
indicators}. The original orchestrated LM Studio via its CLI (load/eject model,
unload Ollama) for GPU management — PORTHUB replaces that with normal model-alias
routing, so all that machinery is dropped; only the prompt + normalization logic
is ported. The model call goes through the unified router.

External contract preserved:
- GET  /api/health
- POST /api/vt/generate-questionnaire   {project, materials, indicators}
                                        -> {success, requestId, questionnaire}
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app.apps.base import AppTemplate, PortConfig
from app.apps.eval_common import call_model

SYSTEM_PROMPT = "你是资深问卷研究员。/no_think 只输出严格JSON，不要Markdown，不要解释，不要输出思考过程。"

DEFAULT_LIKERT = ["非常不同意", "不同意", "一般", "同意", "非常同意"]


def _num(v: Any):
    try:
        f = float(v)
        return int(f) if f.is_integer() else f
    except Exception:
        return None


def normalize_type(t: Any) -> str:
    v = str(t or "").strip().lower()
    if v in ("radio", "single"):
        return "radio"
    if v in ("checkbox", "multiple"):
        return "checkbox"
    if v in ("text", "open"):
        return "text"
    return "scale"


_PUNCT_RE = re.compile(r"""[?？，。!！?；;：:"'“”‘’（）()\[\]、,.]""")


def normalize_question_key(q: dict) -> str:
    s = str((q or {}).get("questionText") or (q or {}).get("title") or "")
    s = re.sub(r"\s+", "", s)
    s = _PUNCT_RE.sub("", s)
    return s.strip().lower()


def strip_model_noise(text: Any) -> str:
    s = str(text or "")
    s = re.sub(r"<think>[\s\S]*?</think>", "", s, flags=re.I)
    s = s.replace("```json", "").replace("```JSON", "").replace("```", "")
    return s.strip()


def extract_json(text: str) -> dict:
    cleaned = strip_model_noise(text)
    try:
        return json.loads(cleaned)
    except Exception:
        i, j = cleaned.find("{"), cleaned.rfind("}")
        if i >= 0 and j > i:
            return json.loads(cleaned[i:j + 1])
        raise ValueError("model did not return valid JSON")


def normalize_questionnaire(raw: dict, input_data: dict) -> dict:
    indicators = input_data.get("indicators") if isinstance(input_data.get("indicators"), list) else []
    materials = input_data.get("materials") if isinstance(input_data.get("materials"), list) else []
    material_ids = [m for m in (_num(x.get("id")) for x in materials if isinstance(x, dict)) if m is not None]
    raw_questions = raw.get("questions") if isinstance(raw.get("questions"), list) else []
    questions: list[dict] = []

    for indicator in indicators:
        if not isinstance(indicator, dict):
            continue
        indicator_id = _num(indicator.get("id"))
        seen: set[str] = set()
        matched: list[dict] = []
        for q in raw_questions:
            if not isinstance(q, dict) or _num(q.get("indicatorId")) != indicator_id:
                continue
            key = normalize_question_key(q)
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            matched.append(q)
            if len(matched) >= 2:
                break

        if matched:
            source_questions = matched
        else:
            source_questions = [{
                "indicatorId": indicator_id,
                "indicatorName": indicator.get("name", ""),
                "questionText": f"请根据测试素材评价“{indicator.get('name') or '该指标'}”的表现。",
                "questionType": "scale", "score": 10, "materialIds": material_ids, "options": [],
            }]

        for q in source_questions:
            sel = []
            if isinstance(q.get("materialIds"), list):
                sel = [mid for mid in (_num(x) for x in q["materialIds"]) if mid in material_ids]
            qtype = normalize_type(q.get("questionType") or q.get("type"))
            options: list = q.get("options") if isinstance(q.get("options"), list) else []
            if qtype in ("radio", "checkbox"):
                norm_opts = []
                for idx, opt in enumerate(options):
                    if isinstance(opt, dict):
                        label = str(opt.get("label") or opt.get("text") or "").strip()
                        value = str(opt.get("value") or idx + 1)
                    else:
                        label = str(opt or "").strip()
                        value = str(idx + 1)
                    if label:
                        norm_opts.append({"value": value, "label": label})
                norm_opts = norm_opts[:6]
                if len(norm_opts) < 2:
                    norm_opts = [{"value": str(i + 1), "label": l} for i, l in enumerate(DEFAULT_LIKERT)]
                options = norm_opts
            else:
                options = []
            questions.append({
                "indicatorId": indicator_id,
                "indicatorName": indicator.get("name") or q.get("indicatorName") or "",
                "questionText": str(q.get("questionText") or q.get("title")
                                    or f"请根据测试素材评价“{indicator.get('name') or '该指标'}”的表现。").strip(),
                "questionType": qtype,
                "score": _num(q.get("score")) or 10,
                "materialIds": sel if sel else material_ids,
                "options": options,
            })

    project = input_data.get("project") or {}
    return {
        "title": str(raw.get("title") or f"{project.get('name') or '项目'}的问卷")[:100],
        "description": str(raw.get("description") or "请根据看到的测试素材，按真实感受完成以下问题。")[:500],
        "questions": questions,
    }


def build_prompt(input_data: dict) -> str:
    project = input_data.get("project") or {}
    materials = input_data.get("materials") if isinstance(input_data.get("materials"), list) else []
    indicators = input_data.get("indicators") if isinstance(input_data.get("indicators"), list) else []
    mat_lines = "\n".join(
        f"- ID:{m.get('id')} 名称:{m.get('name', '')} 类型:{m.get('type', '')} 说明:{m.get('explanation', '')}"
        for m in materials if isinstance(m, dict))
    ind_lines = "\n".join(
        f"- ID:{i.get('id')} 名称:{i.get('name', '')}" for i in indicators if isinstance(i, dict))
    return f"""请基于下面的项目、素材和测试指标，生成一份中文测试问卷JSON。

项目名称：{project.get('name', '')}
项目类型：{project.get('type', '')}
行业：{project.get('industryName', '')}
需求：{project.get('requirement', '')}
特殊要求：{project.get('specialRequirements', '')}

素材列表：
{mat_lines}

测试指标列表：
{ind_lines}

输出JSON结构必须为：
{{
  "title": "问卷标题",
  "description": "给被访者看的简短说明",
  "questions": [
    {{
      "indicatorId": 123,
      "indicatorName": "指标名称",
      "questionText": "题目文本",
      "questionType": "scale|radio|checkbox|text",
      "score": 10,
      "materialIds": [1,2],
      "options": [{{"value": "1", "label": "选项文本"}}]
    }}
  ]
}}

硬性要求：
1. 每个测试指标生成1到2道题。
2. 每道题必须绑定至少一个素材ID。可以根据题意绑定全部素材，也可以绑定单个素材。
3. 默认优先使用scale量表题，score为10；需要选择题时用radio或checkbox，并给出2到6个选项。
4. 不要生成姓名、电话、身份证等个人敏感信息题。
5. indicatorId必须来自测试指标列表，materialIds必须来自素材列表。
6. 只输出JSON。"""


def validate_input(input_data: Any) -> None:
    if not isinstance(input_data, dict):
        raise HTTPException(400, "Request body is required.")
    if not input_data.get("project"):
        raise HTTPException(400, "project is required.")
    if not isinstance(input_data.get("materials"), list) or not input_data["materials"]:
        raise HTTPException(400, "materials must not be empty.")
    if not isinstance(input_data.get("indicators"), list) or not input_data["indicators"]:
        raise HTTPException(400, "indicators must not be empty.")


def _request_id() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:8]


def build_questionnaire_app(config: PortConfig) -> FastAPI:
    app = FastAPI(title=f"{config.name} (questionnaire_eval)")

    @app.get("/api/health")
    async def health():
        return {"ok": True, "service": "vt-questionnaire-service",
                "slug": config.slug, "model_alias": config.model_alias}

    @app.post("/api/vt/generate-questionnaire")
    async def generate(request: Request):
        input_data = await request.json()
        validate_input(input_data)
        rid = _request_id()
        system = config.system_prompt.strip() if config.system_prompt and config.system_prompt.strip() else SYSTEM_PROMPT
        try:
            text = await call_model(config, system, "/no_think\n" + build_prompt(input_data),
                                    params={"temperature": 0.25, "max_tokens": 8192},
                                    request_excerpt=f"project={input_data.get('project', {}).get('id', '')}")
            raw = extract_json(text)
        except Exception:
            raw = {"title": "", "description": "", "questions": []}
        questionnaire = normalize_questionnaire(raw, input_data)
        return JSONResponse({"success": True, "requestId": rid, "questionnaire": questionnaire},
                            media_type="application/json; charset=utf-8")

    return app


class QuestionnaireEvalTemplate(AppTemplate):
    app_type = "questionnaire_eval"
    title = "问卷生成 / Questionnaire Gen"
    description = ("VT 问卷生成：根据项目+素材+测试指标，自动生成中文测试问卷 JSON（每指标1-2题，"
                  "量表/单选/多选/开放题，自动绑定素材）。POST /api/vt/generate-questionnaire。")
    default_prompt = SYSTEM_PROMPT

    def build_app(self, config: PortConfig):
        return build_questionnaire_app(config)
