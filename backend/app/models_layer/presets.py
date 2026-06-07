"""Provider presets: well-known vendors with their kind + base_url.

Sent to the frontend so users pick a vendor instead of hand-typing base_url.
Most Chinese & international vendors expose an OpenAI-compatible endpoint, so
they use kind="openai_compat"; Anthropic and Gemini use their native adapters.
The user still supplies the API key (and model name). `custom` is a blank
template for anything not listed.
"""
from __future__ import annotations

# group: intl | cn | local | custom
PROVIDER_PRESETS: list[dict] = [
    # ---- international ----
    {"id": "openai", "kind": "openai_compat", "base_url": "https://api.openai.com/v1", "group": "intl"},
    {"id": "anthropic", "kind": "anthropic", "base_url": "https://api.anthropic.com", "group": "intl"},
    {"id": "gemini", "kind": "gemini", "base_url": "https://generativelanguage.googleapis.com", "group": "intl"},
    {"id": "groq", "kind": "openai_compat", "base_url": "https://api.groq.com/openai/v1", "group": "intl"},
    {"id": "openrouter", "kind": "openai_compat", "base_url": "https://openrouter.ai/api/v1", "group": "intl"},
    {"id": "mistral", "kind": "openai_compat", "base_url": "https://api.mistral.ai/v1", "group": "intl"},
    {"id": "xai", "kind": "openai_compat", "base_url": "https://api.x.ai/v1", "group": "intl"},
    # ---- China ----
    {"id": "deepseek", "kind": "openai_compat", "base_url": "https://api.deepseek.com/v1", "group": "cn"},
    {"id": "qwen", "kind": "openai_compat", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "group": "cn"},
    {"id": "kimi", "kind": "openai_compat", "base_url": "https://api.moonshot.cn/v1", "group": "cn"},
    {"id": "zhipu", "kind": "openai_compat", "base_url": "https://open.bigmodel.cn/api/paas/v4", "group": "cn"},
    {"id": "doubao", "kind": "openai_compat", "base_url": "https://ark.cn-beijing.volces.com/api/v3", "group": "cn"},
    {"id": "hunyuan", "kind": "openai_compat", "base_url": "https://api.hunyuan.cloud.tencent.com/v1", "group": "cn"},
    {"id": "minimax", "kind": "openai_compat", "base_url": "https://api.minimax.chat/v1", "group": "cn"},
    {"id": "stepfun", "kind": "openai_compat", "base_url": "https://api.stepfun.com/v1", "group": "cn"},
    {"id": "yi", "kind": "openai_compat", "base_url": "https://api.lingyiwanwu.com/v1", "group": "cn"},
    {"id": "baichuan", "kind": "openai_compat", "base_url": "https://api.baichuan-ai.com/v1", "group": "cn"},
    {"id": "spark", "kind": "openai_compat", "base_url": "https://spark-api-open.xf-yun.com/v1", "group": "cn"},
    {"id": "siliconflow", "kind": "openai_compat", "base_url": "https://api.siliconflow.cn/v1", "group": "cn"},
    # ---- local runtimes ----
    {"id": "ollama", "kind": "ollama", "base_url": "http://127.0.0.1:11434", "group": "local"},
    {"id": "lmstudio", "kind": "lmstudio", "base_url": "http://127.0.0.1:1234/v1", "group": "local"},
    {"id": "llamacpp", "kind": "llamacpp", "base_url": "http://127.0.0.1:8080/v1", "group": "local"},
    {"id": "vllm", "kind": "openai_compat", "base_url": "http://127.0.0.1:8000/v1", "group": "local"},
    # ---- custom (blank template) ----
    {"id": "custom", "kind": "openai_compat", "base_url": "", "group": "custom"},
]
