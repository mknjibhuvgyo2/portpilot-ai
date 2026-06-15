"""Ported scoring service (was D:\\桌面\\@@@pingfenzhenghe, port 9000).

A visual-evaluation engine: the VT system POSTs poster/video material + a list
of questions (score questions and single/multi choice questions); the service
fetches the media, asks a vision model, parses the strict line-format answer and
returns per-question scores / selected options.

External contract preserved:
- POST /score_json     {image_base_url, image_paths|images, questions_json|questions, ...}
- POST /score_upload   multipart: aiReqJson=<json> + files=<media...>
- GET/POST /prompt_config
- GET /health
- POST /v1/chat/completions (generic passthrough so the port is OpenAI-compatible)

The model call is delegated to the unified router (config.model_alias); the old
per-model `model_map.json` / direct OneAPI+Ollama clients are gone.
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from app.apps.base import AppTemplate, PortConfig
from app.apps.eval_common import (
    call_model,
    download_media,
    load_prompt,
    media_to_data_url,
    normalize_int_list,
    normalize_list,
    pick_first,
    process_media_bytes,
    save_prompt,
    try_json_loads,
)

DEFAULT_ROLE_PROMPT = """你是一个视觉设计评测引擎（Visual Evaluation Engine），用于系统自动化视觉评估测试。
系统会传入评估对象（图片/视频抽帧）与评测问题（question），你需要基于视觉内容给出评分或选择结果，并附上简短理由/建议。

若系统提供【role_definition】（视角，如"普通银发退休老人""Z世代校园群体"），你仍是"视觉设计评测引擎"，
但要以该人群视角客观评价（理由/建议贴合该人群认知），不要第一人称演戏；未提供则按"普通大众"视角。

