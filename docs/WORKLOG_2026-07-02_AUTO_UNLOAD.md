# Worklog: auto-unload local models (release sync)

Date: 2026-07-02
Repo: `D:\kuanopen` / GitHub `mknjibhuvgyo2/portpilot-ai`

Release-side sync of the dev change logged in `ai-port-hub`'s
`docs/WORKLOG_2026-07-02_AUTO_UNLOAD.md` (full detail + live 8088 verification
there). Generic model-layer infra, applies to all port templates.

## What changed

- New `backend/app/models_layer/unload.py`: `auto_unload` Setting
  (`{enabled, lms_ttl}`, default `{true, 60}`, 5s cache) +
  `inject_request_unload` (LM Studio `ttl`) + `native_unload_sync`
  (Ollama native `keep_alive:0` unload; strips `/v1`).
- `providers/base.py` gains `db_kind` (set by `router.build_provider`).
- `providers/ollama.py::_keep_alive`: explicit provider setting wins, else `0`
  when auto-unload is on.
- `providers/openai_compat.py::_body`: injects `ttl` for LM Studio.
- API `GET/PUT /api/models/auto-unload`; Settings card (toggle + ttl), i18n
  zh/en/ja (`settings.au*`).

## Verification

- `tests/test_unload.py` (4 cases); backend `pytest`: **128 passed**; frontend
  builds. Dev side additionally verified live on 8088 (model gone from
  `/api/ps` right after a run).
