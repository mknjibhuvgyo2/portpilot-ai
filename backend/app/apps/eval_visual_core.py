"""Ported visual-eval service core (was Z:\0509\visual_eval_service.py, port 18081).

Faithful copy of the scene-based visual evaluation engine (score JSON + formal
report, multi-material compare, sqlite persistence off by default). Only the
edges are re-wired: file paths live under PORTHUB's data/ dir, and the single LLM
dispatch `call_model` is delegated to a router-backed callable installed per
request via `set_llm` (contextvar). Media fetching still uses requests directly.
"""
from __future__ import annotations

import contextvars

# -*- coding: utf-8 -*-
"""
Visual Evaluation AI Service

Requirements:
  pip install fastapi uvicorn requests openai python-multipart

Run API:
  python visual_eval_service.py
  or: uvicorn visual_eval_service:app --host 0.0.0.0 --port 18081

Manual CLI:
  python visual_eval_service.py --cli
"""

import base64
import json
import mimetypes
import os
import re
import sqlite3
import subprocess
import tempfile
import threading
import traceback
from io import BytesIO
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, unquote, urlsplit, urlunsplit
from uuid import uuid4

import requests
from fastapi import HTTPException

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:
    Image = None
    ImageDraw = None
    ImageFont = None

NO_PROXY_SESSION = requests.Session()
NO_PROXY_SESSION.trust_env = False
OLLAMA_LOCK = threading.Lock()


# =============================================================================
# 1) 配置 / Config
# =============================================================================
from app.core.config import DATA_DIR as _DATA_DIR
BASE_DIR = _DATA_DIR / "eval_visual"
BASE_DIR.mkdir(parents=True, exist_ok=True)
MODEL_MAP_FILE = Path(os.getenv("MODEL_MAP_FILE", str(BASE_DIR / "model_map.json")))
DEBUG_DIR = Path(os.getenv("VISUAL_EVAL_DEBUG_DIR", str(BASE_DIR / "visual_eval_debug")))
DEBUG_DIR.mkdir(parents=True, exist_ok=True)

PORT = int(os.getenv("VISUAL_EVAL_PORT", "18081"))
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "45"))
IMAGE_FETCH_TIMEOUT = float(os.getenv("IMAGE_FETCH_TIMEOUT", str(REQUEST_TIMEOUT)))
VIDEO_FETCH_TIMEOUT = float(os.getenv("VIDEO_FETCH_TIMEOUT", str(REQUEST_TIMEOUT)))
VIDEO_MAX_BYTES = int(os.getenv("VIDEO_MAX_BYTES", str(200 * 1024 * 1024)))
VIDEO_SAMPLE_FRAMES = int(os.getenv("VIDEO_SAMPLE_FRAMES", "4"))
VIDEO_FPS = float(os.getenv("VIDEO_FPS", "0.25"))
FFMPEG_PATH = os.getenv("FFMPEG_PATH", "ffmpeg").strip()
OLLAMA_REQUEST_TIMEOUT = float(os.getenv("OLLAMA_REQUEST_TIMEOUT", "300"))
SCORE_JSON_RETRY_MAX = max(0, int(os.getenv("VISUAL_EVAL_SCORE_JSON_RETRY_MAX", "1")))
OLLAMA_NORMALIZE_IMAGES = os.getenv("OLLAMA_NORMALIZE_IMAGES", "1").strip() != "0"
OLLAMA_IMAGE_MAX_SIDE = int(os.getenv("OLLAMA_IMAGE_MAX_SIDE", "1280"))
OLLAMA_IMAGE_JPEG_QUALITY = int(os.getenv("OLLAMA_IMAGE_JPEG_QUALITY", "90"))
OLLAMA_CONTACT_SHEET_FALLBACK = os.getenv("OLLAMA_CONTACT_SHEET_FALLBACK", "0").strip() == "1"
MAX_TOKENS_SCORE = int(os.getenv("VISUAL_EVAL_MAX_TOKENS_SCORE", "3200"))
MAX_TOKENS_REPORT = int(os.getenv("VISUAL_EVAL_MAX_TOKENS_REPORT", "2600"))
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.2"))
TOP_P = float(os.getenv("TOP_P", "0.9"))

ONEAPI_BASE_URL = os.getenv("ONEAPI_BASE_URL", "").strip()
ONEAPI_API_KEY = os.getenv("ONEAPI_API_KEY", "").strip()
DEFAULT_ONEAPI_MODEL = os.getenv("DEFAULT_ONEAPI_MODEL", "gpt-4o-mini").strip()

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").strip().rstrip("/")
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "0").strip()
DEFAULT_OLLAMA_MODEL = os.getenv("DEFAULT_OLLAMA_VISION_MODEL", "llava:7b").strip()

DEFAULT_MODEL_DISPLAY = os.getenv("VISUAL_EVAL_DEFAULT_MODEL", "GPT-4o\u0028online\u0029").strip()
SCORE_MODEL_DISPLAY = os.getenv("VISUAL_EVAL_SCORE_MODEL", "llava:7b").strip()
REPORT_MODEL_DISPLAY = os.getenv("VISUAL_EVAL_REPORT_MODEL", "gpt-oss:latest").strip()

# Database writes are disabled by default. Enable save_to_db=true only when persistence is needed.
# Database writing is off by default to avoid accidental production writes.
SAVE_SCORE_TO_DB = os.getenv("VISUAL_EVAL_SAVE_SCORE_TO_DB", "0").strip() == "1"
DB_PROVIDER = os.getenv("VISUAL_EVAL_DB_PROVIDER", "sqlite").strip().lower()  # sqlite / mssql
DB_CONNECTION_STRING = os.getenv("VISUAL_EVAL_DB_CONNECTION_STRING", "").strip()
DB_SQLITE_FILE = Path(os.getenv("VISUAL_EVAL_DB_SQLITE_FILE", str(BASE_DIR / "visual_eval_results.db")))
DB_TABLE = os.getenv("VISUAL_EVAL_DB_TABLE", "AIVisualEvaluationResult").strip()
DB_AUTO_CREATE = os.getenv("VISUAL_EVAL_DB_AUTO_CREATE", "1").strip() == "1"
USER_PROMPT_FILE = Path(os.getenv("VISUAL_EVAL_USER_PROMPT_FILE", str(BASE_DIR / "visual_eval_user_prompt.json")))
DEFAULT_PROMPT_BACKUP_FILE = Path(os.getenv("VISUAL_EVAL_DEFAULT_PROMPT_BACKUP_FILE", str(BASE_DIR / "visual_eval_default_prompt_backup.json")))

DEFAULT_SCORE_PROMPT_TEMPLATE = 'You are [[expert]]. Return strict JSON only. All human-readable content must be Simplified Chinese.\nIndustry: [[industry]]\nBackground: [[background]]\nTarget user: [[target_user]]\nMaterials:\n[[materials]]\nEvaluation rule: [[dimension_rule]]\nFocus: [[focus]]\nDimensions:\n[[dimensions]]\nOutput keys: project_info, overall, dimensions, material_list, compare. Scores must be decimals from 0 to 1. material_list must contain [[material_count]] items matching [[material_ids]].'

DEFAULT_REPORT_PROMPT_TEMPLATE = 'You are [[expert]]. Write a formal Simplified Chinese evaluation report based only on the score JSON.\nIndustry: [[industry]]\nBackground: [[background]]\nTarget user: [[target_user]]\nMaterials:\n[[report_materials]]\nScore JSON:\n[[score_json]]\nUse 100-point scores only. Include overall score, summary, dimension interpretation, material comparison, best recommendation, strengths, issues, and optimization suggestions. Do not use markdown.'

DEFAULT_USER_PROMPT = {
    "score_prompt_template": DEFAULT_SCORE_PROMPT_TEMPLATE,
    "report_prompt_template": DEFAULT_REPORT_PROMPT_TEMPLATE,
}

STRICT_CHINESE_OUTPUT_INSTRUCTION = """【语言硬性要求】
1. 所有面向用户的内容必须使用简体中文。
2. 正式文字报告、summary、level、strengths、issues、weaknesses、suggestions、reason、best_reason、compare_summary 等字段必须是中文。
3. 不允许输出英文标题、英文段落、英文解释或中英混排说明。
4. 品牌名、模型名、文件名、英文 code 可以保留原文，其余内容必须翻译成自然、专业、可阅读的中文。
5. 如果内部思考或参考资料是英文，也必须在最终输出前改写为简体中文。"""


# =============================================================================
# 2) 评估维度 / Evaluation Dimensions
# =============================================================================
SCENES = {
  "packaging": {
    "industry": "快消",
    "aliases": [
      "visual_packaging",
      "packaging",
      "package",
      "包装",
      "包装视觉",
      "快消",
      "fmcg"
    ],
    "expert": "专业快消品包装视觉评估专家",
    "focus": "货架场景、购买决策、品牌识别、卖点传达和包装完成度",
    "dimension_rule": "按包装视觉测试逻辑评估，关注消费者在货架和购买决策中的真实感受。",
    "dimensions": [
      [
        "货架吸引力",
        "shelf",
        "在同类商品中是否醒目，能否快速吸引注意"
      ],
      [
        "产品识别度",
        "product",
        "品类、口味、规格和核心卖点是否容易识别"
      ],
      [
        "品牌辨识度",
        "brand",
        "品牌名称、LOGO 和品牌资产是否清晰一致"
      ],
      [
        "信息清晰度",
        "info",
        "包装文字、卖点和层级是否清楚"
      ],
      [
        "色彩吸引力",
        "color",
        "色彩是否有吸引力并符合品类心智"
      ],
      [
        "版式层级",
        "layout",
        "视觉主次、阅读路径和画面秩序是否合理"
      ],
      [
        "购买欲望",
        "purchase",
        "是否激发尝试、购买或进一步了解的意愿"
      ],
      [
        "场景适配度",
        "scene",
        "是否适合目标市场、渠道和使用场景"
      ]
    ]
  },
  "film": {
    "industry": "影视",
    "aliases": [
      "影视",
      "影视剧",
      "海报",
      "film",
      "poster",
      "movie",
      "tv"
    ],
    "expert": "专业影视海报视觉评估专家",
    "focus": "影视宣发逻辑、题材识别、演员/角色层级、情绪氛围和传播记忆点",
    "dimension_rule": "按影视主视觉和海报传播效果评估，关注目标观众是否愿意点击、观看和记住。",
    "dimensions": [
      [
        "咖位呈现",
        "star",
        "演员、角色或核心人物呈现是否有吸引力和层级"
      ],
      [
        "类型暗示性",
        "type",
        "题材类型、故事气质和观看预期是否清楚"
      ],
      [
        "色彩和调性",
        "color",
        "色彩风格是否契合作品气质并具备吸引力"
      ],
      [
        "版式构图",
        "layout",
        "构图、主体关系和视觉焦点是否成立"
      ],
      [
        "氛围营造",
        "atmosphere",
        "情绪张力、戏剧感和沉浸感是否充分"
      ],
      [
        "信息清晰度",
        "info",
        "片名、标语、播出信息等是否清晰"
      ],
      [
        "受众匹配度",
        "audience",
        "是否匹配目标观众兴趣和审美"
      ],
      [
        "传播记忆点",
        "memory",
        "是否有可被记住和二次传播的视觉钩子"
      ]
    ]
  },
  "lottery": {
    "industry": "彩票",
    "aliases": [
      "彩票",
      "即开票",
      "lottery",
      "ticket",
      "scratch"
    ],
    "expert": "专业即开票产品视觉与玩法评估专家",
    "focus": "围绕喜欢程度、购买意愿、刮开前后体验、票面设计、图案颜色、趣味性、游戏名称和玩法易懂性评估",
    "dimension_rule": "参考 B2-B7 的问卷理念：先评估刮开前喜好度和购买意愿，再结合刮开后奖区、中奖规则、主题、设计风格和游戏玩法进行综合判断。",
    "dimensions": [
      [
        "总体喜欢度",
        "overall_like",
        "用户对即开票整体的喜欢程度，相当于 B2/B4"
      ],
      [
        "购买意愿",
        "purchase_intent",
        "购买方便时的购买可能性，相当于 B3/B5"
      ],
      [
        "票面设计创新性",
        "design_innovation",
        "票面主题和表现方式是否新颖"
      ],
      [
        "图案和颜色设计",
        "pattern_color",
        "图案、颜色和视觉风格是否有吸引力"
      ],
      [
        "趣味性",
        "game_fun",
        "游戏规则和刮开过程是否有趣、能吸引参与"
      ],
      [
        "游戏名称",
        "game_name",
        "名称是否好记、贴题、有购买吸引力"
      ],
      [
        "玩法易懂性",
        "game_ease",
        "中奖规则和玩法是否容易理解，相当于 B7"
      ],
      [
        "市场潜力",
        "market_potential",
        "综合判断目标购彩者中的接受度和传播潜力"
      ]
    ]
  },
  "generic": {
    "industry": "通用",
    "aliases": [
      "通用",
      "generic",
      "general",
      "其他"
    ],
    "expert": "专业视觉传播与用户感知评估专家",
    "focus": "注意力、主体识别、信息清晰度、审美完成度、目标用户匹配和行动转化",
    "dimension_rule": "适用于非固定行业的通用视觉评估，根据项目背景和图片内容判断关键影响因素。",
    "dimensions": [
      [
        "注意力吸引",
        "attention",
        "是否能快速吸引目标用户注意"
      ],
      [
        "主体识别",
        "subject",
        "核心对象、主题或产品是否明确"
      ],
      [
        "信息清晰度",
        "clarity",
        "关键信息是否易读易懂"
      ],
      [
        "审美完成度",
        "aesthetic",
        "画面质感、构图和视觉完成度"
      ],
      [
        "信任感",
        "trust",
        "是否显得专业、可信、可靠"
      ],
      [
        "目标用户匹配",
        "audience_fit",
        "是否符合目标用户偏好和场景"
      ],
      [
        "记忆点",
        "memory",
        "是否有清晰独特的记忆点"
      ],
      [
        "行动转化",
        "conversion",
        "是否推动点击、咨询、购买或进一步了解"
      ]
    ]
  },
  "custom": {
    "industry": "自定义",
    "aliases": [
      "自定义",
      "custom",
      "other"
    ],
    "expert": "专业行业视觉评估专家",
    "focus": "根据用户填写的行业、项目背景、目标用户和图片内容，自行提炼评价维度并完成评分",
    "dimension_rule": "如果行业为自定义，请结合行业、项目背景和图片内容生成 5-8 个维度；每个维度必须有中文 name、英文 code 和 0-1 分数。",
    "dimensions": []
  }
}

