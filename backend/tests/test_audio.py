"""Tests for the asr/tts app templates + router audio fallback + provider wire format."""
import asyncio
import os
import tempfile

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ.setdefault("HUB_DATABASE_URL", f"sqlite:///{_tmp.name.replace(os.sep, '/')}")

from fastapi.testclient import TestClient  # noqa: E402

from app.apps.base import PortConfig  # noqa: E402
from app.models_layer.router import ModelRouter, ResolvedAlias, ResolvedTarget  # noqa: E402


def _rt(model, label):
    return ResolvedTarget(kind="x", base_url="", api_key="", model=model, label=label)


# ─────────────────────────── registry ───────────────────────────

def test_audio_templates_registered():
    from app.apps.registry import get_template, list_templates
    tpls = {t["app_type"]: t for t in list_templates()}
    assert "asr" in tpls and "tts" in tpls
    assert get_template("asr") is not None and get_template("tts") is not None
    # they group under their own picker heading rather than falling into generic
    assert tpls["asr"]["category"] == "audio"
    assert tpls["tts"]["category"] == "audio"


# ─────────────────────────── router fallback ───────────────────────────

def test_router_transcribe_skips_unsupported_then_succeeds(monkeypatch):
    import app.models_layer.router as r

    class P1:  # a text-only target sitting in front of the audio one
        async def transcribe(self, model, audio, filename, params):
            raise NotImplementedError

    class P2:
        async def transcribe(self, model, audio, filename, params):
            return {"text": "开放时间早上9点"}

    provs = iter([P1(), P2()])
    monkeypatch.setattr(r, "build_provider", lambda *a, **k: next(provs))
    resolved = ResolvedAlias(alias="a", targets=[_rt("m1", "t1"), _rt("m2", "t2")])
    out = asyncio.run(ModelRouter().transcribe(resolved, b"RIFF", "a.wav", {}))
    assert out["text"] == "开放时间早上9点"


def test_router_speak_skips_unsupported_then_succeeds(monkeypatch):
    import app.models_layer.router as r

    class P1:
        async def speak(self, model, text, params):
            raise NotImplementedError

    class P2:
        async def speak(self, model, text, params):
            return b"ID3audio", "audio/mpeg"

    provs = iter([P1(), P2()])
    monkeypatch.setattr(r, "build_provider", lambda *a, **k: next(provs))
    resolved = ResolvedAlias(alias="t", targets=[_rt("m1", "t1"), _rt("m2", "t2")])
    audio, ctype = asyncio.run(ModelRouter().speak(resolved, "你好", {}))
    assert audio == b"ID3audio" and ctype == "audio/mpeg"


def test_router_transcribe_all_targets_fail(monkeypatch):
    import app.models_layer.router as r

    class P:
        async def transcribe(self, model, audio, filename, params):
            raise NotImplementedError

    monkeypatch.setattr(r, "build_provider", lambda *a, **k: P())
    resolved = ResolvedAlias(alias="a", targets=[_rt("m1", "t1")])
    try:
        asyncio.run(ModelRouter().transcribe(resolved, b"x", "a.wav", {}))
    except RuntimeError as e:
        assert "All transcription targets failed" in str(e)
    else:
        raise AssertionError("expected RuntimeError")


# ─────────────────────────── ASR endpoint ───────────────────────────

def _asr_client(monkeypatch, extra=None, capture=None):
    import app.apps.asr as amod

    monkeypatch.setattr(amod, "resolve_alias", lambda db, alias: object())
    monkeypatch.setattr(amod.metrics, "record", lambda *a, **k: None)

    class FakeRouter:
        def __init__(self, **k):
            pass

        async def transcribe(self, resolved, audio, filename, params):
            if capture is not None:
                capture.update({"audio": audio, "filename": filename, "params": params})
            return {"text": "hello"}

    monkeypatch.setattr(amod, "ModelRouter", FakeRouter)
    cfg = PortConfig(id=1, name="asr", slug="asr", port=9097, app_type="asr",
                     model_alias="voice-asr", logging_enabled=False,
                     extra=extra or {})
    return TestClient(amod.build_asr_app(cfg))


