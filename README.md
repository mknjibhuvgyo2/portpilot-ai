<div align="center">

# PORTHUB · AI Port Hub

**English** · [简体中文](README.zh-CN.md) · [日本語](README.ja.md)

**A self-hosted platform to run many AI service endpoints — one per port —** each bound to an app template with its own system prompt and model routing, exposed to your LAN through one OpenAI-compatible gateway.

[![CI](https://github.com/mknjibhuvgyo2/portpilot-ai/actions/workflows/ci.yml/badge.svg)](https://github.com/mknjibhuvgyo2/portpilot-ai/actions/workflows/ci.yml)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.12-3776AB)
![Vue](https://img.shields.io/badge/Vue-3-42b883)

</div>

---

## 📸 Screenshots

| Dashboard | Ports | PromptLab |
| --- | --- | --- |
| ![dashboard](docs/dashboard.png) | ![ports](docs/ports.png) | ![promptlab](docs/promptlab.png) |

---

## ✨ Features

**Core**
- **Multi-port orchestration** — register a port service, start/stop, health checks; each exposes an OpenAI-compatible endpoint.
- **Unified model layer** — model routes (aliases) with a primary + fallback chain, guaranteed fallback text, timeout/retry, concurrency cap.
- **Live model hot-swap** — change a running port's model / system prompt / runtime params with **no restart**.
- **Reverse-proxy gateway** — `/gw/<slug>/...` forwards to the right port, with optional API-key auth.

**App templates (9)**
`generic_chat` · `scoring` · `translate` · `vision` · `summarize` · `embedding` (`/v1/embeddings`, RAG) · `rerank` (`/v1/rerank`, Jina/Cohere-compatible) · `passthrough` (transparent full OpenAI body: tools / JSON mode / seed …) · `custom`.

**Providers**
- Native adapters: **OpenAI-compatible**, **Ollama**, **Anthropic (Claude)**, **Google Gemini**.
- 24 vendor presets in 4 groups (fill key + model): International (OpenAI/Anthropic/Gemini/Groq/OpenRouter/Mistral/xAI), **China (DeepSeek/Qwen/Kimi/Zhipu GLM/Doubao/Hunyuan/MiniMax/StepFun/01.AI/Baichuan/iFlytek/SiliconFlow)**, Local (Ollama/LM Studio/llama.cpp/vLLM), Custom.
- Custom request headers; multimodal image & video input.

**Load balancing**
- Pool strategies: **weighted-random / round-robin / least-connections / least-VRAM (by GPU usage)**.
- **Failure circuit breaker**, **pinned-GPU routing**, cross-instance fallback.

**Ops**
- **GPU/VRAM monitoring** (NVML: util/temp/power/fan) with a service↔GPU map on the dashboard.
- **Usage & cost stats** — real tokens, per port/model/key, CSV export, per-key trend.
- **API-key management** — quota, usage, cost estimate.
- **Local engine management** — one-click connect Ollama/LM Studio; list / pull (streamed progress) / delete Ollama models.
- **Config backup/migration** — export/import all providers + routes + ports (keys redacted by default, portable across instances).
- **Reverse-proxy export** — generate Nginx / Caddy config.
- **Auto DB migration** — older databases get new columns added on startup.

**PromptLab**
- Give input→output examples (images supported) and **infer a system prompt**; pick constraints, test reproduction, save to the prompt library, one-click apply to a port.

**Platform**
- **RBAC user management** (admin/user, lockout guards).
- **i18n EN / 中文 / 日本語**, light/dark theme, *wafu* (和風) UI.
- Single-process deploy (backend serves the built frontend), **one-command Docker**.

---

## 📦 Downloads

1. **Prebuilt installers** (no Python/Node) — see [Releases](https://github.com/mknjibhuvgyo2/portpilot-ai/releases):
   - Windows x64 — `porthub-<ver>-windows-x64.zip` (unzip, run `porthub.exe`)
   - Linux x64 / arm64 — `.tar.gz` or `.deb` (`sudo dpkg -i porthub_<ver>_amd64.deb`, then `porthub`)
   - macOS x64 / arm64 — `PORTHUB-<ver>-macos-<arch>.zip` (unzip to `PORTHUB.app`)

   Opens `http://localhost:8000` automatically; runtime data lives in a `data/` folder next to the binary. If port 8000 is busy the launcher **auto-picks the next free port**.

2. **Docker image** — published to GHCR: `ghcr.io/mknjibhuvgyo2/portpilot-ai` (see below).
3. **From source** — see Local development.

> macOS Gatekeeper blocks the unsigned `.app` on first open: right-click → Open, or `xattr -dr com.apple.quarantine PORTHUB.app`.

---

## 🚀 Quickstart with Docker

> Requires Docker (with Compose).

**Bash / macOS / Linux**
```bash
git clone https://github.com/mknjibhuvgyo2/portpilot-ai.git porthub
cd porthub
HUB_ADMIN_PASSWORD=change-me docker compose up -d --build
```

**Windows PowerShell**
```powershell
git clone https://github.com/mknjibhuvgyo2/portpilot-ai.git porthub
cd porthub
$env:HUB_ADMIN_PASSWORD = "change-me"; docker compose up -d --build
```

Open **http://localhost:8000** and log in as `admin` with the password you set.

<details>
<summary>Without Compose (plain <code>docker run</code>)</summary>

**Bash** (use `\` to continue lines):
```bash
docker build -t ai-port-hub .
docker run -d -p 8000:8000 -v porthub-data:/app/data \
  -e HUB_ADMIN_PASSWORD=change-me \
  --add-host host.docker.internal:host-gateway \
  ai-port-hub
```

**Windows PowerShell** (PowerShell uses backtick `` ` `` to continue lines — **not** `\`):
```powershell
docker build -t ai-port-hub .
docker run -d -p 8000:8000 -v porthub-data:/app/data `
  -e HUB_ADMIN_PASSWORD=change-me `
  --add-host host.docker.internal:host-gateway `
  ai-port-hub
```

Or as a single line (works in any shell):
```text
docker run -d -p 8000:8000 -v porthub-data:/app/data -e HUB_ADMIN_PASSWORD=change-me --add-host host.docker.internal:host-gateway ai-port-hub
```
</details>

- Runtime data (SQLite / secret key / prompt library) persists in volume `/app/data`.
- Reach host engines (Ollama `:11434` / LM Studio `:1234` / llama.cpp `:8085`) via `host.docker.internal`.
- **GPU/VRAM monitoring**: add `--gpus all` to `docker run` (or the GPU block in the compose file) so NVML inside the container can see the host GPUs — requires the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) (on Windows: Docker Desktop + WSL2 GPU). Without it the dashboard shows "no GPU detected". Re-add the flag every time you recreate the container.
- Port services you create bind their own ports inside the container: publish with `-p` as needed, or use `--network host` on Linux (the compose file has an example).
- Pushing a `vX.Y.Z` tag builds & publishes the image to GHCR (`.github/workflows/release.yml`).

---

## 🛠️ Local development

**Requirements**: Python 3.12+, Node 20+ (22 recommended).

```bash
# backend
cd backend
python -m venv .venv
# Windows: ./.venv/Scripts/python -m pip install -r requirements.txt
# *nix:    ./.venv/bin/python   -m pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# frontend (another terminal)
cd frontend
npm install
npm run dev        # http://localhost:5173 (proxies /api and /gw to :8000)
npm run build      # dist/ is served by the backend on :8000 (production)
```

One-shot scripts: `./scripts/start.sh` (macOS/Linux) or `./scripts/start.ps1` (Windows); pass `dev` for dev mode. The first run prints the admin password (or set `HUB_ADMIN_PASSWORD` in `backend/.env`).

**Test / build**
```bash
cd backend && pytest -q          # backend unit tests
cd frontend && npm run build     # type-check + build
```

---

## 🔌 Usage

1. **Models & Providers** → add a provider (vendor preset or local Ollama).
2. Add a **Model Route**: primary model + fallback chain + load-balancing strategy + fallback text.
3. **Ports** → New: name, slug, port, app type, model route, system prompt → Start.
4. Call from the LAN:
   - Via gateway (recommended — auth + usage stats): `POST http://<host>:8000/gw/<slug>/v1/chat/completions`
   - Direct: `POST http://<host>:<port>/v1/chat/completions`

```bash
curl http://<host>:8000/gw/<slug>/v1/chat/completions \
  -H "Authorization: Bearer <api-key>" -H "Content-Type: application/json" \
  -d '{"model":"<route>","messages":[{"role":"user","content":"hello"}]}'
```

---

## 🏗️ Architecture

```
backend/   FastAPI + SQLAlchemy(SQLite) + httpx — API / model routing / port orchestration / gateway
frontend/  Vue 3 + Vite + TS + Tailwind + Pinia — admin UI (EN / 中文 / 日本語)
data/      runtime: sqlite / secret key / prompt library / logs (git-ignored)
```

The backend serves the built frontend in a single process — production is one `uvicorn` command or one Docker container.

---

## 🔐 Security

- **Change the default admin password** with `HUB_ADMIN_PASSWORD`; if unset, a random one is generated and printed to the console once.
- **JWT secret**: `HUB_SECRET_KEY`; if empty it's generated and stored at `data/secret.key` — set it explicitly to keep sessions across restarts/instances.
- **Never commit** `backend/.env` or `data/` (contains `secret.key`, `hub.db`) — already in `.gitignore`.
- **Gateway vs direct**: direct ports bypass the gateway — **no API-key check, not counted toward usage**; use `/gw/<slug>/` for auth/metering.
- **LAN-oriented**: to expose publicly, put it behind a reverse proxy (Nginx/Caddy — exportable from Settings) + TLS, and enable "gateway requires API key".
- Report vulnerabilities privately per [SECURITY.md](SECURITY.md).

---

## 💛 Sponsor

**I'm broke — please give me money. 🙏**

WeChat tip code:

<img src="docs/sponsor-wechat.jpg" alt="WeChat tip code" width="240">

---

## 🗺️ Roadmap

- [ ] More app templates (audio/ASR, image-generation proxy)
- [ ] Finer VRAM-threshold / mixed-weight scheduling
- [ ] Usage-trend charts, Prometheus metrics export
- [ ] Wizard to migrate standalone scripts into app templates
- [ ] End-to-end tests / Playwright

Suggestions welcome in Issues. Shipped capabilities: see Features above and [CHANGELOG.md](CHANGELOG.md).

## 🤝 Contributing

PRs welcome! Read [CONTRIBUTING.md](CONTRIBUTING.md) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) first. Use the [issue templates](.github/ISSUE_TEMPLATE).

## 📄 License

[GPL-3.0](LICENSE) © PORTHUB contributors