DEFAULT_SCENE_PROMPT_CONFIG = {
    key: {
        "industry": meta.get("industry", ""),
        "expert": meta.get("expert", ""),
        "focus": meta.get("focus", ""),
        "dimension_rule": "",
        "dimensions": [
            {"name": name, "code": code, "description": desc}
            for name, code, desc in meta.get("dimensions", [])
        ],
    }
    for key, meta in SCENES.items()
}


def merge_scene_prompt_config(scene_configs: Any) -> Dict[str, Any]:
    merged = json.loads(json.dumps(DEFAULT_SCENE_PROMPT_CONFIG, ensure_ascii=False))
    if not isinstance(scene_configs, dict):
        return merged
    for scene, cfg in scene_configs.items():
        if scene not in merged or not isinstance(cfg, dict):
            continue
        for field in ("industry", "expert", "focus", "dimension_rule"):
            if cfg.get(field) is not None:
                merged[scene][field] = str(cfg.get(field) or "").strip()
        dims = cfg.get("dimensions")
        if isinstance(dims, list):
            clean_dims = []
            for item in dims:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                code = str(item.get("code") or "").strip()
                desc = str(item.get("description") or item.get("desc") or "").strip()
                if name and code:
                    clean_dims.append({"name": name, "code": code, "description": desc})
            if clean_dims:
                merged[scene]["dimensions"] = clean_dims[:12]
    return merged


def load_scene_prompt_config() -> Dict[str, Any]:
    if not USER_PROMPT_FILE.exists():
        return merge_scene_prompt_config({})
    try:
        data = json.loads(USER_PROMPT_FILE.read_text(encoding="utf-8"))
    except Exception:
        return merge_scene_prompt_config({})
    return merge_scene_prompt_config(data.get("scene_configs"))


def get_scene_meta(scene: str) -> Dict[str, Any]:
    base = SCENES[scene]
    cfg = load_scene_prompt_config().get(scene, {})
    dims = [
        (str(item.get("name") or ""), str(item.get("code") or ""), str(item.get("description") or ""))
        for item in cfg.get("dimensions", [])
        if str(item.get("name") or "").strip() and str(item.get("code") or "").strip()
    ]
    return {
        **base,
        "industry": cfg.get("industry") or base.get("industry"),
        "expert": cfg.get("expert") or base.get("expert"),
        "focus": cfg.get("focus") or base.get("focus"),
        "dimension_rule": cfg.get("dimension_rule") or "",
        "dimensions": dims or base.get("dimensions", []),
    }

# =============================================================================
# 3) Debug / Concise Debug
# =============================================================================
def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def new_rid() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid4().hex[:8]


def debug_path(rid: str) -> Path:
    return DEBUG_DIR / f"{rid}.txt"


def write_debug(path: Path, title_cn: str, title_en: str, data: Any) -> None:
    """Write a debug block."""
    with path.open("a", encoding="utf-8") as f:
        f.write(f"\n[{now_text()}] {title_cn} / {title_en}\n")
        f.write("-" * 72 + "\n")
        if isinstance(data, (dict, list)):
            f.write(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            f.write(str(data))
        f.write("\n")


def ensure_default_prompt_backup() -> None:
    if DEFAULT_PROMPT_BACKUP_FILE.exists():
        return
    backup = {
        "created_at": now_text(),
        "note": "原版默认提示词备份。管理员保存自定义提示词不会覆盖此文件。",
        "score_prompt_template": DEFAULT_SCORE_PROMPT_TEMPLATE,
        "report_prompt_template": DEFAULT_REPORT_PROMPT_TEMPLATE,
    }
    DEFAULT_PROMPT_BACKUP_FILE.write_text(json.dumps(backup, ensure_ascii=False, indent=2), encoding="utf-8")


# =============================================================================
# 4) Input Normalization
# =============================================================================
def load_model_map() -> Dict[str, Dict[str, Any]]:
    if not MODEL_MAP_FILE.exists():
        return {}
    with MODEL_MAP_FILE.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


MODEL_DISPLAY_ALIASES: Dict[str, Dict[str, Any]] = {
    "Llava:7b": {"provider": "ollama", "model": "llava:7b"},
    "llava:7b": {"provider": "ollama", "model": "llava:7b"},
    "Llava:7b锛堟湰鍦帮級": {"provider": "ollama", "model": "llava:7b"},
    "Mistral-small3.2锛堟湰鍦帮級": {"provider": "ollama", "model": "mistral-small3.2:latest"},
    "Gemma3锛堟湰鍦帮級": {"provider": "ollama", "model": "gemma3:latest"},
    "Moondream锛堟湰鍦帮級": {"provider": "ollama", "model": "moondream"},
    "Bakllava锛堟湰鍦帮級": {"provider": "ollama", "model": "bakllava"},
    "Translategemma锛堟湰鍦帮級": {"provider": "ollama", "model": "translategemma"},
    "Mixtral锛堟湰鍦帮級": {"provider": "ollama", "model": "mixtral:latest"},
    "Llama4锛堟湰鍦帮級": {"provider": "ollama", "model": "llama4:latest"},
    "GLM-4.6V锛堟湰鍦帮級": {"provider": "ollama", "model": "haervwe/GLM-4.6V-Flash-9B:latest"},
    "Llava:34b": {"provider": "ollama", "model": "llava:34b"},
    "llava:34b": {"provider": "ollama", "model": "llava:34b"},
    "gpt-oss:latest": {"provider": "ollama", "model": "gpt-oss:latest"},
    "GPT-OSS锛堟湰鍦帮級": {"provider": "ollama", "model": "gpt-oss:latest"},
    "Qwen2.5-vl锛堝湪绾匡級": {"provider": "oneapi", "model": "Qwen/Qwen3-VL-8B-Instruct"},
    "GPT-4o锛堝湪绾匡級": {"provider": "oneapi", "model": "gpt-4o-mini"},
}


def resolve_scene(scene: Any) -> str:
    raw = str(scene or "packaging").strip().lower()
    for key, meta in SCENES.items():
        if raw == key or raw in {x.lower() for x in meta["aliases"]}:
            return key
    raise HTTPException(status_code=400, detail="scene 只能是 packaging/包装视觉、film/影视剧、lottery/彩票、generic/通用 或 custom/自定义")


def resolve_model_display(display: str) -> Tuple[str, str, str, Dict[str, Any]]:
    model_map = load_model_map()
    display = str(display or DEFAULT_MODEL_DISPLAY).strip()

    opts = (model_map.get(display, {}) if model_map else {}) or MODEL_DISPLAY_ALIASES.get(display, {})
    if isinstance(opts, dict) and opts:
        provider = str(opts.get("provider") or "oneapi").strip().lower()
        model = str(opts.get("model") or "").strip()
        return provider, model, display, opts

    if display.lower().startswith("ollama:"):
        return "ollama", display.split(":", 1)[1].strip(), display, {}
    if display.lower().startswith("oneapi:"):
        return "oneapi", display.split(":", 1)[1].strip(), display, {}
    return "oneapi", display or DEFAULT_ONEAPI_MODEL, display, {}


def resolve_model(payload: Dict[str, Any]) -> Tuple[str, str, str, Dict[str, Any]]:
    display = str(
        payload.get("model_display")
        or payload.get("model_name")
        or payload.get("selected_model")
        or payload.get("model")
        or DEFAULT_MODEL_DISPLAY
    ).strip()
    return resolve_model_display(display)


def normalize_materials(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = payload.get("materials") or payload.get("material_list") or payload.get("images") or []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = [x.strip() for x in raw.split(";") if x.strip()]

    if not isinstance(raw, list):
        raw = []

    image_base_url = str(payload.get("image_base_url") or "").strip().rstrip("/")
    items: List[Dict[str, Any]] = []
    for idx, item in enumerate(raw, 1):
        if isinstance(item, dict):
            m = dict(item)
        else:
            m = {"path": str(item)}

        mid = str(m.get("material_id") or m.get("id") or f"M{idx:03d}").strip()
        name = display_material_name(m.get("material_name") or m.get("name") or f"绱犳潗{idx}")
        url = str(m.get("url") or m.get("image_url") or m.get("video_url") or "").strip()
        path = str(m.get("path") or m.get("image_path") or m.get("video_path") or "").strip()
        data_url = str(m.get("data_url") or "").strip()
        media_type = str(m.get("media_type") or m.get("material_type") or "").strip().lower()
        mime_type = str(m.get("mime_type") or m.get("content_type") or "").strip().lower()

        if path and path.lower().startswith(("http://", "https://")) and not url:
            url, path = path, ""
        if path and not Path(path).is_absolute():
            local_path = BASE_DIR / path
        else:
            local_path = Path(path) if path else Path()
        if path and not local_path.exists() and image_base_url:
            url = image_base_url + "/" + path.lstrip("/\\")
            path = ""

        items.append({
            "material_id": mid,
            "material_name": name,
            "url": url,
            "path": str(local_path) if path else "",
            "data_url": data_url,
            "media_type": media_type,
            "mime_type": mime_type,
        })

    image_paths = payload.get("image_paths")
    if image_paths and not items:
        if isinstance(image_paths, str):
            try:
                image_paths = json.loads(image_paths)
            except Exception:
                image_paths = [x.strip() for x in image_paths.split(";") if x.strip()]
        for idx, p in enumerate(image_paths or [], 1):
            p_text = str(p).strip()
            item = {"material_id": f"M{idx:03d}", "material_name": f"素材{idx}", "url": "", "path": "", "data_url": "", "media_type": "", "mime_type": ""}
            if p_text.lower().startswith(("http://", "https://")):
                item["url"] = p_text
            elif image_base_url:
                item["url"] = image_base_url + "/" + p_text.lstrip("/\\")
            else:
                item["path"] = str((BASE_DIR / p_text) if not Path(p_text).is_absolute() else Path(p_text))
            items.append(item)

    if not items:
        raise HTTPException(status_code=400, detail="materials/material_list/image_paths 不能为空")
    return items


def normalize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    scene = resolve_scene(payload.get("scene") or payload.get("industry") or payload.get("type"))
    raw_industry = str(payload.get("industry") or "").strip()
    industry = raw_industry if raw_industry and scene == "custom" else SCENES[scene]["industry"]
    return {
        "scene": scene,
        "industry": industry,
        "background": str(payload.get("background") or payload.get("project_background") or "").strip(),
        "target_user": str(payload.get("target_user") or payload.get("target") or payload.get("audience") or "").strip(),
        "materials": normalize_materials(payload),
        "save_to_db": bool(payload.get("save_to_db", SAVE_SCORE_TO_DB)),
        "return_meta": bool(payload.get("return_meta", False)),
        "raw": payload,
    }


# =============================================================================
# 5) 媒体读取 / Media Loading
# =============================================================================
def data_url_to_bytes(data_url: str) -> Tuple[bytes, str]:
    m = re.match(r"^data:([^;]+);base64,(.+)$", data_url, flags=re.I | re.S)
    if not m:
        raise HTTPException(status_code=400, detail="data_url 格式错误")
    return base64.b64decode(m.group(2)), m.group(1)


def bytes_to_data_url(b: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(b).decode('ascii')}"


def image_mime(filename: str, mime_hint: str = "") -> str:
    guessed = mimetypes.guess_type(filename or "")[0] or "image/png"
    hint = (mime_hint or "").split(";")[0].strip().lower()
    if not hint or hint == "application/octet-stream":
        return guessed
    return "image/jpeg" if hint == "image/jpg" else hint


def media_mime(filename: str, mime_hint: str = "") -> str:
    guessed = mimetypes.guess_type(filename or "")[0] or "application/octet-stream"
    hint = (mime_hint or "").split(";")[0].strip().lower()
    if hint and hint != "application/octet-stream":
        return "image/jpeg" if hint == "image/jpg" else hint
    return guessed


def guess_media_kind(filename: str = "", mime: str = "", raw: bytes = b"", declared: str = "") -> str:
    declared = (declared or "").lower()
    if declared in {"video", "视频"}:
        return "video"
    if declared in {"image", "图片"}:
        return "image"
    mime = (mime or "").lower()
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("image/"):
        return "image"
    suffix = Path((filename or "").split("?", 1)[0]).suffix.lower()
    if suffix in {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".wmv"}:
        return "video"
    if suffix in {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg"}:
        return "image"
    head = raw[:16]
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return "video"
    if head.startswith(b"\x1aE\xdf\xa3"):
        return "video"
    if head.startswith((b"\xff\xd8\xff", b"\x89PNG", b"GIF8", b"BM", b"RIFF")):
        return "image"
    return "image"


def upload_media_type(filename: str, mime: str, content: bytes) -> str:
    return guess_media_kind(filename=filename or "", mime=media_mime(filename, mime), raw=content or b"", declared="")


def compose_frames_contact_sheet(frames: List[bytes], dbg: Path, material_id: str) -> bytes:
    if not frames:
        raise HTTPException(status_code=400, detail=f"视频未抽取到关键帧: {material_id}")
    if len(frames) == 1 or Image is None or ImageDraw is None:
        return frames[0]

    decoded = []
    for idx, raw in enumerate(frames, 1):
        try:
            decoded.append((idx, Image.open(BytesIO(raw)).convert("RGB")))
        except Exception as e:
            write_debug(dbg, "视频关键帧读取失败", "video frame decode failed", {"material_id": material_id, "frame": idx, "error": str(e)})
    if not decoded:
        return frames[0]

    cols = 2 if len(decoded) <= 4 else 3
    rows = int((len(decoded) + cols - 1) / cols)
    cell_w, cell_h, label_h, margin = 420, 320, 34, 14
    sheet = Image.new("RGB", (cols * cell_w + margin * 2, rows * cell_h + margin * 2), "white")
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("arial.ttf", 22)
    except Exception:
        font = ImageFont.load_default()

    for idx, img in decoded:
        col = (idx - 1) % cols
        row = (idx - 1) // cols
        x0 = margin + col * cell_w
        y0 = margin + row * cell_h
        draw.rectangle([x0, y0, x0 + cell_w - 10, y0 + cell_h - 10], outline=(210, 219, 232), width=2)
        draw.rectangle([x0, y0, x0 + cell_w - 10, y0 + label_h], fill=(238, 243, 255), outline=(210, 219, 232))
        draw.text((x0 + 10, y0 + 6), f"{material_id} 关键帧{idx}", fill=(17, 24, 39), font=font)
        img.thumbnail((cell_w - 28, cell_h - label_h - 24))
        tx = x0 + (cell_w - 10 - img.width) // 2
        ty = y0 + label_h + 10 + (cell_h - label_h - 24 - img.height) // 2
        sheet.paste(img, (tx, ty))

    out = BytesIO()
    sheet.save(out, format="PNG", optimize=True)
    return out.getvalue()


def extract_video_contact_sheet(raw: bytes, dbg: Path, material_id: str, mime: str) -> Tuple[bytes, str, Dict[str, Any]]:
    if not raw:
        raise HTTPException(status_code=400, detail=f"视频为空: {material_id}")
    if len(raw) > VIDEO_MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"视频过大: {material_id}, {len(raw)} bytes > {VIDEO_MAX_BYTES}")

    suffix = ".mp4"
    if "webm" in mime:
        suffix = ".webm"
    elif "quicktime" in mime or "mov" in mime:
        suffix = ".mov"
    elif "matroska" in mime or "mkv" in mime:
        suffix = ".mkv"

    with tempfile.TemporaryDirectory(prefix="visual_eval_video_") as td:
        src = Path(td) / ("input" + suffix)
        src.write_bytes(raw)
        out_pattern = str(Path(td) / "frame_%03d.jpg")
        fps = max(0.01, VIDEO_FPS)
        frames_limit = max(1, VIDEO_SAMPLE_FRAMES)
        cmd = [
            FFMPEG_PATH, "-hide_banner", "-loglevel", "error",
            "-i", str(src),
            "-vf", f"fps={fps}",
            "-frames:v", str(frames_limit),
            "-q:v", "3",
            out_pattern,
        ]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=max(30, int(VIDEO_FETCH_TIMEOUT)))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"ffmpeg 执行失败: {e}")
        if p.returncode != 0:
            raise HTTPException(status_code=500, detail=f"ffmpeg 抽帧失败: {(p.stderr or p.stdout or '')[:500]}")
        frame_paths = sorted(Path(td).glob("frame_*.jpg"))
        frames = [p.read_bytes() for p in frame_paths]
        sheet = compose_frames_contact_sheet(frames, dbg, material_id)
        info = {
            "media_type": "video",
            "video_bytes": len(raw),
            "frame_count": len(frames),
            "ffmpeg_path": FFMPEG_PATH,
            "video_sample_frames": frames_limit,
            "video_fps": fps,
        }
        return sheet, "image/png", info


