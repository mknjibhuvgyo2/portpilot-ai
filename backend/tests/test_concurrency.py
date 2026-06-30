"""Global request serialization: with HUB_GLOBAL_MAX_CONCURRENCY=1 (default), the
port apps process at most one mutating request at a time; GET/health bypass the
queue. Verified by firing concurrent requests through the ASGI app and tracking
the peak number in flight."""
import asyncio
import os
import tempfile

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ.setdefault("HUB_DATABASE_URL", f"sqlite:///{_tmp.name.replace(os.sep, '/')}")

import httpx  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from starlette.responses import PlainTextResponse  # noqa: E402
from starlette.routing import Route  # noqa: E402

from app.core.concurrency import GLOBAL_MAX, GlobalSerializeMiddleware  # noqa: E402


def _make_app(state):
    async def work(request):
        state["cur"] += 1
        state["peak"] = max(state["peak"], state["cur"])
        await asyncio.sleep(0.03)
        state["cur"] -= 1
        return PlainTextResponse("ok")

    app = Starlette(routes=[
        Route("/work", work, methods=["POST"]),
        Route("/health", work, methods=["GET"]),
    ])
    app.add_middleware(GlobalSerializeMiddleware)
    return app


async def _peak_for(method: str, path: str, n: int = 6) -> int:
    state = {"cur": 0, "peak": 0}
    app = _make_app(state)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        await asyncio.gather(*(c.request(method, path) for _ in range(n)))
    return state["peak"]


def test_post_requests_are_serialized():
    assert GLOBAL_MAX == 1  # default
    assert asyncio.run(_peak_for("POST", "/work")) == 1   # never two at once


def test_get_requests_bypass_the_queue():
    assert asyncio.run(_peak_for("GET", "/health")) > 1    # health not serialized
