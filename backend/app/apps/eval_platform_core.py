"""Ported visual-eval platform core (was D:\visual_eval_platform_..._field_match.py, ports 9100/9101).

This module is a faithful copy of the original deterministic analytics pipeline
(preprocess -> material scores -> drivers -> significance -> insight -> actions)
plus the project chat-context store. Only the I/O edges are re-wired:
- file paths now live under PORTHUB's data/ dir (not the script folder)
- the single LLM touchpoint `llm_chat` is delegated to a router-backed callable
  installed per-request via `set_llm` (a contextvar), so each port uses its own
  configured model alias instead of the old model_map.json + direct OneAPI/Ollama.
"""
from __future__ import annotations

import contextvars


import os
import json
import math
import copy
import time
import uuid
import traceback
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from app.core.config import DATA_DIR as _DATA_DIR
BASE_DIR = str(_DATA_DIR / "eval_platform")
LOG_DIR = os.path.join(BASE_DIR, "logs")
DEBUG_DIR = os.path.join(BASE_DIR, "debug_logs")
CTX_DIR = os.path.join(BASE_DIR, "project_contexts")
PROMPT_DIR = os.path.join(BASE_DIR, "prompts")
for d in [LOG_DIR, DEBUG_DIR, CTX_DIR, PROMPT_DIR]:
    os.makedirs(d, exist_ok=True)

SCORE_MIN = 0.0
SCORE_MAX = 10.0
DEFAULT_SIGNIFICANT_DIFF = 0.15
DEFAULT_MODEL_DISPLAY = "GPT-4o（在线）"

ONEAPI_BASE_URL = os.getenv("ONEAPI_BASE_URL", "http://127.0.0.1:2222/v1")
ONEAPI_API_KEY = os.getenv("ONEAPI_API_KEY", "sk-local")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "120"))
AI_RETRY_TIMES = int(os.getenv("AI_RETRY_TIMES", "3"))
CHAT_HISTORY_ROUNDS = int(os.getenv("CHAT_HISTORY_ROUNDS", "3"))
CHAT_MEMORY_MAX_CHARS = int(os.getenv("CHAT_MEMORY_MAX_CHARS", "1800"))
ENABLE_API_KEY_AUTH = os.getenv("ENABLE_API_KEY_AUTH", "0") == "1"
SERVICE_API_KEY = os.getenv("SERVICE_API_KEY", "")

MODEL_MAP_CANDIDATES = [
    os.path.join(BASE_DIR, "model_map.json"),
    os.path.join("/mnt/data", "model_map.json"),
]

analysis_prompt_default = """你是视觉评估平台的分析助手。
你会根据结构化统计结果，生成简洁、专业、可落地的中文分析。
输出必须是 JSON，不要输出 markdown，不要输出解释。
"""
chat_prompt_default = """你是视觉评估平台的项目问答助手。
你会基于项目的最新分析结果、项目上下文和历史问答，回答用户问题。
要求：
1. 只用中文回答；
2. 优先引用项目当前分析结果；
3. 简洁、准确；
4. 如果上下文中没有答案，要明确说没有足够信息；
5. 输出纯文本，不要 markdown 表格。
"""

def ensure_prompt_file(name: str, content: str):
    path = os.path.join(PROMPT_DIR, name)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

ensure_prompt_file("analysis_prompt.txt", analysis_prompt_default)
ensure_prompt_file("chat_prompt.txt", chat_prompt_default)

def load_prompt(name: str, default: str) -> str:
    path = os.path.join(PROMPT_DIR, name)
    if os.path.exists(path):
        return open(path, "r", encoding="utf-8").read().strip()
    return default

ANALYSIS_PROMPT = load_prompt("analysis_prompt.txt", analysis_prompt_default)
CHAT_PROMPT = load_prompt("chat_prompt.txt", chat_prompt_default)

import logging
logger = logging.getLogger("visual_eval_service")
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s")
    fh = logging.FileHandler(os.path.join(LOG_DIR, "service.log"), encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)

def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def new_rid() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]