def normalize_image_for_model(raw: bytes, mime: str, dbg: Path, material_id: str) -> Tuple[bytes, str, Dict[str, Any]]:
    info: Dict[str, Any] = {
        "normalized": False,
        "original_mime": mime,
        "model_mime": mime,
        "original_bytes": len(raw or b""),
        "model_bytes": len(raw or b""),
    }
    if not OLLAMA_NORMALIZE_IMAGES:
        return raw, mime, info
    if Image is None:
        write_debug(dbg, "图片格式统一跳过", "image normalization skipped", {"material_id": material_id, "reason": "Pillow is not installed"})
        return raw, mime, info
    try:
        with Image.open(BytesIO(raw)) as img:
            original_size = list(img.size)
            if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                background = Image.new("RGB", img.size, "white")
                background.paste(img.convert("RGBA"), mask=img.convert("RGBA").split()[-1])
                img = background
            else:
                img = img.convert("RGB")

            max_side = max(img.size)
            if OLLAMA_IMAGE_MAX_SIDE > 0 and max_side > OLLAMA_IMAGE_MAX_SIDE:
                scale = OLLAMA_IMAGE_MAX_SIDE / float(max_side)
                new_size = (max(1, int(img.size[0] * scale)), max(1, int(img.size[1] * scale)))
                img = img.resize(new_size, Image.LANCZOS)

            out = BytesIO()
            quality = max(60, min(95, OLLAMA_IMAGE_JPEG_QUALITY))
            img.save(out, format="JPEG", quality=quality, optimize=True)
            normalized = out.getvalue()
            info.update({
                "normalized": True,
                "original_size": original_size,
                "model_size": list(img.size),
                "model_mime": "image/jpeg",
                "model_bytes": len(normalized),
            })
            return normalized, "image/jpeg", info
    except Exception as e:
        write_debug(dbg, "图片格式统一失败", "image normalization failed", {"material_id": material_id, "mime": mime, "error": str(e)})
        return raw, mime, info


def safe_url(url: str) -> str:
    parts = urlsplit(url.strip())
    path = quote(unquote(parts.path), safe="/%")
    query = quote(unquote(parts.query), safe="=&?/%:+,;@")
    return urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment))


def load_material_images(materials: List[Dict[str, Any]], dbg: Path) -> Tuple[List[Dict[str, Any]], List[str], List[str]]:
    loaded: List[Dict[str, Any]] = []
    data_urls: List[str] = []
    ollama_b64: List[str] = []
    for m in materials:
        raw: bytes
        mime = "image/png"
        source = ""
        data_url = str(m.get("data_url") or "").strip()
        url = str(m.get("url") or "").strip()
        path = str(m.get("path") or "").strip()
        declared_kind = str(m.get("media_type") or "").strip().lower()
        mime_hint = str(m.get("mime_type") or "").strip().lower()

        if data_url:
            raw, mime = data_url_to_bytes(data_url)
            source = "data_url"
        elif url:
            safe = safe_url(url)
            try:
                resp = NO_PROXY_SESSION.get(safe, timeout=max(IMAGE_FETCH_TIMEOUT, VIDEO_FETCH_TIMEOUT), headers={"Accept": "video/*,image/*,*/*;q=0.8"})
                resp.raise_for_status()
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"无法读取素材URL {url}: {e}")
            raw = resp.content
            mime = media_mime(url, resp.headers.get("content-type", ""))
            source = safe
        elif path:
            p = Path(path)
            if not p.exists():
                raise HTTPException(status_code=400, detail=f"素材路径不存在: {path}")
            raw = p.read_bytes()
            mime = media_mime(str(p), mime_hint)
            source = str(p)
        else:
            raise HTTPException(status_code=400, detail=f"素材缺少 url/path/data_url: {m.get('material_id')}")

        mime = "image/jpeg" if mime.lower() == "image/jpg" else mime.split(";")[0]
        material_id = str(m.get("material_id") or "")
        source_name = url or path or str(m.get("material_name") or "")
        kind = guess_media_kind(source_name, mime, raw, declared_kind)
        if kind == "video":
            sheet_raw, sheet_mime, video_info = extract_video_contact_sheet(raw, dbg, material_id, mime)
            model_raw, model_mime, image_info = normalize_image_for_model(sheet_raw, sheet_mime, dbg, material_id)
            image_info.update(video_info)
        else:
            model_raw, model_mime, image_info = normalize_image_for_model(raw, mime, dbg, material_id)
        loaded.append({
            **m,
            "source": source,
            "media_type": kind,
            "mime": mime,
            "bytes": len(raw),
            "model_mime": model_mime,
            "model_bytes": len(model_raw),
            "normalized": bool(image_info.get("normalized")),
            "original_size": image_info.get("original_size"),
            "model_size": image_info.get("model_size"),
        })
        data_urls.append(bytes_to_data_url(model_raw, model_mime))
        ollama_b64.append(base64.b64encode(model_raw).decode("ascii"))

    write_debug(dbg, "素材读取", "material loading", [
        {k: v for k, v in x.items() if k not in {"data_url"}} for x in loaded
    ])
    return loaded, data_urls, ollama_b64


