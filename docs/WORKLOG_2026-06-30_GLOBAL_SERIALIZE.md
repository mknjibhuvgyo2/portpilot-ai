# Worklog: global request serialization (release sync)

Date: 2026-06-30
Repo: `D:\kuanopen` / GitHub `mknjibhuvgyo2/portpilot-ai`

Release-side sync of the dev change logged in `ai-port-hub`'s
`docs/WORKLOG_2026-06-30_GLOBAL_SERIALIZE.md` (full detail there). Generic infra,
applies identically to all port templates.

## What changed

- New `backend/app/core/concurrency.py`: process-global
  `threading.BoundedSemaphore(GLOBAL_MAX)` (`GLOBAL_MAX = env
  HUB_GLOBAL_MAX_CONCURRENCY`, default 1) + `GlobalSerializeMiddleware` (ASGI).
- `app/ports/manager.py::PortRunner.start` installs the middleware as the
  outermost layer of every port app: the machine processes at most one mutating
  port request at a time; the rest queue. GET/HEAD/OPTIONS bypass. Main hub API
  is not wrapped.

## Verification

- `backend/tests/test_concurrency.py`: POST peak in-flight == 1; GET peak > 1.
- Backend `pytest`: **124 passed**.
