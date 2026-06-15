"""Shared helpers for the ported "VT" evaluation services.

The standalone scripts (under the desktop / D: / Z: drives) each spoke a bespoke HTTP
contract (custom endpoints, custom request/response shapes, media handling,
line-format LLM output) and called an upstream OneAPI gateway directly. When we
re-implement them as PORTHUB app templates we keep that external contract, but
the actual model call is delegated to the unified ModelRouter so they gain alias
routing, fallback chains, load-balancing and usage metrics for free.

This module collects the pieces every ported service needs:
- payload normalization helpers (PascalCase / snake_case tolerant)
- media fetch + format conversion (webp/bmp/gif -> png, video -> frames)
- an editable per-port prompt store (mirrors the old /prompt_config files)
- `call_model` / `call_model_text`: build a (multimodal) ChatRequest and run it
  through the router, recording metrics.
"""
from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import httpx

from app.apps.base import PortConfig
from app.core.config import DATA_DIR
from app.db.session import SessionLocal
from app.models_layer.router import AliasNotFound, ModelRouter, resolve_alias
from app.models_layer.types import ChatMessage, ChatRequest
from app.monitor.metrics import metrics

# -----------------------------------------------------------------------------
# Optional Pillow (image format convert / GIF frames)
# -----------------------------------------------------------------------------
try:
    from io import BytesIO

    from PIL import Image  # type: ignore

    PIL_OK = True
except Exception:  # pragma: no cover - pillow optional
    PIL_OK = False

# Optional ffmpeg (video frame extraction)
import os
import subprocess
import tempfile

FFMPEG_PATH = os.getenv("FFMPEG_PATH", "ffmpeg").strip()
ENABLE_VIDEO = os.getenv("EVAL_ENABLE_VIDEO", "1").strip() == "1"
VIDEO_SAMPLE_FRAMES = int(os.getenv("EVAL_VIDEO_SAMPLE_FRAMES", "4"))
VIDEO_FPS = float(os.getenv("EVAL_VIDEO_FPS", "1"))
MAX_IMAGE_BYTES = int(os.getenv("EVAL_MAX_IMAGE_BYTES", str(12 * 1024 * 1024)))
REQUEST_TIMEOUT = float(os.getenv("EVAL_MEDIA_TIMEOUT", "45"))


# =============================================================================
# Payload normalization (tolerant of C#/VT PascalCase + JS/snake_case)
# =============================================================================
def try_json_loads(s: Any) -> Any:
    try:
        return json.loads((s or "").strip()) if isinstance(s, str) else None
    except Exception:
        return None


def normalize_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        parsed = try_json_loads(value)
        if parsed is None:
            return [value]
        return parsed if isinstance(parsed, list) else [parsed]
    return [value]


def normalize_int_list(value: Any) -> list[int] | None:
    if value is None:
        return None
    if isinstance(value, int):
        return [value]
    seq: list[Any]
    if isinstance(value, list):
        seq = value
    elif isinstance(value, str):
        parsed = try_json_loads(value)
        seq = parsed if isinstance(parsed, list) else [x for x in value.split(",")]
    else:
        return None
    out: list[int] = []
    for x in seq:
        try:
            out.append(int(str(x).strip()))
        except Exception:
            pass
    return out or None


def pick_first(payload: dict[str, Any], keys: list[str]) -> Any:
    for k in keys:
        if k in payload:
            return payload[k]
    return None


