"""Tests for the rerank app template + router rerank fallback + provider parse."""
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


def test_router_rerank_skips_unsupported_then_succeeds(monkeypatch):
    import app.models_layer.router as r

    class P1:  # can't rerank
        async def rerank(self, model, query, documents, top_n=None):
            raise NotImplementedError

    class P2:  # works
        async def rerank(self, model, query, documents, top_n=None):
            return [{"index": 1, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.2}], Usage(prompt_tokens=4)

    provs = iter([P1(), P2()])
    monkeypatch.setattr(r, "build_provider", lambda *a, **k: next(provs))
    resolved = ResolvedAlias(alias="rr", targets=[_rt("m1", "t1"), _rt("m2", "t2")])
    results, usage = asyncio.run(ModelRouter().rerank(resolved, "q", ["a", "b"]))
    assert results[0]["index"] == 1
    assert usage.prompt_tokens == 4


def test_rerank_registered():
    from app.apps.registry import get_template, list_templates
    types = {t["app_type"] for t in list_templates()}
    assert "rerank" in types
    assert get_template("rerank") is not None


def test_rerank_endpoint_shape_and_validation(monkeypatch):
    import app.apps.rerank as rmod

    monkeypatch.setattr(rmod, "resolve_alias", lambda db, alias: object())
    monkeypatch.setattr(rmod.metrics, "record", lambda *a, **k: None)

    class FakeRouter:
        def __init__(self, **k):
            pass

        async def rerank(self, resolved, query, documents, top_n=None):
            # return out-of-order; the endpoint trusts provider/router order
            return [{"index": 2, "relevance_score": 0.95},
                    {"index": 0, "relevance_score": 0.80},
                    {"index": 1, "relevance_score": 0.10}], Usage(prompt_tokens=7)

    monkeypatch.setattr(rmod, "ModelRouter", FakeRouter)
    cfg = PortConfig(id=1, name="rr", slug="rr", port=9098, app_type="rerank",
                     model_alias="r", logging_enabled=False)
    c = TestClient(rmod.build_rerank_app(cfg))

    docs = ["alpha", "bravo", "charlie"]
    r = c.post("/v1/rerank", json={"query": "q", "documents": docs})
    assert r.status_code == 200
    d = r.json()
    assert d["object"] == "list"
    assert d["model"] == "r"
    assert d["results"][0]["index"] == 2
    assert d["results"][0]["relevance_score"] == 0.95
    assert d["usage"]["prompt_tokens"] == 7
    # by default no document text echoed
    assert "document" not in d["results"][0]

    # top_n truncates
    r2 = c.post("/v1/rerank", json={"query": "q", "documents": docs, "top_n": 2})
    assert len(r2.json()["results"]) == 2

    # return_documents echoes the source text by index
    r3 = c.post("/v1/rerank", json={"query": "q", "documents": docs, "return_documents": True})
    top = r3.json()["results"][0]
    assert top["document"]["text"] == "charlie"  # index 2

    # validation
    assert c.post("/v1/rerank", json={"documents": docs}).status_code == 400        # no query
    assert c.post("/v1/rerank", json={"query": "q"}).status_code == 400             # no documents
    assert c.post("/v1/rerank", json={"query": "q", "documents": []}).status_code == 400


def test_openai_compat_rerank_normalizes_and_sorts(monkeypatch):
    """Provider parses `score`/`relevance_score`, sorts desc, reads usage."""
    import app.models_layer.providers.openai_compat as oc

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": [
                {"index": 0, "score": 0.3},                 # uses `score`
                {"index": 1, "relevance_score": 0.7},       # uses `relevance_score`
            ], "usage": {"prompt_tokens": 11}}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            assert url.endswith("/v1/rerank")
            assert json["query"] == "q" and json["documents"] == ["a", "b"]
            return _Resp()

    monkeypatch.setattr(oc.httpx, "AsyncClient", _Client)
    prov = oc.OpenAICompatProvider(base_url="http://x", api_key="")
    results, usage = asyncio.run(prov.rerank("m", "q", ["a", "b"]))
    assert [r["index"] for r in results] == [1, 0]        # sorted by score desc
    assert results[0]["relevance_score"] == 0.7
    assert usage.prompt_tokens == 11