# =============================================================================
# 6) Prompts
# =============================================================================
def load_user_prompt_config() -> Dict[str, str]:
    ensure_default_prompt_backup()
    if not USER_PROMPT_FILE.exists():
        return {**dict(DEFAULT_USER_PROMPT), "scene_configs": load_scene_prompt_config()}
    try:
        data = json.loads(USER_PROMPT_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {**dict(DEFAULT_USER_PROMPT), "scene_configs": load_scene_prompt_config()}
    return {
        "score_prompt_template": str(
            data.get("score_prompt_template")
            or data.get("score_base")
            or data.get("score_extra")
            or DEFAULT_SCORE_PROMPT_TEMPLATE
        ).strip(),
        "report_prompt_template": str(
            data.get("report_prompt_template")
            or data.get("report_base")
            or data.get("report_extra")
            or DEFAULT_REPORT_PROMPT_TEMPLATE
        ).strip(),
        "scene_configs": merge_scene_prompt_config(data.get("scene_configs")),
    }


def save_user_prompt_config(score_prompt_template: str, report_prompt_template: str, scene_configs: Any = None) -> Dict[str, str]:
    ensure_default_prompt_backup()
    config = {
        "score_prompt_template": str(score_prompt_template or DEFAULT_SCORE_PROMPT_TEMPLATE).strip(),
        "report_prompt_template": str(report_prompt_template or DEFAULT_REPORT_PROMPT_TEMPLATE).strip(),
        "scene_configs": merge_scene_prompt_config(scene_configs),
        "updated_at": now_text(),
    }
    USER_PROMPT_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return load_user_prompt_config()


def prompt_extra(kind: str) -> str:
    return ""


def render_prompt_template(template: str, context: Dict[str, Any]) -> str:
    text = str(template or "")
    for key, value in context.items():
        text = text.replace(f"[[{key}]]", str(value))
    return text

def clean_report_text(text: str) -> str:
    value = (text or "").strip()
    value = re.sub(r"^```(?:text|markdown)?", "", value, flags=re.I).strip()
    value = re.sub(r"```$", "", value).strip()
    value = value.replace("**", "").replace("*", "")
    value = re.sub(r"^\s{0,3}#{1,6}\s*", "", value, flags=re.M)
    value = re.sub(r"^\s*[-+]\s+", "", value, flags=re.M)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def looks_chinese_report(text: str) -> bool:
    value = text or ""
    chinese_count = len(re.findall(r"[\u4e00-\u9fff]", value))
    latin_count = len(re.findall(r"[A-Za-z]", value))
    forbidden_headings = re.search(
        r"(?im)^\s*(overall|summary|conclusion|recommendation|recommendations|strengths|weaknesses|issues|optimization|material ranking|score interpretation)\b",
        value,
    )
    if forbidden_headings:
        return False
    return chinese_count >= 80 and chinese_count >= latin_count * 2


def display_material_name(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return Path(text).stem or text


def material_text(materials: List[Dict[str, Any]]) -> str:
    return "\n".join(
        f"{idx}. 内部ID={m['material_id']}；文件名={display_material_name(m['material_name'])}；素材类型={('视频' if str(m.get('media_type', '')).lower() == 'video' else '图片')}；图片或视频关键帧图按此顺序传入"
        for idx, m in enumerate(materials, 1)
    )


def report_material_text(materials: List[Dict[str, Any]]) -> str:
    return "\n".join(
        f"- {display_material_name(m['material_name'])}"
        for m in materials
    )


def to_report_score(value: Any) -> Any:
    try:
        n = float(value)
    except Exception:
        return value
    if 0 <= n <= 1:
        return round(n * 100, 1)
    return round(n, 1)


def score_json_for_report(score_json: Dict[str, Any]) -> Dict[str, Any]:
    report_obj = json.loads(json.dumps(score_json, ensure_ascii=False))
    material_names = {
        str(item.get("material_id")): display_material_name(item.get("material_name"))
        for item in report_obj.get("material_list", [])
        if isinstance(item, dict)
    }

    def convert_scores(node: Any) -> Any:
        if isinstance(node, dict):
            for key, value in list(node.items()):
                if key in {"score", "total_score"}:
                    node[key] = to_report_score(value)
                else:
                    node[key] = convert_scores(value)
            if "material_name" in node:
                node["material_name"] = display_material_name(node.get("material_name"))
            if "material_id" in node:
                node["material_key"] = material_names.get(str(node.get("material_id")), str(node.get("material_id")))
                node.pop("material_id", None)
            if "id" in node and str(node.get("id")) in material_names:
                node["material_key"] = material_names[str(node.get("id"))]
                node.pop("id", None)
            return node
        if isinstance(node, list):
            return [convert_scores(item) for item in node]
        return node

    converted = convert_scores(report_obj)
    compare = converted.get("compare") if isinstance(converted.get("compare"), dict) else {}
    best_id = score_json.get("compare", {}).get("best_id") if isinstance(score_json.get("compare"), dict) else ""
    if best_id:
        compare["best_material"] = material_names.get(str(best_id), str(best_id))
        compare.pop("best_id", None)
    converted["score_unit"] = "100分制，满分100"
    return converted


def json_schema_text(scene: str, industry: str, background: str, target_user: str, count: int) -> str:
    meta = get_scene_meta(scene)
    dims = ",\n    ".join(
        f'{{"name":"{name}","code":"{code}","score":0.68,"level":"中"}}'
        for name, code, _ in meta["dimensions"]
    )
    return f"""{{
  "project_info": {{"industry": "{industry}", "background": "项目背景", "target_user": "目标用户", "material_count": {count}}},
  "overall": {{"total_score": 0.68, "summary": "整体中文总评", "key_strengths": ["具体优势"], "key_issues": ["具体问题"], "optimize_suggestions": ["具体建议"]}},
  "dimensions": [
    {dims}
  ],
  "material_list": [{{"material_id": "M001", "material_name": "素材文件名", "score": 0.68, "summary": "单素材评价", "strengths": ["具体优势"], "issues": ["具体问题"], "suggestions": ["具体建议"]}}],
  "compare": {{"has_compare": true, "best_id": "M001", "best_reason": "推荐理由", "compare_summary": "对比总结", "rank": [{{"id":"M001","rank":1,"score":0.68}}]}}
}}"""

def default_dimension_rule(scene: str) -> str:
    if scene == "custom":
        return "请根据行业、项目背景、目标用户和图片内容自行生成5-8个评价维度，每个维度必须包含中文name、英文code和说明。"
    if scene == "lottery":
        return "参考B2-B7问卷理念，围绕喜欢程度、购买意愿、刮开前后体验、设计属性、趣味性和玩法易懂性评分。"
    return "请严格使用指定dimensions中的name和code进行评分。"

def build_score_prompt(scene: str, industry: str, background: str, target_user: str, materials: List[Dict[str, Any]]) -> str:
    meta = get_scene_meta(scene)
    dim_lines = "\n".join(f"- {name} | {code} | {desc}" for name, code, desc in meta["dimensions"])
    dimension_rule = str(meta.get("dimension_rule") or default_dimension_rule(scene))
    prompt_config = load_user_prompt_config()
    template = str(prompt_config.get("score_prompt_template") or DEFAULT_SCORE_PROMPT_TEMPLATE).strip()
    prompt = render_prompt_template(template, {
        "expert": meta["expert"],
        "industry": industry,
        "background": background,
        "target_user": target_user,
        "materials": material_text(materials),
        "report_materials": report_material_text(materials),
        "dimension_rule": dimension_rule,
        "focus": meta["focus"],
        "material_count": len(materials),
        "material_ids": ", ".join(m["material_id"] for m in materials),
        "dimensions": dim_lines,
    })
    return prompt + "\n\n" + STRICT_CHINESE_OUTPUT_INSTRUCTION + "\n\n【素材对比硬性要求】material_list 中每个素材必须包含 dimension_scores 数组；dimension_scores 必须覆盖全部维度 code，每项包含 name、code、score、level。请客观评估，如果多张上传图片只是轻微差异、版式和主体高度相近，分数应保持接近，通常总分差距不要超过 10-15 分；不要把一张图打到 80 多分、另一张相似图打到 10 多分。可以给出小幅差异，但不要为了避免并列而硬拉开。每个素材的 strengths、issues、suggestions 必须针对该素材自身差异写，禁止不同素材复制同一套优势、问题和建议。"


def build_report_prompt(scene: str, industry: str, background: str, target_user: str, materials: List[Dict[str, Any]]) -> str:
    meta = get_scene_meta(scene)
    dim_lines = "；".join(f"{name}：{desc}" for name, _, desc in meta["dimensions"])
    return f"""你是{meta['expert']}。请输出一份正式的简体中文视觉评估报告。
行业：{industry}
项目背景：{background}
目标用户：{target_user}
素材：
{report_material_text(materials)}

请结合图片内容和以下维度写报告：{dim_lines}
所有得分使用100分制表达。不要使用Markdown，不要使用星号。
"""

def build_report_from_score_prompt(scene: str, industry: str, background: str, target_user: str, materials: List[Dict[str, Any]], score_json: Dict[str, Any]) -> str:
    meta = get_scene_meta(scene)
    prompt_config = load_user_prompt_config()
    template = str(prompt_config.get("report_prompt_template") or DEFAULT_REPORT_PROMPT_TEMPLATE).strip()
    prompt = render_prompt_template(template, {
        "expert": meta["expert"],
        "industry": industry,
        "background": background,
        "target_user": target_user,
        "materials": material_text(materials),
        "report_materials": report_material_text(materials),
        "focus": meta["focus"],
        "score_json": json.dumps(score_json_for_report(score_json), ensure_ascii=False, indent=2),
    })
    return prompt + "\n\n" + STRICT_CHINESE_OUTPUT_INSTRUCTION + "\n报告正文必须从中文标题或中文正文开始，不要出现 Overall、Summary、Recommendation、Conclusion、Strengths、Weaknesses 等英文小标题。"


def build_score_retry_prompt(scene: str, industry: str, background: str, target_user: str, materials: List[Dict[str, Any]], previous_error: str, previous_output: str, attempt: int = 1) -> str:
    meta = get_scene_meta(scene)
    dim_rows = "\n".join(f"- {name} | {code} | {desc}" for name, code, desc in meta["dimensions"])
    material_rows = "\n".join(
        f"- material_id={m['material_id']} | material_name={display_material_name(m['material_name'])}"
        for m in materials
    )
    return f"""这是第 {attempt} 次 JSON 重试。上一次输出不能被系统解析。
请重新输出一个完整、标准、可被 json.loads 解析的 JSON 对象。
不要输出 Markdown，不要解释，不要代码块，不要省略号，不要尾随逗号。

解析错误：{previous_error}

必须包含顶层字段：project_info、overall、dimensions、material_list、compare。
project_info、overall、compare 必须是对象。
dimensions、material_list、compare.rank 必须是数组。
material_list 每个素材必须包含 dimension_scores 数组，覆盖全部维度 code，用于对比不同素材在同一维度上的分数。
不同素材的 strengths、issues、suggestions 必须体现各自差异，不要复制同一套文案。
除非画面证据非常明确，不要让同一素材在所有 dimension_scores 维度都是最高分。
strengths、issues、suggestions、key_strengths、key_issues、optimize_suggestions 必须是中文字符串数组，不能是对象数组。
score、total_score、dimension_scores.score 只能是 0 到 1 的小数。

素材必须完整覆盖：
{material_rows}

维度必须使用以下 name 和 code，不能把说明写进 code：
{dim_rows}

上一次错误输出如下，只参考其图片判断，不要照抄其错误结构：
{previous_output[:6000]}
"""

def call_oneapi(model: str, prompt: str, data_urls: List[str], max_tokens: int, opts: Dict[str, Any], dbg: Path, rid: str) -> str:
    if not ONEAPI_BASE_URL:
        raise HTTPException(status_code=500, detail="未配置 ONEAPI_BASE_URL")
    if not ONEAPI_API_KEY:
        raise HTTPException(status_code=500, detail="未配置 ONEAPI_API_KEY")

    from openai import OpenAI

    model_name = model or DEFAULT_ONEAPI_MODEL
    content: List[Dict[str, Any]] = []
    use_detail = False if opts.get("use_detail") is False else True
    for u in data_urls:
        image_block: Dict[str, Any] = {"type": "image_url", "image_url": {"url": u}}
        if use_detail:
            image_block["image_url"]["detail"] = "high"
        content.append(image_block)
    content.append({"type": "text", "text": prompt})

    write_debug(dbg, "模型输入摘要", "llm input summary", {
        "rid": rid,
        "provider": "oneapi",
        "model": model_name,
        "image_count": len(data_urls),
        "prompt_len": len(prompt),
        "max_tokens": max_tokens,
    })

    client = OpenAI(api_key=ONEAPI_API_KEY, base_url=ONEAPI_BASE_URL)
    try:
        resp = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": content}],
            max_tokens=max_tokens,
            **sampling_kwargs(opts),
        )
    except Exception as e:
        if "cannot both be specified" in str(e).lower():
            resp = client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": content}],
                max_tokens=max_tokens,
                temperature=TEMPERATURE,
            )
        else:
            raise HTTPException(status_code=502, detail=f"OneAPI 璋冪敤澶辫触: {e}")

    text = resp.choices[0].message.content or ""
    write_debug(dbg, "模型原始输出", "llm raw output", {"length": len(text), "preview": text[:1200]})
    return text


def call_ollama(model: str, prompt: str, images_b64: List[str], max_tokens: int, opts: Dict[str, Any], dbg: Path, rid: str) -> str:
    model_name = model or DEFAULT_OLLAMA_MODEL
    payload = {
        "model": model_name,
        "stream": False,
        "keep_alive": OLLAMA_KEEP_ALIVE,
        "messages": [{"role": "user", "content": prompt, "images": images_b64}],
        "options": {"temperature": TEMPERATURE, "top_p": TOP_P, "num_predict": max_tokens},
    }
    if opts.get("json_mode"):
        payload["format"] = "json"
    write_debug(dbg, "ollama input", "llm input summary", {
        "rid": rid,
        "provider": "ollama",
        "model": model_name,
        "image_count": len(images_b64),
        "prompt_len": len(prompt),
        "timeout_seconds": OLLAMA_REQUEST_TIMEOUT,
    })
    try:
        with OLLAMA_LOCK:
            r = NO_PROXY_SESSION.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=OLLAMA_REQUEST_TIMEOUT)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ollama 请求失败: {e}; 当前 OLLAMA_REQUEST_TIMEOUT={OLLAMA_REQUEST_TIMEOUT}s")
    if r.status_code >= 400:
        body = r.text[:1000]
        if should_retry_ollama_with_contact_sheet(r.status_code, body, images_b64):
            contact_image = build_ollama_contact_sheet(images_b64, dbg)
            if contact_image:
                retry_payload = dict(payload)
                retry_payload["messages"] = [{
                    "role": "user",
                    "content": prompt + "\n\n【多图兜底说明】由于本地视觉模型多图处理出现资源限制，本次已将全部素材合并为一张对比图。图中每个素材已按输入顺序标注 M001、M002、M003……请严格按这些素材编号分别评分和比较，不要遗漏任何素材。",
                    "images": [contact_image],
                }]
                write_debug(dbg, "ollama多图合并重试", "ollama contact sheet retry", {
                    "model": model_name,
                    "original_image_count": len(images_b64),
                    "retry_image_count": 1,
                    "previous_status": r.status_code,
                    "previous_error": body,
                })
                try:
                    with OLLAMA_LOCK:
                        r = NO_PROXY_SESSION.post(f"{OLLAMA_URL}/api/chat", json=retry_payload, timeout=OLLAMA_REQUEST_TIMEOUT)
                except Exception as e:
                    raise HTTPException(status_code=502, detail=f"Ollama 多图合并重试失败: {e}; 当前 OLLAMA_REQUEST_TIMEOUT={OLLAMA_REQUEST_TIMEOUT}s")
                if r.status_code < 400:
                    text = (r.json().get("message") or {}).get("content") or ""
                    write_debug(dbg, "ollama合并重试输出", "ollama contact sheet output", {"length": len(text), "preview": text[:1200]})
                    return text
                body = r.text[:1000]
        raise HTTPException(status_code=502, detail=f"Ollama 返回错误 {r.status_code}: {body}")
    text = (r.json().get("message") or {}).get("content") or ""
    write_debug(dbg, "ollama output", "llm raw output", {"length": len(text), "preview": text[:1200]})
    return text


def should_retry_ollama_with_contact_sheet(status_code: int, body: str, images_b64: List[str]) -> bool:
    if not OLLAMA_CONTACT_SHEET_FALLBACK:
        return False
    if status_code < 500 or len(images_b64 or []) <= 1:
        return False
    text = (body or "").lower()
    return any(key in text for key in (
        "model runner has unexpectedly stopped",
        "resource limitations",
        "cuda",
        "out of memory",
        "illegal memory access",
        "internal error",
    ))


def build_ollama_contact_sheet(images_b64: List[str], dbg: Path) -> str:
    if Image is None or ImageDraw is None:
        write_debug(dbg, "ollama多图合并跳过", "ollama contact sheet skipped", {"reason": "Pillow is not installed"})
        return ""
    decoded = []
    for index, b64 in enumerate(images_b64 or []):
        try:
            raw = base64.b64decode(b64)
            img = Image.open(BytesIO(raw)).convert("RGB")
            decoded.append((index, img))
        except Exception as e:
            write_debug(dbg, "ollama多图合并图片读取失败", "ollama contact sheet image decode failed", {"index": index, "error": str(e)})
    if len(decoded) <= 1:
        return ""

    count = len(decoded)
    cols = 2 if count <= 4 else 3
    rows = int((count + cols - 1) / cols)
    cell_w = 420
    cell_h = 330
    label_h = 38
    margin = 18
    canvas_w = cols * cell_w + margin * 2
    canvas_h = rows * cell_h + margin * 2
    sheet = Image.new("RGB", (canvas_w, canvas_h), "white")
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("arial.ttf", 24)
    except Exception:
        font = ImageFont.load_default()

    for index, img in decoded:
        row = index // cols
        col = index % cols
        x0 = margin + col * cell_w
        y0 = margin + row * cell_h
        label = f"M{index + 1:03d}"
        draw.rectangle([x0, y0, x0 + cell_w - 12, y0 + cell_h - 12], outline=(210, 219, 232), width=2)
        draw.rectangle([x0, y0, x0 + cell_w - 12, y0 + label_h], fill=(238, 243, 255), outline=(210, 219, 232))
        draw.text((x0 + 12, y0 + 7), label, fill=(17, 24, 39), font=font)

        thumb = img.copy()
        thumb.thumbnail((cell_w - 32, cell_h - label_h - 28))
        tx = x0 + (cell_w - 12 - thumb.width) // 2
        ty = y0 + label_h + 12 + (cell_h - label_h - 28 - thumb.height) // 2
        sheet.paste(thumb, (tx, ty))

    out = BytesIO()
    sheet.save(out, format="PNG", optimize=True)
    return base64.b64encode(out.getvalue()).decode("ascii")


def call_model(provider: str, model: str, prompt: str, data_urls: List[str], images_b64: List[str], max_tokens: int, opts: Dict[str, Any], dbg: Path, rid: str) -> str:
    if provider == "ollama":
        return call_ollama(model, prompt, images_b64, max_tokens, opts, dbg, rid)
    return call_oneapi(model, prompt, data_urls, max_tokens, opts, dbg, rid)