# =============================================================================
# URL helpers (Chinese paths) + media download / conversion
# =============================================================================
def encode_url_path(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(
        (parts.scheme, parts.netloc, quote(unquote(parts.path)), parts.query, parts.fragment)
    )


def _looks_like_webp(b: bytes) -> bool:
    return len(b) >= 12 and b[0:4] == b"RIFF" and b[8:12] == b"WEBP"


def _is_gif(b: bytes) -> bool:
    return b[:6] in (b"GIF87a", b"GIF89a")


def _is_bmp(b: bytes) -> bool:
    return len(b) >= 2 and b[:2] == b"BM"


def _looks_like_mp4(b: bytes) -> bool:
    return len(b) >= 12 and b[4:8] == b"ftyp"


def _looks_like_webm(b: bytes) -> bool:
    return len(b) >= 4 and b[:4] == b"\x1a\x45\xdf\xa3"


def guess_media_kind(b: bytes, mime_hint: str = "") -> str:
    mh = (mime_hint or "").lower().split(";")[0].strip()
    if mh.startswith("video/"):
        return "video"
    if mh.startswith("image/"):
        return "image"
    if _looks_like_mp4(b) or _looks_like_webm(b):
        return "video"
    return "image"


def _image_to_png(b: bytes) -> bytes:
    im = Image.open(BytesIO(b))
    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGBA")
    out = BytesIO()
    im.save(out, format="PNG")
    return out.getvalue()


def _is_animated_gif(b: bytes) -> bool:
    if not PIL_OK:
        return False
    try:
        im = Image.open(BytesIO(b))
        return bool(getattr(im, "is_animated", False)) and int(getattr(im, "n_frames", 1)) > 1
    except Exception:
        return False


def _gif_first_frame_png(b: bytes) -> bytes:
    im = Image.open(BytesIO(b))
    im.seek(0)
    out = BytesIO()
    im.convert("RGBA").save(out, format="PNG")
    return out.getvalue()


def _extract_video_frames(video_bytes: bytes, sample_frames: int) -> list[bytes]:
    if not ENABLE_VIDEO:
        raise RuntimeError("video disabled (set EVAL_ENABLE_VIDEO=1)")
    with tempfile.TemporaryDirectory() as td:
        vin = os.path.join(td, "in.mp4")
        Path(vin).write_bytes(video_bytes)
        vout = os.path.join(td, "frame_%03d.png")
        cmd = [FFMPEG_PATH, "-y", "-i", vin, "-vf", f"fps={VIDEO_FPS}",
               "-vframes", str(sample_frames), vout]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if p.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {p.stderr[:400]}")
        frames: list[bytes] = []
        for i in range(1, sample_frames + 1):
            fp = os.path.join(td, f"frame_{i:03d}.png")
            if os.path.exists(fp):
                frames.append(Path(fp).read_bytes())
        if not frames:
            raise RuntimeError("no video frames extracted")
        return frames


def _process_media_sync(raw: bytes, mime_hint: str, for_cloud: bool) -> tuple[list[bytes], str]:
    """Convert raw media bytes into a list of model-ready frames + their mime.
    Runs Pillow / ffmpeg, so callers should invoke via asyncio.to_thread."""
    if not raw:
        raise RuntimeError("empty media")
    kind = guess_media_kind(raw, mime_hint)
    if kind == "video":
        return _extract_video_frames(raw, max(1, VIDEO_SAMPLE_FRAMES)), "image/png"
    if len(raw) > MAX_IMAGE_BYTES:
        raise RuntimeError(f"image too large (> {MAX_IMAGE_BYTES} bytes)")
    if not PIL_OK:
        return [raw], (mime_hint.split(";")[0].strip() or "image/png")
    # normalize odd formats to PNG for cloud stability
    if _looks_like_webp(raw) or _is_bmp(raw):
        return [_image_to_png(raw)], "image/png"
    if _is_gif(raw):
        if _is_animated_gif(raw):
            return [_gif_first_frame_png(raw)], "image/png"
        return [_image_to_png(raw)], "image/png"
    return [raw], (mime_hint.split(";")[0].strip() or "image/png")


async def process_media_bytes(raw: bytes, mime_hint: str, for_cloud: bool = True) -> tuple[list[bytes], str]:
    return await asyncio.to_thread(_process_media_sync, raw, mime_hint, for_cloud)


async def download_media(url: str, for_cloud: bool = True) -> tuple[list[bytes], str, bool]:
    """Fetch a URL and return (frames, mime, is_video)."""
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "video/*,image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Connection": "close",
    }
    candidates = [url]
    safe = encode_url_path(url)
    if safe != url:
        candidates.append(safe)
    last_exc: Exception | None = None
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, trust_env=False,
                                 follow_redirects=True) as client:
        for u in candidates:
            try:
                r = await client.get(u, headers=headers)
                r.raise_for_status()
                b = r.content
                ct = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if not ct:
                    guess, _ = mimetypes.guess_type(u)
                    ct = (guess or "").lower()
                is_video = guess_media_kind(b, ct) == "video"
                frames, mime = await process_media_bytes(b, ct, for_cloud=for_cloud)
                return frames, mime, is_video
            except Exception as e:  # noqa: BLE001
                last_exc = e
    raise RuntimeError(f"fetch media failed: {last_exc}")