def test_asr_endpoint_returns_transcript(monkeypatch):
    cap = {}
    c = _asr_client(monkeypatch, capture=cap)
    r = c.post("/v1/audio/transcriptions",
               files={"file": ("turn.wav", b"RIFFfake", "audio/wav")},
               data={"model": "ignored-by-design"})
    assert r.status_code == 200
    assert r.json()["text"] == "hello"
    assert cap["audio"] == b"RIFFfake"
    assert cap["filename"] == "turn.wav"


def test_asr_port_pins_language_and_request_overrides(monkeypatch):
    """extra.audio is the port default; an explicit form field still wins."""
    cap = {}
    c = _asr_client(monkeypatch, extra={"audio": {"language": "zh"}}, capture=cap)

    c.post("/v1/audio/transcriptions", files={"file": ("a.wav", b"x", "audio/wav")})
    assert cap["params"]["language"] == "zh"

    c.post("/v1/audio/transcriptions", files={"file": ("a.wav", b"x", "audio/wav")},
           data={"language": "en"})
    assert cap["params"]["language"] == "en"


def test_asr_rejects_empty_upload(monkeypatch):
    c = _asr_client(monkeypatch)
    r = c.post("/v1/audio/transcriptions", files={"file": ("empty.wav", b"", "audio/wav")})
    assert r.status_code == 400


def test_asr_health_and_models(monkeypatch):
    c = _asr_client(monkeypatch)
    assert c.get("/health").json()["app_type"] == "asr"
    assert c.get("/v1/models").json()["data"][0]["id"] == "voice-asr"


# ─────────────────────────── TTS endpoint ───────────────────────────

def _tts_client(monkeypatch, extra=None, capture=None, audio=b"ID3x", ctype="audio/mpeg"):
    import app.apps.tts as tmod

    monkeypatch.setattr(tmod, "resolve_alias", lambda db, alias: object())
    monkeypatch.setattr(tmod.metrics, "record", lambda *a, **k: None)

    class FakeRouter:
        def __init__(self, **k):
            pass

        async def speak(self, resolved, text, params):
            if capture is not None:
                capture.update({"text": text, "params": params})
            return audio, ctype

    monkeypatch.setattr(tmod, "ModelRouter", FakeRouter)
    cfg = PortConfig(id=2, name="tts", slug="tts", port=9096, app_type="tts",
                     model_alias="voice-tts", logging_enabled=False,
                     extra=extra or {})
    return TestClient(tmod.build_tts_app(cfg))


def test_tts_returns_binary_not_json(monkeypatch):
    """The bytes must come back raw; JSON-encoding them would corrupt the audio."""
    c = _tts_client(monkeypatch, audio=b"\x00\x01\x02ID3", ctype="audio/mpeg")
    r = c.post("/v1/audio/speech", json={"input": "你好"})
    assert r.status_code == 200
    assert r.content == b"\x00\x01\x02ID3"
    assert r.headers["content-type"].startswith("audio/mpeg")


def test_tts_passes_upstream_content_type(monkeypatch):
    c = _tts_client(monkeypatch, audio=b"RIFF", ctype="audio/wav")
    r = c.post("/v1/audio/speech", json={"input": "hi", "response_format": "wav"})
    assert r.headers["content-type"].startswith("audio/wav")


def test_tts_port_pins_voice_and_request_overrides(monkeypatch):
    cap = {}
    c = _tts_client(monkeypatch, extra={"audio": {"voice": "Chinese Female"}}, capture=cap)

    c.post("/v1/audio/speech", json={"input": "一"})
    assert cap["params"]["voice"] == "Chinese Female"

    c.post("/v1/audio/speech", json={"input": "一", "voice": "Chinese Male"})
    assert cap["params"]["voice"] == "Chinese Male"


def test_tts_validation(monkeypatch):
    c = _tts_client(monkeypatch)
    assert c.post("/v1/audio/speech", json={}).status_code == 400
    assert c.post("/v1/audio/speech", json={"input": "   "}).status_code == 400
    assert c.post("/v1/audio/speech", json={"input": 123}).status_code == 400
    # a body that isn't UTF-8 JSON is the caller's fault, not the backend's
    bad = c.post("/v1/audio/speech", content="\xa3\xa8not utf-8",
                 headers={"Content-Type": "application/json"})
    assert bad.status_code == 400
    assert c.post("/v1/audio/speech", json=["not", "an", "object"]).status_code == 400
    import app.apps.tts as tmod
    too_long = "字" * (tmod.MAX_INPUT_CHARS + 1)
    assert c.post("/v1/audio/speech", json={"input": too_long}).status_code == 400


