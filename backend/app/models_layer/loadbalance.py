"""Load-balance coordinator: strategy ordering + health circuit-breaking.

A shared, in-process singleton that holds, per load-balance group / target:
  - round-robin cursors (round_robin strategy),
  - in-flight request counts (least_conn strategy),
  - a simple circuit breaker that, after a few consecutive failures, pushes a
    target to the back of the try-order until a cooldown elapses.

Only *multi-target load-balance pools* are reordered here; solo targets and the
primary→fallback chain keep their explicit order (see router._attempt_order), so
behaviour is unchanged for anyone not using lb_group pools. The default
'weighted' strategy reproduces the previous weighted-random ordering, and an
empty/unknown strategy falls back to it — fully backward compatible.
"""
from __future__ import annotations

import random
import threading
import time
from typing import Callable, Sequence, TypeVar

T = TypeVar("T")

FAIL_THRESHOLD = 3     # consecutive failures before the circuit opens
COOLDOWN_S = 15.0      # how long a target stays de-prioritised once open

VALID_STRATEGIES = ("weighted", "round_robin", "least_conn", "least_vram")


def weighted_shuffle(pool: Sequence[T], weight_of: Callable[[T], int]) -> list[T]:
    """Order a pool by weighted random draw without replacement."""
    items = list(pool)
    weights = [max(weight_of(t), 1) for t in items]
    out: list[T] = []
    while items:
        idx = random.choices(range(len(items)), weights=weights, k=1)[0]
        out.append(items.pop(idx))
        weights.pop(idx)
    return out


class LBCoordinator:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rr: dict[str, int] = {}
        self._inflight: dict[str, int] = {}
        self._fails: dict[str, int] = {}
        self._open_until: dict[str, float] = {}

    # ---- health (circuit breaker) ----
    def record_success(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)
            self._open_until.pop(key, None)

    def record_failure(self, key: str) -> None:
        with self._lock:
            n = self._fails.get(key, 0) + 1
            self._fails[key] = n
            if n >= FAIL_THRESHOLD:
                self._open_until[key] = time.monotonic() + COOLDOWN_S

    def is_open(self, key: str) -> bool:
        return self._open_until.get(key, 0.0) > time.monotonic()

    # ---- in-flight tracking (least_conn) ----
    def acquire(self, key: str) -> None:
        with self._lock:
            self._inflight[key] = self._inflight.get(key, 0) + 1

    def release(self, key: str) -> None:
        with self._lock:
            if self._inflight.get(key, 0) > 0:
                self._inflight[key] -= 1

    def inflight(self, key: str) -> int:
        return self._inflight.get(key, 0)

    # ---- ordering ----
    def order(self, group_key: str, pool: Sequence[T], strategy: str, *,
              key_of: Callable[[T], str], weight_of: Callable[[T], int],
              gpu_mem_of: Callable[[T], float] | None = None) -> list[T]:
        """Return the try-order for one load-balance pool under `strategy`,
        with circuit-open targets stable-moved to the back (tried last, never
        dropped, so the pool always offers a candidate)."""
        items = list(pool)
        if not items:
            return items
        with self._lock:
            now = time.monotonic()
            if strategy == "round_robin":
                idx = self._rr.get(group_key, 0) % len(items)
                self._rr[group_key] = (idx + 1) % len(items)
                base = items[idx:] + items[:idx]
            elif strategy == "least_conn":
                base = sorted(
                    items,
                    key=lambda t: (self._inflight.get(key_of(t), 0), -max(weight_of(t), 1)),
                )
            elif strategy == "least_vram" and gpu_mem_of is not None:
                # Prefer the instance whose GPU currently has the lowest VRAM use;
                # break ties by higher weight. Targets without a known GPU sort last.
                base = sorted(items, key=lambda t: (gpu_mem_of(t), -max(weight_of(t), 1)))
            else:  # weighted (default / unknown)
                base = weighted_shuffle(items, weight_of)
            healthy = [t for t in base if self._open_until.get(key_of(t), 0.0) <= now]
            broken = [t for t in base if self._open_until.get(key_of(t), 0.0) > now]
        return healthy + broken

    def reset(self) -> None:
        """Clear all state (used by tests)."""
        with self._lock:
            self._rr.clear()
            self._inflight.clear()
            self._fails.clear()
            self._open_until.clear()


lb = LBCoordinator()
