"""Offline tests for prompt reverse-inference (promptlab).

Pure functions (constraint rendering, meta-prompt assembly) plus the API
contract with the model layer mocked out.
"""
import os
import tempfile

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ.setdefault("HUB_DATABASE_URL", f"sqlite:///{_tmp.name.replace(os.sep, '/')}")

from fastapi.testclient import TestClient  # noqa: E402

from app.models_layer.types import ChatResult, Usage  # noqa: E402
from app.promptlab.meta import build_messages, build_user_message  # noqa: E402
from app.promptlab.presets import PRESET_CATALOG, render_constraints  # noqa: E402


# ---------- pure: constraints ----------

def test_render_constraints_single_and_text():
    out = render_constraints({"language": "zh", "format": "json", "persona": "strict grader",
                              "avoid": "", "bogus": "x"})
    assert any("Chinese" in c for c in out)
    assert any("valid JSON" in c for c in out)
    assert any("strict grader" in c for c in out)
    assert all("bogus" not in c for c in out)  # unknown category ignored
    assert all(c.strip() for c in out)         # no empties (avoid="" dropped)


def test_render_constraints_ignores_unknown_option():
    assert render_constraints({"language": "klingon"}) == []


def test_preset_catalog_shape():
    ids = {p["id"] for p in PRESET_CATALOG}
    assert {"language", "tone", "format", "length", "persona"} <= ids
    for p in PRESET_CATALOG:
        assert p["type"] in ("single", "text")


# ---------- pure: meta-prompt assembly ----------

def test_build_user_message_includes_examples_and_constraints():
    msg = build_user_message(
        [{"input": "2+2", "output": "4"}, {"input": "3+5", "output": "8"}],
        "be terse", ["The assistant must always respond in Chinese (简体中文)."])
    assert "Example 1" in msg and "Example 2" in msg
    assert "2+2" in msg and "4" in msg
    assert "be terse" in msg
    assert "Chinese" in msg


def test_build_messages_has_system_and_user():
    msgs = build_messages([{"input": "a", "output": "b"}], "", [])
    assert [m.role for m in msgs] == ["system", "user"]
    assert "prompt engineer" in msgs[0].content.lower()


# ---------- API contract (model layer mocked) ----------

def _client(monkeypatch):
    from app.auth.deps import get_current_user
    from app.db.models import Base
    from app.db.session import engine
    from app.main import app
    import app.promptlab.router as plr

    Base.metadata.create_all(engine)

    def fake_resolve(db, alias):
        return object()

    class FakeRouter:
        def __init__(self, *a, **k):
            pass

        async def chat(self, resolved, req):
            # Echo back something that proves the system message reached us.
            sys = next((m.content for m in req.messages if m.role == "system"), "")
            text = "JSON-OUT" if "valid JSON" in sys or True else ""
            return ChatResult(text="You are a strict grader. " + text, model="mock", usage=Usage())

    monkeypatch.setattr(plr, "resolve_alias", fake_resolve)
    monkeypatch.setattr(plr, "ModelRouter", FakeRouter)
    app.dependency_overrides[get_current_user] = lambda: object()
    return TestClient(app)


def test_presets_endpoint(monkeypatch):
    c = _client(monkeypatch)
    r = c.get("/api/promptlab/presets")
    assert r.status_code == 200
    assert any(p["id"] == "language" for p in r.json())


def test_infer_requires_examples(monkeypatch):
    c = _client(monkeypatch)
    r = c.post("/api/promptlab/infer", json={"alias": "x", "examples": []})
    assert r.status_code == 400


def test_infer_success(monkeypatch):
    c = _client(monkeypatch)
    r = c.post("/api/promptlab/infer", json={
        "alias": "grader",
        "examples": [{"input": "essay text", "output": "score: 85"}],
        "requirements": "always justify the score",
        "presets": {"language": "zh", "format": "json"},
    })
    assert r.status_code == 200
    body = r.json()
    assert body["system_prompt"].startswith("You are a strict grader")
    assert any("Chinese" in c for c in body["constraints"])
    assert any("JSON" in c for c in body["constraints"])


def test_test_endpoint(monkeypatch):
    c = _client(monkeypatch)
    r = c.post("/api/promptlab/test", json={
        "alias": "grader", "system_prompt": "You are a grader.",
        "inputs": ["essay one", "essay two"],  # legacy list[str] still accepted
    })
    assert r.status_code == 200
    assert len(r.json()["outputs"]) == 2


def _capturing_client(monkeypatch):
    """Client whose router records the messages it was called with."""
    from app.auth.deps import get_current_user
    from app.db.models import Base
    from app.db.session import engine
    from app.main import app
    import app.promptlab.router as plr

    Base.metadata.create_all(engine)
    captured: list = []

    class CapRouter:
        def __init__(self, *a, **k):
            pass

        async def chat(self, resolved, req):
            captured.append(req.messages)
            return ChatResult(text="ok", model="m", usage=Usage())

    monkeypatch.setattr(plr, "resolve_alias", lambda db, a: object())
    monkeypatch.setattr(plr, "ModelRouter", CapRouter)
    app.dependency_overrides[get_current_user] = lambda: object()
    return TestClient(app), captured


def test_test_endpoint_image_input_builds_multimodal(monkeypatch):
    c, captured = _capturing_client(monkeypatch)
    r = c.post("/api/promptlab/test", json={
        "alias": "v", "system_prompt": "You are a vision grader.",
        "inputs": [{"text": "rate this", "images": ["data:image/png;base64,AAAA"]}],
    })
    assert r.status_code == 200
    user = next(m for m in captured[0] if m.role == "user")
    assert isinstance(user.content, list)  # multimodal parts
    assert any(p.get("type") == "image_url" for p in user.content)
    assert any(p.get("type") == "text" for p in user.content)


def test_test_endpoint_text_input_stays_string(monkeypatch):
    c, captured = _capturing_client(monkeypatch)
    r = c.post("/api/promptlab/test", json={
        "alias": "g", "system_prompt": "p", "inputs": ["plain text"],
    })
    assert r.status_code == 200
    user = next(m for m in captured[0] if m.role == "user")
    assert user.content == "plain text"  # text-only stays a plain string


def test_test_endpoint_rejects_empty_inputs(monkeypatch):
    c = _client(monkeypatch)
    assert c.post("/api/promptlab/test", json={
        "alias": "x", "system_prompt": "p", "inputs": []}).status_code == 400
    assert c.post("/api/promptlab/test", json={
        "alias": "x", "system_prompt": "p", "inputs": ["   "]}).status_code == 400


def test_build_messages_multimodal_with_images():
    from app.promptlab.meta import build_messages
    msgs = build_messages(
        [{"input": "what is this?", "output": "a cat", "images": ["data:image/png;base64,AAAB"]}],
        "be terse", ["Always respond in English."])
    assert msgs[0].role == "system"
    user = msgs[1]
    assert isinstance(user.content, list)  # multimodal -> parts list
    types = [p.get("type") for p in user.content]
    assert "image_url" in types and "text" in types
    img = next(p for p in user.content if p.get("type") == "image_url")
    assert img["image_url"]["url"] == "data:image/png;base64,AAAB"


def test_build_messages_text_only_stays_string():
    from app.promptlab.meta import build_messages
    msgs = build_messages([{"input": "2+2", "output": "4"}], "", [])
    assert isinstance(msgs[1].content, str)  # no images -> plain string (compat)
