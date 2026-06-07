"""Offline tests for native Anthropic/Gemini adapters, custom headers, presets."""
import os
import tempfile

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ.setdefault("HUB_DATABASE_URL", f"sqlite:///{_tmp.name.replace(os.sep, '/')}")

from fastapi.testclient import TestClient  # noqa: E402

from app.models_layer.providers.anthropic import AnthropicProvider  # noqa: E402
from app.models_layer.providers.gemini import GeminiProvider  # noqa: E402
from app.models_layer.providers.openai_compat import OpenAICompatProvider  # noqa: E402
from app.models_layer.router import build_provider  # noqa: E402
from app.models_layer.types import ChatMessage, ChatRequest  # noqa: E402


def _req():
    return ChatRequest(messages=[
        ChatMessage(role="system", content="You are terse."),
        ChatMessage(role="user", content="hi"),
        ChatMessage(role="assistant", content="hello"),
        ChatMessage(role="user", content="bye"),
    ], params={"temperature": 0.5, "max_tokens": 256})


# ---------- Anthropic ----------

def test_anthropic_body_and_split():
    p = AnthropicProvider(base_url="https://api.anthropic.com", api_key="sk-ant")
    body = p._body("claude-3-5-sonnet", _req(), stream=False)
    assert body["model"] == "claude-3-5-sonnet"
    assert body["max_tokens"] == 256          # from params
    assert body["system"] == "You are terse."  # system pulled out of messages
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["user", "assistant", "user"]  # no system in messages
    assert body["temperature"] == 0.5


def test_anthropic_image_block():
    p = AnthropicProvider(base_url="https://api.anthropic.com", api_key="x")
    req = ChatRequest(messages=[ChatMessage(role="user", content=[
        {"type": "text", "text": "what is this?"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAB"}},
    ])])
    body = p._body("claude-3-5-sonnet", req, stream=False)
    blocks = body["messages"][0]["content"]
    assert blocks[0] == {"type": "text", "text": "what is this?"}
    assert blocks[1]["type"] == "image"
    assert blocks[1]["source"] == {"type": "base64", "media_type": "image/png", "data": "AAAB"}


def test_anthropic_max_tokens_default():
    p = AnthropicProvider(base_url="https://api.anthropic.com", api_key="x")
    body = p._body("m", ChatRequest(messages=[ChatMessage(role="user", content="hi")]), False)
    assert body["max_tokens"] == 1024  # required by Anthropic, sensible default


def test_anthropic_headers_with_custom():
    p = AnthropicProvider(base_url="https://api.anthropic.com", api_key="sk-ant",
                          extra={"headers": {"anthropic-beta": "x"}})
    h = p._headers()
    assert h["x-api-key"] == "sk-ant"
    assert h["anthropic-version"]
    assert h["anthropic-beta"] == "x"


# ---------- Gemini ----------

def test_gemini_body_and_split():
    p = GeminiProvider(base_url="https://generativelanguage.googleapis.com", api_key="k")
    body = p._body(_req())
    assert body["systemInstruction"]["parts"][0]["text"] == "You are terse."
    roles = [c["role"] for c in body["contents"]]
    assert roles == ["user", "model", "user"]  # assistant -> model
    assert body["generationConfig"]["maxOutputTokens"] == 256
    assert body["generationConfig"]["temperature"] == 0.5


def test_gemini_image_part():
    p = GeminiProvider(base_url="https://x", api_key="k")
    req = ChatRequest(messages=[ChatMessage(role="user", content=[
        {"type": "text", "text": "caption"},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,ZZZ"}},
    ])])
    parts = p._body(req)["contents"][0]["parts"]
    assert parts[0] == {"text": "caption"}
    assert parts[1] == {"inlineData": {"mimeType": "image/jpeg", "data": "ZZZ"}}


def test_gemini_extract():
    p = GeminiProvider(base_url="https://generativelanguage.googleapis.com", api_key="k")
    data = {"candidates": [{"content": {"parts": [{"text": "he"}, {"text": "llo"}]}}]}
    assert p._extract(data) == "hello"
    assert p._extract({"candidates": []}) == ""


def test_gemini_headers_custom():
    p = GeminiProvider(base_url="https://x", api_key="k", extra={"headers": {"X-Foo": "bar"}})
    h = p._headers()
    assert h["x-goog-api-key"] == "k"
    assert h["X-Foo"] == "bar"


# ---------- OpenAI-compat custom headers ----------

def test_openai_compat_custom_headers():
    p = OpenAICompatProvider(base_url="https://api.deepseek.com/v1", api_key="sk",
                             extra={"headers": {"X-Title": "PortHub"}})
    h = p._headers()
    assert h["Authorization"] == "Bearer sk"
    assert h["X-Title"] == "PortHub"


# ---------- kind registration ----------

def test_build_provider_kinds():
    assert isinstance(build_provider("anthropic", "https://api.anthropic.com", "k", 10), AnthropicProvider)
    assert isinstance(build_provider("gemini", "https://x", "k", 10), GeminiProvider)
    # unknown kind falls back to OpenAI-compatible
    assert isinstance(build_provider("totally-new-vendor", "https://x", "k", 10), OpenAICompatProvider)


# ---------- presets endpoint ----------

def test_test_endpoint_passes_extra(monkeypatch):
    """Regression: /providers/{id}/test must pass provider.extra (custom headers
    / advanced) to build_provider, or health/model-listing breaks for vendors
    that need a custom auth header."""
    import app.models_layer.api as api_mod
    from app.auth.deps import require_admin
    from app.db.models import Base, Provider
    from app.db.session import SessionLocal, engine
    from app.main import app

    Base.metadata.create_all(engine)
    db = SessionLocal()
    db.query(Provider).delete()
    p = Provider(name="hdr-test", kind="openai_compat", base_url="https://x",
                 api_key="k", extra={"headers": {"X-Foo": "bar"}})
    db.add(p)
    db.commit()
    pid = p.id
    db.close()

    captured: dict = {}

    class FakeProv:
        async def health(self):
            return True

        async def list_models(self):
            return ["m1", "m2"]

    def fake_build(kind, base_url, api_key, timeout=10, extra=None):
        captured["extra"] = extra
        return FakeProv()

    monkeypatch.setattr(api_mod, "build_provider", fake_build)
    app.dependency_overrides[require_admin] = lambda: object()
    try:
        r = TestClient(app).post(f"/api/models/providers/{pid}/test")
        assert r.status_code == 200
        assert r.json()["models"] == ["m1", "m2"]
        assert captured["extra"] == {"headers": {"X-Foo": "bar"}}  # the fix
    finally:
        app.dependency_overrides.pop(require_admin, None)


def test_provider_presets_endpoint():
    from app.auth.deps import get_current_user
    from app.db.models import Base
    from app.db.session import engine
    from app.main import app

    Base.metadata.create_all(engine)
    app.dependency_overrides[get_current_user] = lambda: object()
    c = TestClient(app)
    data = c.get("/api/models/provider-presets").json()
    ids = {p["id"] for p in data}
    assert {"openai", "anthropic", "gemini", "deepseek", "qwen", "kimi", "custom"} <= ids
    # Chinese vendors present
    assert any(p["group"] == "cn" for p in data)
    # anthropic/gemini use native kinds
    by_id = {p["id"]: p for p in data}
    assert by_id["anthropic"]["kind"] == "anthropic"
    assert by_id["gemini"]["kind"] == "gemini"
    assert by_id["deepseek"]["kind"] == "openai_compat"
