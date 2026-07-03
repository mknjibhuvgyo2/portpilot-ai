# Worklog: template params mechanism (release sync)

Date: 2026-07-02
Repo: `D:\kuanopen` / GitHub `mknjibhuvgyo2/portpilot-ai`

Release-side sync of the dev feature logged in `ai-port-hub`'s
`docs/WORKLOG_2026-07-02_TEMPLATE_PARAMS.md` (full detail there). Goal: every
template behavior knob is declared and editable in the UI, so a template can be
rebuilt from scratch and customized without touching code.

## What changed (mechanism only)

- `AppTemplate.params_schema` (`{key,label,type,default,description,group}`,
  type = number|bool|text|textarea|json) exposed via `/api/ports/templates`.
- Values stored in `extra.params` (hot-swappable); `PortCreate/Update.params`
  + `_apply_tasks` folding; editing flags follow the tasks/routes pattern.
- Port editor: new "Template Params" tab — dynamic form rendered from the
  schema; json fields edited as text and validated on save (errors jump back
  to the tab); defaults shown as placeholder + hint with a fill-default button.
- i18n zh/en/ja (`ports.params.*`, tab label).

Release templates declare no params yet, so the tab shows the "no extra params"
note; the dev repo's six eval templates use it heavily (scoring retry/frames,
matcher caps, visual scene JSON, analyze threshold, chat memory, questionnaire
user-prompt template). The dev-only `eval-media` settings endpoint/card was not
synced (release has no `eval_common`).

## Verification

- Backend `pytest`: **128 passed**; frontend builds.
