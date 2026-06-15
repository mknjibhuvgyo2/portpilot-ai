"""Ported two-stage indicator matcher (was D:\\桌面\\@@@tuxiangpipeizhushou, port 18080).

Given a material (image or video) and a list of candidate indicators
({indicator_code, indicator_name}), pick the indicators most relevant to what the
material actually shows.

Original design was two local-Ollama stages: llava describes the image, then a
text model selects indicators as strict JSON. We keep that two-stage shape but
route each stage through the unified model layer:
- vision stage  -> alias  config.extra["vision_alias"]  (default: config.model_alias)
- text stage    -> alias  config.extra["text_alias"]    (default: config.model_alias)
A single multimodal alias works for both; set two aliases to mirror the old
llava + gpt-oss split.

External contract preserved:
- POST /match  {indicators:[{indicator_code,indicator_name}], materials:[{material_url,...}], model?}
               -> {"indicators":[{indicator_code,indicator_name,message}]}
- GET/POST /prompt_config   (edits the stage-2 template)
- GET /health
"""
from __future__ import annotations

import json
import re
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app.apps.base import AppTemplate, PortConfig
from app.apps.eval_common import (
    build_user_content,
    call_model,
    download_media,
    load_prompt,
    media_to_data_url,
    save_prompt,
)

DEFAULT_MATCH_PROMPT = """你是一个"指标筛选器"。

你会得到：
1) 【图片描述】（由另一个模型生成）
2) 【候选指标列表】（每条包含 indicator_code 与 indicator_name）

任务：
- 只从候选列表中选出与图片内容最相关的指标
- 尽量返回 5-6 个；除非候选为空，否则不要返回空数组
- 禁止杜撰不存在的 indicator_code / indicator_name
- 删除 indicators 之外的无关字段和数据

【图片描述】
{{IMAGE_DESCRIPTION}}

【候选指标列表】
{{CANDIDATES}}

严格输出一个 JSON 对象，禁止输出任何解释文字，禁止输出代码块。
输出格式必须为：
{
  "indicators": [
    { "indicator_code": "Vx.x.x", "indicator_name": "xxx", "message": "一句理由" }
  ]
}"""

VISION_DESC_PROMPT = (
    "你是一个图像描述器。请客观描述这张/这些图片（同一素材，可能是视频多帧）的内容，包括："
    "画面主体、人物/物体、构图与布局、可识别的文字、风格/氛围。"
    "只描述事实，不要评价，不要输出 JSON。"
)

MAX_RETURN = 20
STAGE2_FIXED_N = 8
STAGE2_MSG_MAX_CHARS = 18


# =============================================================================
# Input validation
# =============================================================================
def validate_indicators(indicators: Any) -> list[dict[str, str]]:
    if not isinstance(indicators, list) or not indicators:
        raise HTTPException(400, {"stage": "input_validation", "msg": "indicators 为空或不是数组"})
    valid: list[dict[str, str]] = []
    for it in indicators:
        if not isinstance(it, dict):
            continue
        code, name = it.get("indicator_code"), it.get("indicator_name")
        if isinstance(code, str) and isinstance(name, str) and code.strip() and name.strip():
            valid.append({"indicator_code": code.strip(), "indicator_name": name.strip()})
    if not valid:
        raise HTTPException(400, {"stage": "input_validation", "msg": "indicators 中无合法条目"})
    return valid


_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".flv", ".m4v"}


def pick_material_url(materials: Any) -> str:
    if not isinstance(materials, list) or not materials:
        raise HTTPException(400, {"stage": "input_validation", "msg": "materials 为空或不是数组"})
    # prefer a video material if present (matches the original behavior)
    chosen = None
    for m in materials:
        if isinstance(m, dict) and isinstance(m.get("material_url"), str) and m["material_url"].strip():
            u = m["material_url"].strip()
            if chosen is None:
                chosen = u
            if u.lower().split("?")[0].endswith(tuple(_VIDEO_EXTS)):
                return u
    if not chosen:
        raise HTTPException(400, {"stage": "input_validation", "msg": "materials 无有效 material_url"})
    return chosen


# =============================================================================
# Stage-2 JSON parsing (robust, mirrors the original fallbacks)
# =============================================================================
def _strip_fences(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"^\s*```(?:json)?\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*```\s*$", "", s)
    return s.strip()


def _extract_json_object(text: str) -> str:
    s = _strip_fences(text)
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        raise ValueError("no JSON object boundary")
    return s[i:j + 1].strip()


