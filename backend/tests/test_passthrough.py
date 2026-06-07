"""Tests for the passthrough proxy app template + router raw passthrough."""
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


def test_router_raw_chat_skips_unsupported_then_succeeds(monkeypatch):
    import app.models_layer.router as r

    class P1:  # native provider w/o passthrough
        async def raw_chat(self, model, body):
            raise NotImplementedError

    class P2:
        async def raw_chat(self, model, body):
            return {"model": model, "echo": body.get("tools")}

    provs = iter([P1(), P2()])
    monkeypatch.setattr(r, "build_provider", lambda *a, **k: next(provs))
    resolved = ResolvedAlias(alias="p", targets=[_rt("m1", "t1"), _rt("m2", "t2")])
    data = asyncio.run(ModelRouter().raw_chat(resolved, {"messages": [], "tools": ["x"]}))
    assert data["model"] == "m2"
    assert data["echo"] == ["x"]


def test_passthrough_registered():
    from app.apps.registry import get_template, list_templates
    types = {t["app_type"] for t in list_templates()}
    assert "passthrough" in types
    assert get_template("passthrough") is not None


def test_passthrough_endpoint_forwards_full_body(monkeypatch):
    import app.apps.passthrough as pmod

    monkeypatch.setattr(pmod, "resolve_alias", lambda db, alias: object())
    monkeypatch.setattr(pmod.metrics, "record", lambda *a, **k: None)
    captured = {}

    class FakeRouter:
        def __init__(self, **k):
            pass

        async def raw_chat(self, resolved, body):
            captured.update(body)
            # echo upstream-style response verbatim
            return {"id": "x", "model": "real-model", "choices": [{"message": {"content": "hi"}}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 1}}

    monkeypatch.setattr(pmod, "ModelRouter", FakeRouter)
    cfg = PortConfig(id=1, name="pt", slug="pt", port=9097, app_type="passthrough",
                     model_alias="p", logging_enabled=False)
    c = TestClient(pmod.build_passthrough_app(cfg))

    body = {
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [{"type": "function", "function": {"name": "f"}}],
        "response_format": {"type": "json_object"},
        "seed": 42, "stream": False,
    }
    r = c.post("/v1/chat/completions", json=body)
    assert r.status_code == 200
    # response returned verbatim from upstream
    assert r.json()["model"] == "real-model"
    # full body forwarded (no param filtering, no system-prompt injection)
    assert captured["tools"][0]["function"]["name"] == "f"
    assert captured["response_format"] == {"type": "json_object"}
    assert captured["seed"] == 42
    assert all(m["role"] != "system" for m in captured["messages"])  # no injected system

    # missing messages -> 400
    assert c.post("/v1/chat/completions", json={}).status_code == 400


def test_passthrough_streaming_forwards_bytes(monkeypatch):
    import app.apps.passthrough as pmod

    monkeypatch.setattr(pmod, "resolve_alias", lambda db, alias: object())
    monkeypatch.setattr(pmod.metrics, "record", lambda *a, **k: None)

    class FakeRouter:
        def __init__(self, **k):
            pass

        async def raw_stream(self, resolved, body):
            yield b"data: {\"choices\":[{\"delta\":{\"content\":\"A\"}}]}\n\n"
            yield b"data: [DONE]\n\n"

    monkeypatch.setattr(pmod, "ModelRouter", FakeRouter)
    cfg = PortConfig(id=2, name="pt", slug="pt2", port=9096, app_type="passthrough",
                     model_alias="p", streaming=True, logging_enabled=False)
    c = TestClient(pmod.build_passthrough_app(cfg))
    r = c.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "x"}],
                                             "stream": True})
    assert r.status_code == 200
    assert "delta" in r.text and "[DONE]" in r.text


def test_openai_compat_raw_chat_overrides_model_and_stream(monkeypatch):
    import app.models_layer.providers.openai_compat as oc

    seen = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": True}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            seen.update(json)
            return _Resp()

    monkeypatch.setattr(oc.httpx, "AsyncClient", _Client)
    prov = oc.OpenAICompatProvider(base_url="http://x", api_key="")
    data = asyncio.run(prov.raw_chat("real-model", {"messages": [], "stream": True, "tools": [1]}))
    assert data == {"ok": True}
    assert seen["model"] == "real-model"   # model overridden to upstream name
    assert seen["stream"] is False         # forced non-stream for raw_chat
    assert seen["tools"] == [1]            # arbitrary fields preserved
