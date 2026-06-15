"""Ported visual-eval service template (was port 18081).

Wraps `eval_visual_core` (scene-based score JSON + formal report engine) as a
PORTHUB port. The core's `call_model` is delegated to the unified router; media
fetching stays inside the core (requests).

External contract preserved:
- POST /score_json            -> score JSON
- POST /report_text           -> plain-text report
- POST /evaluate              {mode: score|report|score_report}
- POST /evaluate_upload       multipart: aiReqJson + files
- GET/POST /prompt_config
- GET /health
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse

from app.apps import eval_visual_core as core
from app.apps.base import AppTemplate, PortConfig
from app.db.session import SessionLocal
from app.models_layer.router import _attempt_order, resolve_alias
from app.monitor.metrics import metrics


def _chat_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    return f"{base}/chat/completions" if base.endswith("/v1") else f"{base}/v1/chat/completions"


def make_router_llm(config: PortConfig):
    """Return llm(prompt, data_urls, images_b64, max_tokens) -> text routed through
    the port's model alias (OpenAI-compatible, multimodal when data_urls present)."""
    def _llm(prompt: str, data_urls: list[str], images_b64: list[str], max_tokens: int) -> str:
        if data_urls:
            content: Any = [{"type": "image_url", "image_url": {"url": u, "detail": "high"}}
                            for u in data_urls]
            content.append({"type": "text", "text": prompt})
        else:
            content = prompt
        messages = [{"role": "user", "content": content}]

        db = SessionLocal()
        try:
            resolved = resolve_alias(db, config.model_alias)
        finally:
            db.close()
        targets = _attempt_order(resolved.targets)
        if not targets:
            raise HTTPException(500, f"model alias '{config.model_alias}' has no usable targets")
        last_err: Exception | None = None
        started = time.perf_counter()
        for t in targets:
            headers = {"Content-Type": "application/json"}
            if t.api_key:
                headers["Authorization"] = f"Bearer {t.api_key}"
            extra_h = (t.extra or {}).get("headers") if isinstance(t.extra, dict) else None
            if isinstance(extra_h, dict):
                headers.update({str(k): str(v) for k, v in extra_h.items()})
            body = {"model": t.model, "messages": messages,
                    "temperature": core.TEMPERATURE, "max_tokens": int(max_tokens or core.MAX_TOKENS_SCORE)}
            try:
                with httpx.Client(timeout=config.timeout, trust_env=False) as client:
                    r = client.post(_chat_url(t.base_url), headers=headers, json=body)
                    r.raise_for_status()
                    data = r.json()
                txt = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
                usage = data.get("usage") or {}
                metrics.record(config.id, True, (time.perf_counter() - started) * 1000,
                               model=t.label, prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
                               completion_tokens=int(usage.get("completion_tokens", 0) or 0),
                               request_excerpt=prompt[:300], response_excerpt=txt[:500],
                               logging_enabled=config.logging_enabled, log_keep=config.log_keep)
                return txt
            except Exception as e:  # noqa: BLE001
                last_err = e
        metrics.record(config.id, False, (time.perf_counter() - started) * 1000,
                       model=config.model_alias, error=str(last_err),
                       logging_enabled=config.logging_enabled, log_keep=config.log_keep)
        raise HTTPException(502, f"all targets failed: {last_err}")
    return _llm


def _norm_mode(body: dict) -> str:
    mode = str(body.get("mode") or "score").strip().lower()
    if mode in {"report", "报告"}:
        return "report"
    if mode in {"score_report", "score_then_report", "both", "评分报告", "评分后报告"}:
        return "score_report"
    return "score"


