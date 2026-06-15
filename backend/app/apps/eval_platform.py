"""Ported visual-eval platform template (was ports 9100 analyze + 9101 chat).

Wraps `eval_platform_core` (the verbatim analytics pipeline + context store) in a
single PORTHUB port that exposes both the analyze API and the project-chat API.
The LLM is delegated to the unified router via a per-request contextvar.

External contract preserved:
- POST /analyze
- POST /chat                              {project_id, question|message, model?}
- GET  /project/{project_id}/context
- POST /project/{project_id}/context/clear
- GET  /project/{project_id}/context/summary
- GET  /health
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app.apps import eval_platform_core as core
from app.apps.base import AppTemplate, PortConfig
from app.db.session import SessionLocal
from app.models_layer.router import _attempt_order, resolve_alias
from app.monitor.metrics import metrics


# ---------------------------------------------------------------------------
# Router-backed synchronous LLM callable (the analytics code is sync)
# ---------------------------------------------------------------------------
def _chat_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    return f"{base}/chat/completions" if base.endswith("/v1") else f"{base}/v1/chat/completions"


def make_router_llm(config: PortConfig):
    """Return llm(display_name, messages, temperature) -> (text, meta) that resolves
    the port's model alias and calls each target (OpenAI-compatible) until one works."""
    def _llm(display_name: Any, messages: list[dict], temperature: float = 0.3):
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
            body = {"model": t.model, "messages": messages, "temperature": temperature}
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
                               response_excerpt=txt[:500],
                               logging_enabled=config.logging_enabled, log_keep=config.log_keep)
                return txt, {"provider": t.kind, "model": t.model, "display_name": t.label}
            except Exception as e:  # noqa: BLE001
                last_err = e
        metrics.record(config.id, False, (time.perf_counter() - started) * 1000,
                       model=config.model_alias, error=str(last_err),
                       logging_enabled=config.logging_enabled, log_keep=config.log_keep)
        raise HTTPException(502, f"all targets failed: {last_err}")
    return _llm


# ---------------------------------------------------------------------------
# /chat logic (re-implemented from the original 9101 web layer)
# ---------------------------------------------------------------------------
def _build_rule_chat_answer(question: str, ctx: dict) -> str:
    analysis = ctx.get("latest_analysis") or {}
    if not analysis:
        return "当前项目还没有可用的分析结果，请先调用分析接口。"
    summary = analysis.get("summary", {})
    if "最佳" in question or "主方案" in question:
        return (f"当前建议主方案是{summary.get('best_material')}，综合得分为{summary.get('best_score')}。"
                + (analysis.get("actions", {}) or {}).get("main_plan", ""))
    if "驱动" in question or "指标" in question:
        drivers = (analysis.get("drivers") or [])[:3]
        if drivers:
            return "当前最关键的驱动指标包括：" + "、".join(d["indicator_name"] for d in drivers) + "。"
        return "当前项目没有可用的驱动指标数据。"
    return summary.get("ai_summary") or "当前项目已有分析结果，但没有足够上下文回答这个问题。"