def _truncate(s: str, n: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[:n]


def _build_candidates(indicators: list[dict[str, str]]) -> str:
    return "\n".join(f"- {i['indicator_code']}：{i['indicator_name']}" for i in indicators)


def _extract_codes_from_text(text: str, indicators: list[dict[str, str]]) -> list[dict[str, str]]:
    name_map = {i["indicator_code"]: i["indicator_name"] for i in indicators}
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for m in re.finditer(r"\b([A-Za-z]+\d+(?:\.\d+)+)\b", text or ""):
        code = m.group(1).strip()
        if code in name_map and code not in seen:
            seen.add(code)
            out.append({"indicator_code": code, "indicator_name": name_map[code], "message": "AI推荐"})
            if len(out) >= STAGE2_FIXED_N:
                break
    return out


def _fallback(indicators: list[dict[str, str]]) -> list[dict[str, str]]:
    return [{"indicator_code": i["indicator_code"], "indicator_name": i["indicator_name"], "message": "fallback"}
            for i in indicators[:STAGE2_FIXED_N]]


def _finalize(obj_indicators: list[Any], indicators: list[dict[str, str]]) -> list[dict[str, str]]:
    name_map = {i["indicator_code"]: i["indicator_name"] for i in indicators}
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for it in obj_indicators[:MAX_RETURN]:
        if not isinstance(it, dict):
            continue
        code = it.get("indicator_code")
        if not isinstance(code, str):
            continue
        code = code.strip()
        if not code or code not in name_map or code in seen:
            continue
        seen.add(code)
        msg = it.get("message", "")
        msg = msg if isinstance(msg, str) else (str(msg) if msg is not None else "")
        out.append({"indicator_code": code, "indicator_name": name_map[code],
                    "message": _truncate(msg, STAGE2_MSG_MAX_CHARS)})
    # pad up to fixed N from the head of the candidate list
    for i in indicators:
        if len(out) >= STAGE2_FIXED_N:
            break
        if i["indicator_code"] not in seen:
            seen.add(i["indicator_code"])
            out.append({"indicator_code": i["indicator_code"], "indicator_name": i["indicator_name"],
                        "message": "补齐默认"})
    return out[:STAGE2_FIXED_N]


def parse_stage2(text: str, indicators: list[dict[str, str]]) -> list[dict[str, str]]:
    try:
        obj = json.loads(_extract_json_object(text))
        if isinstance(obj, dict) and isinstance(obj.get("indicators"), list):
            return _finalize(obj["indicators"], indicators)
    except Exception:
        pass
    extracted = _extract_codes_from_text(text, indicators)
    return extracted if extracted else _fallback(indicators)


# =============================================================================
# Pipeline
# =============================================================================
async def run_match(config: PortConfig, payload: dict[str, Any]) -> JSONResponse:
    indicators = validate_indicators(payload.get("indicators"))
    url = pick_material_url(payload.get("materials"))

    vision_alias = (config.extra or {}).get("vision_alias") or config.model_alias
    text_alias = (config.extra or {}).get("text_alias") or payload.get("model") or config.model_alias

    # ---- Stage 1: vision description (image or video frames) ----
    try:
        frames, mime, _is_video = await download_media(url, for_cloud=True)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, {"stage": "download", "msg": "素材下载/处理失败", "err": str(e)})
    data_urls = [media_to_data_url(b, mime) for b in frames[:6]]
    image_desc = await call_model(
        config, "", build_user_content(VISION_DESC_PROMPT, data_urls),
        alias=vision_alias, params={"temperature": 0}, request_excerpt=url)
    if not (image_desc or "").strip():
        raise HTTPException(502, {"stage": "vision_empty", "msg": "vision 输出为空"})

    # ---- Stage 2: indicator selection (text JSON) ----
    template = load_prompt(config, DEFAULT_MATCH_PROMPT)
    prompt = (template
              .replace("{{IMAGE_DESCRIPTION}}", image_desc)
              .replace("{{CANDIDATES}}", _build_candidates(indicators))
              .replace("{{INDICATORS_JSON}}", json.dumps(indicators, ensure_ascii=False))
              .replace("{{MAX_RETURN}}", str(MAX_RETURN)).strip())
    prompt += ("\n\n最终输出必须是严格 JSON（第一字符必须是 {），禁止任何解释文字。"
               f"\nindicators 数组长度必须等于 {STAGE2_FIXED_N}，每条 message 不超过 {STAGE2_MSG_MAX_CHARS} 个字。")
    try:
        text = await call_model(config, "", prompt, alias=text_alias,
                                params={"temperature": 0, "top_p": 0.9}, record=False)
    except Exception:
        return JSONResponse({"indicators": _fallback(indicators)})
    return JSONResponse({"indicators": parse_stage2(text, indicators)})


# =============================================================================
# App
# =============================================================================
def build_matcher_app(config: PortConfig) -> FastAPI:
    app = FastAPI(title=f"{config.name} (matcher_eval)")

    @app.get("/health")
    async def health():
        return {"ok": True, "status": "ok", "slug": config.slug, "app_type": config.app_type,
                "model_alias": config.model_alias,
                "vision_alias": (config.extra or {}).get("vision_alias") or config.model_alias,
                "text_alias": (config.extra or {}).get("text_alias") or config.model_alias}

    @app.get("/prompt_config")
    async def get_prompt_config():
        cur = load_prompt(config, DEFAULT_MATCH_PROMPT)
        return {"prompt_key": "match_prompt",
                "default_prompt": {"prompt": DEFAULT_MATCH_PROMPT, "match_prompt": DEFAULT_MATCH_PROMPT},
                "user_prompt": {"prompt": cur, "match_prompt": cur}}

    @app.post("/prompt_config")
    async def set_prompt_config(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "请求体必须是 JSON 对象")
        prompt = str(body.get("prompt") or body.get("match_prompt") or "").strip()
        if not prompt:
            raise HTTPException(400, "提示词不能为空")
        save_prompt(config, prompt)
        return await get_prompt_config()

    @app.post("/match")
    async def match(payload: dict[str, Any]):
        if not isinstance(payload, dict):
            raise HTTPException(400, "请求体必须是 JSON 对象")
        return await run_match(config, payload)

    return app


class MatcherEvalTemplate(AppTemplate):
    app_type = "matcher_eval"
    title = "指标匹配 / Indicator Matcher"
    description = ("两阶段图像/视频指标筛选：看图描述 → 从候选指标中选最相关项并输出 JSON。"
                   "POST /match。可在 extra 配置 vision_alias / text_alias 分离视觉与文本模型。")
    default_prompt = DEFAULT_MATCH_PROMPT

    def build_app(self, config: PortConfig):
        return build_matcher_app(config)
