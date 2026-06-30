"""Process-wide request serialization for the port services.

Each port runs in its own thread with its own asyncio event loop (see
`app/ports/manager.py`), so an `asyncio.Semaphore` cannot synchronize across
ports. We use a process-global `threading.BoundedSemaphore` instead, acquired
from async code via `asyncio.to_thread` so the event loop is never blocked while
waiting — requests simply queue.

Default limit = 1: the machine processes at most one port request at a time,
everything else queues (no concurrent model inference). Raise via the env var
`HUB_GLOBAL_MAX_CONCURRENCY` (then restart) if the hardware can do more.

The guard is installed once in `PortRunner.start()` as the outermost middleware
of every port app, so it covers all templates. The main hub/admin API is NOT
wrapped, so the dashboard stays responsive. GET/HEAD/OPTIONS (health, info,
models, prompt reads) bypass the queue; only mutating requests (POST/...) — i.e.
the model-bearing endpoints — are serialized. The slot is held for the entire
response, including streamed bodies, because the ASGI app coroutine only returns
after the last body chunk is sent.
"""
from __future__ import annotations

import asyncio
import os
import threading

GLOBAL_MAX = max(1, int(os.getenv("HUB_GLOBAL_MAX_CONCURRENCY", "1") or "1"))
_slots = threading.BoundedSemaphore(GLOBAL_MAX)

# methods that never need a slot (read-only / health / CORS preflight)
_EXEMPT_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


async def acquire() -> None:
    await asyncio.to_thread(_slots.acquire)


def release() -> None:
    try:
        _slots.release()
    except ValueError:  # released more than acquired — ignore
        pass


class GlobalSerializeMiddleware:
    """ASGI middleware: serialize mutating requests across all ports to GLOBAL_MAX."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("method", "GET").upper() in _EXEMPT_METHODS:
            await self.app(scope, receive, send)
            return
        await acquire()
        try:
            await self.app(scope, receive, send)
        finally:
            release()