def sanitize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    v8.3.1:
    前端不再需要传 channel。
    即使旧前端还传 channel，也会在服务端入口直接删除，不参与日志、不参与逻辑。
    模型路由只通过 model_map.json + model 字段完成。
    """
    if not isinstance(payload, dict):
        return payload
    cleaned = copy.deepcopy(payload)
    cleaned.pop("channel", None)
    return cleaned

def safe_json(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        return str(obj)

def dbg_write(dbg_path: str, title: str, content: Any):
    with open(dbg_path, "a", encoding="utf-8") as f:
        f.write("\n" + "=" * 80 + "\n")
        f.write(f"[{now_str()}] {title}\n")
        f.write("-" * 80 + "\n")
        if isinstance(content, str):
            f.write(content + "\n")
        else:
            f.write(safe_json(content) + "\n")

_MODEL_MAP_CACHE: Dict[str, Dict[str, Any]] = {}

def load_model_map() -> Dict[str, Dict[str, Any]]:
    """
    v8.3.1 clean-router:
    每次读取本地 model_map.json，避免改了 model_map 后必须重启。
    如果没有文件，使用最小默认映射。
    """
    for p in MODEL_MAP_CANDIDATES:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
    return {
        "GPT-4o（在线）": {"provider": "oneapi", "model": "gpt-4o-mini"},
        "gpt-4o-mini": {"provider": "oneapi", "model": "gpt-4o-mini"},
        "Gemma3（本地）": {"provider": "ollama", "model": "gemma3:latest"},
    }

def resolve_model(display_name: Optional[str]) -> Dict[str, Any]:
    """
    只通过 model 字段解析。
    display_name 可以是：
    1. model_map.json 里的显示名，例如 GPT-4o（在线）
    2. model_map.json 里的真实模型名，例如 gpt-4o-mini
    3. 未配置的真实模型名，默认按 oneapi 走
    """
    mm = load_model_map()
    name = display_name or DEFAULT_MODEL_DISPLAY

    # 1) 直接按显示名查
    cfg = mm.get(name)
    if cfg:
        return {
            "display_name": name,
            "provider": cfg.get("provider", "oneapi"),
            "model": cfg.get("model", name),
            "raw": cfg,
            "source": "model_map.display_name"
        }

    # 2) 允许传真实模型名，反查 model_map
    for display, item in mm.items():
        if str(item.get("model", "")).strip() == str(name).strip():
            return {
                "display_name": display,
                "provider": item.get("provider", "oneapi"),
                "model": item.get("model", name),
                "raw": item,
                "source": "model_map.real_model"
            }

    # 3) 找不到就当 oneapi 真实模型名
    return {
        "display_name": name,
        "provider": "oneapi",
        "model": name,
        "raw": {},
        "source": "direct_model_fallback"
    }

def ctx_path(project_id: str) -> str:
    return os.path.join(CTX_DIR, f"{project_id}.json")

def load_context(project_id: str) -> Dict[str, Any]:
    p = ctx_path(project_id)
    if os.path.exists(p):
        try:
            return json.load(open(p, "r", encoding="utf-8"))
        except Exception:
            pass
    return {
        "project_id": project_id,
        "latest_payload": None,
        "latest_analysis": None,
        "analysis_updated_at": None,
        "chat_history": [],
    }

def save_context(project_id: str, data: Dict[str, Any]):
    with open(ctx_path(project_id), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


CHOICE_TEXT_SCORE_MAP = {
    "非常好": 5, "很好": 5, "非常满意": 5, "很满意": 5, "非常喜欢": 5, "很喜欢": 5,
    "非常愿意": 5, "很愿意": 5, "非常高": 5, "很高": 5, "完全符合": 5, "非常符合": 5,
    "比较好": 4, "较好": 4, "满意": 4, "比较满意": 4, "喜欢": 4, "比较喜欢": 4,
    "愿意": 4, "比较愿意": 4, "比较高": 4, "较高": 4, "比较符合": 4,
    "一般": 3, "普通": 3, "中立": 3, "说不清": 3, "还可以": 3,
    "不太好": 2, "较差": 2, "比较差": 2, "不太满意": 2, "不太喜欢": 2,
    "不太愿意": 2, "比较低": 2, "较低": 2, "不太符合": 2,
    "非常不好": 1, "很不好": 1, "非常差": 1, "很差": 1, "非常不满意": 1, "很不满意": 1,
    "非常不喜欢": 1, "很不喜欢": 1, "完全不愿意": 1, "非常不愿意": 1,
    "非常低": 1, "很低": 1, "完全不符合": 1, "非常不符合": 1,

    # 购买意愿
    "肯定会购买": 5,
    "一定会购买": 5,
    "很可能购买": 4,
    "可能会购买": 4,
    "不确定": 3,
    "可能不会购买": 2,
    "肯定不会购买": 1,

    # 匹配度
    "非常匹配": 5,
    "很匹配": 5,
    "比较匹配": 4,
    "一般匹配": 3,
    "不太匹配": 2,
    "非常不匹配": 1,

    # 档次
    "非常有档次": 5,
    "很有档次": 5,
    "比较有档次": 4,
    "一般有档次": 3,
    "不太有档次": 2,
    "非常没档次": 1,
    "很没档次": 1,

    # 新颖度
    "非常新颖": 5,
    "很新颖": 5,
    "比较新颖": 4,
    "一般新颖": 3,
    "不太新颖": 2,
    "非常不新颖": 1,
}


def extract_question_text(qa: Dict[str, Any]) -> str:
    """
    兼容前端/后端不同字段名：
    - quetiontext：旧拼写
    - question_text：当前后端字段
    - questionText/question/question_title/title：其他常见写法
    """
    return str(
        qa.get("quetiontext")
        or qa.get("question_text")
        or qa.get("questionText")
        or qa.get("question")
        or qa.get("question_title")
        or qa.get("title")
        or ""
    ).strip()

def extract_answer_value(qa: Dict[str, Any]) -> Any:
    """
    v8.3.9:
    后端现在会传：
      answer: null
      answer_options: [{option_text: "整体设计 比较好", score: ""}]
    所以 answer 为空时，要从 answer_options 中取 score / option_score / option_text。
    """
    ans = qa.get("answer")
    if ans not in [None, ""]:
        return ans

    # 兼容其他答案字段
    for key in ["answer_text", "answerText", "value", "selected_text", "selectedText", "selected_option_text"]:
        v = qa.get(key)
        if v not in [None, ""]:
            return v

    opts = qa.get("answer_options")
    if not opts:
        opts = qa.get("answerOptions")
    if not opts:
        opts = qa.get("options")

    if isinstance(opts, dict):
        opts = [opts]

    if isinstance(opts, list):
        for opt in opts:
            if not isinstance(opt, dict):
                continue

            # 优先使用后端显式分数
            for score_key in ["score", "option_score", "optionScore", "value"]:
                score_val = opt.get(score_key)
                if score_val not in [None, ""]:
                    return score_val

            # 再使用选项文本
            for text_key in ["option_text", "optionText", "text", "label", "name"]:
                text_val = opt.get(text_key)
                if text_val not in [None, ""]:
                    return text_val

    return ans

def normalize_choice_text_answer(value: Any) -> Optional[float]:
    """
    v8.3.7:
    把单选文本答案转成数值分。
    非常好=5，比较好=4，一般=3，不太好=2，非常不好=1。
    """
    if value is None:
        return None

    if isinstance(value, dict):
        for key in ["text", "label", "name", "option", "value", "answer"]:
            if key in value:
                r = normalize_choice_text_answer(value.get(key))
                if r is not None:
                    return r

    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        s2 = s.replace(" ", "").replace("　", "")
        for prefix in ["A.", "B.", "C.", "D.", "E.", "A、", "B、", "C、", "D、", "E、", "1.", "2.", "3.", "4.", "5.", "1、", "2、", "3、", "4、", "5、"]:
            if s2.startswith(prefix):
                s2 = s2[len(prefix):]
        if s2 in CHOICE_TEXT_SCORE_MAP:
            return float(CHOICE_TEXT_SCORE_MAP[s2])
        for k, v in CHOICE_TEXT_SCORE_MAP.items():
            if k in s2:
                return float(v)
    return None

def clean_num(x: Any) -> Optional[float]:
    mapped = normalize_choice_text_answer(x)
    if mapped is not None:
        return mapped

    if x is None:
        return None
    if isinstance(x, bool):
        return None
    try:
        if isinstance(x, str):
            s = x.strip()
            if not s:
                return None
            s = s.replace("分", "").strip()
            return float(s)
        return float(x)
    except Exception:
        return None

def normalize_score(x: float) -> float:
    return round((x - SCORE_MIN) / (SCORE_MAX - SCORE_MIN), 4)

def strength_by_abs_corr(c: float) -> str:
    c = abs(c)
    if c >= 0.7:
        return "强"
    if c >= 0.5:
        return "中"
    return "弱"

def pearson(x: List[float], y: List[float]) -> float:
    n = min(len(x), len(y))
    if n < 2:
        return 0.0
    x = x[:n]
    y = y[:n]
    mx = sum(x) / n
    my = sum(y) / n
    num = sum((a - mx) * (b - my) for a, b in zip(x, y))
    denx = math.sqrt(sum((a - mx) ** 2 for a in x))
    deny = math.sqrt(sum((b - my) ** 2 for b in y))
    if denx == 0 or deny == 0:
        return 0.0
    return num / (denx * deny)

def round4(v: float) -> float:
    return round(float(v), 4)

def oneapi_chat(model: str, messages: List[Dict[str, str]], temperature: float = 0.3) -> str:
    url = ONEAPI_BASE_URL.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {ONEAPI_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages, "temperature": temperature}
    r = requests.post(url, headers=headers, json=payload, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"]

def ollama_chat(model: str, messages: List[Dict[str, str]], temperature: float = 0.3) -> str:
    url = OLLAMA_URL.rstrip("/") + "/api/chat"
    payload = {"model": model, "messages": messages, "stream": False, "options": {"temperature": temperature}}
    r = requests.post(url, json=payload, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    msg = data.get("message", {})
    return msg.get("content", "")

def llm_chat(display_name: Optional[str], messages: List[Dict[str, str]], temperature: float = 0.3) -> Tuple[str, Dict[str, Any]]:
    resolved = resolve_model(display_name)
    provider = resolved["provider"]
    model = resolved["model"]
    if provider == "ollama":
        txt = ollama_chat(model, messages, temperature=temperature)
    else:
        txt = oneapi_chat(model, messages, temperature=temperature)
    return txt, {"provider": provider, "model": model, "display_name": resolved["display_name"]}

def extract_json_object(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise ValueError("empty llm response")
    if text.startswith("```"):
        text = text.strip("`")
        if "\n" in text:
            text = text.split("\n", 1)[1]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("json object not found")
    return json.loads(text[start:end+1])

def normalize_materials(payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    res = {}
    for m in payload.get("materials", []):
        mid = str(m.get("material_id", "")).strip()
        if not mid:
            continue
        res[mid] = {
            "material_id": mid,
            "material_name": m.get("material_name", mid),
            "material_url": m.get("material_url"),
            "material_type": m.get("material_type"),
        }
    if not res:
        raise HTTPException(status_code=400, detail="materials 中没有合法素材")
    return res


DEFAULT_INDICATORS = [
    {"indicator_code": "AUTO_001", "indicator_name": "整体视觉吸引力"},
    {"indicator_code": "AUTO_002", "indicator_name": "信息识别清晰度"},
    {"indicator_code": "AUTO_003", "indicator_name": "产品匹配度"},
    {"indicator_code": "AUTO_004", "indicator_name": "包装档次感"},
    {"indicator_code": "AUTO_005", "indicator_name": "设计新颖度"},
]

def default_indicators_dict() -> Dict[str, Dict[str, Any]]:
    return {
        x["indicator_code"]: {
            "indicator_code": x["indicator_code"],
            "indicator_name": x["indicator_name"],
            "is_default": True
        }
        for x in DEFAULT_INDICATORS
    }

def default_drivers(sample_size: int = 0) -> List[Dict[str, Any]]:
    return [
        {
            "indicator_code": x["indicator_code"],
            "indicator_name": x["indicator_name"],
            "correlation": 0.0,
            "strength": "待计算",
            "sample_size": sample_size
        }
        for x in DEFAULT_INDICATORS
    ]

def default_significance() -> List[Dict[str, Any]]:
    return [
        {
            "indicator_name": x["indicator_name"],
            "result": "待补充评分后计算"
        }
        for x in DEFAULT_INDICATORS
    ]

def normalize_indicators(payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """
    v8.3.6:
    如果 indicators 为空，自动补默认指标。
    这样前后端仍然能拿到固定 drivers/significance 指标结构。
    """
    res = {}
    for it in payload.get("indicators", []) or []:
        code = str(it.get("indicator_code", "")).strip()
        name = str(it.get("indicator_name", "")).strip()
        if not code or not name:
            continue
        res[code] = {"indicator_code": code, "indicator_name": name, "is_default": False}

    if not res:
        return default_indicators_dict()

    return res

def get_question_material_ids(qa: Dict[str, Any], q: Dict[str, Any]) -> List[str]:
    raw = None
    if "materials_id" in qa:
        raw = qa.get("materials_id")
    elif "material_id" in qa:
        raw = qa.get("material_id")
    elif "materials_id" in q:
        raw = q.get("materials_id")
    else:
        raw = q.get("material_id")
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw if str(x).strip()]
    return [str(raw)]

def normalize_responses(payload: Dict[str, Any], indicators: Dict[str, Dict[str, Any]], materials: Dict[str, Dict[str, Any]], dbg_path: str) -> List[Dict[str, Any]]:
    rows = []
    mapping_log = []
    questionnaires = payload.get("questionnaires", []) or []
    no_indicator_mode = len(indicators) == 0

    for qi, q in enumerate(questionnaires):
        respondent = str(q.get("user_id", f"u_{qi}"))
        q_uid = f"q_{qi}"
        per_material_scores: Dict[str, Dict[str, float]] = {}
        qas = q.get("QuestionAndAnswers", []) or []

        for qa_idx, qa in enumerate(qas):
            code = str(qa.get("indicator_code", "")).strip()
            qtext = extract_question_text(qa)
            raw_answer_value = extract_answer_value(qa)
            ans = clean_num(raw_answer_value)
            mids = get_question_material_ids(qa, q)

            if not mids:
                mapping_log.append({
                    "user_id": respondent,
                    "status": "skip",
                    "reason": "material_id_not_found",
                    "indicator_code": code,
                    "quetiontext": qtext
                })
                continue

            if ans is None or ans < SCORE_MIN or ans > SCORE_MAX:
                mapping_log.append({
                    "user_id": respondent,
                    "status": "skip",
                    "reason": "answer_empty_or_out_of_range",
                    "indicator_code": code,
                    "answer": raw_answer_value,
                    "quetiontext": qtext
                })
                continue

            is_default_indicator_mode = all(v.get("is_default") for v in indicators.values()) if indicators else False

            if not no_indicator_mode and code not in indicators:
                if is_default_indicator_mode:
                    qtext_s = str(qtext or "")
                    if "清晰" in qtext_s or "识别" in qtext_s or "信息" in qtext_s:
                        code = "AUTO_002"
                    elif "匹配" in qtext_s or "符合" in qtext_s or "低温酸奶" in qtext_s or "购买意愿" in qtext_s or "购买" in qtext_s:
                        code = "AUTO_003"
                    elif "档次" in qtext_s or "高级" in qtext_s or "品质" in qtext_s or "质感" in qtext_s or "材质" in qtext_s:
                        code = "AUTO_004"
                    elif "新颖" in qtext_s or "创新" in qtext_s or "独特" in qtext_s:
                        code = "AUTO_005"
                    elif "瓶型" in qtext_s or "瓶身" in qtext_s or "瓶盖" in qtext_s or "视觉" in qtext_s or "整体设计" in qtext_s:
                        code = "AUTO_001"
                    else:
                        code = "AUTO_001"
                else:
                    mapping_log.append({
                        "user_id": respondent,
                        "status": "skip",
                        "reason": "indicator_code_not_found",
                        "indicator_code": code,
                        "quetiontext": qtext
                    })
                    continue

            if no_indicator_mode:
                code_for_row = f"__AUTO_Q_{qa_idx+1}"
                indicator_name_for_log = qtext or code_for_row
            else:
                code_for_row = code
                indicator_name_for_log = indicators[code]["indicator_name"]

            for mid in mids:
                if mid not in materials:
                    mapping_log.append({
                        "user_id": respondent,
                        "status": "skip",
                        "reason": "material_not_exists",
                        "material_id": mid,
                        "indicator_code": code,
                        "quetiontext": qtext
                    })
                    continue
                per_material_scores.setdefault(mid, {})
                per_material_scores[mid][code_for_row] = float(ans)
                mapping_log.append({
                    "user_id": respondent,
                    "status": "ok",
                    "material_id": mid,
                    "indicator_code": code_for_row,
                    "indicator_name": indicator_name_for_log,
                    "answer": float(ans),
                    "quetiontext": qtext
                })

        for mid, score_map in per_material_scores.items():
            if not score_map:
                continue
            rows.append({
                "respondent_id": respondent,
                "questionnaire_uid": q_uid,
                "material_id": mid,
                "scores_raw": score_map,
                "submit_time": q.get("submit_time"),
                "extra": {}
            })

    dbg_write(dbg_path, "QUESTION_INDICATOR_MAPPING", mapping_log)
    dbg_write(dbg_path, "NORMALIZED_RESPONSE_ROWS", rows)
    return rows

def build_default_weights(indicators: Dict[str, Dict[str, Any]]) -> Dict[str, float]:
    names = [v["indicator_name"] for v in indicators.values()]
    each = round(1.0 / max(1, len(names)), 6)
    return {name: each for name in names}

def ai_fill_weights_if_needed(payload: Dict[str, Any], indicators: Dict[str, Dict[str, Any]], dbg_path: str) -> Tuple[Dict[str, float], Dict[str, Any]]:
    client_weights = payload.get("weights") or {}
    client_weights = {str(k): float(v) for k, v in client_weights.items() if clean_num(v) is not None}
    indicator_names = [v["indicator_name"] for v in indicators.values()]
    default_weights = build_default_weights(indicators)
    if set(client_weights.keys()) >= set(indicator_names):
        used = {k: client_weights[k] for k in indicator_names}
        total = sum(used.values()) or 1.0
        used = {k: round(v / total, 6) for k, v in used.items()}
        return used, {
            "weight_source": "client",
            "client_weights": client_weights,
            "default_weights": default_weights,
            "ai_filled_weights": {},
            "missing_indicator_names": []
        }
    missing = [n for n in indicator_names if n not in client_weights]
    remain = max(0.0, 1.0 - sum(client_weights.values()))
    ai_filled = {}
    source = "default"
    if missing:
        try:
            ask = {
                "scene": payload.get("scene"),
                "project_name": payload.get("project_name"),
                "indicator_names": missing,
                "remain_weight_total": remain,
                "required_output_json": {"weights": {n: 0.0 for n in missing}}
            }
            messages = [
                {"role": "system", "content": "你是权重分配助手。只输出 JSON。总和必须等于 remain_weight_total，且所有值>=0。"},
                {"role": "user", "content": safe_json(ask)}
            ]
            txt, meta = llm_chat(payload.get("model"), messages, temperature=0.2)
            obj = extract_json_object(txt)
            got = obj.get("weights", {})
            ai_filled = {k: float(got[k]) for k in missing if clean_num(got.get(k)) is not None}
            if set(ai_filled.keys()) == set(missing):
                s = sum(ai_filled.values()) or 1.0
                ai_filled = {k: (v / s) * remain for k, v in ai_filled.items()}
                source = "partial+ai_fill" if client_weights else "ai"
            dbg_write(dbg_path, "AI_WEIGHT_FILL", {"meta": meta, "raw": txt, "parsed": obj})
        except Exception:
            dbg_write(dbg_path, "AI_WEIGHT_FILL_FAILED", {"traceback": traceback.format_exc()})
    used = {}
    if source in ("ai", "partial+ai_fill"):
        used.update(client_weights)
        used.update(ai_filled)
    else:
        used = client_weights.copy()
        miss_defaults = {k: default_weights[k] for k in missing}
        scale = remain / (sum(miss_defaults.values()) or 1.0) if missing else 1.0
        for k, v in miss_defaults.items():
            used[k] = v * scale
        source = "default" if not client_weights else "partial+default_fill"
    total = sum(used.values()) or 1.0
    used = {k: round(v / total, 6) for k, v in used.items()}
    return used, {
        "weight_source": source,
        "client_weights": client_weights,
        "default_weights": default_weights,
        "ai_filled_weights": {k: round(v, 6) for k, v in ai_filled.items()},
        "missing_indicator_names": missing
    }

def preprocess_module(payload: Dict[str, Any], dbg_path: str) -> Dict[str, Any]:
    indicators = normalize_indicators(payload)
    materials = normalize_materials(payload)
    responses = normalize_responses(payload, indicators, materials, dbg_path)
    weights_used, weight_meta = ai_fill_weights_if_needed(payload, indicators, dbg_path)
    normalized_rows = []
    sample_count_by_material = {}
    qids_total, qids_valid = set(), set()
    for row in responses:
        qid = row.get("questionnaire_uid")
        if qid:
            qids_total.add(qid)
        norm = {code: normalize_score(v) for code, v in row["scores_raw"].items()}
        row2 = copy.deepcopy(row)
        row2["scores_norm"] = norm
        normalized_rows.append(row2)
        sample_count_by_material[row["material_id"]] = sample_count_by_material.get(row["material_id"], 0) + 1
        if qid:
            qids_valid.add(qid)
    total_questionnaires = len(payload.get("questionnaires", []))
    valid_questionnaires = len(qids_valid) if qids_valid else total_questionnaires
    invalid_questionnaires = max(0, total_questionnaires - valid_questionnaires)
    summary = {
        "total_samples": len(responses),
        "valid_samples": len(responses),
        "invalid_samples": 0,
        "excluded_outlier_samples": 0,
        "included_outlier_samples": 0,
        "effective_samples": len(normalized_rows),
        "effective_rate": 1.0 if responses else 0.0,
        "sample_count_by_material": sample_count_by_material,
        "total_questionnaires": total_questionnaires,
        "valid_questionnaires": valid_questionnaires,
        "invalid_questionnaires": invalid_questionnaires,
    }
    dbg_write(dbg_path, "PREPROCESS SUMMARY", summary)
    resolved = resolve_model(payload.get("model"))
    dbg_write(dbg_path, "WEIGHT META", {
        "scene": payload.get("scene"),
        "project_id": payload.get("project_id"),
        "project_name": payload.get("project_name"),
        "provider": resolved["provider"],
        "model": resolved["model"],
        "score_range": {"min": SCORE_MIN, "max": SCORE_MAX},
        "weights_used": weights_used,
        **weight_meta
    })
    return {
        "payload": payload,
        "indicators": indicators,
        "materials": materials,
        "rows": normalized_rows,
        "summary": summary,
        "weights_used": weights_used,
        "weight_meta": weight_meta,
    }

def compute_material_scores(pre: Dict[str, Any]) -> List[Dict[str, Any]]:
    indicators, materials, weights_used = pre["indicators"], pre["materials"], pre["weights_used"]
    by_material = {mid: [] for mid in materials.keys()}
    for row in pre["rows"]:
        by_material[row["material_id"]].append(row)
    out = []
    for mid, rows in by_material.items():
        if not rows:
            continue
        indicator_means = {}
        for code, meta in indicators.items():
            vals = [r["scores_norm"].get(code) for r in rows if code in r["scores_norm"]]
            indicator_means[code] = round4(sum(vals) / len(vals)) if vals else 0.0
        overall = 0.0
        for code, meta in indicators.items():
            overall += indicator_means[code] * weights_used.get(meta["indicator_name"], 0.0)
        out.append({
            "material_id": mid,
            "material_name": materials[mid]["material_name"],
            "overall_mean": round4(overall),
            "indicator_means": indicator_means,
            "sample_size": len(rows),
        })
    out.sort(key=lambda x: x["overall_mean"], reverse=True)
    for i, m in enumerate(out, start=1):
        m["rank"] = i
    return out

def driver_module(pre: Dict[str, Any], dbg_path: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    indicators, materials = pre["indicators"], pre["materials"]
    by_material = {mid: [] for mid in materials.keys()}
    for row in pre["rows"]:
        row["_overall_row"] = sum(row["scores_norm"].values()) / max(1, len(row["scores_norm"]))
        by_material[row["material_id"]].append(row)
    per_material = []
    for mid, rows in by_material.items():
        for code, meta in indicators.items():
            xs = [r["scores_norm"].get(code, 0.0) for r in rows]
            ys = [r["_overall_row"] for r in rows]
            corr = abs(pearson(xs, ys))
            per_material.append({
                "material_id": mid,
                "material_name": materials[mid]["material_name"],
                "indicator_code": code,
                "indicator_name": meta["indicator_name"],
                "correlation": round4(corr),
                "r2": round4(corr * corr),
                "drive_level": strength_by_abs_corr(corr),
                "sample_size": len(rows)
            })
    dbg_write(dbg_path, "DRIVER TABLE PREVIEW", per_material)
    global_rows = []
    for code, meta in indicators.items():
        xs, ys = [], []
        for row in pre["rows"]:
            xs.append(row["scores_norm"].get(code, 0.0))
            ys.append(row["_overall_row"])
        corr = abs(pearson(xs, ys))
        global_rows.append({
            "indicator_code": code,
            "indicator_name": meta["indicator_name"],
            "correlation": round4(corr),
            "strength": strength_by_abs_corr(corr),
            "sample_size": len(xs)
        })
    global_rows.sort(key=lambda x: x["correlation"], reverse=True)
    return per_material, global_rows

def significance_module(pre: Dict[str, Any], material_scores: List[Dict[str, Any]], dbg_path: str) -> List[Dict[str, Any]]:
    indicators = pre["indicators"]
    threshold = ((pre["payload"].get("threshold") or {}).get("significant_diff")) or DEFAULT_SIGNIFICANT_DIFF
    by_id = {m["material_id"]: m for m in material_scores}
    out, preview = [], []
    for code, meta in indicators.items():
        pairs = [(mid, m["material_name"], m["indicator_means"].get(code, 0.0)) for mid, m in by_id.items()]
        pairs.sort(key=lambda x: x[2], reverse=True)
        if len(pairs) < 2:
            out.append({"indicator_name": meta["indicator_name"], "result": "无对比素材"})
            continue
        diff = round4(pairs[0][2] - pairs[-1][2])
        if diff >= threshold:
            out.append({
                "indicator_name": meta["indicator_name"],
                "winner": pairs[0][1],
                "loser": pairs[-1][1],
                "diff": diff,
                "result": "显著优势"
            })
            preview.append({
                "indicator_code": code,
                "indicator_name": meta["indicator_name"],
                "type": "显著优势/短板",
                "difference": diff,
                "message": f"{pairs[0][1]} 在「{meta['indicator_name']}」上显著高于 {pairs[-1][1]}，差值 {diff}"
            })
        else:
            out.append({"indicator_name": meta["indicator_name"], "result": "差异不明显"})
            preview.append({
                "indicator_code": code,
                "indicator_name": meta["indicator_name"],
                "type": "无显著差异",
                "difference": diff,
                "message": f"各素材在「{meta['indicator_name']}」上的差距未达到显著阈值 {threshold}"
            })
    dbg_write(dbg_path, "SIGNIFICANCE PREVIEW", preview)
    return out

def build_rule_summary(pre: Dict[str, Any], material_scores: List[Dict[str, Any]], global_drivers: List[Dict[str, Any]]) -> str:
    qs = pre["summary"]["valid_questionnaires"]
    rate = round(pre["summary"]["valid_questionnaires"] / max(1, pre["summary"]["total_questionnaires"]) * 100, 1)
    if not material_scores:
        return f"本次共回收有效问卷{qs}份，有效率{rate}%。暂无可分析结果。"
    best = material_scores[0]
    top2 = global_drivers[:2]
    driver_names = "与".join([x["indicator_name"] for x in top2]) if top2 else "核心指标"
    txt = f"本次共回收有效问卷{qs}份，有效率{rate}%。{best['material_name']}整体表现最佳（平均分{best['overall_mean']}），{driver_names}为主要驱动指标，建议作为当前主方案。"
    if len(material_scores) >= 2:
        worst = material_scores[-1]
        weak_name = global_drivers[2]["indicator_name"] if len(global_drivers) >= 3 else (top2[-1]["indicator_name"] if top2 else "关键指标")
        txt += f"{worst['material_name']}整体表现相对较弱（平均分{worst['overall_mean']}），「{weak_name}」存在不足。"
    return txt

def ai_summary_if_possible(pre: Dict[str, Any], material_scores: List[Dict[str, Any]], global_drivers: List[Dict[str, Any]], dbg_path: str) -> Tuple[str, str]:
    rule_text = build_rule_summary(pre, material_scores, global_drivers)
    if not pre["payload"].get("auto_ai_summary", True):
        return rule_text, "rule_based_only"
    try:
        ask = {
            "project_id": pre["payload"].get("project_id"),
            "project_name": pre["payload"].get("project_name"),
            "scene": pre["payload"].get("scene"),
            "valid_questionnaires": pre["summary"]["valid_questionnaires"],
            "valid_rate": round(pre["summary"]["valid_questionnaires"] / max(1, pre["summary"]["total_questionnaires"]) * 100, 1),
            "material_scores": material_scores,
            "drivers_top3": global_drivers[:3],
            "required_style": "简洁中文摘要，2-3句，适合直接展示到网页 summary.ai_summary 字段"
        }
        messages = [
            {"role": "system", "content": ANALYSIS_PROMPT + "\n直接输出一段中文摘要，不要 JSON。"},
            {"role": "user", "content": safe_json(ask)}
        ]
        txt, meta = llm_chat(pre["payload"].get("model"), messages, temperature=0.3)
        txt = (txt or "").strip()
        if not txt:
            raise ValueError("empty ai summary")
        dbg_write(dbg_path, "AI_SUMMARY_SUCCESS", {"meta": meta, "raw": txt})
        return txt, "ai"
    except Exception:
        dbg_write(dbg_path, "AI_SUMMARY_FAILED", {"traceback": traceback.format_exc()})
        return rule_text, "rule_based_fallback"

def insight_module(pre: Dict[str, Any], per_material_drivers: List[Dict[str, Any]], global_drivers: List[Dict[str, Any]], material_scores: List[Dict[str, Any]], dbg_path: str) -> Dict[str, Any]:
    summary_text, summary_mode = ai_summary_if_possible(pre, material_scores, global_drivers, dbg_path)
    best = material_scores[0] if material_scores else None
    out = {
        "summary_mode": summary_mode,
        "summary_text": summary_text,
        "summary_text_rule_based": build_rule_summary(pre, material_scores, global_drivers),
        "best_material": {
            "material_id": best["material_id"],
            "material_name": best["material_name"],
            "overall_mean": best["overall_mean"],
        } if best else None,
        "core_drivers": [{"indicator_code": x["indicator_code"], "indicator_name": x["indicator_name"], "r2": round4(x["correlation"] ** 2)} for x in global_drivers[:2]],
        "explain_rate": round4(sum((x["correlation"] ** 2) for x in global_drivers[:2])),
        "material_briefs": [
            {
                "material_id": m["material_id"],
                "material_name": m["material_name"],
                "overall_mean": m["overall_mean"],
                "level": "最优" if i == 0 else ("较弱" if i == len(material_scores)-1 else "中等"),
                "highlights": [pre["indicators"][c]["indicator_name"] for c, _ in sorted(m["indicator_means"].items(), key=lambda kv: kv[1], reverse=True)[:2]],
                "weaknesses": [pre["indicators"][c]["indicator_name"] for c, _ in sorted(m["indicator_means"].items(), key=lambda kv: kv[1])[:2]],
            } for i, m in enumerate(material_scores)
        ],
        "material_indicator_means": {m["material_id"]: {k: round4(v) for k, v in m["indicator_means"].items()} for m in material_scores},
        "ranking": [
            {
                "material_id": m["material_id"],
                "material_name": m["material_name"],
                "overall_mean": m["overall_mean"],
                "strengths": [{"indicator_code": c, "indicator_name": pre["indicators"][c]["indicator_name"], "score": round4(v)} for c, v in sorted(m["indicator_means"].items(), key=lambda kv: kv[1], reverse=True)[:2]],
                "weaknesses": [{"indicator_code": c, "indicator_name": pre["indicators"][c]["indicator_name"], "score": round4(v)} for c, v in sorted(m["indicator_means"].items(), key=lambda kv: kv[1])[:2]],
                "top_drivers": [x for x in per_material_drivers if x["material_id"] == m["material_id"]][:3]
            } for m in material_scores
        ]
    }
    dbg_write(dbg_path, "AI INSIGHT", out)
    return out


def action_item_to_text(item: Any) -> str:
    """固定 actions 数组为前端可接收的字符串。"""
    if item is None:
        return ""
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        material = str(item.get("material_name") or item.get("material") or item.get("target_material_name") or item.get("target") or "").strip()
        indicator = str(item.get("indicator_name") or item.get("indicator") or item.get("dimension") or "").strip()
        suggestion = str(item.get("suggestion") or item.get("action") or item.get("advice") or item.get("text") or "").strip()
        if material and indicator and suggestion:
            return f"{material}建议提升{indicator}：{suggestion}"
        if material and indicator:
            return f"{material}建议提升{indicator}"
        if material and suggestion:
            return f"{material}：{suggestion}"
        if indicator and suggestion:
            return f"建议提升{indicator}：{suggestion}"
        if suggestion:
            return suggestion
        return json.dumps(item, ensure_ascii=False)
    if isinstance(item, list):
        return "；".join([action_item_to_text(x) for x in item if action_item_to_text(x)])
    return str(item).strip()

def normalize_action_list(items: Any) -> List[str]:
    if items is None:
        return []
    if isinstance(items, (str, dict)):
        items = [items]
    if not isinstance(items, list):
        items = [items]
    out = []
    for x in items:
        text = action_item_to_text(x)
        if text:
            out.append(text)
    return out

def normalize_actions_schema(actions: Dict[str, Any], fallback: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    fallback = fallback or {}
    if not isinstance(actions, dict):
        actions = {}
    main_plan = action_item_to_text(actions.get("main_plan")) or action_item_to_text(fallback.get("main_plan"))
    priority = normalize_action_list(actions.get("priority")) or normalize_action_list(fallback.get("priority"))
    weak_material_fix = normalize_action_list(actions.get("weak_material_fix")) or normalize_action_list(fallback.get("weak_material_fix"))
    return {
        "main_plan": main_plan,
        "priority": priority,
        "weak_material_fix": weak_material_fix,
    }

def build_rule_actions(pre: Dict[str, Any], material_scores: List[Dict[str, Any]], global_drivers: List[Dict[str, Any]], significance: List[Dict[str, Any]]) -> Dict[str, Any]:
    best = material_scores[0] if material_scores else None
    weak = material_scores[-1] if material_scores else None
    main_plan = f"建议采用{best['material_name']}作为主方案" if best else "建议基于当前数据继续观察"
    priority = [f"优先提升{d['indicator_name']}相关元素" for d in global_drivers[:2]]
    weak_fix = []
    if weak:
        for code, _ in sorted(weak["indicator_means"].items(), key=lambda kv: kv[1])[:2]:
            weak_fix.append(f"{weak['material_name']}建议提升{pre['indicators'][code]['indicator_name']}")
    for d in [d for d in global_drivers if d["strength"] == "弱"][:2]:
        priority.append(f"若资源有限，可弱化对{d['indicator_name']}的过度优化")
    return {"main_plan": main_plan, "priority": priority, "weak_material_fix": weak_fix}

def ai_actions_if_possible(pre: Dict[str, Any], material_scores: List[Dict[str, Any]], global_drivers: List[Dict[str, Any]], significance: List[Dict[str, Any]], dbg_path: str) -> Tuple[Dict[str, Any], str]:
    """
    AI 优先生成行动建议；但必须经过强校验：
    1. 不允许照抄示例素材名；
    2. 不允许出现当前项目不存在的素材名；
    3. 不允许出现当前项目不存在的指标名；
    4. 校验失败自动回退本地规则，避免前端展示“素材A/素材B”这种假结果。
    """
    rule = build_rule_actions(pre, material_scores, global_drivers, significance)

    material_names = [m["material_name"] for m in material_scores]
    indicator_names = [v["indicator_name"] for v in pre["indicators"].values()]
    best_name = material_scores[0]["material_name"] if material_scores else ""
    single_material = len(material_scores) <= 1

    def contains_fake_or_unknown(text: str) -> bool:
        """
        v8.3.2 修复点：
        单素材项目里，AI 说“无需针对其他素材进行优化建议 / 暂无其他素材可对比”
        这是正确表达，不再误判为幻觉。
        仍然禁止 AI 编造具体不存在的“素材A/素材B/素材C”。
        """
        if not text:
            return False

        fake_names = ["素材A", "素材B", "素材C", "A素材", "B素材", "C素材"]
        for fake in fake_names:
            if fake in text and fake not in material_names:
                return True

        if single_material and "其他素材" in text:
            allowed_no_other_material_phrases = [
                "无需针对其他素材",
                "不需要针对其他素材",
                "暂无其他素材",
                "没有其他素材",
                "无其他素材",
                "当前没有其他素材",
                "当前仅一个素材",
                "当前只有一个素材",
                "仅有一个素材",
                "只有一个素材",
                "暂无对比素材",
                "没有对比素材",
                "无对比素材",
                "无需与其他素材对比",
                "不涉及其他素材",
            ]
            if any(p in text for p in allowed_no_other_material_phrases):
                return False

        if ("素材" in text or "海报" in text or "主方案" in text) and material_names:
            allowed_generic = [
                "当前素材", "该素材", "本素材",
                "当前海报", "该海报", "本海报",
                "其他素材", "对比素材", "弱势素材"
            ]
            if any(g in text for g in allowed_generic):
                return False
            if not any(n in text for n in material_names):
                return True

        return False

    def validate_actions(obj: Dict[str, Any]) -> Tuple[bool, str]:
        if not isinstance(obj, dict):
            return False, "not_dict"
        obj = normalize_actions_schema(obj, rule)
        if not isinstance(obj.get("main_plan"), str):
            return False, "main_plan_not_string"
        if not isinstance(obj.get("priority"), list):
            return False, "priority_not_list"
        if not isinstance(obj.get("weak_material_fix"), list):
            return False, "weak_material_fix_not_list"

        all_texts = [obj.get("main_plan", "")]
        all_texts += [str(x) for x in obj.get("priority", [])]
        all_texts += [str(x) for x in obj.get("weak_material_fix", [])]

        for t in all_texts:
            if contains_fake_or_unknown(t):
                return False, f"fake_or_unknown_material_name: {t}"

        if single_material:
            for t in obj.get("weak_material_fix", []):
                t = str(t)
                allowed_no_other_material_phrases = [
                    "无需针对其他素材",
                    "不需要针对其他素材",
                    "暂无其他素材",
                    "没有其他素材",
                    "无其他素材",
                    "当前没有其他素材",
                    "当前仅一个素材",
                    "当前只有一个素材",
                    "仅有一个素材",
                    "只有一个素材",
                    "暂无对比素材",
                    "没有对比素材",
                    "无对比素材",
                    "无需与其他素材对比",
                    "不涉及其他素材",
                ]
                if any(p in t for p in allowed_no_other_material_phrases):
                    continue
                if any(x in t for x in ["另一个素材", "弱素材", "素材A", "素材B", "素材C"]):
                    return False, f"single_material_bad_fix: {t}"

        return True, "ok"

    try:
        payload = {
            "scene": pre["payload"].get("scene"),
            "project_name": pre["payload"].get("project_name"),
            "materials_allowed": material_names,
            "indicators_allowed": indicator_names,
            "best_material_name": best_name,
            "material_scores": material_scores,
            "global_drivers_top3": global_drivers[:3],
            "significance": significance,
            "output_schema": {
                "main_plan": "必须使用 materials_allowed 中真实存在的素材名；如果只有一个素材，就写“建议继续以XXX作为当前优化对象”",
                "priority": ["只能围绕 indicators_allowed 中真实存在的指标给建议"],
                "weak_material_fix": ["只能针对真实存在的素材和指标给建议；单素材时不要编造素材A/素材B"]
            },
            "hard_rules": [
                "只输出 JSON，不要 markdown，不要解释",
                "禁止输出素材A、素材B、素材C，除非它们就是 materials_allowed 里的真实素材名",
                "禁止输出不存在的素材名",
                "禁止输出不存在的指标名",
                "如果只有一个素材，不要写对比其他素材，只写当前素材优化建议"
            ]
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "你是视觉评估行动建议助手。"
                    "必须严格基于用户给出的真实素材名和指标名生成建议。"
                    "只输出 JSON，字段必须是 main_plan, priority, weak_material_fix。"
                    "不要照抄示例，不要编造素材名。"
                )
            },
            {"role": "user", "content": safe_json(payload)}
        ]
        txt, meta = llm_chat(pre["payload"].get("model"), messages, temperature=0.2)
        obj = extract_json_object(txt)

        ok, reason = validate_actions(obj)
        if not ok:
            raise ValueError(f"invalid ai actions content: {reason}")

        parsed = normalize_actions_schema(obj, rule)
        dbg_write(dbg_path, "AI_ACTION_SUCCESS", {"meta": meta, "raw": txt, "parsed": parsed, "validation": "ok"})
        return parsed, "ai"
    except Exception as e:
        dbg_write(dbg_path, "AI_ACTION_FAILED", {"error": str(e), "traceback": traceback.format_exc(), "fallback": rule})
        return rule, "rule_based_fallback"

def action_module(pre: Dict[str, Any], material_scores: List[Dict[str, Any]], global_drivers: List[Dict[str, Any]], significance: List[Dict[str, Any]], dbg_path: str) -> Dict[str, Any]:
    actions, mode = ai_actions_if_possible(pre, material_scores, global_drivers, significance, dbg_path)
    actions = normalize_actions_schema(actions, build_rule_actions(pre, material_scores, global_drivers, significance))
    best = material_scores[0] if material_scores else None
    out = {
        "scene": pre["payload"].get("scene"),
        "recommended_primary_material": {
            "material_id": best["material_id"],
            "material_name": best["material_name"],
            "overall_mean": best["overall_mean"]
        } if best else None,
        "main_plan": actions["main_plan"],
        "priority": actions["priority"],
        "weak_material_fix": actions["weak_material_fix"],
        "action_mode": mode,
    }
    dbg_write(dbg_path, "ACTION PLAN", out)
    return out


def build_no_indicator_or_no_data_response(payload: Dict[str, Any], reason: str, dbg_path: str) -> Dict[str, Any]:
    materials = []
    for i, m in enumerate(payload.get("materials", []) or [], start=1):
        mid = str(m.get("material_id", "")).strip()
        if not mid:
            continue
        materials.append({
            "material_id": mid,
            "material_name": m.get("material_name", mid),
            "overall_score": 0.0,
            "rank": i
        })

    questionnaires = payload.get("questionnaires", []) or []
    total_questionnaires = len(questionnaires)
    valid_questionnaires = 0
    for q in questionnaires:
        qas = q.get("QuestionAndAnswers", []) or []
        has_numeric = False
        for qa in qas:
            ans = clean_num(qa.get("answer"))
            if ans is not None and SCORE_MIN <= ans <= SCORE_MAX:
                has_numeric = True
                break
        if has_numeric:
            valid_questionnaires += 1

    msg_map = {
        "no_indicators": "当前项目已生成默认分析维度，暂无可用于计算的有效评分数据。",
        "no_numeric_answers": "当前项目暂无可用于计算的有效评分数据，已保留分析维度供页面展示。",
        "no_materials": "当前请求没有有效素材，无法完成评分统计分析。"
    }
    ai_summary = msg_map.get(reason, "当前请求数据不足，已返回安全默认结果。")

    resp = {
        "code": 200,
        "msg": "success",
        "project_id": str(payload.get("project_id", "")),
        "project_name": payload.get("project_name", ""),
        "update_time": now_str(),
        "summary": {
            "valid_samples": valid_questionnaires,
            "valid_rate": round(valid_questionnaires / max(1, total_questionnaires) * 100, 1),
            "best_material": None,
            "best_score": None,
            "worst_material": None,
            "worst_score": None,
            "ai_summary": ai_summary
        },
        "material_scores": materials,
        "drivers": default_drivers(valid_questionnaires),
        "significance": default_significance(),
        "actions": {
            "main_plan": "当前项目暂无有效评分数据，建议等待问卷评分回收后刷新分析",
            "priority": [
                "等待问卷评分数据回收后自动更新分析结果",
                "优先关注整体视觉吸引力、信息识别清晰度与产品匹配度",
                "评分数据补齐后可进一步判断素材优劣与优化方向"
            ],
            "weak_material_fix": []
        }
    }
    dbg_write(dbg_path, "SAFE DEFAULT INDICATOR RESPONSE", {"reason": reason, "response": resp})
    return resp

def analyze(payload: Dict[str, Any], rid: str, dbg_path: str) -> Dict[str, Any]:
    original_has_indicators = bool(payload.get("indicators"))

    pre = preprocess_module(payload, dbg_path)

    # v8.3.9: 没有任何可用评分时，才返回带默认指标的固定结构；
    # 如果 answer_options.option_text 已被成功换算成分数，则继续正常统计。
    if not pre.get("rows"):
        return build_no_indicator_or_no_data_response(
            payload,
            "no_indicators" if not original_has_indicators else "no_numeric_answers",
            dbg_path
        )

    material_scores = compute_material_scores(pre)
    per_material_drivers, global_drivers = driver_module(pre, dbg_path)
    significance = significance_module(pre, material_scores, dbg_path)
    insight = insight_module(pre, per_material_drivers, global_drivers, material_scores, dbg_path)
    actions = action_module(pre, material_scores, global_drivers, significance, dbg_path)

    response = {
        "code": 200,
        "msg": "success",
        "project_id": str(payload.get("project_id", "")),
        "project_name": payload.get("project_name", ""),
        "update_time": now_str(),
        "summary": {
            "valid_samples": pre["summary"]["valid_questionnaires"],
            "valid_rate": round(pre["summary"]["valid_questionnaires"] / max(1, pre["summary"]["total_questionnaires"]) * 100, 1),
            "best_material": material_scores[0]["material_name"] if material_scores else None,
            "best_score": material_scores[0]["overall_mean"] if material_scores else None,
            "worst_material": material_scores[-1]["material_name"] if len(material_scores) > 1 else None,
            "worst_score": material_scores[-1]["overall_mean"] if len(material_scores) > 1 else None,
            "ai_summary": insight["summary_text"],
        },
        "material_scores": [
            {
                "material_id": m["material_id"],
                "material_name": m["material_name"],
                "overall_score": m["overall_mean"],
                "rank": m["rank"],
            } for m in material_scores
        ],
        "drivers": global_drivers,
        "significance": significance,
        "actions": {
            "main_plan": actions["main_plan"],
            "priority": actions["priority"],
            "weak_material_fix": actions["weak_material_fix"],
        }
    }
    dbg_write(dbg_path, "RESPONSE PREVIEW", response)
    project_id = str(payload.get("project_id", "")).strip()
    if project_id:
        ctx = load_context(project_id)
        ctx["latest_payload"] = payload
        ctx["latest_analysis"] = response
        ctx["analysis_updated_at"] = response["update_time"]
        save_context(project_id, ctx)
    return response


def check_api_key(x_api_key: Optional[str] = None):
    if not ENABLE_API_KEY_AUTH:
        return
    if not SERVICE_API_KEY:
        raise HTTPException(status_code=500, detail="服务端已启用鉴权，但未配置 SERVICE_API_KEY")
    if x_api_key != SERVICE_API_KEY:
        raise HTTPException(status_code=401, detail="无效的 X-API-Key")

def compact_analysis_for_chat(analysis: Dict[str, Any]) -> Dict[str, Any]:
    if not analysis:
        return {}
    return {
        "project_id": analysis.get("project_id"),
        "project_name": analysis.get("project_name"),
        "summary": analysis.get("summary"),
        "material_scores": (analysis.get("material_scores") or [])[:5],
        "drivers": (analysis.get("drivers") or [])[:8],
        "significance": (analysis.get("significance") or [])[:8],
        "actions": analysis.get("actions"),
    }

def compact_payload_for_chat(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not payload:
        return {}
    return {
        "project_id": payload.get("project_id"),
        "project_name": payload.get("project_name"),
        "scene": payload.get("scene"),
        "materials": payload.get("materials", []),
        "indicators": payload.get("indicators", []),
    }

def update_chat_memory_summary(ctx: Dict[str, Any], question: str, answer: str):
    old = ctx.get("chat_memory_summary", "") or ""
    item = f"用户问：{question}\n助手答：{answer}\n"
    merged = (old + "\n" + item).strip()
    if len(merged) > CHAT_MEMORY_MAX_CHARS:
        merged = merged[-CHAT_MEMORY_MAX_CHARS:]
    ctx["chat_memory_summary"] = merged



# ---------------------------------------------------------------------------
# LLM delegation: the template installs a router-backed callable per request.
# Signature: llm(display_name, messages, temperature) -> (text, meta_dict)
# ---------------------------------------------------------------------------
_LLM_VAR: "contextvars.ContextVar" = contextvars.ContextVar("eval_platform_llm", default=None)


def set_llm(fn) -> None:
    _LLM_VAR.set(fn)


def llm_chat(display_name, messages, temperature: float = 0.3):  # noqa: F811 - override
    fn = _LLM_VAR.get()
    if fn is None:
        raise HTTPException(status_code=500, detail="LLM not configured for this port")
    return fn(display_name, messages, temperature)
