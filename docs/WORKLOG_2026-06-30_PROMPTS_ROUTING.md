# Worklog: per-stage prompts & modular per-path routing (release sync)

Date: 2026-06-30
Repo: `D:\kuanopen` / GitHub `mknjibhuvgyo2/portpilot-ai` (public release, builds the 8000 image)

This is the release-side sync of the dev work logged in `ai-port-hub`'s
`docs/WORKLOG_2026-06-30_PROMPTS_ROUTING.md`. The release ships **no eval
templates**, so it gets the mechanism + UI but not the eval-specific content.

## What changed (this session, oldest → newest commit)

1. **`7dacbcd` per-stage prompt editing + modular endpoint routing**
   - `AppTemplate.stages` (single-stage templates synthesize one stage from
     `default_prompt`) — prompt tab shows one full editor per stage.
   - `AppTemplate.routes` + `app/apps/routing.py::mount_routes` (config-driven
     endpoint mounting from `extra.routes`, native defaults when unset).
   - `RouteItem` schema + `PortCreate/Update.routes`; `_apply_tasks` folds routes
     into `extra`. `io_format` viewer + Default I/O format viewer (earlier sync).
2. **`e1538f8` Port Paths headers fix + read-only overview**
   - Fixed vertical table headers (`[&>th]:label` → plain utilities).
3. **`75745b5` per-path task flow via a route submenu**
   - `eval_common.ACTIVE_TASKS` contextvar + `RouteItem.tasks`: each route can
     carry its own complete task flow; `mount_routes` activates it per request.
   - Routes tab accordion with a per-path "配置/Configure" submenu + the new
     reusable `TaskFlowEditor.vue`. Port Paths page = read-only overview with a
     "Configure routes" jump button.

> Note: `app/apps/routing.py` and `eval_common.ACTIVE_TASKS` are carried for
> parity; the release has no eval templates that call `mount_routes`, so route
> config / per-path task flow have no effect here until eval templates are added.

## Verification done

- Backend `pytest`: **122 passed**.
- Frontend build passed.

## Continue from home

1. `git pull` this repo and the dev repo `mknjibhuvgyo2/ai-port-hub` (matching
   worklog: `docs/WORKLOG_2026-06-30_PROMPTS_ROUTING.md`, full detail + 8088 verification).
2. The eval-template prompt completion, short-answer (VT type 6) fix, and 8088
   deploy live only in the dev repo.
