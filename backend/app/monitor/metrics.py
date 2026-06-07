"""In-memory per-port metrics + persisted request logs (pruned to last N)."""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.billing.pricing import compute_cost, get_pricing
from app.db.models import RequestLog, UsageStat
from app.db.session import SessionLocal


@dataclass
class PortMetrics:
    total: int = 0
    errors: int = 0
    latencies: deque = field(default_factory=lambda: deque(maxlen=100))
    last_ts: float = 0.0

    @property
    def avg_latency_ms(self) -> float:
        return round(sum(self.latencies) / len(self.latencies), 1) if self.latencies else 0.0

    @property
    def error_rate(self) -> float:
        return round(self.errors / self.total, 4) if self.total else 0.0


class MetricsRegistry:
    """Thread-safe registry; called from per-port uvicorn worker threads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_port: dict[int, PortMetrics] = defaultdict(PortMetrics)

    def record(
        self,
        port_id: int,
        ok: bool,
        latency_ms: float,
        *,
        model: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        request_excerpt: str = "",
        response_excerpt: str = "",
        error: str = "",
        logging_enabled: bool = True,
        log_keep: int = 10,
        api_key_id: int | None = None,
    ) -> None:
        with self._lock:
            m = self._by_port[port_id]
            m.total += 1
            if not ok:
                m.errors += 1
            m.latencies.append(latency_ms)
            m.last_ts = time.time()
        # cumulative usage/cost stats are always recorded (independent of logging)
        self._accumulate(port_id, api_key_id, model, ok, prompt_tokens, completion_tokens)
        if logging_enabled:
            self._persist(
                port_id, ok, latency_ms, model, prompt_tokens, completion_tokens,
                request_excerpt, response_excerpt, error, log_keep,
            )

    def _accumulate(self, port_id, api_key_id, model, ok, pt, ct) -> None:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        db = SessionLocal()
        try:
            cost = compute_cost(model or "", pt, ct, get_pricing(db))
            row = (db.query(UsageStat)
                   .filter(UsageStat.day == day, UsageStat.port_id == port_id,
                           UsageStat.api_key_id == api_key_id, UsageStat.model == (model or ""))
                   .first())
            if row is None:
                row = UsageStat(day=day, port_id=port_id, api_key_id=api_key_id,
                                model=model or "")
                db.add(row)
            row.requests += 1
            if not ok:
                row.errors += 1
            row.prompt_tokens += int(pt or 0)
            row.completion_tokens += int(ct or 0)
            row.cost = round(row.cost + cost, 6)
            db.commit()
        except Exception:  # noqa: BLE001 — stats must never break the request path
            db.rollback()
        finally:
            db.close()

    def _persist(self, port_id, ok, latency_ms, model, pt, ct, req, resp, error, log_keep) -> None:
        db = SessionLocal()
        try:
            db.add(RequestLog(
                port_id=port_id, ok=ok, latency_ms=latency_ms, model_used=model,
                prompt_tokens=pt, completion_tokens=ct,
                request_excerpt=req[:2000], response_excerpt=resp[:2000], error=error[:2000],
            ))
            db.commit()
            # prune to last `log_keep`
            ids = [r.id for r in db.query(RequestLog.id)
                   .filter(RequestLog.port_id == port_id)
                   .order_by(RequestLog.id.desc())
                   .offset(max(log_keep, 0)).all()]
            if ids:
                db.query(RequestLog).filter(RequestLog.id.in_(ids)).delete(synchronize_session=False)
                db.commit()
        finally:
            db.close()

    def snapshot(self, port_id: int) -> dict:
        with self._lock:
            m = self._by_port.get(port_id)
            if not m:
                return {"total": 0, "errors": 0, "avg_latency_ms": 0.0, "error_rate": 0.0}
            return {
                "total": m.total,
                "errors": m.errors,
                "avg_latency_ms": m.avg_latency_ms,
                "error_rate": m.error_rate,
                "last_ts": m.last_ts,
            }


metrics = MetricsRegistry()