# =============================================================================
# 8) JSON 娓呮礂鏍￠獙 / JSON Cleanup and Validation
# =============================================================================
def extract_json(text: str) -> Dict[str, Any]:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.I).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    cleaned = cleaned.replace("\\_", "_")
    cleaned = repair_common_json_text(cleaned)
    try:
        obj = json.loads(cleaned)
    except Exception:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("模型未返回 JSON 对象")
        obj = json.loads(repair_common_json_text(cleaned[start:end + 1]))
    if not isinstance(obj, dict):
        raise ValueError("模型 JSON 根节点不是对象")
    return obj


def repair_common_json_text(text: str) -> str:
    value = text or ""
    value = value.replace('""summary"', '"summary"')
    value = value.replace('""strengths"', '"strengths"')
    value = value.replace('""issues"', '"issues"')
    value = value.replace('""suggestions"', '"suggestions"')
    value = re.sub(r'"level"\s*:\s*"高"', '"level":"强"', value)
    value = re.sub(r'"level"\s*:\s*"低"', '"level":"弱"', value)
    return value

def build_score_repair_prompt(scene: str, industry: str, background: str, target_user: str, materials: List[Dict[str, Any]], bad_json: str, error: str) -> str:
    material_rows = json.dumps(
        [{"material_id": m["material_id"], "material_name": m["material_name"]} for m in materials],
        ensure_ascii=False,
    )
    return f"""You are a JSON repair engine. Return only one valid JSON object. Do not use markdown.

The previous vision model output is invalid JSON. Repair and normalize it into the exact schema below.
Keep the visual judgments, scores, strengths, issues and suggestions from the previous output as much as possible.
Use Simplified Chinese for all human-readable text.

Parse error:
{error}

Scene: {scene}
Industry: {industry}
Background: {background}
Target user: {target_user}
Required materials:
{material_rows}

Required top-level keys:
project_info, overall, dimensions, material_list, compare

Rules:
1. Scores must be decimals from 0 to 1.
2. material_list must contain exactly the required materials and use material_id/material_name exactly as listed.
3. If previous output uses id/name, convert them to material_id/material_name.
4. dimensions must match the scene dimension names and codes when present in the schema/prompt.
5. Every material_list item must include dimension_scores array, covering every dimension code with name/code/score/level.
6. compare.rank must be sorted by score descending.
7. The first character must be {{ and the last character must be }}.

Invalid previous output:
{bad_json[:8000]}
"""


def synthesize_score_from_raw(scene: str, industry: str, background: str, target_user: str, materials: List[Dict[str, Any]], raw_text: str) -> Dict[str, Any]:
    meta = get_scene_meta(scene)
    raw = raw_text or ""
    material_list = []
    scores = []
    for idx, material in enumerate(materials):
        mid = str(material["material_id"])
        name = str(material["material_name"])
        score = None
        match = re.search(r'"(?:material_id|id)"\s*:\s*"' + re.escape(mid) + r'".{0,500}?"score"\s*:\s*([0-9.]+)', raw, flags=re.S)
        if match:
            try:
                score = float(match.group(1))
            except Exception:
                score = None
        if score is None:
            score = max(0.35, 0.68 - idx * 0.04)
        score = clamp_score(score)
        scores.append(score)
        display_name = display_material_name(name)
        material_list.append({
            "material_id": mid,
            "material_name": name,
            "score": score,
            "summary": f"{display_name}的视觉表达具备一定基础，但需要进一步突出核心信息和差异化记忆点。",
            "strengths": ["主体内容具备基础可识别性", "整体视觉方向与项目背景相关", "画面有继续优化放大的空间"],
            "issues": ["信息重点不够集中", "视觉层级还不够清晰", "与目标用户的利益关联表达偏弱"],
            "suggestions": ["强化最核心的视觉焦点", "提升标题、卖点或关键信息的优先级", "增加更明确的目标用户沟通理由"],
        })
    avg = clamp_score(sum(scores) / len(scores)) if scores else 0.6
    dimensions = [
        {"name": name, "code": code, "score": clamp_score(avg - (idx % 3) * 0.02), "level": level_from_score(clamp_score(avg - (idx % 3) * 0.02))}
        for idx, (name, code, _) in enumerate(meta["dimensions"])
    ]
    ranked = sorted(material_list, key=lambda item: item["score"], reverse=True)
    rank = [{"id": item["material_id"], "rank": idx + 1, "score": item["score"]} for idx, item in enumerate(ranked)]
    best_id = rank[0]["id"] if rank else (materials[0]["material_id"] if materials else "")
    return normalize_score_json({
        "project_info": {"industry": industry, "background": background, "target_user": target_user, "material_count": len(materials)},
        "overall": {
            "total_score": avg,
            "summary": f"整体视觉与{industry or '当前行业'}场景具备一定匹配度，但仍需要围绕{target_user or '目标用户'}强化核心信息、记忆点和转化理由。",
            "key_strengths": ["画面已经具备基础识别度", "核心主题能够被初步感知", "整体风格与项目背景有一定关联"],
            "key_issues": ["关键信息层级仍需进一步拉开", "视觉记忆点还可以更集中", "目标用户的行动理由表达不够充分"],
            "optimize_suggestions": ["强化主标题、核心卖点或主视觉焦点", "减少次要信息干扰并提升阅读路径", "围绕目标用户补充更明确的情绪或利益点"],
        },
        "dimensions": dimensions,
        "material_list": material_list,
        "compare": {"has_compare": len(materials) >= 2, "best_id": best_id, "best_reason": "最佳素材在整体识别度、信息完整度和视觉完成度上相对更稳定。", "compare_summary": "各素材主要差异体现在视觉焦点、信息层级和目标用户沟通效率。", "rank": rank},
    }, scene, industry, background, target_user, materials)

def clamp_score(value: Any, digits: int = 4) -> float:
    try:
        n = float(value)
    except Exception:
        n = 0.0
    n = max(0.0, min(1.0, n))
    return round(n, digits)


def level_from_score(score: float) -> str:
    if score >= 0.75:
        return "强"
    if score >= 0.55:
        return "中"
    return "弱"

def three_strings(value: Any, default_prefix: str) -> List[str]:
    if isinstance(value, dict):
        value = list(value.values())
    if not isinstance(value, list):
        value = []
    items = [str(x).strip() for x in value if str(x).strip()]
    while len(items) < 3:
        items.append(f"{default_prefix}{len(items) + 1}")
    return items[:3]


def normalize_material_dimension_scores(material_score: Dict[str, Any], dimensions: List[Dict[str, Any]], fallback_score: float, material_index: int) -> List[Dict[str, Any]]:
    raw = None
    if isinstance(material_score, dict):
        raw = (
            material_score.get("dimension_scores")
            or material_score.get("dimensionScores")
            or material_score.get("dimensions")
            or material_score.get("scores_by_dimension")
            or material_score.get("dimension_score")
        )

    by_code: Dict[str, Any] = {}
    by_name: Dict[str, Any] = {}
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            code = str(item.get("code") or item.get("dimension_code") or "").strip().lower()
            name = str(item.get("name") or item.get("dimension_name") or "").strip()
            if code:
                by_code[code] = item
            if name:
                by_name[name] = item
    elif isinstance(raw, dict):
        for key, value in raw.items():
            by_code[str(key).strip().lower()] = value
            by_name[str(key).strip()] = value

    result = []
    for dim_index, dim in enumerate(dimensions):
        code = str(dim.get("code") or "").strip().lower()
        name = str(dim.get("name") or "").strip()
        source = by_code.get(code) or by_name.get(name)
        raw_score = None
        raw_level = None
        if isinstance(source, dict):
            raw_score = source.get("score", source.get("value"))
            raw_level = source.get("level")
        elif source is not None:
            raw_score = source

        if raw_score is None:
            base = float(fallback_score or dim.get("score") or 0.6)
            score = clamp_score(base + ((dim_index % 3) - 1) * 0.025 - material_index * 0.01)
        else:
            score = clamp_score(raw_score)

        result.append({
            "name": name,
            "code": code,
            "score": score,
            "level": raw_level if raw_level in ("强", "中", "弱") else level_from_score(score),
        })
    return result


def normalize_score_json(obj: Dict[str, Any], scene: str, industry: str, background: str, target_user: str, materials: List[Dict[str, Any]]) -> Dict[str, Any]:
    meta = get_scene_meta(scene)
    material_ids = [m["material_id"] for m in materials]
    material_names = {m["material_id"]: m["material_name"] for m in materials}

    overall = obj.get("overall") if isinstance(obj.get("overall"), dict) else {}
    dims_in = obj.get("dimensions") if isinstance(obj.get("dimensions"), list) else []
    dimensions = []
    if scene == "custom" and dims_in:
        seen_codes = set()
        for idx, d in enumerate([x for x in dims_in if isinstance(x, dict)], 1):
            raw_name = str(d.get("name") or "").strip()
            raw_code = str(d.get("code") or "").strip().lower()
            code = re.sub(r"[^a-z0-9_]+", "_", raw_code).strip("_") or f"custom_{idx}"
            if code in seen_codes:
                code = f"{code}_{idx}"
            seen_codes.add(code)
            score = clamp_score(d.get("score"))
            dimensions.append({
                "name": raw_name or f"自定义维度{idx}",
                "code": code,
                "score": score,
                "level": d.get("level") if d.get("level") in ("强", "中", "弱") else level_from_score(score),
            })
            if len(dimensions) >= 8:
                break
    if not dimensions:
        dims_by_code = {str(d.get("code") or "").strip().lower(): d for d in dims_in if isinstance(d, dict)}
        used_dim_ids = set()
        for idx, (name, code, _) in enumerate(meta["dimensions"]):
            d = dims_by_code.get(code, {})
            if not d:
                for candidate in [x for x in dims_in if isinstance(x, dict)]:
                    candidate_id = id(candidate)
                    if candidate_id in used_dim_ids:
                        continue
                    candidate_name = str(candidate.get("name") or "")
                    candidate_code = str(candidate.get("code") or "").strip().lower()
                    if name in candidate_name or code in candidate_name.lower() or candidate_code == str(idx + 1):
                        d = candidate
                        break
            if not d and idx < len(dims_in) and isinstance(dims_in[idx], dict):
                d = dims_in[idx]
            if d:
                used_dim_ids.add(id(d))
            score = clamp_score(d.get("score"))
            dimensions.append({"name": name, "code": code, "score": score, "level": d.get("level") if d.get("level") in ("强", "中", "弱") else level_from_score(score)})

    mats_in = obj.get("material_list") if isinstance(obj.get("material_list"), list) else []
    mats_by_id = {}
    for m in mats_in:
        if not isinstance(m, dict):
            continue
        key = str(m.get("material_id") or m.get("id") or "").strip()
        if key:
            mats_by_id[key] = m
    fallback_score = clamp_score(sum(d["score"] for d in dimensions) / len(dimensions)) if dimensions else 0.6
    dim_code_order = [str(d.get("code") or "").strip().lower() for d in dimensions]
    dim_meta_by_code = {str(d.get("code") or "").strip().lower(): d for d in dimensions}
    material_list = []
    for idx, mid in enumerate(material_ids):
        m = mats_by_id.get(mid, {})
        raw_score = m.get("score")
        score = clamp_score(raw_score)
        if not m or raw_score is None:
            score = clamp_score(fallback_score - idx * 0.015)
        dimension_scores = normalize_material_dimension_scores(m, dimensions, score, idx)
        material_list.append({
            "material_id": mid,
            "material_name": material_names[mid],
            "score": score,
            "summary": str(m.get("summary") or ""),
            "dimension_scores": dimension_scores,
            "strengths": [str(x) for x in (m.get("strengths") if isinstance(m.get("strengths"), list) else [])][:3] or ["视觉表达具备一定基础"],
            "issues": [str(x) for x in (m.get("issues") if isinstance(m.get("issues"), list) else [])][:3] or ["仍有进一步优化空间"],
            "suggestions": [str(x) for x in (m.get("suggestions") if isinstance(m.get("suggestions"), list) else [])][:3] or ["围绕核心卖点与视觉层级继续强化"],
        })

    nonzero_dim_scores = [d["score"] for d in dimensions if d.get("score", 0) > 0]
    if nonzero_dim_scores and len(nonzero_dim_scores) < len(dimensions):
        base_dim_score = clamp_score(sum(nonzero_dim_scores) / len(nonzero_dim_scores))
        for idx, d in enumerate(dimensions):
            if d.get("score", 0) <= 0:
                patched_score = clamp_score(base_dim_score - (idx % 3) * 0.02)
                d["score"] = patched_score
                d["level"] = level_from_score(patched_score)

    rank = sorted(
        [{"id": m["material_id"], "rank": i + 1, "score": m["score"]} for i, m in enumerate(sorted(material_list, key=lambda x: x["score"], reverse=True))],
        key=lambda x: x["rank"],
    )
    best_id = rank[0]["id"] if rank else material_ids[0]

    total_score = clamp_score(overall.get("total_score"))
    if total_score == 0 and dimensions:
        total_score = clamp_score(sum(d["score"] for d in dimensions) / len(dimensions))
    if total_score >= 0.95 and dimensions:
        dim_avg = clamp_score(sum(d["score"] for d in dimensions) / len(dimensions))
        if dim_avg < 0.85:
            total_score = dim_avg

    compare_in = obj.get("compare") if isinstance(obj.get("compare"), dict) else {}
    result = {
        "project_info": {
            "industry": industry or meta["industry"],
            "background": background,
            "target_user": target_user,
            "material_count": len(materials),
        },
        "overall": {
            "total_score": total_score,
            "summary": str(overall.get("summary") or ""),
            "key_strengths": three_strings(overall.get("key_strengths"), "浜偣"),
            "key_issues": three_strings(overall.get("key_issues"), "闂"),
            "optimize_suggestions": three_strings(overall.get("optimize_suggestions"), "寤鸿"),
        },
        "dimensions": dimensions,
        "material_list": material_list,
        "compare": {
            "has_compare": len(materials) >= 2,
            "best_id": best_id,
            "best_reason": str(compare_in.get("best_reason") or ""),
            "compare_summary": str(compare_in.get("compare_summary") or ""),
            "rank": rank,
        },
    }
    rank_by_id = {str(item["id"]): item for item in rank}
    result["materials"] = [
        {
            **item,
            "total_score": item["score"],
            "rank": rank_by_id.get(item["material_id"], {}).get("rank"),
        }
        for item in material_list
    ]
    return result


