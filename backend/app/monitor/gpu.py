"""GPU / VRAM monitoring via NVML, with graceful fallback when unavailable."""
from __future__ import annotations

import time

_nvml_ready = False
try:
    import pynvml  # type: ignore

    pynvml.nvmlInit()
    _nvml_ready = True
except Exception:
    _nvml_ready = False


def gpu_stats() -> dict:
    if not _nvml_ready:
        return {"available": False, "gpus": []}
    gpus = []
    try:
        count = pynvml.nvmlDeviceGetCount()
        for i in range(count):
            h = pynvml.nvmlDeviceGetHandleByIndex(i)
            mem = pynvml.nvmlDeviceGetMemoryInfo(h)
            util = pynvml.nvmlDeviceGetUtilizationRates(h)
            name = pynvml.nvmlDeviceGetName(h)
            if isinstance(name, bytes):
                name = name.decode("utf-8", "ignore")

            def _try(fn, default=None):
                try:
                    return fn()
                except Exception:  # noqa: BLE001 — some metrics unsupported per GPU/driver
                    return default

            temp = _try(lambda: pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU))
            power_w = _try(lambda: round(pynvml.nvmlDeviceGetPowerUsage(h) / 1000, 1))
            power_limit_w = _try(
                lambda: round(pynvml.nvmlDeviceGetEnforcedPowerLimit(h) / 1000, 1))
            fan_pct = _try(lambda: pynvml.nvmlDeviceGetFanSpeed(h))
            gpus.append({
                "index": i,
                "name": name,
                "mem_total_mb": round(mem.total / 1024 / 1024),
                "mem_used_mb": round(mem.used / 1024 / 1024),
                "mem_pct": round(mem.used / mem.total * 100, 1) if mem.total else 0.0,
                "util_pct": util.gpu,
                "temp_c": temp,
                "power_w": power_w,
                "power_limit_w": power_limit_w,
                "fan_pct": fan_pct,
            })
    except Exception as e:  # noqa: BLE001
        return {"available": False, "gpus": [], "error": str(e)}
    return {"available": True, "gpus": gpus}


_mem_cache: dict = {"ts": 0.0, "map": {}}


def gpu_mem_pct_map(ttl: float = 2.0) -> dict[str, float]:
    """{gpu_index_str: mem_used_pct} with a short TTL cache (called per request
    by the least_vram load-balancer, so NVML isn't hit on every call)."""
    now = time.time()
    if now - _mem_cache["ts"] < ttl:
        return _mem_cache["map"]
    out: dict[str, float] = {}
    stats = gpu_stats()
    if stats.get("available"):
        for g in stats["gpus"]:
            out[str(g["index"])] = float(g.get("mem_pct", 0.0) or 0.0)
    _mem_cache["ts"] = now
    _mem_cache["map"] = out
    return out