# ─────────────────────────── provider wire format ───────────────────────────

def test_openai_compat_transcribe_posts_multipart(monkeypatch):
    """Content-Type must be absent so httpx can set the multipart boundary."""
    import app.models_layer.providers.openai_compat as oc

    seen = {}

    class _Resp:
        headers = {"content-type": "application/json"}

        def raise_for_status(self):
            pass

        def json(self):
            return {"text": "ok"}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, data=None, files=None, json=None):
            seen.update(url=url, headers=headers, data=data, files=files)
            return _Resp()

    monkeypatch.setattr(oc.httpx, "AsyncClient", _Client)
    prov = oc.OpenAICompatProvider(base_url="http://x/v1", api_key="k")
    out = asyncio.run(prov.transcribe("m", b"RIFF", "turn.webm", {"language": "zh"}))

    assert out == {"text": "ok"}
    assert seen["url"].endswith("/v1/audio/transcriptions")
    assert "Content-Type" not in seen["headers"]
    assert seen["headers"]["Authorization"] == "Bearer k"
    assert seen["data"] == {"model": "m", "language": "zh"}
    assert seen["files"]["file"] == ("turn.webm", b"RIFF", "audio/webm")


def test_openai_compat_transcribe_accepts_plain_text_response(monkeypatch):
    """response_format=text returns text/plain; calling .json() would blow up."""
    import app.models_layer.providers.openai_compat as oc

    class _Resp:
        headers = {"content-type": "text/plain; charset=utf-8"}
        text = "plain transcript"

        def raise_for_status(self):
            pass

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return _Resp()

    monkeypatch.setattr(oc.httpx, "AsyncClient", _Client)
    prov = oc.OpenAICompatProvider(base_url="http://x/v1", api_key="")
    out = asyncio.run(prov.transcribe("m", b"x", "a.wav", {"response_format": "text"}))
    assert out == {"text": "plain transcript"}


def test_openai_compat_speak_returns_bytes_and_type(monkeypatch):
    import app.models_layer.providers.openai_compat as oc

    seen = {}

    class _Resp:
        content = b"\xff\xfbaudio"
        headers = {"content-type": "audio/mpeg"}

        def raise_for_status(self):
            pass

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            seen.update(url=url, json=json)
            return _Resp()

    monkeypatch.setattr(oc.httpx, "AsyncClient", _Client)
    prov = oc.OpenAICompatProvider(base_url="http://x/v1", api_key="")
    audio, ctype = asyncio.run(prov.speak("m", "你好", {"voice": "Chinese Female", "speed": 1.0}))

    assert audio == b"\xff\xfbaudio" and ctype == "audio/mpeg"
    assert seen["url"].endswith("/v1/audio/speech")
    assert seen["json"] == {"model": "m", "input": "你好",
                            "voice": "Chinese Female", "speed": 1.0}


def test_audio_mime_guesses_by_suffix():
    from app.models_layer.providers.openai_compat import _audio_mime
    assert _audio_mime("a.wav") == "audio/wav"
    assert _audio_mime("a.mp3") == "audio/mpeg"
    assert _audio_mime("a.webm") == "audio/webm"
    # unknown suffix must not be guessed wrong -- let the server sniff
    assert _audio_mime("a.xyz") == "application/octet-stream"
    assert _audio_mime("noext") == "application/octet-stream"


def test_base_provider_audio_methods_are_optional():
    """A text-only provider must refuse audio in a way the router can skip."""
    from app.models_layer.providers.base import BaseProvider

    class TextOnly(BaseProvider):
        async def chat(self, model, req):
            raise NotImplementedError

        async def stream(self, model, req, usage_out=None):
            raise NotImplementedError

    p = TextOnly(base_url="http://x")
    for coro in (p.transcribe("m", b"x"), p.speak("m", "t")):
        try:
            asyncio.run(coro)
        except NotImplementedError:
            pass
        else:
            raise AssertionError("expected NotImplementedError")
