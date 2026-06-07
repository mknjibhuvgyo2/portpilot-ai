"""Tests for the embedding app template + router embed fallback."""
import asyncio
import os
import tempfile

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ.setdefault("HUB_DATABASE_URL", f"sqlite:///{_tmp.name.replace(os.sep, '/')}")

from fastapi.testclient import TestClient  # noqa: E402

from app.apps.base import PortConfig  # noqa: E402
from app.models_layer.router import ModelRouter, ResolvedAlias, ResolvedTarget  # noqa: E402
from app.models_layer.types import Usage  # noqa: E402


def _rt(model, label):
    return ResolvedTarget(kind="x", base_url="", api_key="", model=model, label=label)


def test_router_embed_skips_unsupported_then_succeeds(monkeypatch):
    import app.models_layer.router as r

    class P1:  # can't embed
        async def embed(self, model, inputs):
            raise NotImplementedError

    class P2:  # works
        async def embed(self, model, inputs):
            return [[0.1, 0.2] for _ in inputs], Usage(prompt_tokens=3)

    provs = iter([P1(), P2()])
    monkeypatch.setattr(r, "build_provider", lambda *a, **k: next(provs))
    resolved = ResolvedAlias(alias="e", targets=[_rt("m1", "t1"), _rt("m2", "t2")])
    vectors, usage = asyncio.run(ModelRouter().embed(resolved, ["hi", "yo"]))
    assert vectors == [[0.1, 0.2], [0.1, 0.2]]
    assert usage.prompt_tokens == 3


def test_embedding_registered():
    from app.apps.registry import get_template, list_templates
    types = {t["app_type"] for t in list_templates()}
    assert "embedding" in types
    assert get_template("embedding") is not None


def test_embedding_endpoint_openai_shape(monkeypatch):
    import app.apps.embedding as emod

    monkeypatch.setattr(emod, "resolve_alias", lambda db, alias: object())
    monkeypatch.setattr(emod.metrics, "record", lambda *a, **k: None)

    class FakeRouter:
        def __init__(self, **k):
            pass

        async def embed(self, resolved, texts):
            return [[0.1, 0.2, 0.3] for _ in texts], Usage(prompt_tokens=5)

    monkeypatch.setattr(emod, "ModelRouter", FakeRouter)
    cfg = PortConfig(id=1, name="emb", slug="emb", port=9099, app_type="embedding",
                     model_alias="e", logging_enabled=False)
    c = TestClient(emod.build_embedding_app(cfg))

    r = c.post("/v1/embeddings", json={"input": ["a", "b"]})
    assert r.status_code == 200
    d = r.json()
    assert d["object"] == "list"
    assert len(d["data"]) == 2
    assert d["data"][0]["embedding"] == [0.1, 0.2, 0.3]
    assert d["data"][1]["index"] == 1
    assert d["model"] == "e"
    assert d["usage"]["prompt_tokens"] == 5

    # string input is accepted too
    assert c.post("/v1/embeddings", json={"input": "solo"}).json()["data"][0]["index"] == 0
    # missing input -> 400
    assert c.post("/v1/embeddings", json={}).status_code == 400
