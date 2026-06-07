"""Built-in app templates.

All of these reuse the OpenAI-compatible generic-chat engine but ship distinct
titles + suggested default system prompts, so picking a template gives the user
a sensible starting point. `custom` is a blank template for free configuration.
"""
from __future__ import annotations

from app.apps.generic_chat import GenericChatTemplate


class ScoringTemplate(GenericChatTemplate):
    app_type = "scoring"
    title = "评分助手 / Scoring"
    description = "对输入按给定标准打分，输出分数与简要理由。"
    default_prompt = (
        "你是严格、客观的评分助手。请根据用户给定的标准对输入进行评分，"
        "输出一个分数（如 0-100）并附简要理由。只输出评分结果，不要多余寒暄。"
    )


class TranslateTemplate(GenericChatTemplate):
    app_type = "translate"
    title = "翻译 / Translate"
    description = "在中英日等语言间准确翻译，仅输出译文。"
    default_prompt = (
        "你是专业翻译。准确、地道地翻译用户输入；如未指定目标语言，"
        "中文输入译为英文、其他语言译为中文。只输出译文，不加解释。"
    )


class VisionTemplate(GenericChatTemplate):
    app_type = "vision"
    title = "视觉 / 多模态 Vision"
    description = "分析图像/视频内容（需后端模型支持多模态）。"
    default_prompt = (
        "你是视觉分析助手。仔细观察用户提供的图像或视频，"
        "客观描述其内容、要点与可回答的问题。"
    )


class SummarizeTemplate(GenericChatTemplate):
    app_type = "summarize"
    title = "摘要 / Summarize"
    description = "对长文本提炼要点与结论。"
    default_prompt = (
        "你是摘要助手。对用户输入的长文本提炼核心要点、结论与关键信息，"
        "用简洁的条目呈现。"
    )


class CustomTemplate(GenericChatTemplate):
    app_type = "custom"
    title = "自定义 / Custom"
    description = "通用 OpenAI 兼容端点，完全由你的系统提示词与参数定义。"
    default_prompt = ""
