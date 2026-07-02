# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Default I/O format viewer** — the port editor's prompt menu shows each
  template's endpoints, input/output JSON examples and full default prompt
  (read-only), noting the output format is decided by the prompt.
- **Per-stage prompt editing** — the prompt tab shows one full editor per
  pipeline stage (`AppTemplate.stages`), each pre-filled with its complete
  default prompt (view/restore per stage).
- **Modular endpoint routing** — `AppTemplate.routes` + `app/apps/routing.py`
  `mount_routes` mount endpoints from `extra.routes`
  (`{path, handler, enabled, description, tasks}`), falling back to native
  default paths. A Routes tab configures each path via a submenu.
- **Per-path task flow** — each route can carry its own complete task flow
  (per-path model/prompt/I/O) via `eval_common.ACTIVE_TASKS`; `RouteItem.tasks`
  + a yield-dependency activate it per request. New reusable `TaskFlowEditor`
  Vue component. (Release ships no eval templates, so the Routes tab shows the
  generic note and prompts collapse to a single stage.)
- **Global request serialization** (`app/core/concurrency.py`) — a process-wide
  `BoundedSemaphore` installed as the outermost middleware of every port app
  (`PortRunner.start`): the machine handles at most one mutating port request at
  a time, the rest queue; GET/HEAD/OPTIONS bypass. Limit via
  `HUB_GLOBAL_MAX_CONCURRENCY` (default 1).
- **Auto-unload local models** (`app/models_layer/unload.py`) — default ON:
  after each run the local model is unloaded to free VRAM. Ollama via
  `keep_alive=0` (native path; `/v1` callers get a follow-up native unload),
  LM Studio via its JIT `ttl` request field. `GET/PUT /api/models/auto-unload`
  + a Settings card (toggle + LM Studio ttl seconds). Cloud/OneAPI unaffected;
  an explicit `provider.extra.advanced.keep_alive` still wins.
- Unit-test coverage for the Import Wizard (`/api/importer` JSON extraction and
  auto-fill apply), Task Flows (`_apply_tasks` normalization), and the
  port-conflict guard (`port_in_use` + conflict status / 409-on-start).

### Fixed
- Port Paths table headers no longer stack vertically (`[&>th]:label` collapsed
  to `display:block`; replaced with plain utilities — same Tailwind-v3 issue as
  the earlier Ports/Keys/Users fix).

## [0.1.3] - 2026-06-26

### Added
- **Per-task advanced I/O controls** on each task-flow stage — an expandable
  "Advanced (I/O format)" panel storing its config in `extra.tasks[i].io`:
  - **Generation**: `temperature` / `top_p` / `max_tokens` / sampling mode
    (`both` / `temperature` / `top_p`, for models that reject temp+top_p together).
  - **Input / image**: image detail (`high` / `low` / `auto`), image source,
    video frame count, force-two-stage.
  - **Output format**: output count, per-item char limit, force-JSON.
  - Blank field = use the template default. For generic templates the generation
    params and image detail take effect (task config is the default; values the
    caller sends in the request body still win); output-format knobs are honored
    by pipeline templates. All changes hot-swap (no restart).

## [0.1.2] - 2026-06-08

### Changed
- Removed the 6 evaluation app templates (visual scoring / matching /
  questionnaire pipelines) — they were domain-specific and out of scope for the
  open-source core. Generic Task Flows and the Import Wizard remain. 9 app
  templates ship.

> Note: `v0.1.1` was unpublished (its binaries bundled the removed templates).
> Use `v0.1.2` or later.

## [0.1.1] - 2026-06-08 (unpublished)

### Fixed
- Packaged launcher now auto-selects the next free port when the configured
  port (default 8000) is busy, instead of failing to bind and exiting.
- The console window stays open on a startup error (frozen builds) so the
  message is readable instead of flashing and closing.
- CI: backend test collection (`No module named 'app'`) — added
  `backend/conftest.py` and run tests via `python -m pytest`.

## [0.1.0] - 2026-06-05

First public release. 🎉

### Added

**Core**
- Multi-port service orchestration: register a port service, start/stop, health checks; each exposes an OpenAI-compatible endpoint.
- Unified model layer: model routes (aliases) with primary + fallback chain, guaranteed fallback text, timeout/retry, concurrency cap.
- **Live model hot-swap**: change a running port's model / system prompt / runtime params with no restart.
- Built-in reverse-proxy gateway `/gw/<slug>/...` with optional API-key auth.

**App templates (9)**
- `generic_chat`, `scoring`, `translate`, `vision`, `summarize`, `custom`.
- `embedding` — OpenAI-compatible `/v1/embeddings` for RAG.
- `rerank` — Jina/Cohere-compatible `/v1/rerank`.
- `passthrough` — transparent full OpenAI body passthrough (tools, JSON mode, seed, …).

**Providers**
- Native adapters: OpenAI-compatible, Ollama, Anthropic (Claude), Google Gemini.
- 24 vendor presets across International / China (12 vendors) / Local / Custom groups.
- Custom request headers; multimodal image & video input.

**Load balancing**
- Strategies: weighted-random, round-robin, least-connections, least-VRAM (by GPU usage).
- Failure circuit breaker, pinned-GPU routing, cross-instance fallback.

**Ops**
- GPU/VRAM monitoring (NVML: util/temp/power/fan) with service↔GPU mapping.
- Usage & cost stats (real tokens, per port/model/key, CSV export, per-key trend).
- API key management (quota, usage, cost estimate).
- Local-engine management: one-click connect Ollama/LM Studio; Ollama model list / streamed pull / delete.
- Config backup & migration (export/import providers+routes+ports; secrets redacted by default).
- Reverse-proxy config exporter (Nginx / Caddy).
- Automatic DB column migration on startup.

**PromptLab**
- Reverse-infer a system prompt from input→output examples (text & images); test, save, apply to a port.

**Platform**
- RBAC user management (admin/user, self-lockout guards).
- i18n (zh / en / ja), light/dark theme, hand-drawn *wafu* UI.
- Single-process deployment (backend serves the built frontend); Docker image + Compose.
- CI (pytest + frontend build + image build) and GHCR release workflow.

[Unreleased]: https://github.com/mknjibhuvgyo2/portpilot-ai/compare/v0.1.2...HEAD
[0.1.2]: https://github.com/mknjibhuvgyo2/portpilot-ai/releases/tag/v0.1.2
[0.1.1]: https://github.com/mknjibhuvgyo2/portpilot-ai/releases/tag/v0.1.1
[0.1.0]: https://github.com/mknjibhuvgyo2/portpilot-ai/releases/tag/v0.1.0
