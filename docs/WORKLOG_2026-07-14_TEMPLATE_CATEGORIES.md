# Worklog 2026-07-14: Template categories (agent family groundwork)

## What

Prepare the platform and menus for a future **agent template family**: templates
now carry a category, and the UI groups and badges them accordingly.

## Changes

### Backend

- `app/apps/base.py` — `AppTemplate.category: str = "generic"`
  (`generic` | `eval` | `agent`).
- `app/apps/registry.py` — `list_templates()` exposes `category`; a `_category()`
  helper keeps backward compatibility by classifying any `*_eval` app_type as
  `eval` even if the template class predates the attribute.
- **Fix** `app/apps/routing.py` — the per-path task-flow dependency lazily
  imported `app.apps.eval_common`, a module that is not part of this release;
  any route configured with its own task flow would therefore 500 with
  ImportError at request time. `ACTIVE_TASKS` (contextvar) is now defined in
  `routing.py` itself.

### Frontend

- `views/Ports.vue` — the app-type `<select>` in the port editor groups
  templates with `<optgroup>` by category (order: generic → agent → eval);
  ports whose template is agent-categorized show an "Agent" chip in the list.
- i18n `zh/en/ja` — new `ports.category` block.

## Verification

- Backend: `pytest` 130 passed.
- Frontend: `npm run build` OK.
- This release ships only generic templates, so everything lands in the
  "Generic" group today; the grouping and badge light up as soon as an
  agent-categorized template is registered.