def _ai_chat_answer(question: str, model_display: Any, ctx: dict) -> tuple[str, str]:
    latest_analysis = ctx.get("latest_analysis")
    if not latest_analysis:
        return "当前项目还没有可用的分析结果，请先调用分析接口。", "rule_based_fallback"
    try:
        messages = [{"role": "system", "content": core.CHAT_PROMPT},
                    {"role": "system", "content": "以下是项目最新分析结果精简版：\n"
                     + core.safe_json(core.compact_analysis_for_chat(latest_analysis))}]
        if ctx.get("latest_payload"):
            messages.append({"role": "system", "content": "以下是项目基础信息精简版：\n"
                             + core.safe_json(core.compact_payload_for_chat(ctx["latest_payload"]))})
        if ctx.get("chat_memory_summary"):
            messages.append({"role": "system", "content": "以下是本项目历史问答摘要：\n" + ctx["chat_memory_summary"]})
        for h in ctx.get("chat_history", [])[-core.CHAT_HISTORY_ROUNDS:]:
            messages.append({"role": "user", "content": h.get("question", "")})
            messages.append({"role": "assistant", "content": h.get("answer", "")})
        messages.append({"role": "user", "content": question})
        txt, _meta = core.llm_chat(model_display, messages, temperature=0.3)
        txt = (txt or "").strip()
        if not txt:
            raise ValueError("empty ai chat answer")
        return txt, "ai"
    except Exception:
        return _build_rule_chat_answer(question, ctx), "rule_based_fallback"


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
def build_platform_app(config: PortConfig) -> FastAPI:
    app = FastAPI(title=f"{config.name} (platform_eval)")
    llm = make_router_llm(config)

    def _dbg() -> str:
        import os
        return os.path.join(core.DEBUG_DIR, f"req_{core.new_rid()}.txt")

    @app.get("/health")
    async def health():
        return {"ok": True, "service": "platform", "slug": config.slug,
                "app_type": config.app_type, "model_alias": config.model_alias}

    @app.post("/analyze")
    async def api_analyze(payload: dict[str, Any]):
        if not isinstance(payload, dict):
            raise HTTPException(400, "请求体必须是 JSON 对象")
        core.set_llm(llm)
        payload = core.sanitize_payload(payload)
        return JSONResponse(await asyncio.to_thread(core.analyze, payload, core.new_rid(), _dbg()))

    @app.post("/chat")
    async def chat_api(payload: dict[str, Any]):
        if not isinstance(payload, dict):
            raise HTTPException(400, "请求体必须是 JSON 对象")
        core.set_llm(llm)
        payload = core.sanitize_payload(payload)
        project_id = str(payload.get("project_id", "")).strip()
        question = str(payload.get("question") or payload.get("message") or "").strip()
        if not project_id:
            raise HTTPException(400, "project_id 不能为空")
        if not question:
            raise HTTPException(400, "question 不能为空")

        def _run() -> dict:
            ctx = core.load_context(project_id)
            answer, mode = _ai_chat_answer(question, payload.get("model"), ctx)
            ctx.setdefault("chat_history", []).append(
                {"time": core.now_str(), "question": question, "answer": answer, "mode": mode})
            core.update_chat_memory_summary(ctx, question, answer)
            core.save_context(project_id, ctx)
            pname = ((ctx.get("latest_payload") or {}).get("project_name")
                     or (ctx.get("latest_analysis") or {}).get("project_name") or "")
            return {"code": 200, "msg": "success", "project_id": project_id, "project_name": pname,
                    "update_time": core.now_str(), "answer": answer, "chat_mode": mode,
                    "chat_history_count": len(ctx["chat_history"])}

        return JSONResponse(await asyncio.to_thread(_run))

    @app.get("/project/{project_id}/context")
    async def get_context(project_id: str):
        return core.load_context(project_id)

    @app.post("/project/{project_id}/context/clear")
    async def clear_context(project_id: str):
        core.save_context(project_id, {"project_id": project_id, "latest_payload": None,
                                       "latest_analysis": None, "analysis_updated_at": None,
                                       "chat_history": []})
        return {"code": 200, "msg": "success", "project_id": project_id}

    @app.get("/project/{project_id}/context/summary")
    async def context_summary(project_id: str):
        ctx = core.load_context(project_id)
        analysis = ctx.get("latest_analysis") or {}
        payload = ctx.get("latest_payload") or {}
        return {"code": 200, "msg": "success", "project_id": project_id,
                "project_name": analysis.get("project_name") or payload.get("project_name") or "",
                "analysis_updated_at": ctx.get("analysis_updated_at"),
                "has_analysis": bool(ctx.get("latest_analysis")),
                "chat_history_count": len(ctx.get("chat_history", [])),
                "chat_memory_summary_length": len(ctx.get("chat_memory_summary", "") or "")}

    return app


class PlatformEvalTemplate(AppTemplate):
    app_type = "platform_eval"
    title = "视觉评估平台 / Eval Platform"
    description = ("问卷统计分析 + 项目问答：/analyze 对问卷打分做聚合（最佳素材/驱动指标/显著性/行动建议），"
                  "/chat 基于分析结果的项目记忆问答。原 9100+9101 合并为一个端口。")
    default_prompt = ""

    def build_app(self, config: PortConfig):
        return build_platform_app(config)
