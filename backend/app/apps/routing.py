"""Config-driven endpoint mounting (modular routing, like the task flow).

A template exposes a set of named built-in *handlers*; which public path each is
served at, whether it's enabled, and its human description are configurable per
port via ``config.extra['routes']`` (a list of ``{path, handler, enabled,
description}``). When no route config is present, every handler is mounted at its
native default path — so the original contract (e.g. VT's ``/score_json``) is
unchanged unless the user deliberately edits it.

Each `handlers` entry: ``name -> {"methods": [str], "path": str, "fn": callable,
"description": str, "main": bool?}``. The matching static metadata for the UI is
produced by `routes_meta()` (no callables, safe to JSON-serialize via the
registry).
"""
from __future__ import annotations

from typing import Any

from fastapi import Depends


def _tasks_dependency(tasks: list):
    """A yield-dependency that activates this route's own task flow for the request,
    then restores the previous flow afterwards."""
    async def _dep():
        from app.apps.eval_common import ACTIVE_TASKS
        token = ACTIVE_TASKS.set(tasks)
        try:
            yield
        finally:
            ACTIVE_TASKS.reset(token)
    return _dep


def routes_meta(specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Static route metadata for the frontend route editor (drops callables)."""
    return [{"handler": s["handler"], "method": "/".join(s.get("methods", [])),
             "path": s["path"], "description": s.get("description", ""),
             "main": bool(s.get("main"))}
            for s in specs]


def mount_routes(app, config, handlers: dict[str, dict[str, Any]]) -> None:
    """Register routes on `app` from config.extra['routes'] or the native defaults.

    Safe by construction: unknown handlers and malformed/duplicate (method, path)
    pairs are skipped, `/health` is always reachable, and an empty/missing route
    config falls back to mounting every handler at its native path.
    """
    routes = (config.extra or {}).get("routes")
    if not isinstance(routes, list) or not routes:
        routes = [{"handler": n, "path": h["path"], "enabled": True}
                  for n, h in handlers.items()]

    mounted: set[tuple[str, str]] = set()

    def _add(path: str, h: dict[str, Any], tasks: list | None = None) -> bool:
        methods = [str(m).upper() for m in h.get("methods", [])]
        if any((m, path) in mounted for m in methods):
            return False
        for m in methods:
            mounted.add((m, path))
        # a route may bind its own complete task flow (per-path model/prompt/I/O)
        deps = [Depends(_tasks_dependency(tasks))] if isinstance(tasks, list) and tasks else None
        app.add_api_route(path, h["fn"], methods=methods, dependencies=deps)
        return True

    for r in routes:
        if not isinstance(r, dict) or not r.get("enabled", True):
            continue
        h = handlers.get(str(r.get("handler") or "").strip())
        path = str(r.get("path") or "").strip()
        if not h or not path.startswith("/"):
            continue
        _add(path, h, r.get("tasks"))

    # the health check must always be reachable, at its template's native path
    health = handlers.get("health")
    if health:
        hp = str(health.get("path") or "/health")
        if not any(p == hp for _, p in mounted):
            _add(hp, health)

    # optional back-compat: serve the template's main handler at config.path_alias
    alias = (getattr(config, "path_alias", "") or "").strip()
    if alias.startswith("/"):
        main = next((n for n, h in handlers.items() if h.get("main")), None)
        if main:
            _add(alias, handlers[main])