def build_visual_app(config: PortConfig) -> FastAPI:
    app = FastAPI(title=f"{config.name} (visual_eval)")
    llm = make_router_llm(config)

    @app.get("/health")
    async def health():
        return {"ok": True, "service": "visual_eval", "slug": config.slug,
                "app_type": config.app_type, "model_alias": config.model_alias}

    @app.get("/prompt_config")
    async def get_prompt_config():
        core.ensure_default_prompt_backup()
        return {"user_prompt_exists": core.USER_PROMPT_FILE.exists(),
                "user_prompt": core.load_user_prompt_config(),
                "default_prompt": dict(core.DEFAULT_USER_PROMPT),
                "scene_configs": core.load_scene_prompt_config(),
                "default_scene_configs": core.merge_scene_prompt_config({})}

    @app.post("/prompt_config")
    async def set_prompt_config(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "请求体必须是 JSON 对象")
        user_prompt = core.save_user_prompt_config(
            str(body.get("score_prompt_template") or body.get("score_base") or body.get("score_extra") or ""),
            str(body.get("report_prompt_template") or body.get("report_base") or body.get("report_extra") or ""),
            body.get("scene_configs"))
        return {"saved": True, "user_prompt": user_prompt,
                "default_prompt": dict(core.DEFAULT_USER_PROMPT),
                "scene_configs": core.load_scene_prompt_config(),
                "default_scene_configs": core.merge_scene_prompt_config({})}

    @app.post("/score_json")
    async def score_json(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "请求体必须是 JSON 对象")
        core.set_llm(llm)
        rid, result, meta = await asyncio.to_thread(core.evaluate, body, "score")
        if body.get("return_meta"):
            return JSONResponse({"rid": rid, "data": result, "meta": meta},
                                media_type="application/json; charset=utf-8")
        return JSONResponse(result, media_type="application/json; charset=utf-8")

    @app.post("/report_text")
    async def report_text(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "请求体必须是 JSON 对象")
        core.set_llm(llm)
        _, report, _ = await asyncio.to_thread(core.evaluate, body, "report")
        return PlainTextResponse(str(report), media_type="text/plain; charset=utf-8")

    @app.post("/evaluate")
    async def evaluate_json(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "请求体必须是 JSON 对象")
        core.set_llm(llm)
        mode = _norm_mode(body)
        rid, data, meta = await asyncio.to_thread(core.evaluate, body, mode)
        if mode == "score_report":
            return JSONResponse({"rid": rid, "mode": mode, "data": data.get("score"),
                                 "report": data.get("report"), "meta": meta},
                                media_type="application/json; charset=utf-8")
        return JSONResponse({"rid": rid, "mode": mode, "data": data, "meta": meta},
                            media_type="application/json; charset=utf-8")

    @app.post("/evaluate_upload")
    async def evaluate_upload(aiReqJson: str = Form(...), files: list[UploadFile] = File(...)):
        import json
        try:
            body = json.loads(aiReqJson)
        except Exception:
            raise HTTPException(400, "aiReqJson 必须是合法 JSON 字符串")
        if not isinstance(body, dict):
            raise HTTPException(400, "aiReqJson 必须是 JSON 对象")
        uploaded: list[tuple[str, str, bytes]] = []
        for f in files:
            uploaded.append((f.filename or "upload.png", f.content_type or "image/png", await f.read()))
        core.set_llm(llm)
        mode = _norm_mode(body)
        rid, data, meta = await asyncio.to_thread(lambda: core.evaluate(body, mode, uploaded_files=uploaded))
        if mode == "report":
            return JSONResponse({"rid": rid, "mode": mode, "report": data, "meta": meta},
                                media_type="application/json; charset=utf-8")
        if mode == "score_report":
            return JSONResponse({"rid": rid, "mode": mode, "data": data.get("score"),
                                 "report": data.get("report"), "meta": meta},
                                media_type="application/json; charset=utf-8")
        return JSONResponse({"rid": rid, "mode": mode, "data": data, "meta": meta},
                            media_type="application/json; charset=utf-8")

    return app


class VisualEvalTemplate(AppTemplate):
    app_type = "visual_eval"
    title = "视觉评估报告 / Visual Eval"
    description = ("场景化视觉评估：/score_json 出结构化评分 JSON，/report_text 出正式中文报告，"
                  "/evaluate 支持 score/report/score_report，/evaluate_upload 支持上传素材。多素材对比。")
    default_prompt = ""

    def build_app(self, config: PortConfig):
        return build_visual_app(config)
