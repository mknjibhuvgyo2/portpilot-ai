"""Port service orchestration.

Each registered PortService is run as an independent uvicorn server on its own
port, inside a background thread with its own event loop. The manager can
start/stop them and report whether they are alive.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time

import httpx
import uvicorn

from app.apps.base import PortConfig
from app.apps.registry import get_template

log = logging.getLogger("hub.ports")

# Config fields the running subprocess re-reads per request (or rebuilds lazily,
# e.g. the concurrency semaphore), so they can be hot-swapped without a restart.
# Excludes id/port/slug/app_type, which are bound at process start.
HOT_SWAP_FIELDS = frozenset({
    "name", "model_alias", "system_prompt", "streaming", "concurrency",
    "timeout", "max_retries", "logging_enabled", "log_keep", "extra",
})


class PortRunner:
    def __init__(self, config: PortConfig):
        self.config = config
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        template = get_template(self.config.app_type)
        if not template:
            raise ValueError(f"Unknown app_type: {self.config.app_type}")
        app = template.build_app(self.config)
        uconfig = uvicorn.Config(
            app, host="0.0.0.0", port=self.config.port,
            log_level="warning", loop="asyncio",
        )
        self._server = uvicorn.Server(uconfig)
        # uvicorn installs signal handlers only on the main thread; disable here.
        self._server.install_signal_handlers = lambda: None

        def _run() -> None:
            asyncio.run(self._server.serve())

        self._thread = threading.Thread(target=_run, name=f"port-{self.config.port}", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        if self._server:
            self._server.should_exit = True
        if self._thread:
            self._thread.join(timeout=timeout)
        self._server = None
        self._thread = None

    @property
    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())


class PortManager:
    def __init__(self) -> None:
        self._runners: dict[int, PortRunner] = {}
        self._lock = threading.Lock()

    def start(self, config: PortConfig) -> None:
        with self._lock:
            existing = self._runners.get(config.id)
            if existing and existing.alive:
                return
            runner = PortRunner(config)
            runner.start()
            self._runners[config.id] = runner

    def stop(self, port_id: int) -> None:
        with self._lock:
            runner = self._runners.pop(port_id, None)
        if runner:
            runner.stop()

    def update_config(self, port_id: int, fields: dict) -> bool:
        """Hot-swap a running port's config in place (no restart).

        Mutates the live PortConfig that the subprocess app reads at request
        time, so model alias / system prompt / runtime knobs take effect on the
        next request. Non-hot-swappable or unknown keys are ignored. Returns
        True iff a live runner was found and updated.
        """
        with self._lock:
            runner = self._runners.get(port_id)
            if not runner or not runner.alive:
                return False
            for k, v in fields.items():
                if k in HOT_SWAP_FIELDS and hasattr(runner.config, k):
                    setattr(runner.config, k, v)
            if "extra" in fields:  # keep derived debug flag in sync with extra
                runner.config.debug = bool((runner.config.extra or {}).get("debug"))
            return True

    def is_running(self, port_id: int) -> bool:
        runner = self._runners.get(port_id)
        return bool(runner and runner.alive)

    def running_ids(self) -> list[int]:
        return [pid for pid, r in self._runners.items() if r.alive]

    def stop_all(self) -> None:
        for pid in list(self._runners.keys()):
            self.stop(pid)


manager = PortManager()


async def health_check(port: int, timeout: float = 4.0) -> bool:
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            r = await client.get(f"http://127.0.0.1:{port}/health")
            return r.status_code == 200
    except Exception:
        return False


def port_in_use(port: int) -> bool:
    """True if something is already listening on 127.0.0.1:<port> (any process)."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        try:
            return s.connect_ex(("127.0.0.1", int(port))) == 0
        except OSError:
            return False


def config_from_row(row) -> PortConfig:
    """Build a thread-safe PortConfig snapshot from a PortService ORM row."""
    return PortConfig(
        id=row.id, name=row.name, slug=row.slug, port=row.port,
        app_type=row.app_type, model_alias=row.model_alias,
        system_prompt=row.system_prompt, streaming=row.streaming,
        concurrency=row.concurrency, timeout=row.timeout, max_retries=row.max_retries,
        logging_enabled=row.logging_enabled, log_keep=row.log_keep,
        auth_required=row.auth_required, debug=bool((row.extra or {}).get("debug")),
        path_alias=str((row.extra or {}).get("path_alias", "") or ""),
        extra=dict(row.extra or {}),
    )