行为规则（必须遵守）：
1) 只评估系统传入的问题，不增删拆分。
2) 输出必须严格逐题、每题一行，不要输出 JSON / markdown / 多余解释。
3) 评分题输出：ID=<题目ID> SCORE=<整数分数> REASON=<一句简洁中文理由> SUGGESTION=<一句可执行建议或无>
4) 选择题输出：ID=<题目ID> ANSWER=<选项value或逗号分隔value列表> REASON=<理由> SUGGESTION=<建议或无>
5) SCORE 为 0~MAX_SCORE 的整数；选择题 ANSWER 必须取自给定 OPTIONS 的 value，单选一个、多选用英文逗号分隔。
6) 理由/建议须简短、与视觉内容强相关；图文不匹配时也要作答并在 REASON 说明原因。"""

MAX_FRAMES = 12
MAX_RETRY_MISSING = 2

NO_IMAGE_HINTS = (
    "未提供图片", "无法查看图片", "看不到图片", "无法访问图片", "未提供视频", "无法查看视频",
    "未提供图片/视频", "未提供图片或视频", "no image", "can't see the image",
    "cannot view the image", "unable to view the image", "no images provided",
)


# =============================================================================
# Payload normalization
# =============================================================================
def normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    canon = {
        "image_base_url": pick_first(payload, ["image_base_url", "material_base_url", "base_url", "BaseUrl", "imageBaseUrl"]),
        "image_paths": pick_first(payload, ["image_paths", "image_urls", "paths", "images", "imagePaths", "imageUrls", "Images"]),
        "questions": pick_first(payload, ["questions_json", "questionsJson", "QuestionsJson", "questions", "rows", "Rows"]),
        "question_ids": pick_first(payload, ["question_ids", "questionIds", "ids", "Ids"]),
        "role_definition": pick_first(payload, ["role_definition", "roleDefinition", "RoleDefinition"]),
        "single_question": pick_first(payload, ["question", "Question"]),
        "single_question_id": pick_first(payload, ["question_id", "questionId", "QuestionID"]),
        "single_question_type": pick_first(payload, ["question_type", "questionType", "QuestionType", "type", "Type"]),
        "single_options": pick_first(payload, ["options", "Options", "choices", "Choices", "answers", "Answers"]),
    }
    if canon["role_definition"] is None and payload.get("role_name") is not None:
        canon["role_definition"] = payload.get("role_name")
    return canon


# =============================================================================
# Options / choice helpers
# =============================================================================
def normalize_options(value: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for x in normalize_list(value):
        if isinstance(x, dict):
            v = str(pick_first(x, ["value", "Value", "id", "ID", "key", "Key", "code", "Code"]) or "").strip()
            t = str(pick_first(x, ["text", "Text", "label", "Label", "name", "Name", "title", "Title"]) or "").strip()
            if v:
                out.append({"value": v, "text": t or v})
        elif isinstance(x, str) and x.strip():
            out.append({"value": x.strip(), "text": x.strip()})
    return out


def option_map(options: list[dict[str, str]]) -> dict[str, str]:
    return {o["value"]: o.get("text", "") for o in options if o.get("value")}


def question_type_name(qt: int) -> str:
    return {1: "single", 2: "multi"}.get(qt, "score")


def format_options(options: list[dict[str, str]]) -> str:
    arr = [f"{o['value']}:{o.get('text', '')}" for o in options if o.get("value")]
    return " | ".join(arr) if arr else "无"


def infer_question_type(qtype_raw: Any, options: list[dict[str, str]]) -> int:
    if qtype_raw is None:
        return 1 if options else 0
    try:
        return int(qtype_raw)
    except Exception:
        pass
    qt = str(qtype_raw).strip().lower()
    if qt in ("single", "single_choice", "radio", "choice", "select"):
        return 1
    if qt in ("multi", "multiple", "multiple_choice", "checkbox", "multi_choice"):
        return 2
    return 1 if options else 0


def normalize_answer_values(ans: str) -> list[str]:
    if not ans:
        return []
    s = ans
    for sep in ("，", "、", "；", ";", "|", "/", "\n", "\t"):
        s = s.replace(sep, ",")
    seen: set[str] = set()
    out: list[str] = []
    for p in (x.strip() for x in s.split(",")):
        if p and p not in seen:
            out.append(p)
            seen.add(p)
    return out


def sanitize_choice_answer(raw_answer: str, qtype: int, options: list[dict[str, str]]) -> list[str]:
    opt_map = option_map(options)
    valid = set(opt_map.keys())
    vals = [x for x in normalize_answer_values(raw_answer) if x in valid]
    if not vals and raw_answer:
        for v in sorted(valid, key=len, reverse=True):
            if re.search(r"(?<![A-Za-z0-9_])" + re.escape(v) + r"(?![A-Za-z0-9_])", raw_answer):
                vals.append(v)
    if not vals and raw_answer:
        for v, text in opt_map.items():
            if text and text in raw_answer:
                vals.append(v)
    deduped: list[str] = []
    seen: set[str] = set()
    for v in vals:
        if v not in seen:
            deduped.append(v)
            seen.add(v)
    return deduped[:1] if (qtype == 1 and deduped) else deduped


def _answer_matches(a: dict[str, Any], q: dict[str, Any]) -> bool:
    qtype = int(q.get("QuestionType") or 0)
    if qtype in (1, 2):
        if a.get("kind") != "answer":
            return False
        vals = sanitize_choice_answer(str(a.get("answer_raw") or ""), qtype, normalize_options(q.get("Options")))
        return len(vals) == 1 if qtype == 1 else len(vals) > 0
    return a.get("kind") == "score"


def build_valid_answer_map(parsed: list[dict[str, Any]], qmap: dict[int, dict[str, Any]]) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    for a in parsed:
        try:
            qid = int(a["id"])
        except Exception:
            continue
        q = qmap.get(qid)
        if q is not None and _answer_matches(a, q):
            out[qid] = a
    return out


def build_choice_retry_hint(qmap: dict[int, dict[str, Any]], missing_ids: list[int]) -> str:
    parts: list[str] = []
    for qid in missing_ids:
        q = qmap.get(qid)
        if not q:
            continue
        qtype = int(q.get("QuestionType") or 0)
        if qtype in (1, 2):
            opts = normalize_options(q.get("Options"))
            parts.append(
                f"ID={qid} 是{'单选' if qtype == 1 else '多选'}题，不要输出 SCORE，"
                f"必须输出：ID={qid} ANSWER=<value{'[,value...]' if qtype == 2 else ''}> "
                f"REASON=<理由> SUGGESTION=<建议>。可选值：{format_options(opts)}"
            )
    return ("\n\n格式纠正：\n" + "\n".join(parts)) if parts else ""


# =============================================================================
# Parse line-format model output
# =============================================================================
SCORE_RE = re.compile(
    r"ID\s*=\s*(\d+)\s+SCORE\s*=\s*([0-9]+(?:\.[0-9]+)?)\s+REASON\s*=\s*(.*?)(?:\s+SUGGESTION\s*=\s*(.*))?$", re.I)
ANSWER_RE = re.compile(
    r"ID\s*=\s*(\d+)\s+ANSWER\s*=\s*(.*?)(?:\s+REASON\s*=\s*(.*?))(?:\s+SUGGESTION\s*=\s*(.*))?$", re.I)


def _clean(t: str) -> str:
    return (t or "").replace("<|begin_of_box|>", "").replace("<|end_of_box|>", "").replace(chr(0), "").strip()


def parse_lines(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in _clean(text).splitlines():
        line = raw.strip()
        if not line:
            continue
        m = SCORE_RE.search(line)
        if m:
            out.append({"id": int(m.group(1)), "kind": "score", "score": float(m.group(2)),
                        "reason": (m.group(3) or "").strip() or "无",
                        "suggestion": ((m.group(4) or "").strip() or "无")})
            continue
        m = ANSWER_RE.search(line)
        if m:
            out.append({"id": int(m.group(1)), "kind": "answer", "answer_raw": (m.group(2) or "").strip(),
                        "reason": (m.group(3) or "").strip() or "无",
                        "suggestion": ((m.group(4) or "").strip() or "无")})
    return out


def _looks_no_image(text: str) -> bool:
    t = (text or "").strip().lower()
    return bool(t) and any(h.lower() in t for h in NO_IMAGE_HINTS)


# =============================================================================
# Question building + prompt
# =============================================================================
def build_questions(canon: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    sq, sqid = canon.get("single_question"), canon.get("single_question_id")
    if sq is not None and sqid is not None:
        try:
            qid_int = int(sqid)
        except Exception:
            raise HTTPException(400, "question_id 必须是整数")
        opts = normalize_options(canon.get("single_options"))
        return ([{"ID": qid_int, "QuestionText": str(sq), "QuestionType": infer_question_type(canon.get("single_question_type"), opts),
                  "Options": opts, "Score": 10}], True)

    q_list = normalize_list(canon.get("questions"))
    if len(q_list) == 1 and isinstance(q_list[0], str):
        q_list = try_json_loads(q_list[0]) or []

    questions: list[dict[str, Any]] = []
    for q in q_list:
        if not isinstance(q, dict):
            continue
        qid = pick_first(q, ["ID", "Id", "id", "question_id", "questionId", "QuestionID"])
        if qid is None:
            continue
        qtext = pick_first(q, ["QuestionText", "question_text", "questionText", "question", "Question", "title", "Title"])
        opts = normalize_options(pick_first(q, ["Options", "options", "choices", "Choices", "answers", "Answers"]))
        qtype = infer_question_type(pick_first(q, ["QuestionType", "question_type", "questionType", "type", "Type"]), opts)
        score = pick_first(q, ["Score", "score", "MaxScore", "max_score", "maxScore"])
        questions.append({"ID": int(qid), "QuestionText": "" if qtext is None else str(qtext),
                          "QuestionType": qtype, "Options": opts, "Score": 10 if score is None else score})

    qid_list = normalize_int_list(canon.get("question_ids"))
    if qid_list:
        questions = [q for q in questions if int(q["ID"]) in qid_list]
    if not questions:
        raise HTTPException(400, "no valid questions")
    return questions, False


def build_question_block(role_definition: Any, questions: list[dict[str, Any]]) -> str:
    """The instruction+questions text (role prompt is sent separately as system)."""
    lines: list[str] = []
    if role_definition:
        lines.append(f"【role_definition（视角）】{str(role_definition).strip()}")
        lines.append("")
    lines += [
        "严格逐题输出，每题一行：",
        "评分题：ID=<ID> SCORE=<分数> REASON=<理由> SUGGESTION=<建议或无>",
        "选择题：ID=<ID> ANSWER=<选项value或逗号分隔value列表> REASON=<理由> SUGGESTION=<建议或无>",
        "不要输出多余文字、JSON、markdown。",
        "",
    ]
    for q in questions:
        qid = int(q["ID"])
        qtext = str(q.get("QuestionText") or "").strip()
        qtype = int(q.get("QuestionType") or 0)
        if qtype in (1, 2):
            lines.append(f"ID={qid} QUESTION_TYPE={qtype} QUESTION_TYPE_NAME={question_type_name(qtype)} "
                         f"QUESTION={qtext} OPTIONS={format_options(normalize_options(q.get('Options')))}")
        else:
            smax = 10 if q.get("Score") is None else int(q.get("Score"))
            lines.append(f"ID={qid} QUESTION_TYPE=0 QUESTION_TYPE_NAME=score MAX_SCORE={smax} QUESTION={qtext}")
    return "\n".join(lines)


# =============================================================================
# Pipeline
# =============================================================================
async def _collect_frames(urls: list[str] | None,
                          uploads: list[tuple[bytes, str, str]] | None) -> tuple[list[bytes], str]:
    frames: list[bytes] = []
    mime = "image/png"
    for u in (urls or []):
        fr, m, _ = await download_media(u, for_cloud=True)
        frames.extend(fr)
        mime = m or mime
    for b, ct, _fn in (uploads or []):
        fr, m = await process_media_bytes(b, ct, for_cloud=True)
        frames.extend(fr)
        mime = m or mime
    if not frames:
        raise HTTPException(400, "下载/上传媒体为空")
    return frames[:MAX_FRAMES], mime


async def run_scoring(config: PortConfig, canon: dict[str, Any],
                      urls: list[str] | None, uploads: list[tuple[bytes, str, str]] | None) -> JSONResponse:
    role_prompt = load_prompt(config, DEFAULT_ROLE_PROMPT)
    role_def = canon.get("role_definition")
    questions, _single = build_questions(canon)
    qmap = {int(q["ID"]): q for q in questions}
    expect_ids = list(qmap.keys())

    frames, mime = await _collect_frames(urls, uploads)
    data_urls = [media_to_data_url(b, mime) for b in frames]

    prompt = build_question_block(role_def, questions)
    from app.apps.eval_common import build_user_content

    out = await call_model(config, role_prompt, build_user_content(prompt, data_urls),
                           request_excerpt=prompt[:500])
    if _looks_no_image(out):  # one retry (router already handles provider fallback)
        out = await call_model(config, role_prompt, build_user_content(prompt, data_urls), record=False)
    answer_map = build_valid_answer_map(parse_lines(out), qmap)

    for _ in range(MAX_RETRY_MISSING):
        missing = [qid for qid in expect_ids if qid not in answer_map]
        if not missing:
            break
        retry_prompt = build_question_block(role_def, [qmap[q] for q in missing]) + build_choice_retry_hint(qmap, missing)
        try:
            out_retry = await call_model(config, role_prompt, build_user_content(retry_prompt, data_urls), record=False)
        except Exception:
            break
        for a in build_valid_answer_map(parse_lines(out_retry), qmap).items():
            answer_map[a[0]] = a[1]

    data: list[dict[str, Any]] = []
    for qid in expect_ids:
        q = qmap[qid]
        qtype = int(q.get("QuestionType") or 0)
        if qtype in (1, 2):
            opts = normalize_options(q.get("Options"))
            omap = option_map(opts)
            if qid in answer_map and answer_map[qid].get("kind") == "answer":
                a = answer_map[qid]
                vals = sanitize_choice_answer(str(a.get("answer_raw") or ""), qtype, opts)
                texts = [omap.get(v, v) for v in vals]
                txt = f"选择：{','.join(vals) if vals else '无'}"
                if texts:
                    txt += f"（{'、'.join(texts)}）"
                txt += f" | 理由：{a.get('reason', '无')} | 建议：{a.get('suggestion', '无')}"
            else:
                vals, texts = [], []
                txt = "模型输出未命中选择题解析格式或调用失败 | 建议：检查提示词/options/模型路由"
            data.append({"ID": qid, "QuestionText": q.get("QuestionText", ""), "QuestionType": qtype,
                         "SelectedValues": vals, "SelectedTexts": texts, "AnswersText": txt})
        else:
            max_score = int(q.get("Score") or 10)
            if qid in answer_map and answer_map[qid].get("kind") == "score":
                a = answer_map[qid]
                score_i = max(0, min(int(round(float(a.get("score", 0.0)))), max_score))
                txt = f"{a.get('reason', '无')} | 建议：{a.get('suggestion', '无')}"
            else:
                score_i = 0
                txt = "模型输出未命中解析格式或调用失败 | 建议：检查模型路由/渠道/余额"
            data.append({"ID": qid, "QuestionText": q.get("QuestionText", ""), "QuestionType": 0,
                         "Score": score_i, "AnswersText": txt})
    return JSONResponse({"data": data}, media_type="application/json; charset=utf-8")


# =============================================================================
# App
# =============================================================================
def build_scoring_app(config: PortConfig) -> FastAPI:
    app = FastAPI(title=f"{config.name} (scoring_eval)")

    @app.get("/health")
    async def health():
        return {"ok": True, "status": "ok", "slug": config.slug, "app_type": config.app_type,
                "model_alias": config.model_alias}

    @app.get("/prompt_config")
    async def get_prompt_config():
        return {"prompt_key": "score_role_prompt",
                "default_prompt": {"prompt": DEFAULT_ROLE_PROMPT, "role_prompt": DEFAULT_ROLE_PROMPT},
                "user_prompt": {"prompt": load_prompt(config, DEFAULT_ROLE_PROMPT),
                                "role_prompt": load_prompt(config, DEFAULT_ROLE_PROMPT)}}

    @app.post("/prompt_config")
    async def set_prompt_config(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "请求体必须是 JSON 对象")
        prompt = str(body.get("prompt") or body.get("role_prompt") or "").strip()
        if not prompt:
            raise HTTPException(400, "提示词不能为空")
        save_prompt(config, prompt)
        return await get_prompt_config()

    @app.post("/score_json")
    async def score_json(request: Request):
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "请求体必须是 JSON")
        if not isinstance(body, dict):
            raise HTTPException(400, "请求体必须是 JSON 对象")
        canon = normalize_payload(body)
        image_paths = [str(x).strip() for x in normalize_list(canon.get("image_paths")) if x is not None and str(x).strip()]
        if not image_paths:
            raise HTTPException(400, "image_paths missing or empty")
        base = (canon.get("image_base_url") or "").strip().rstrip("/")
        urls: list[str] = []
        for p in image_paths:
            if p.lower().startswith(("http://", "https://")):
                urls.append(p)
            elif base:
                urls.append(base + "/" + p.lstrip("/"))
            else:
                raise HTTPException(400, "image_base_url missing and image_paths are not full urls")
        return await run_scoring(config, canon, urls, None)

    @app.post("/score_upload")
    async def score_upload(aiReqJson: str = Form(...), files: list[UploadFile] = File(...)):
        body = try_json_loads(aiReqJson)
        if not isinstance(body, dict):
            raise HTTPException(400, "aiReqJson 必须是合法 JSON 对象")
        canon = normalize_payload(body)
        uploads: list[tuple[bytes, str, str]] = []
        for f in files:
            uploads.append((await f.read(), (f.content_type or "").strip(), (f.filename or "upload.bin").strip()))
        return await run_scoring(config, canon, None, uploads)

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        body = await request.json()
        msgs = body.get("messages") or []
        role_prompt = load_prompt(config, DEFAULT_ROLE_PROMPT)
        user_content: Any = ""
        for m in reversed(msgs):
            if m.get("role") == "user":
                user_content = m.get("content", "")
                break
        text = await call_model(config, role_prompt, user_content)
        import uuid
        return JSONResponse({"id": f"chatcmpl-{uuid.uuid4().hex[:24]}", "object": "chat.completion",
                             "model": config.model_alias,
                             "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                                          "finish_reason": "stop"}]})

    return app


class ScoringEvalTemplate(AppTemplate):
    app_type = "scoring_eval"
    title = "视觉评分引擎 / Scoring Eval"
    description = "VT 评分服务：/score_json、/score_upload，支持图片/视频/GIF、评分题与单选/多选题，自动解析与重试。"
    default_prompt = DEFAULT_ROLE_PROMPT

    def build_app(self, config: PortConfig):
        return build_scoring_app(config)
