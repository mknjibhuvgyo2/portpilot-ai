"""Unified model router: alias resolution, fallback chain, retry.

The router is DB-aware only at resolution time: it loads a plain snapshot of
the alias + its provider targets, then performs network calls without holding a
DB session. Fallback order = order of `targets` (first is primary). If every
target fails, the alias `fallback_text` is returned as a guaranteed output.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from sqlalchemy.orm import Session

from app.db.models import ModelAlias, Provider
from app.models_layer.loadbalance import lb, weighted_shuffle
from app.models_layer.providers.anthropic import AnthropicProvider
from app.models_layer.providers.base import BaseProvider
from app.models_layer.providers.gemini import GeminiProvider
from app.models_layer.providers.ollama import OllamaProvider
from app.models_layer.providers.openai_compat import OpenAICompatProvider
from app.models_layer.types import ChatMessage, ChatRequest, ChatResult, Usage
from app.monitor.gpu import gpu_mem_pct_map

log = logging.getLogger("hub.model_router")

# Local runtimes default to native/compat adapters; everything else is OpenAI-compatible.
# anthropic/gemini use their own native APIs; all other vendors are OpenAI-compatible.
_PROVIDER_CLASSES: dict[str, type[BaseProvider]] = {
    "ollama": OllamaProvider,
    "openai_compat": OpenAICompatProvider,
    "lmstudio": OpenAICompatProvider,
    "llamacpp": OpenAICompatProvider,
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
}


def build_provider(kind: str, base_url: str, api_key: str, timeout: float,
                   extra: dict | None = None) -> BaseProvider:
    cls = _PROVIDER_CLASSES.get(kind, OpenAICompatProvider)
    provider = cls(base_url=base_url, api_key=api_key, timeout=timeout, extra=extra or {})
    provider.db_kind = kind  # keep the real DB kind (e.g. "lmstudio") on the instance
    return provider


@dataclass
class ResolvedTarget:
    kind: str
    base_url: str
    api_key: str
    model: str
    label: str  # provider_name/model for logging
    weight: int = 1          # load-balance weight within its group
    gpu_index: str = ""      # informational: which GPU this instance pins to
    lb_group: str = ""       # targets sharing a non-empty group are load-balanced
    extra: dict[str, Any] = field(default_factory=dict)  # provider.extra (advanced params)


@dataclass
class ResolvedAlias:
    alias: str
    targets: list[ResolvedTarget]
    fallback_text: str = ""
    params: dict[str, Any] = field(default_factory=dict)


class AliasNotFound(Exception):
    pass


def resolve_alias(db: Session, alias: str) -> ResolvedAlias:
    row = db.query(ModelAlias).filter(ModelAlias.alias == alias, ModelAlias.enabled.is_(True)).first()
    if not row:
        raise AliasNotFound(f"Model alias '{alias}' not found or disabled")
    targets: list[ResolvedTarget] = []
    for t in row.targets or []:
        prov = db.query(Provider).filter(Provider.id == t.get("provider_id")).first()
        if not prov or not prov.enabled:
            continue
        gpu = (prov.gpu_index or "").strip()
        label = f"{prov.name}/{t.get('model', '')}" + (f"#gpu{gpu}" if gpu else "")
        targets.append(
            ResolvedTarget(
                kind=prov.kind,
                base_url=prov.base_url,
                api_key=prov.api_key,
                model=t.get("model", ""),
                label=label,
                weight=max(int(prov.weight or 1), 1),
                gpu_index=gpu,
                lb_group=str(t.get("lb_group", "") or "").strip(),
                extra=prov.extra if isinstance(prov.extra, dict) else {},
            )
        )
    return ResolvedAlias(
        alias=alias,
        targets=targets,
        fallback_text=row.fallback_text or "",
        params=row.params or {},
    )


def _attempt_order(targets: list[ResolvedTarget], strategy: str = "weighted",
                   pin_gpu: str = "") -> list[ResolvedTarget]:
    """Flatten targets into a try-order: targets sharing a non-empty lb_group
    form a load-balanced pool (ordered by `strategy` + health, via the shared
    LBCoordinator); groups keep their first-seen order and act as fallback
    stages. Empty-group targets are standalone (pure fallback, order preserved).
    When `pin_gpu` is set, targets on that GPU are moved to the front (the rest
    stay as fallback), so an alias can be pinned to a specific GPU.
    """
    groups: dict[str, list[ResolvedTarget]] = {}
    order: list[str] = []
    for i, t in enumerate(targets):
        key = t.lb_group if t.lb_group else f"__solo_{i}"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(t)
    gpu_mem = gpu_mem_pct_map() if strategy == "least_vram" else {}
    out: list[ResolvedTarget] = []
    for key in order:
        pool = groups[key]
        if len(pool) == 1:
            out.extend(pool)
        else:
            out.extend(lb.order(key, pool, strategy,
                                key_of=lambda t: t.label, weight_of=lambda t: t.weight,
                                gpu_mem_of=lambda t: gpu_mem.get(t.gpu_index, 999.0)))
    if pin_gpu:
        out = [t for t in out if t.gpu_index == pin_gpu] + [t for t in out if t.gpu_index != pin_gpu]
    return out


def _strategy_of(resolved: ResolvedAlias) -> str:
    s = str((resolved.params or {}).get("lb_strategy", "") or "").strip()
    return s if s in ("round_robin", "least_conn", "least_vram") else "weighted"


def _pin_gpu_of(resolved: ResolvedAlias) -> str:
    return str((resolved.params or {}).get("pin_gpu", "") or "").strip()


class ModelRouter:
    """Stateless executor over a ResolvedAlias."""

    def __init__(self, timeout: float = 120.0, max_retries: int = 2):
        self.timeout = timeout
        self.max_retries = max_retries

    async def chat(self, resolved: ResolvedAlias, req: ChatRequest) -> ChatResult:
        merged = {**resolved.params, **req.params}
        req = ChatRequest(messages=req.messages, params=merged, stream=False)
        last_err: Exception | None = None
        for target in _attempt_order(resolved.targets, _strategy_of(resolved), _pin_gpu_of(resolved)):
            provider = build_provider(target.kind, target.base_url, target.api_key,
                                      self.timeout, extra=target.extra)
            for attempt in range(self.max_retries + 1):
                lb.acquire(target.label)
                try:
                    result = await provider.chat(target.model, req)
                    lb.record_success(target.label)
                    result.model = target.label
                    return result
                except Exception as e:  # noqa: BLE001 - want broad fallback
                    last_err = e
                    log.warning("chat target %s attempt %d failed: %s", target.label, attempt, e)
                    await asyncio.sleep(min(0.5 * (attempt + 1), 3))
                finally:
                    lb.release(target.label)
            lb.record_failure(target.label)  # all attempts on this target failed
        if resolved.fallback_text:
            return ChatResult(text=resolved.fallback_text, model="fallback", usage=Usage())
        raise RuntimeError(f"All targets failed for alias '{resolved.alias}': {last_err}")

    async def stream(self, resolved: ResolvedAlias, req: ChatRequest,
                     usage_out: dict | None = None) -> AsyncIterator[str]:
        merged = {**resolved.params, **req.params}
        req = ChatRequest(messages=req.messages, params=merged, stream=True)
        last_err: Exception | None = None
        for target in _attempt_order(resolved.targets, _strategy_of(resolved), _pin_gpu_of(resolved)):
            provider = build_provider(target.kind, target.base_url, target.api_key,
                                      self.timeout, extra=target.extra)
            lb.acquire(target.label)
            try:
                gen = provider.stream(target.model, req, usage_out=usage_out)
                first = await gen.__anext__()
            except StopAsyncIteration:
                lb.record_success(target.label)
                lb.release(target.label)
                return  # empty but successful stream
            except Exception as e:  # noqa: BLE001
                lb.record_failure(target.label)
                lb.release(target.label)
                last_err = e
                log.warning("stream target %s failed before first chunk: %s", target.label, e)
                continue
            # First chunk obtained -> commit to this target.
            try:
                yield first
                async for chunk in gen:
                    yield chunk
                lb.record_success(target.label)
                return
            finally:
                lb.release(target.label)
        # No target produced output.
        if resolved.fallback_text:
            yield resolved.fallback_text
            return
        raise RuntimeError(f"All targets failed for alias '{resolved.alias}': {last_err}")

    async def embed(self, resolved: ResolvedAlias, inputs: list[str]) -> tuple[list[list[float]], Usage]:
        """Embed inputs through the alias's targets, with the same fallback chain.
        Targets that can't embed (NotImplementedError) are skipped to the next."""
        last_err: Exception | None = None
        for target in _attempt_order(resolved.targets, _strategy_of(resolved), _pin_gpu_of(resolved)):
            provider = build_provider(target.kind, target.base_url, target.api_key,
                                      self.timeout, extra=target.extra)
            for attempt in range(self.max_retries + 1):
                try:
                    return await provider.embed(target.model, inputs)
                except NotImplementedError:
                    last_err = RuntimeError(f"{target.label} has no embeddings")
                    break  # try the next target
                except Exception as e:  # noqa: BLE001
                    last_err = e
                    log.warning("embed target %s attempt %d failed: %s", target.label, attempt, e)
                    await asyncio.sleep(min(0.5 * (attempt + 1), 3))
        raise RuntimeError(f"All embedding targets failed for '{resolved.alias}': {last_err}")

    async def rerank(self, resolved: ResolvedAlias, query: str, documents: list[str],
                     top_n: int | None = None) -> tuple[list[dict], Usage]:
        """Rerank documents through the alias's targets, with the same fallback
        chain. Targets that can't rerank (NotImplementedError) are skipped."""
        last_err: Exception | None = None
        for target in _attempt_order(resolved.targets, _strategy_of(resolved), _pin_gpu_of(resolved)):
            provider = build_provider(target.kind, target.base_url, target.api_key,
                                      self.timeout, extra=target.extra)
            for attempt in range(self.max_retries + 1):
                try:
                    return await provider.rerank(target.model, query, documents, top_n)
                except NotImplementedError:
                    last_err = RuntimeError(f"{target.label} has no rerank")
                    break  # try the next target
                except Exception as e:  # noqa: BLE001
                    last_err = e
                    log.warning("rerank target %s attempt %d failed: %s", target.label, attempt, e)
                    await asyncio.sleep(min(0.5 * (attempt + 1), 3))
        raise RuntimeError(f"All rerank targets failed for '{resolved.alias}': {last_err}")

    async def raw_chat(self, resolved: ResolvedAlias, body: dict) -> dict:
        """Transparent (non-stream) passthrough of a full OpenAI body through the
        alias's targets, with the same fallback chain. Targets that can't do raw
        passthrough (NotImplementedError, e.g. native Ollama) are skipped."""
        last_err: Exception | None = None
        for target in _attempt_order(resolved.targets, _strategy_of(resolved), _pin_gpu_of(resolved)):
            provider = build_provider(target.kind, target.base_url, target.api_key,
                                      self.timeout, extra=target.extra)
            for attempt in range(self.max_retries + 1):
                lb.acquire(target.label)
                try:
                    data = await provider.raw_chat(target.model, body)
                    lb.record_success(target.label)
                    return data
                except NotImplementedError:
                    last_err = RuntimeError(f"{target.label} has no passthrough")
                    break  # try the next target
                except Exception as e:  # noqa: BLE001
                    last_err = e
                    log.warning("raw_chat target %s attempt %d failed: %s", target.label, attempt, e)
                    await asyncio.sleep(min(0.5 * (attempt + 1), 3))
                finally:
                    lb.release(target.label)
            else:
                continue
            lb.record_failure(target.label)
        raise RuntimeError(f"All passthrough targets failed for '{resolved.alias}': {last_err}")

    async def raw_stream(self, resolved: ResolvedAlias, body: dict) -> AsyncIterator[bytes]:
        """Transparent streaming passthrough: forward upstream SSE bytes verbatim
        from the first target that connects (same fallback ordering)."""
        last_err: Exception | None = None
        for target in _attempt_order(resolved.targets, _strategy_of(resolved), _pin_gpu_of(resolved)):
            provider = build_provider(target.kind, target.base_url, target.api_key,
                                      self.timeout, extra=target.extra)
            lb.acquire(target.label)
            try:
                gen = provider.raw_stream(target.model, body)
                first = await gen.__anext__()
            except StopAsyncIteration:
                lb.record_success(target.label)
                lb.release(target.label)
                return
            except NotImplementedError:
                last_err = RuntimeError(f"{target.label} has no passthrough")
                lb.release(target.label)
                continue
            except Exception as e:  # noqa: BLE001
                last_err = e
                lb.record_failure(target.label)
                lb.release(target.label)
                log.warning("raw_stream target %s failed before first chunk: %s", target.label, e)
                continue
            try:
                yield first
                async for chunk in gen:
                    yield chunk
                lb.record_success(target.label)
                return
            finally:
                lb.release(target.label)
        raise RuntimeError(f"All passthrough targets failed for '{resolved.alias}': {last_err}")


def messages_from_payload(payload: list[dict[str, Any]]) -> list[ChatMessage]:
    return [ChatMessage(role=m.get("role", "user"), content=m.get("content", "")) for m in payload]