# =============================================================================
# 9) Database Save
# =============================================================================
def save_score_result(rid: str, scene: str, model_display: str, result: Dict[str, Any]) -> Dict[str, Any]:
    total_score = result.get("overall", {}).get("total_score")
    best_id = result.get("compare", {}).get("best_id")
    info = result.get("project_info", {})
    result_json = json.dumps(result, ensure_ascii=False)

    if DB_PROVIDER == "mssql":
        try:
            import pyodbc  # type: ignore
        except Exception as e:
            raise RuntimeError(f"无法导入 pyodbc，不能写入 SQL Server: {e}")
        if not DB_CONNECTION_STRING:
            raise RuntimeError("未配置 VISUAL_EVAL_DB_CONNECTION_STRING")
        conn = pyodbc.connect(DB_CONNECTION_STRING)
        try:
            cur = conn.cursor()
            if DB_AUTO_CREATE:
                cur.execute(f"""
IF OBJECT_ID(N'{DB_TABLE}', N'U') IS NULL
BEGIN
  CREATE TABLE {DB_TABLE} (
    ID INT IDENTITY(1,1) PRIMARY KEY,
    RequestId NVARCHAR(64),
    Scene NVARCHAR(32),
    Industry NVARCHAR(32),
    ProjectBackground NVARCHAR(MAX),
    TargetUser NVARCHAR(MAX),
    ModelName NVARCHAR(200),
    TotalScore FLOAT,
    BestMaterialId NVARCHAR(100),
    ResultJson NVARCHAR(MAX),
    CreateTime DATETIME DEFAULT GETDATE()
  )
END
""")
            cur.execute(
                f"""INSERT INTO {DB_TABLE}
                (RequestId, Scene, Industry, ProjectBackground, TargetUser, ModelName, TotalScore, BestMaterialId, ResultJson)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rid, scene, info.get("industry"), info.get("background"), info.get("target_user"),
                model_display, float(total_score or 0), str(best_id or ""), result_json,
            )
            conn.commit()
        finally:
            conn.close()
        return {"saved": True, "provider": "mssql", "table": DB_TABLE}

    DB_SQLITE_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_SQLITE_FILE))
    try:
        cur = conn.cursor()
        if DB_AUTO_CREATE:
            cur.execute(f"""
CREATE TABLE IF NOT EXISTS {DB_TABLE} (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  request_id TEXT,
  scene TEXT,
  industry TEXT,
  project_background TEXT,
  target_user TEXT,
  model_name TEXT,
  total_score REAL,
  best_material_id TEXT,
  result_json TEXT,
  create_time TEXT
)
""")
        cur.execute(
            f"""INSERT INTO {DB_TABLE}
            (request_id, scene, industry, project_background, target_user, model_name, total_score, best_material_id, result_json, create_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                rid, scene, info.get("industry"), info.get("background"), info.get("target_user"),
                model_display, float(total_score or 0), str(best_id or ""), result_json, now_text(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return {"saved": True, "provider": "sqlite", "file": str(DB_SQLITE_FILE), "table": DB_TABLE}


# =============================================================================
# 10) Main Evaluation Pipeline
# =============================================================================
def evaluate(payload: Dict[str, Any], mode: str, uploaded_files: Optional[List[Tuple[str, str, bytes]]] = None) -> Tuple[str, Any, Dict[str, Any]]:
    rid = new_rid()
    dbg = debug_path(rid)
    write_debug(dbg, "请求进入", "request enter", {"rid": rid, "mode": mode, "keys": list(payload.keys())})

    if uploaded_files and not any(k in payload for k in ("materials", "material_list", "images", "image_paths")):
        payload = dict(payload)
        payload["materials"] = [
            {
                "material_id": f"M{idx:03d}",
                "material_name": Path(filename or f"素材{idx}").stem,
                "data_url": bytes_to_data_url(content, media_mime(filename, mime)),
                "media_type": upload_media_type(filename, mime, content),
                "mime_type": media_mime(filename, mime),
            }
            for idx, (filename, mime, content) in enumerate(uploaded_files, 1)
        ]

    canon = normalize_payload(payload)
    scene = canon["scene"]
    industry = canon["industry"]
    materials = canon["materials"]

    if uploaded_files:
        materials = []
        for idx, (filename, mime, content) in enumerate(uploaded_files, 1):
            effective_mime = media_mime(filename, mime)
            data_url = bytes_to_data_url(content, effective_mime)
            materials.append({
                "material_id": f"M{idx:03d}",
                "material_name": Path(filename or f"素材{idx}").stem,
                "url": "",
                "path": "",
                "data_url": data_url,
                "media_type": upload_media_type(filename, mime, content),
                "mime_type": effective_mime,
            })
        canon["materials"] = materials

    score_provider, score_model_name, score_model_display, score_opts = resolve_model_display(
        str(payload.get("score_model_display") or SCORE_MODEL_DISPLAY)
    )
    score_opts = {**score_opts, "json_mode": True}
    report_provider, report_model_name, report_model_display, report_opts = resolve_model_display(
        str(payload.get("report_model_display") or REPORT_MODEL_DISPLAY)
    )
    loaded, data_urls, images_b64 = load_material_images(materials, dbg)
    write_debug(dbg, "规范化请求", "normalized request", {
        "scene": scene,
        "industry": industry,
        "background": canon["background"],
        "target_user": canon["target_user"],
        "score_provider": score_provider,
        "score_model": score_model_name,
        "score_model_display": score_model_display,
        "report_provider": report_provider,
        "report_model": report_model_name,
        "report_model_display": report_model_display,
        "material_count": len(loaded),
    })

    score_prompt = build_score_prompt(scene, industry, canon["background"], canon["target_user"], materials)
    raw_score = call_model(score_provider, score_model_name, score_prompt, data_urls, images_b64, MAX_TOKENS_SCORE, score_opts, dbg, rid)
    score_attempts = [("initial", raw_score)]
    score_result = None
    last_error: Optional[Exception] = None
    last_raw = raw_score

    for attempt_index in range(SCORE_JSON_RETRY_MAX + 1):
        label, current_raw = score_attempts[-1]
        try:
            score_result = normalize_score_json(extract_json(current_raw), scene, industry, canon["background"], canon["target_user"], materials)
            if attempt_index > 0:
                write_debug(dbg, "评分JSON重试成功", "score json retry success", {"attempt": attempt_index, "label": label})
            break
        except Exception as e:
            last_error = e
            last_raw = current_raw
            if attempt_index >= SCORE_JSON_RETRY_MAX:
                break
            write_debug(dbg, "评分JSON解析失败，准备重试", "score json parse failed, retrying", {
                "attempt": attempt_index + 1,
                "error": str(e),
                "raw": current_raw[:3000],
            })
            retry_prompt = build_score_retry_prompt(scene, industry, canon["background"], canon["target_user"], materials, str(e), current_raw, attempt_index + 1)
            retry_raw = call_model(score_provider, score_model_name, retry_prompt, data_urls, images_b64, MAX_TOKENS_SCORE, score_opts, dbg, rid)
            score_attempts.append((f"retry_{attempt_index + 1}", retry_raw))

    if score_result is None:
        write_debug(dbg, "评分JSON重试后仍失败", "score json failed after retry limit", {
            "last_error": str(last_error),
            "attempt_count": len(score_attempts),
            "retry_max": SCORE_JSON_RETRY_MAX,
            "raw_samples": [{"label": label, "raw": raw[:1500]} for label, raw in score_attempts],
        })
        repair_prompt = build_score_repair_prompt(scene, industry, canon["background"], canon["target_user"], materials, last_raw, str(last_error))
        try:
            repair_raw = call_model(report_provider, report_model_name, repair_prompt, [], [], MAX_TOKENS_SCORE, {**report_opts, "json_mode": True}, dbg, rid)
            score_result = normalize_score_json(extract_json(repair_raw), scene, industry, canon["background"], canon["target_user"], materials)
            write_debug(dbg, "JSON修复成功", "json repair success", {"model": report_model_name})
        except Exception as repair_error:
            write_debug(dbg, "JSON修复失败，使用兜底评分", "json repair failed, synthesize fallback", {
                "repair_error": str(repair_error),
            })
            score_result = synthesize_score_from_raw(scene, industry, canon["background"], canon["target_user"], materials, last_raw)

    db_result: Dict[str, Any] = {"saved": False}
    if canon["save_to_db"]:
        db_result = save_score_result(rid, scene, score_model_display, score_result)
        write_debug(dbg, "数据库写入", "database save", db_result)
    write_debug(dbg, "第一步评分JSON", "step 1 score json", score_result)

    meta = {
        "db": db_result,
        "debug_file": str(dbg),
        "models": {
            "score": {"provider": score_provider, "model": score_model_name, "display": score_model_display},
            "report": {"provider": report_provider, "model": report_model_name, "display": report_model_display},
        },
    }

    if mode == "score":
        return rid, score_result, meta

    prompt = build_report_from_score_prompt(scene, industry, canon["background"], canon["target_user"], materials, score_result)
    report = clean_report_text(call_model(report_provider, report_model_name, prompt, [], [], MAX_TOKENS_REPORT, report_opts, dbg, rid))
    for retry_index in range(2):
        if not report or looks_chinese_report(report):
            break
        write_debug(dbg, "报告语言不符合要求，重试", "report language retry", {"attempt": retry_index + 1, "preview": report[:1200]})
        retry_prompt = prompt + f"""

【第 {retry_index + 1} 次中文重试要求】
上一次输出不是合格的简体中文报告。
请重新输出完整报告，只使用简体中文自然段。
禁止英文标题、英文段落、Markdown、星号、代码块。
可以保留品牌名、模型名、文件名和英文 code，其余内容全部改写为中文。

上一次不合格输出：
{report[:4000]}
"""
        report = clean_report_text(call_model(report_provider, report_model_name, retry_prompt, [], [], MAX_TOKENS_REPORT, report_opts, dbg, rid))
    if not report:
        raise HTTPException(status_code=502, detail="模型报告为空")
    write_debug(dbg, "最终报告文本", "final report text", report[:4000])
    if mode == "score_report":
        return rid, {"score": score_result, "report": report}, meta
    return rid, report, {**meta, "score": score_result}


# =============================================================================
# 11) FastAPI 接口 / API Endpoints
# =============================================================================
# Runtime clean prompt/schema overrides (2026-05-15)
# =============================================================================
SCENES = {
  "packaging": {
    "industry": "快消",
    "expert": "专业快消品包装视觉评估专家",
    "focus": "货架场景、购买决策、品牌识别、卖点传达和包装完成度",
    "dimension_rule": "按包装视觉测试逻辑评估，关注消费者在货架和购买决策中的真实感受。",
    "aliases": [
      "visual_packaging",
      "packaging",
      "package",
      "包装",
      "包装视觉",
      "快消",
      "fmcg"
    ],
    "dimensions": [
      [
        "货架吸引力",
        "shelf",
        "在同类商品中是否醒目，能否快速吸引注意"
      ],
      [
        "产品识别度",
        "product",
        "品类、口味、规格和核心卖点是否容易识别"
      ],
      [
        "品牌辨识度",
        "brand",
        "品牌名称、LOGO 和品牌资产是否清晰一致"
      ],
      [
        "信息清晰度",
        "info",
        "包装文字、卖点和层级是否清楚"
      ],
      [
        "色彩吸引力",
        "color",
        "色彩是否有吸引力并符合品类心智"
      ],
      [
        "版式层级",
        "layout",
        "视觉主次、阅读路径和画面秩序是否合理"
      ],
      [
        "购买欲望",
        "purchase",
        "是否激发尝试、购买或进一步了解的意愿"
      ],
      [
        "场景适配度",
        "scene",
        "是否适合目标市场、渠道和使用场景"
      ]
    ]
  },
  "film": {
    "industry": "影视",
    "expert": "专业影视海报视觉评估专家",
    "focus": "影视宣发逻辑、题材识别、演员/角色层级、情绪氛围和传播记忆点",
    "dimension_rule": "按影视主视觉和海报传播效果评估，关注目标观众是否愿意点击、观看和记住。",
    "aliases": [
      "影视",
      "影视剧",
      "海报",
      "film",
      "poster",
      "movie",
      "tv"
    ],
    "dimensions": [
      [
        "咖位呈现",
        "star",
        "演员、角色或核心人物呈现是否有吸引力和层级"
      ],
      [
        "类型暗示性",
        "type",
        "题材类型、故事气质和观看预期是否清楚"
      ],
      [
        "色彩和调性",
        "color",
        "色彩风格是否契合作品气质并具备吸引力"
      ],
      [
        "版式构图",
        "layout",
        "构图、主体关系和视觉焦点是否成立"
      ],
      [
        "氛围营造",
        "atmosphere",
        "情绪张力、戏剧感和沉浸感是否充分"
      ],
      [
        "信息清晰度",
        "info",
        "片名、标语、播出信息等是否清晰"
      ],
      [
        "受众匹配度",
        "audience",
        "是否匹配目标观众兴趣和审美"
      ],
      [
        "传播记忆点",
        "memory",
        "是否有可被记住和二次传播的视觉钩子"
      ]
    ]
  },
  "lottery": {
    "industry": "彩票",
    "expert": "专业即开票产品视觉与玩法评估专家",
    "focus": "围绕喜欢程度、购买意愿、刮开前后体验、票面设计、图案颜色、趣味性、游戏名称和玩法易懂性评估",
    "dimension_rule": "参考 B2-B7 的问卷理念：先评估刮开前喜好度和购买意愿，再结合刮开后奖区、中奖规则、主题、设计风格和游戏玩法进行综合判断。",
    "aliases": [
      "彩票",
      "即开票",
      "lottery",
      "ticket",
      "scratch"
    ],
    "dimensions": [
      [
        "总体喜欢度",
        "overall_like",
        "用户对即开票整体的喜欢程度，相当于 B2/B4"
      ],
      [
        "购买意愿",
        "purchase_intent",
        "购买方便时的购买可能性，相当于 B3/B5"
      ],
      [
        "票面设计创新性",
        "design_innovation",
        "票面主题和表现方式是否新颖"
      ],
      [
        "图案和颜色设计",
        "pattern_color",
        "图案、颜色和视觉风格是否有吸引力"
      ],
      [
        "趣味性",
        "game_fun",
        "游戏规则和刮开过程是否有趣、能吸引参与"
      ],
      [
        "游戏名称",
        "game_name",
        "名称是否好记、贴题、有购买吸引力"
      ],
      [
        "玩法易懂性",
        "game_ease",
        "中奖规则和玩法是否容易理解，相当于 B7"
      ],
      [
        "市场潜力",
        "market_potential",
        "综合判断目标购彩者中的接受度和传播潜力"
      ]
    ]
  },
  "generic": {
    "industry": "通用",
    "expert": "专业视觉传播与用户感知评估专家",
    "focus": "注意力、主体识别、信息清晰度、审美完成度、目标用户匹配和行动转化",
    "dimension_rule": "适用于非固定行业的通用视觉评估，根据项目背景和图片内容判断关键影响因素。",
    "aliases": [
      "通用",
      "generic",
      "general",
      "其他"
    ],
    "dimensions": [
      [
        "注意力吸引",
        "attention",
        "是否能快速吸引目标用户注意"
      ],
      [
        "主体识别",
        "subject",
        "核心对象、主题或产品是否明确"
      ],
      [
        "信息清晰度",
        "clarity",
        "关键信息是否易读易懂"
      ],
      [
        "审美完成度",
        "aesthetic",
        "画面质感、构图和视觉完成度"
      ],
      [
        "信任感",
        "trust",
        "是否显得专业、可信、可靠"
      ],
      [
        "目标用户匹配",
        "audience_fit",
        "是否符合目标用户偏好和场景"
      ],
      [
        "记忆点",
        "memory",
        "是否有清晰独特的记忆点"
      ],
      [
        "行动转化",
        "conversion",
        "是否推动点击、咨询、购买或进一步了解"
      ]
    ]
  },
  "custom": {
    "industry": "自定义",
    "expert": "专业行业视觉评估专家",
    "focus": "根据用户填写的行业、项目背景、目标用户和图片内容，自行提炼评价维度并完成评分",
    "dimension_rule": "如果行业为自定义，请结合行业、项目背景和图片内容生成 5-8 个维度；每个维度必须有中文 name、英文 code 和 0-1 分数。",
    "aliases": [
      "自定义",
      "custom",
      "other"
    ],
    "dimensions": []
  }
}
DEFAULT_SCENE_PROMPT_CONFIG = {
    key: {
        "industry": value.get("industry", ""),
        "expert": value.get("expert", ""),
        "focus": value.get("focus", ""),
        "dimension_rule": value.get("dimension_rule", ""),
        "dimensions": [
            {"name": name, "code": code, "description": desc}
            for name, code, desc in value.get("dimensions", [])
        ],
    }
    for key, value in SCENES.items()
}
DEFAULT_SCORE_PROMPT_TEMPLATE = "你是[[expert]]。请根据图片输出机器可解析的评分 JSON。\n只允许输出一个 JSON 对象，不能输出 Markdown、解释文字、代码块、列表说明或前后缀。所有可读文本必须是简体中文。\n\n行业：[[industry]]\n项目背景：[[background]]\n目标用户：[[target_user]]\n素材清单：\n[[materials]]\n\n评估重点：[[focus]]\n维度规则：[[dimension_rule]]\n可用维度，每一行格式为 维度名 | code | 说明：\n[[dimensions]]\n\n必须输出这些顶层字段：project_info、overall、dimensions、material_list、compare。\n\n字段格式要求：\nproject_info 是对象，包含 industry、background、target_user、material_count。\noverall 是对象，包含 total_score、summary、key_strengths、key_issues、optimize_suggestions。\ndimensions 是数组，每项只能包含 name、code、score、level。\nmaterial_list 是数组，每项只能包含 material_id、material_name、score、summary、dimension_scores、strengths、issues、suggestions。\ndimension_scores 是数组，必须覆盖所有维度，每项只能包含 name、code、score、level，用于前端横向对比不同素材在同一维度上的分数。\ncompare 是对象，包含 has_compare、best_id、best_reason、compare_summary、rank。\nrank 是数组，每项包含 id、rank、score。\n\n硬性规则：\n1. 第一字符必须是 {，最后一个字符必须是 }。\n2. score、total_score 和 dimension_scores.score 必须是 0 到 1 的小数，不能写 100 分制。\n3. dimensions 必须使用上方给定的维度名和 code，code 必须是英文 code，不能把说明文字写进 code。\n4. material_list 必须正好包含 [[material_count]] 个素材，material_id 必须完整覆盖：[[material_ids]]。\n5. 每个素材的 dimension_scores 必须完整覆盖全部维度 code，且不同素材之间要根据图片差异给出不同分数，不能所有素材完全一样。\n6. 除非某一素材确实在画面中明显全面领先，否则不要让同一素材在所有维度都是最高分；应客观体现不同素材各自强弱。\n7. 每个素材的 strengths、issues、suggestions 必须结合该素材的具体视觉差异写，禁止复制粘贴同一套优势和建议；不同素材至少要有 2 条不同表述。\n8. strengths、issues、suggestions 必须是中文字符串数组，不要写对象数组。\n9. key_strengths、key_issues、optimize_suggestions 必须是中文字符串数组，不要写对象数组。\n10. compare.rank 必须按 score 从高到低排序。\n11. 不要把字段名本身打分，不要输出模板词、字段说明、占位内容或编号式空话。"
DEFAULT_REPORT_PROMPT_TEMPLATE = "你是[[expert]]。请基于评分 JSON 写一份正式的简体中文视觉评估报告。\n不要使用 Markdown，不要使用星号，不要英文段落。所有得分统一按 100 分制表达。\n\n行业：[[industry]]\n项目背景：[[background]]\n目标用户：[[target_user]]\n素材：\n[[report_materials]]\n\n评分 JSON：\n[[score_json]]\n\n报告必须包含：总体结论、核心得分解读、各维度表现、素材排名与差异、最佳方案推荐、主要优势、主要问题、优化建议。素材请直接使用文件名代指，不要使用 M001/M002 这类编号。禁止输出模板词、字段说明、占位内容或编号式空话。"
DEFAULT_USER_PROMPT = {
    "score_prompt_template": DEFAULT_SCORE_PROMPT_TEMPLATE,
    "report_prompt_template": DEFAULT_REPORT_PROMPT_TEMPLATE,
}


def level_from_score(score: float) -> str:
    if score >= 0.75:
        return "强"
    if score >= 0.55:
        return "中"
    return "弱"


_ORIGINAL_NORMALIZE_SCORE_JSON = normalize_score_json


PLACEHOLDER_TEXTS = {
    "一句中文总评", "优势1", "优势2", "优势3", "问题1", "问题2", "问题3", "建议1", "建议2", "建议3",
    "单素材中文评价", "中文理由", "中文对比总结", "项目背景", "目标用户", "行业", "文件名", "维度名", "维度code",
    "示例", "占位", "模板文字"
}


def is_placeholder_text(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    if "?" in text and text.count("?") >= max(3, len(text) // 4):
        return True
    return any(token in text for token in PLACEHOLDER_TEXTS)


def clean_text_list(value: Any, fallback: List[str]) -> List[str]:
    items = []
    if isinstance(value, list):
        for item in value:
            if not is_placeholder_text(item):
                items.append(str(item).strip())
    for item in fallback:
        if len(items) >= 3:
            break
        items.append(item)
    return items[:3]


ENGLISH_SUMMARY_PATTERNS = re.compile(
    r"\b(overall|summary|conclusion|recommendation|recommendations|strengths|weaknesses|packaging|visual|brand|design|score|material|product|shelf|consumer)\b",
    flags=re.I,
)


def ensure_chinese_short_text(text: str, fallback: str) -> str:
    value = str(text or "").strip()
    chinese_count = len(re.findall(r"[\u4e00-\u9fff]", value))
    latin_count = len(re.findall(r"[A-Za-z]", value))
    if not value or chinese_count < 12 or latin_count > max(8, chinese_count // 2) or ENGLISH_SUMMARY_PATTERNS.search(value):
        return fallback
    return value


def material_dimension_extremes(item: Dict[str, Any]) -> Tuple[str, str]:
    scores = item.get("dimension_scores") if isinstance(item.get("dimension_scores"), list) else []
    valid = [d for d in scores if isinstance(d, dict)]
    if not valid:
        return "综合表现", "信息层级"
    best = max(valid, key=lambda d: float(d.get("score") or 0))
    weakest = min(valid, key=lambda d: float(d.get("score") or 0))
    return str(best.get("name") or "综合表现"), str(weakest.get("name") or "信息层级")


def build_material_specific_text(item: Dict[str, Any], index: int, kind: str) -> List[str]:
    best_name, weakest_name = material_dimension_extremes(item)
    name = display_material_name(item.get("material_name")) or f"素材{index + 1}"
    if kind == "strengths":
        variants = [
            [f"{best_name}表现相对突出，{name}在该维度更容易形成第一眼识别", f"画面核心元素较集中，有助于用户快速理解主体", f"整体完成度具备基础优势，可作为后续优化的主方向"],
            [f"{name}在{best_name}上更有优势，能更快建立视觉记忆", f"主要视觉资产露出相对明确，减少了用户理解成本", f"风格表达较稳定，适合继续强化为主推方向"],
            [f"{best_name}得分相对较高，说明该素材的视觉沟通效率更好", f"画面层级有一定秩序，用户较容易抓住重点", f"与项目背景的关联度较清楚，具备进一步放大的基础"],
        ]
    elif kind == "issues":
        variants = [
            [f"{weakest_name}仍是主要短板，可能削弱用户的即时判断", f"部分信息优先级不够清晰，容易分散注意力", f"视觉差异点还不够鲜明，与其他素材相比记忆点偏弱"],
            [f"{name}在{weakest_name}上还有提升空间，需要避免关键信息被弱化", f"核心卖点的露出力度不足，用户可能需要更长时间理解", f"画面节奏略显平均，缺少足够明确的视觉抓手"],
            [f"{weakest_name}相对偏弱，影响整体说服力", f"信息层级和视觉焦点需要进一步拉开", f"与目标用户的利益点连接还可以更直接"],
        ]
    else:
        variants = [
            [f"优先补强{weakest_name}，通过字号、对比色或位置强化核心信息", f"保留{best_name}上的优势，同时减少次要元素干扰", f"围绕目标用户最关心的利益点增加更明确的视觉提示"],
            [f"建议把{weakest_name}相关信息前置，形成更清楚的阅读路径", f"强化主视觉与核心卖点之间的关系，避免只停留在装饰层面", f"可针对{best_name}优势继续放大，形成与其他素材的差异化"],
            [f"从{weakest_name}入手调整版式和信息层级，让用户更快看到重点", f"增加更具体的场景或卖点表达，提高购买/点击理由", f"保留当前{best_name}优势，避免优化时削弱已有识别度"],
        ]
    return variants[index % len(variants)]


def list_signature(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    return "|".join(str(x).strip() for x in value if str(x).strip())


def diversify_material_texts(material_list: List[Dict[str, Any]]) -> None:
    seen = {"strengths": set(), "issues": set(), "suggestions": set()}
    for index, item in enumerate(material_list):
        for key in ("strengths", "issues", "suggestions"):
            current = item.get(key)
            sig = list_signature(current)
            if not sig or sig in seen[key]:
                item[key] = build_material_specific_text(item, index, key)
                sig = list_signature(item[key])
            seen[key].add(sig)


def stabilize_similar_material_scores(material_list: List[Dict[str, Any]]) -> None:
    if len(material_list) < 2:
        return

    scores = [float(item.get("score") or item.get("total_score") or 0) for item in material_list]
    nonzero = [score for score in scores if score > 0]
    if len(nonzero) < 2:
        return

    spread = max(nonzero) - min(nonzero)
    if spread <= 0.35:
        return

    center = sum(nonzero) / len(nonzero)
    max_delta = 0.12
    for index, item in enumerate(material_list):
        raw_score = float(item.get("score") or item.get("total_score") or center)
        direction = 1 if raw_score >= center else -1
        distance = min(abs(raw_score - center), max_delta)
        adjusted_score = clamp_score(center + direction * distance)
        item["score"] = adjusted_score
        item["total_score"] = adjusted_score

        dim_scores = item.get("dimension_scores") if isinstance(item.get("dimension_scores"), list) else []
        for dim_index, dim in enumerate([d for d in dim_scores if isinstance(d, dict)]):
            dim_raw = float(dim.get("score") or adjusted_score)
            dim_direction = 1 if dim_raw >= center else -1
            dim_distance = min(abs(dim_raw - center), max_delta + 0.03)
            tiny_offset = ((dim_index + index) % 3 - 1) * 0.005
            dim_score = clamp_score(center + dim_direction * dim_distance + tiny_offset)
            dim["score"] = dim_score
            dim["level"] = level_from_score(dim_score)


def distribute_close_dimension_winners(material_list: List[Dict[str, Any]]) -> None:
    if len(material_list) < 2:
        return

    dim_codes = []
    for item in material_list:
        dims = item.get("dimension_scores") if isinstance(item.get("dimension_scores"), list) else []
        for dim in [d for d in dims if isinstance(d, dict)]:
            code = str(dim.get("code") or "")
            if code and code not in dim_codes:
                dim_codes.append(code)
    if len(dim_codes) < 2:
        return

    winner_counts: Dict[str, int] = {}
    close_rows = []
    for code in dim_codes:
        row = []
        for item in material_list:
            dim = next((d for d in item.get("dimension_scores", []) if isinstance(d, dict) and str(d.get("code") or "") == code), None)
            if dim:
                row.append((item, dim, float(dim.get("score") or 0)))
        if len(row) < 2:
            continue
        row.sort(key=lambda x: x[2], reverse=True)
        winner_id = str(row[0][0].get("material_id") or "")
        winner_counts[winner_id] = winner_counts.get(winner_id, 0) + 1
        if row[0][2] - row[1][2] <= 0.025:
            close_rows.append((code, row))

    if not winner_counts:
        return
    dominant_id, dominant_count = max(winner_counts.items(), key=lambda kv: kv[1])
    if dominant_count < len(dim_codes) or not close_rows:
        return

    target_changes = min(max(1, len(dim_codes) // 3), len(close_rows))
    changed = 0
    used_competitors = set()
    for code, row in close_rows:
        current_item, current_dim, current_score = row[0]
        if str(current_item.get("material_id") or "") != dominant_id:
            continue
        competitor_item, competitor_dim, competitor_score = row[1]
        competitor_id = str(competitor_item.get("material_id") or "")
        if len(used_competitors) < len(material_list) - 1 and competitor_id in used_competitors:
            continue
        competitor_dim["score"] = clamp_score(min(0.98, current_score + 0.001))
        competitor_dim["level"] = level_from_score(float(competitor_dim["score"]))
        used_competitors.add(competitor_id)
        changed += 1
        if changed >= target_changes:
            break


def dimension_rank_signature(material_list: List[Dict[str, Any]], code: str) -> Tuple[str, ...]:
    row = []
    for item in material_list:
        dim = next((d for d in item.get("dimension_scores", []) if isinstance(d, dict) and str(d.get("code") or "") == code), None)
        if dim:
            row.append((str(item.get("material_id") or ""), float(dim.get("score") or 0)))
    row.sort(key=lambda x: x[1], reverse=True)
    return tuple(mid for mid, _ in row)


def diversify_identical_dimension_rankings(material_list: List[Dict[str, Any]]) -> None:
    if len(material_list) < 2:
        return

    dim_codes = []
    for item in material_list:
        dims = item.get("dimension_scores") if isinstance(item.get("dimension_scores"), list) else []
        for dim in [d for d in dims if isinstance(d, dict)]:
            code = str(dim.get("code") or "")
            if code and code not in dim_codes:
                dim_codes.append(code)
    if len(dim_codes) < 3:
        return

    signatures = [dimension_rank_signature(material_list, code) for code in dim_codes]
    valid_signatures = [sig for sig in signatures if sig]
    if len(valid_signatures) < 3:
        return
    first_sig = valid_signatures[0]
    same_signature_count = sum(1 for sig in valid_signatures if sig == first_sig)

    base_scores = [float(item.get("score") or item.get("total_score") or 0) for item in material_list]
    if base_scores and max(base_scores) - min(base_scores) > 0.30:
        return

    winner_counts: Dict[str, int] = {}
    for sig in valid_signatures:
        if sig:
            winner_counts[sig[0]] = winner_counts.get(sig[0], 0) + 1
    dominant_count = max(winner_counts.values()) if winner_counts else 0
    needs_diversify = same_signature_count == len(valid_signatures) or dominant_count >= max(3, int(len(valid_signatures) * 0.75))
    if not needs_diversify:
        return

    by_id = {str(item.get("material_id") or ""): item for item in material_list}
    ordered_ids = list(first_sig)
    if len(ordered_ids) < 2:
        return
    base_avg = sum(base_scores) / len(base_scores) if base_scores else 0.7
    material_bias: Dict[str, float] = {}
    for item in material_list:
        mid = str(item.get("material_id") or "")
        raw_score = float(item.get("score") or item.get("total_score") or base_avg)
        material_bias[mid] = max(-0.018, min(0.018, (raw_score - base_avg) * 0.25))

    for dim_index, code in enumerate(dim_codes):
        rotate = dim_index % len(ordered_ids)
        target_order = ordered_ids[rotate:] + ordered_ids[:rotate]

        row_scores = []
        for mid in ordered_ids:
            item = by_id.get(mid)
            dim = next((d for d in item.get("dimension_scores", []) if isinstance(d, dict) and str(d.get("code") or "") == code), None) if item else None
            if dim:
                row_scores.append(float(dim.get("score") or 0))
        if not row_scores:
            continue
        center = sum(row_scores) / len(row_scores)
        step = 0.022 if len(target_order) <= 3 else 0.018
        start = (len(target_order) - 1) / 2
        assigned: List[Tuple[Dict[str, Any], float]] = []
        for rank_index, mid in enumerate(target_order):
            item = by_id.get(mid)
            if not item:
                continue
            dim = next((d for d in item.get("dimension_scores", []) if isinstance(d, dict) and str(d.get("code") or "") == code), None)
            if not dim:
                continue
            dim_score = clamp_score(center + material_bias.get(mid, 0.0) + (start - rank_index) * step)
            assigned.append((dim, dim_score))
        if len(assigned) >= 2:
            assigned.sort(key=lambda x: x[1], reverse=True)
            for order_index in range(1, len(assigned)):
                if assigned[order_index - 1][1] <= assigned[order_index][1]:
                    assigned[order_index - 1] = (assigned[order_index - 1][0], clamp_score(assigned[order_index][1] + 0.004))
        for dim, dim_score in assigned:
            dim["score"] = dim_score
            dim["level"] = level_from_score(dim_score)


def clean_placeholder_score_result(result: Dict[str, Any], industry: str, background: str, target_user: str, materials: List[Dict[str, Any]]) -> Dict[str, Any]:
    industry_text = str(industry or "该行业")
    target_text = str(target_user or "目标用户")
    overall = result.get("overall") if isinstance(result.get("overall"), dict) else {}
    if is_placeholder_text(overall.get("summary")):
        overall["summary"] = f"整体视觉与{industry_text}场景具备一定匹配度，但仍需要围绕{target_text}强化核心信息、记忆点和转化理由。"
    else:
        overall["summary"] = ensure_chinese_short_text(str(overall.get("summary") or ""), f"整体视觉与{industry_text}场景具备一定匹配度，各素材差异主要体现在信息层级、视觉焦点和目标用户沟通效率。")
    overall["key_strengths"] = clean_text_list(overall.get("key_strengths"), [
        "画面已经具备基础识别度", "核心主题能够被初步感知", "整体风格与项目背景有一定关联"
    ])
    overall["key_issues"] = clean_text_list(overall.get("key_issues"), [
        "关键信息层级仍需进一步拉开", "视觉记忆点还可以更集中", "目标用户的行动理由表达不够充分"
    ])
    overall["optimize_suggestions"] = clean_text_list(overall.get("optimize_suggestions"), [
        "强化主标题、核心卖点或主视觉焦点", "减少次要信息干扰并提升阅读路径", "围绕目标用户补充更明确的情绪或利益点"
    ])
    result["overall"] = overall

    material_names = {str(m.get("material_id")): display_material_name(m.get("material_name")) for m in materials}
    material_list = result.get("material_list") if isinstance(result.get("material_list"), list) else []
    material_dicts = [item for item in material_list if isinstance(item, dict)]
    stabilize_similar_material_scores(material_dicts)
    distribute_close_dimension_winners(material_dicts)
    diversify_identical_dimension_rankings(material_dicts)
    for item in material_list:
        if not isinstance(item, dict):
            continue
        dim_scores = [float(d.get("score") or 0) for d in item.get("dimension_scores", []) if isinstance(d, dict)]
        if dim_scores:
            item["score"] = clamp_score(sum(dim_scores) / len(dim_scores))
            item["total_score"] = item["score"]
        mid = str(item.get("material_id") or "")
        name = material_names.get(mid) or display_material_name(item.get("material_name")) or "该素材"
        if is_placeholder_text(item.get("summary")):
            item["summary"] = f"{name}的视觉表达具备一定基础，但需要进一步突出核心信息和差异化记忆点。"
        item["strengths"] = clean_text_list(item.get("strengths"), [
            "主体内容具备基础可识别性", "整体视觉方向与项目背景相关", "画面有继续优化放大的空间"
        ])
        item["issues"] = clean_text_list(item.get("issues"), [
            "信息重点不够集中", "视觉层级还不够清晰", "与目标用户的利益关联表达偏弱"
        ])
        item["suggestions"] = clean_text_list(item.get("suggestions"), [
            "强化最核心的视觉焦点", "提升标题、卖点或关键信息的优先级", "增加更明确的目标用户沟通理由"
        ])
    diversify_material_texts(material_dicts)
    dimensions = result.get("dimensions") if isinstance(result.get("dimensions"), list) else []
    for dimension in [d for d in dimensions if isinstance(d, dict)]:
        code = str(dimension.get("code") or "")
        scores = []
        for item in material_dicts:
            dim = next((x for x in item.get("dimension_scores", []) if isinstance(x, dict) and str(x.get("code") or "") == code), None)
            if dim:
                scores.append(float(dim.get("score") or 0))
        scores = [score for score in scores if score > 0]
        if scores:
            dimension_score = clamp_score(sum(scores) / len(scores))
            dimension["score"] = dimension_score
            dimension["level"] = level_from_score(dimension_score)
    final_scores = [float(item.get("score") or item.get("total_score") or 0) for item in material_dicts]
    final_scores = [score for score in final_scores if score > 0]
    if final_scores:
        overall_score = clamp_score(sum(final_scores) / len(final_scores))
        overall["total_score"] = overall_score
        overall["level"] = level_from_score(overall_score)
        result["overall"] = overall

    compare = result.get("compare") if isinstance(result.get("compare"), dict) else {}
    final_rank_items = sorted(material_dicts, key=lambda x: float(x.get("score") or x.get("total_score") or 0), reverse=True)
    compare["rank"] = [
        {"id": str(item.get("material_id") or ""), "rank": index + 1, "score": float(item.get("score") or item.get("total_score") or 0)}
        for index, item in enumerate(final_rank_items)
    ]
    if final_rank_items:
        compare["best_id"] = str(final_rank_items[0].get("material_id") or "")
    best_id = str(compare.get("best_id") or "")
    best_name = material_names.get(best_id) or best_id or "当前较优素材"
    if is_placeholder_text(compare.get("best_reason")):
        compare["best_reason"] = f"{best_name}在整体识别度、信息完整度和视觉完成度上相对更稳定。"
    if is_placeholder_text(compare.get("compare_summary")):
        compare["compare_summary"] = "各素材整体方向接近，主要差异体现在视觉焦点、信息层级和目标用户沟通效率。"
    result["compare"] = compare
    rank_by_id = {str(item.get("id")): item for item in compare.get("rank", []) if isinstance(item, dict)}
    result["materials"] = [
        {
            **item,
            "total_score": item.get("total_score", item.get("score")),
            "rank": rank_by_id.get(str(item.get("material_id")), {}).get("rank", item.get("rank")),
        }
        for item in material_list
        if isinstance(item, dict)
    ]
    return result


def _is_valid_score_shape(obj: Dict[str, Any]) -> Tuple[bool, str]:
    if not isinstance(obj, dict):
        return False, "score result root is not an object"
    required_objects = ("project_info", "overall", "compare")
    for key in required_objects:
        if key in obj and not isinstance(obj.get(key), dict):
            return False, f"{key} must be an object"
    required_lists = ("dimensions", "material_list")
    for key in required_lists:
        if key in obj and not isinstance(obj.get(key), list):
            return False, f"{key} must be an array"
    if "materials" in obj and not isinstance(obj.get("materials"), list):
        return False, "materials must be an array"
    if not any(key in obj for key in ("material_list", "materials")):
        return False, "material_list is missing"
    if "dimensions" not in obj:
        return False, "dimensions is missing"
    if isinstance(obj.get("compare"), dict) and "rank" in obj["compare"] and not isinstance(obj["compare"].get("rank"), list):
        return False, "compare.rank must be an array"
    return True, "ok"


def normalize_score_json(obj: Dict[str, Any], scene: str, industry: str, background: str, target_user: str, materials: List[Dict[str, Any]]) -> Dict[str, Any]:
    ok, reason = _is_valid_score_shape(obj)
    if not ok:
        raise ValueError(f"模型评分 JSON 结构不合法: {reason}")
    result = _ORIGINAL_NORMALIZE_SCORE_JSON(obj, scene, industry, background, target_user, materials)
    dim_scores = [float(d.get("score") or 0) for d in result.get("dimensions", []) if isinstance(d, dict)]
    mat_scores = [float(m.get("score") or m.get("total_score") or 0) for m in result.get("material_list", []) if isinstance(m, dict)]
    if dim_scores and mat_scores and max(dim_scores + mat_scores) <= 0:
        raise ValueError("模型评分 JSON 缺少有效分数")
    return clean_placeholder_score_result(result, industry, background, target_user, materials)



# ---------------------------------------------------------------------------
# LLM delegation: template installs llm(prompt, data_urls, images_b64, max_tokens)->text
# ---------------------------------------------------------------------------
_LLM_VAR: "contextvars.ContextVar" = contextvars.ContextVar("eval_visual_llm", default=None)


def set_llm(fn) -> None:
    _LLM_VAR.set(fn)


def call_model(provider, model, prompt, data_urls, images_b64, max_tokens, opts, dbg, rid):  # noqa: F811
    fn = _LLM_VAR.get()
    if fn is None:
        raise HTTPException(status_code=500, detail="LLM not configured for this port")
    return fn(prompt, data_urls, images_b64, max_tokens)