def media_to_data_url(b: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(b).decode('ascii')}"


# =============================================================================
# Editable per-port prompt store (replaces the old role_prompt.txt files)
# =============================================================================
_PROMPT_DIR = DATA_DIR / "prompts"


def _prompt_file(app_type: str, slug: str) -> Path:
    _PROMPT_DIR.mkdir(parents=True, exist_ok=True)
    return _PROMPT_DIR / f"eval_{app_type}_{slug}.txt"


def load_prompt(config: PortConfig, default_prompt: str) -> str:
    """Resolve the active role prompt: saved override > port system_prompt > default."""
    f = _prompt_file(config.app_type, config.slug)
    if f.exists():
        t = f.read_text(encoding="utf-8").strip()
        if t:
            return t
    if config.system_prompt and config.system_prompt.strip():
        return config.system_prompt.strip()
    return default_prompt.strip()


def save_prompt(config: PortConfig, prompt: str) -> None:
    _prompt_file(config.app_type, config.slug).write_text(prompt.strip(), encoding="utf-8")


# =============================================================================
# Model call via the unified router
# =============================================================================
def _resolve(config: PortConfig, alias: str | None = None):
    db = SessionLocal()
    try:
        return resolve_alias(db, alias or config.model_alias)
    finally:
        db.close()


def build_user_content(text: str, data_urls: list[str] | None = None,
                       use_detail: bool = True) -> Any:
    """OpenAI-style user content: image blocks first, then the text block.
    Returns a plain string when there are no images (keeps text-only calls clean)."""
    if not data_urls:
        return text
    blocks: list[dict[str, Any]] = []
    for u in data_urls:
        img: dict[str, Any] = {"type": "image_url", "image_url": {"url": u}}
        if use_detail:
            img["image_url"]["detail"] = "high"
        blocks.append(img)
    blocks.append({"type": "text", "text": text})
    return blocks


async def call_model(
    config: PortConfig,
    system_prompt: str,
    user_content: Any,
    *,
    alias: str | None = None,
    params: dict[str, Any] | None = None,
    record: bool = True,
    request_excerpt: str = "",
) -> str:
    """Run one chat completion through the router and return the text.
    `alias` overrides config.model_alias (e.g. a separate vision/text stage).
    Records port metrics unless record=False (e.g. for internal retry sub-calls)."""
    msgs: list[ChatMessage] = []
    if system_prompt and system_prompt.strip():
        msgs.append(ChatMessage(role="system", content=system_prompt))
    msgs.append(ChatMessage(role="user", content=user_content))
    req = ChatRequest(messages=msgs, params=params or {}, stream=False)
    router = ModelRouter(timeout=config.timeout, max_retries=config.max_retries)
    started = time.perf_counter()
    try:
        resolved = _resolve(config, alias)
    except AliasNotFound as e:
        raise RuntimeError(str(e))
    try:
        result = await router.chat(resolved, req)
    except Exception as e:  # noqa: BLE001
        if record:
            metrics.record(config.id, False, (time.perf_counter() - started) * 1000,
                           model=config.model_alias, request_excerpt=request_excerpt,
                           error=str(e), logging_enabled=config.logging_enabled,
                           log_keep=config.log_keep)
        raise
    if record:
        metrics.record(config.id, True, (time.perf_counter() - started) * 1000,
                       model=result.model, prompt_tokens=result.usage.prompt_tokens,
                       completion_tokens=result.usage.completion_tokens,
                       request_excerpt=request_excerpt,
                       response_excerpt=result.text,
                       logging_enabled=config.logging_enabled, log_keep=config.log_keep)
    return result.text
