<div align="center">

# PORTHUB · AI Port Hub

**自托管的多端口 AI 服务管理平台** — 一台机器开多个端口，每个端口绑定一个应用模板（独立系统提示词 + 模型路由），供内网统一调用。

*A self-hosted platform to run many AI service endpoints (one per port), each bound to an app template with its own system prompt and model routing.*

[![CI](https://github.com/mknjibhuvgyo2/portpilot-ai/actions/workflows/ci.yml/badge.svg)](https://github.com/mknjibhuvgyo2/portpilot-ai/actions/workflows/ci.yml)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.12-3776AB)
![Vue](https://img.shields.io/badge/Vue-3-42b883)

</div>


---

## 📸 截图 / Screenshots

| 监控总览 Dashboard | 端口编辑 Ports | 提示词逆推 PromptLab |
| --- | --- | --- |
| ![dashboard](docs/dashboard.png) | ![ports](docs/ports.png) | ![promptlab](docs/promptlab.png) |

---

## ✨ 功能 / Features

**核心 / Core**
- **多端口服务编排**：注册端口服务 → 一键启动/停止 → 健康检查；每个服务以 OpenAI 兼容协议对外。
- **统一模型层**：模型路由（别名）→ 主模型 + 回退链 + 兜底文本 + 超时重试 + 并发上限。
- **模型热切换（hot-swap）**：运行中修改模型/提示词/运行参数**即时生效，无需重启**端口。
- **反向代理网关**：`/gw/<slug>/...` 转发到对应端口，可选 API Key 鉴权。

**应用模板 / App templates（9 种）**
`generic_chat`（通用聊天/评分）· `scoring` · `translate` · `vision` · `summarize` · `embedding`（`/v1/embeddings`，RAG 向量化）· `rerank`（`/v1/rerank`，Jina/Cohere 兼容精排）· `passthrough`（透传完整 OpenAI 请求体：tools/JSON 模式/seed…）· `custom`。

**模型厂商 / Providers**
- 原生适配：**OpenAI 兼容**、**Ollama**、**Anthropic（Claude）**、**Google Gemini**。
- 厂商预设 24 项 4 组（填 key+model 即用）：国际（OpenAI/Anthropic/Gemini/Groq/OpenRouter/Mistral/xAI）、**中国（DeepSeek/通义千问/Kimi/智谱 GLM/豆包/混元/MiniMax/阶跃/零一/百川/讯飞星火/硅基流动）**、本地（Ollama/LM Studio/llama.cpp/vLLM）、自定义。
- 自定义请求头、多模态（图片/视频）输入。

**负载均衡 / Load balancing**
- 多实例池策略：**加权随机 / 轮询 / 最少连接 / 最少显存（按 GPU 占用）**。
- **失败自动熔断**（连续失败降级 15s）、**指定 GPU**（pin GPU）、跨实例回退。

**运维 / Ops**
- **GPU/显存监控**（NVML：占用/温度/功耗/风扇），监控总览展示「服务↔显卡」映射。
- **用量/成本统计**：真实 token + 按端口/模型/Key 维度、CSV 导出、每 Key 趋势图。
- **API Key 管理**：配额、用量、成本估算。
- **本地引擎管理**：一键连接 Ollama/LM Studio，Ollama 模型列表/下载（流式进度）/删除。
- **配置备份/迁移**：导出/导入全部 providers+routes+ports（密钥默认脱敏，跨实例可移植）。
- **反代配置导出**：Nginx / Caddy 配置一键生成。
- **数据库自动迁移**：模型加字段时旧库启动自动补列。

**提示词逆推 / PromptLab**
- 给「输入→输出」样例（支持图片），自动**逆推出系统提示词**；可勾选约束、测试复现、保存到提示词库、一键应用到端口。

**平台 / Platform**
- **RBAC 用户管理**（管理员/普通，防自锁守卫）。
- **i18n 中 / 英 / 日**，**明暗主题**，和风（wafu）UI。
- 单进程部署（后端托管前端静态包），**Docker** 一键启动。

---

## 🚀 5 分钟 Docker 启动 / Quickstart

> 需要 Docker（含 Docker Compose）。

```bash
git clone https://github.com/mknjibhuvgyo2/portpilot-ai.git porthub
cd porthub

# 用 compose（推荐）— 设置首启管理员密码
HUB_ADMIN_PASSWORD=change-me docker compose up -d --build
```

打开 **http://localhost:8000**，用 `admin` / 你设置的密码登录。

不用 compose 也行：

```bash
docker build -t ai-port-hub .
docker run -d -p 8000:8000 -v porthub-data:/app/data \
  -e HUB_ADMIN_PASSWORD=change-me \
  --add-host host.docker.internal:host-gateway \
  ai-port-hub
```

- 运行时数据（SQLite / 密钥 / 提示词库）持久化在卷 `/app/data`。
- 访问宿主机本地引擎（Ollama `:11434` / LM Studio `:1234` / llama.cpp `:8085`）用 `host.docker.internal`。
- 你在 UI 创建的**端口服务**会在容器内绑定各自端口：按需 `-p` 发布，或 Linux 上用 `--network host`（compose 文件含示例）。
- 打 `vX.Y.Z` tag 时，GitHub Actions 自动构建并发布镜像到 GHCR（见 `.github/workflows/release.yml`）。

---

## 🛠️ 本地开发 / Local development

**要求**：Python 3.12+、Node 20+（推荐 22）。

```bash
# 后端
cd backend
python -m venv .venv
# Windows: ./.venv/Scripts/python -m pip install -r requirements.txt
# *nix:    ./.venv/bin/python   -m pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# 前端（另开终端）
cd frontend
npm install
npm run dev        # http://localhost:5173 （已代理 /api 与 /gw 到 :8000）
npm run build      # 产物 dist/ 由后端在 :8000 直接托管（生产）
```

一键脚本：`./scripts/start.sh`（macOS/Linux）或 `./scripts/start.ps1`（Windows），`dev` 参数进开发模式。

首次启动控制台会打印管理员密码（或在 `backend/.env` 设 `HUB_ADMIN_PASSWORD`；参考 `backend/.env.example`）。

**测试 / 构建**

```bash
cd backend && pytest -q          # 后端单元测试（100+ 用例）
cd frontend && npm run build     # 前端类型检查 + 构建
```

---

## 🔌 使用 / Usage

1. **模型与厂商** → 新增厂商/端点（选厂商预设或本地 Ollama）。
2. 新增**模型路由**：主模型 + 回退链 + 负载均衡策略 + 兜底文本。
3. **端口服务** → 新建：名称、slug、端口、应用类型、模型路由、系统提示词 → 启动。
4. 内网客户端调用：
   - 经网关（推荐，带鉴权+用量统计）：`POST http://<host>:8000/gw/<slug>/v1/chat/completions`
   - 直连端口：`POST http://<host>:<port>/v1/chat/completions`

```bash
curl http://<host>:8000/gw/<slug>/v1/chat/completions \
  -H "Authorization: Bearer <api-key>" -H "Content-Type: application/json" \
  -d '{"model":"<route>","messages":[{"role":"user","content":"你好"}]}'
```

---

## 🏗️ 架构 / Architecture

```
backend/   FastAPI + SQLAlchemy(SQLite) + httpx — API / 模型路由 / 端口编排 / 网关
frontend/  Vue 3 + Vite + TS + Tailwind + Pinia — 管理 UI（中/英/日）
data/      运行时：sqlite / 密钥 / 提示词库 / 日志（git 忽略）
```

后端单进程即可托管前端静态包；生产部署一条 `uvicorn` 命令或一个 Docker 容器。

---

## 🔐 安全注意事项 / Security

- **请务必修改默认管理员密码**：用 `HUB_ADMIN_PASSWORD` 设定；不设则首启随机生成并打印到控制台一次。
- **JWT 密钥**：`HUB_SECRET_KEY` 留空时自动生成并存到 `data/secret.key`；多实例/重启保持登录请显式设置。
- **不要提交** `backend/.env`、`data/`（含 `secret.key`、`hub.db`）— 已在 `.gitignore` 中。
- **网关 vs 直连**：直连端口绕过网关，**不校验 API Key、不计用量**；需鉴权/计量请走 `/gw/<slug>/`。
- **面向内网部署**：对公网暴露请置于反向代理（Nginx/Caddy，设置页可导出配置）+ TLS 之后，并启用端口的「网关需 API Key」。
- 发现漏洞请按 [SECURITY.md](SECURITY.md) 私下报告，勿开公开 issue。

---

## 🗺️ 路线图 / Roadmap

- [ ] 更多应用模板（如音频/ASR、图像生成代理）
- [ ] 更细的显存阈值/混合权重调度策略
- [ ] 用量统计趋势图增强、Prometheus 指标导出
- [ ] 现有独立脚本 → 应用模板迁移向导
- [ ] 端到端测试 / Playwright

欢迎在 Issues 提建议。已实现的能力见上方功能列表与 [CHANGELOG.md](CHANGELOG.md)。

---

## 🤝 贡献 / Contributing

欢迎 PR！请先读 [CONTRIBUTING.md](CONTRIBUTING.md)（开发流程、测试要求、提交规范）与 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。
Bug / 功能请用 [Issue 模板](.github/ISSUE_TEMPLATE)。

## 📄 License

[GPL-3.0](LICENSE) © PORTHUB contributors
